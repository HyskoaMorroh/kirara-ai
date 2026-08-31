"""发送节流：避免触发 QQ 风控（需求 11）。

被融入的 OneBot 适配器项目在每次 flush 之前主动等一段时间
（`im_onebot_adapters/adapter.py:399`）：

    duration = max(text_length * 0.1, 1) + random.uniform(0.5, 1.5)
    await asyncio.sleep(duration)

那不是重试退避，而是**主动限速**。QQ 对短时间内连发多条消息有风控，命中之后账号
会被限制发言——而这跟「消息发失败」是完全不同的故障形态：接口全部返回成功，
消息却没有到达对方，且要等很久才恢复。日志里一切正常，用户那边什么都没收到。

本项目的 outbox 只有**失败重试**退避（`retry_delay_seconds`、
`retry_backoff_seconds`）：那是「发失败了等一会儿再试」。节流是「发成功了也要等，
下一条才不会被判定为刷屏」。两者方向相反，无法互相替代——融入时漏掉它，
一个从旧项目迁过来的用户会撞上旧项目专门规避掉的那个风控。

三条边界：

* **按文本长度算**，与原实现同一口径：长文本本身已占用对方的阅读时间，
  短文本连发才是风控最敏感的形态。
* **带随机抖动**：固定间隔本身就是一种可识别的机器特征。
* **有上界**：风控看的是频率，不是「等得够不够久」；超过某个点再等只是在惩罚用户。

节流只发生在**同一次投递的页与页之间**，不在第一页之前：让用户多等一秒才看到
第一个字，是把一个不存在的问题加到每一次正常对话上。

## 为什么按长度计费还要再加一层总预算

原实现的 `flush()` 是**按消息段**触发的，那些段通常只有几十个字符；本项目把
文本先分页（一页 3800 字节，约 1300–1900 字符）再逐页发送。同一条
`text_length * 0.1` 公式换了计费单位之后行为完全变了：长度项在 **80 字符**
就撞上 `maximum_seconds`，于是

* 「按长度递增」对任何真实页面都不再起作用——每一页算出同一个数；
* 抖动被上界一起裁掉，页间间隔变成恒定的 8.000 秒，而这正是本文件另一条
  判据「固定间隔本身就是一种可识别的机器特征」要避免的形态；
* 用户侧代价随页数线性累加：一条 4578 字符的回复分成 3 条要纯等 13.7 秒，
  带 3 个代码块的回复分成 10 条要纯等 54.6 秒。而现场报障恰恰是
  「系统显示成功，QQ 却要等很久才收到」。

因此把等待拆成两部分，各自受不同的约束：

* **下界**（``minimum_seconds``）是「不连发」的硬保证，每个间隙都要付，
  不参与预算——它正是风控真正针对的那件事。
* **按长度追加的那部分**受 ``maximum_total_seconds`` 约束，按这次投递的间隙数
  摊开。页数越多每个间隙分到越少，总额不再线性增长。

抖动改为**围绕确定值双向摆动**（``[deterministic - jitter, deterministic + jitter]``，
再夹到 ``[minimum_seconds, maximum_seconds]``）。此前抖动只向上加、随后被
``min()`` 裁掉，于是撞上界时它恰好被完全抵消——恒定 8.000 秒是最可识别的机器
特征，而这正是引入抖动要避免的。双向摆动让它在**任何**页面尺寸下都存在：
短页面向上摆（``1.0..2.0``），满页向下摆（``7.0..8.0``）。

``total_wait_bound()`` 把「这次投递最多等多久」表达成可计算的值，供调用方与
测试断言，而不是让各处自己按参数重推一遍。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional


@dataclass(frozen=True)
class SendPacing:
    """页与页之间的主动限速参数。

    默认开启且取值贴近被融入项目的实测值。默认关掉等于让每个新部署自己踩一次
    风控才知道要开——而那次踩中的代价是账号被限制发言，不是一条消息失败。
    """

    enabled: bool = True
    #: 每个字符附加的等待秒数。原实现是 0.1。
    per_character_seconds: float = 0.1
    #: 下界。短文本连发是风控最敏感的形态，所以长度为 0 也要等。
    minimum_seconds: float = 1.0
    #: 抖动上限，实际抖动取 ``[0, jitter_seconds]``。原实现是 0.5–1.5 的随机量，
    #: 这里拆成「下界 1.0 + 抖动 1.0」，区间与原实现一致。
    jitter_seconds: float = 1.0
    #: 单个间隙的上界。没有它时，一条很长的消息会把整轮对话拖成分钟级。
    maximum_seconds: float = 8.0
    #: **一次投递**里按长度追加的等待总额上界。
    #:
    #: 只有单页上界时，代价随页数线性累加：10 页要等 72 秒，而风控计的是
    #: 单位时间内的条数，不是累计时长。下界不计入这个预算（它是「不连发」的硬
    #: 保证），只有按长度追加的那部分受它约束。
    #:
    #: 为什么必须有这一层：``per_character_seconds = 0.1`` 是从原实现照搬的，
    #: 而原实现按**消息段**计费（一个文本段通常几十字符）。本项目先分页
    #: 再发送，一页 3800 字节；同一个系数换了计费单位之后，0.1 秒/字符
    #: 表达的是「每秒打 10 个字」的打字模拟，而不是任何风控口径。
    #:
    #: 取 6.0：三页的常见回复从 13.7 秒降到 8 秒上下，十页的长回复从 54.6 秒
    #: 降到 15 秒上下，而每个间隙仍然至少有一秒的下界。
    maximum_total_seconds: float = 6.0

    def __post_init__(self) -> None:
        for name in (
            "per_character_seconds",
            "minimum_seconds",
            "jitter_seconds",
            "maximum_seconds",
            "maximum_total_seconds",
        ):
            value = getattr(self, name)
            if value < 0:
                # 负延迟会变成 `asyncio.sleep(-x)`，那是 ValueError，
                # 且会在一次正常发送里冒出来。
                raise ValueError(f"{name} cannot be negative")
        if self.maximum_seconds < self.minimum_seconds:
            # 这组配置无解；静默取其中一个会让另一个设置永远不生效。
            raise ValueError("maximum_seconds cannot be below minimum_seconds")

    async def wait_before_page(
        self,
        page_index: int,
        *,
        text_length: int,
        page_count: Optional[int] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> float:
        """在发送第 ``page_index`` 页之前等待，返回**实际等了多少秒**。

        第一页（``page_index == 0``）不等：首字延迟是用户唯一能直接感知的耗时，
        而风控针对的是「连发」，第一条本身不构成连发。

        ``page_count`` 是本次投递的总页数，用于把按长度追加的等待摊到各个间隙上。
        省略时按单页上界计费，行为与只有 ``maximum_seconds`` 时一致——既有调用方
        与第三方插件不传它也必须能工作。

        返回值供调用方把「我们主动等的时间」单独记入投递时间线（需求 19.5：
        发送限流不能与「上游真的慢」混成一个「QQ 慢」）。此前返回 ``None``，
        于是那段时间只存在于墙钟差里，无从与上游耗时分开。
        既有调用方忽略返回值即可，行为不变。
        """
        if page_index <= 0:
            return 0.0
        delay = resolve_pacing_delay(
            self,
            text_length=text_length,
            page_index=page_index,
            page_count=page_count,
        )
        if delay <= 0:
            return 0.0
        await (sleep or asyncio.sleep)(delay)
        return delay

    def total_wait_bound(self, page_count: int) -> float:
        """一次 ``page_count`` 页的投递最多会等多久。

        由本模块给出而不是让调用方按参数重推：重推的那份迟早与实现分叉，
        而分叉的症状是「按文档算 12 秒，实际等了 22 秒」。
        """
        if not self.enabled:
            return 0.0
        gaps = max(0, int(page_count) - 1)
        if gaps == 0:
            return 0.0
        floor = min(self.minimum_seconds + self.jitter_seconds, self.maximum_seconds)
        return gaps * floor + self.maximum_total_seconds


def resolve_pacing_delay(
    pacing: SendPacing,
    *,
    text_length: int,
    page_index: Optional[int] = None,
    page_count: Optional[int] = None,
) -> float:
    """算出这一页之前该等多少秒；关闭时返回 ``0.0``。

    关闭必须是**恰好 0**而不是「等得少一点」：压测与自建部署要的是完全不等。

    ``page_count`` 给出时，按长度追加的那部分按间隙数（``page_count - 1``）
    摊开，总额不超过 ``maximum_total_seconds``。下界不参与摊分：它是
    「不把两条消息紧贴着发出去」的硬保证，而那正是风控真正针对的行为。
    """
    if not pacing.enabled:
        return 0.0

    length_term = max(0, int(text_length)) * pacing.per_character_seconds
    gaps = _gap_count(page_index, page_count)
    # 摊分只减少每个间隙的**追加**部分，不动下界。
    length_term = min(length_term, pacing.maximum_total_seconds / gaps)

    deterministic = min(
        pacing.minimum_seconds + length_term, pacing.maximum_seconds
    )
    if not pacing.jitter_seconds:
        return deterministic

    # 双向摆动：只向上加会在撞上界时被 min() 完全裁掉，于是长页面的间隙变成
    # 恒定值——正是本模块判据点名要避免的「可识别的机器特征」。
    jitter = random.uniform(-pacing.jitter_seconds, pacing.jitter_seconds)
    return min(
        max(deterministic + jitter, pacing.minimum_seconds), pacing.maximum_seconds
    )


def _gap_count(page_index: Optional[int], page_count: Optional[int]) -> int:
    """本次投递里需要等待的间隙数；无从得知时返回 1（按单页上界计费）。"""
    if page_count is None:
        return 1
    return max(1, int(page_count) - 1)
