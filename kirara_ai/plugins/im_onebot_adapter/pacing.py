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
    #: 上界。没有它时，一条很长的消息会把整轮对话拖成分钟级。
    maximum_seconds: float = 8.0

    def __post_init__(self) -> None:
        for name in ("per_character_seconds", "minimum_seconds", "jitter_seconds", "maximum_seconds"):
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
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        """在发送第 ``page_index`` 页之前等待。

        第一页（``page_index == 0``）不等：首字延迟是用户唯一能直接感知的耗时，
        而风控针对的是「连发」，第一条本身不构成连发。
        """
        if page_index <= 0:
            return
        delay = resolve_pacing_delay(self, text_length=text_length)
        if delay <= 0:
            return
        await (sleep or asyncio.sleep)(delay)


def resolve_pacing_delay(pacing: SendPacing, *, text_length: int) -> float:
    """算出这一页之前该等多少秒；关闭时返回 ``0.0``。

    关闭必须是**恰好 0**而不是「等得少一点」：压测与自建部署要的是完全不等。
    """
    if not pacing.enabled:
        return 0.0
    base = max(
        max(0, int(text_length)) * pacing.per_character_seconds,
        pacing.minimum_seconds,
    )
    if pacing.jitter_seconds:
        base += random.uniform(0.0, pacing.jitter_seconds)
    return min(base, pacing.maximum_seconds)
