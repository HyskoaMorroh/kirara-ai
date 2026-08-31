"""节流不能把一次正常回复拖成分钟级（需求 5）。

`pacing.py` 自己写下的判据是「**风控看的是频率**，不是『等得够不够久』」，
可它的实现按**每一页的字符数**递增计费，而一页的容量是 3800 字节
（约 1300–1900 字符）。默认 `per_character_seconds=0.1`、`maximum_seconds=8.0`
意味着长度项在 **80 字符**就撞上上界——于是：

* 「按长度递增」在真实页面上完全失效，每一页都是同一个数；
* 「随机抖动」也一起失效，页间间隔变成恒定的 8.000 秒。而 `pacing.py`
  另一条判据正是「固定间隔本身就是一种可识别的机器特征」——恒定 8.000s
  是最可识别的那种；
* 用户侧代价按页数线性累加：现场那条 4578 字符的回复分成 3 条，纯等待
  13.7 秒；带 3 个代码块的回复分成 10 条，纯等待 54.6 秒。

现场报障正是「系统显示成功，QQ 却要等很久才收到」，且「Telegram 与 WeCom
没有这种现象」——后半句成立的原因是 `pacing` 全仓只有 OneBot 一家在用。

## 因此按频率计费，而不是按长度累加

一次投递的页与页之间仍然要等（不等就是连发），但**整次投递的等待有总预算**：
风控计的是单位时间内的消息条数，把固定预算摊到这一次回复的各个间隙上，
既保住「没有两条消息紧贴着发出去」，又让用户侧代价不随页数无限增长。

四条边界：

1. **第一页仍然不等。** 首字延迟是用户唯一能直接感知的耗时。
2. **总预算是上界，不是配额。** 页数少时不会为了「花完预算」而多等。
3. **抖动必须在上界之后仍然存在。** 否则那条「不要固定间隔」的判据形同不存在。
4. **关闭时恰好是 0。** 压测与自建部署要的是完全不等。
"""

from __future__ import annotations

import asyncio

import pytest

from kirara_ai.plugins.im_onebot_adapter.pacing import (
    SendPacing,
    resolve_pacing_delay,
)


class TestLengthTermIsNotDeadOnRealPages:
    """长度项必须在真实页面尺寸上仍然有区分度。"""

    def test_a_full_page_does_not_sit_on_the_ceiling(self):
        """一页 1900 字符不该与 80 字符等价。

        撞上界之后长度项就是一个常数，`per_character_seconds` 这个配置项
        对任何真实页面都不再起作用——界面上调了，运行时不变。
        """
        pacing = SendPacing()

        short = resolve_pacing_delay(pacing, text_length=80)
        full_page = resolve_pacing_delay(pacing, text_length=1900)

        assert short < pacing.maximum_seconds, (
            f"80 字符就已经撞上界（{short}s）；长度项对任何真实页面都失效了"
        )
        assert full_page <= pacing.maximum_seconds

    def test_jitter_survives_the_ceiling(self):
        """撞上界之后抖动不能消失。

        `pacing.py` 的判据之一是「固定间隔本身就是一种可识别的机器特征」。
        上界把抖动一起裁掉时，页间间隔变成恒定值——正是那条判据要避免的形态。
        """
        pacing = SendPacing()

        samples = {
            resolve_pacing_delay(pacing, text_length=100_000) for _ in range(200)
        }

        assert len(samples) > 1, "撞上界后间隔变成恒定值，抖动被裁掉了"


class TestDeliveryBudget:
    """一次投递的总等待有上界，不随页数线性增长。"""

    def test_the_default_budget_is_declared(self):
        assert SendPacing().maximum_total_seconds > 0

    def test_a_long_reply_stays_inside_the_budget(self):
        """10 条消息的回复不该等 50 秒以上。"""
        pacing = SendPacing()

        total = 0.0
        for page_index in range(1, 10):
            total += resolve_pacing_delay(
                pacing,
                text_length=1900,
                page_index=page_index,
                page_count=10,
            )

        assert total <= pacing.total_wait_bound(10) + 1e-6, (
            f"9 个间隙共等待 {total:.1f}s，超出声明的上界 "
            f"{pacing.total_wait_bound(10):.1f}s"
        )

    def test_the_declared_bound_is_far_below_the_old_behaviour(self):
        """旧行为下 10 页要等 8×9=72 秒；上界必须显著低于它，否则这层预算没有意义。"""
        pacing = SendPacing()

        old_behaviour = pacing.maximum_seconds * 9

        assert pacing.total_wait_bound(10) < old_behaviour / 2

    def test_a_single_page_reply_waits_nothing(self):
        assert SendPacing().total_wait_bound(1) == 0.0

    def test_a_short_reply_does_not_spend_the_whole_budget(self):
        """预算是上界而不是配额：两页的回复不该为了花完预算而多等。"""
        pacing = SendPacing()

        two_pages = resolve_pacing_delay(
            pacing, text_length=40, page_index=1, page_count=2
        )

        assert two_pages <= pacing.maximum_seconds

    def test_more_pages_means_shorter_gaps_not_a_longer_total(self):
        """页数翻倍时按长度追加的总额不翻倍——风控计的是频率，不是累计时长。

        只看**追加**部分：下界是每个间隙都要付的硬保证，它必然随页数增长，
        把它算进来会让这条断言变成「下界必须能被摊掉」，而那是错的。
        """
        pacing = SendPacing(jitter_seconds=0.0)

        def extra_for(page_count: int) -> float:
            total = sum(
                resolve_pacing_delay(
                    pacing,
                    text_length=1900,
                    page_index=index,
                    page_count=page_count,
                )
                for index in range(1, page_count)
            )
            return total - pacing.minimum_seconds * (page_count - 1)

        assert extra_for(16) <= pacing.maximum_total_seconds + 1e-6
        assert extra_for(4) <= pacing.maximum_total_seconds + 1e-6

    def test_the_budget_can_be_turned_off_together_with_pacing(self):
        pacing = SendPacing(enabled=False)

        assert (
            resolve_pacing_delay(
                pacing, text_length=1900, page_index=5, page_count=10
            )
            == 0.0
        )
        assert pacing.total_wait_bound(10) == 0.0

    def test_a_negative_budget_is_refused(self):
        with pytest.raises(ValueError):
            SendPacing(maximum_total_seconds=-1.0)


class TestPageIndexIsOptional:
    """页序号是可选参数：既有调用方（与第三方插件）不传它也必须能工作。"""

    def test_omitting_the_page_count_still_returns_a_delay(self):
        pacing = SendPacing()

        assert resolve_pacing_delay(pacing, text_length=100) > 0


class TestWaitBeforePage:
    @pytest.mark.asyncio
    async def test_the_first_page_is_still_not_delayed(self):
        pacing = SendPacing()
        slept: list[float] = []

        await pacing.wait_before_page(
            0, text_length=1900, page_count=8, sleep=lambda value: _record(slept, value)
        )

        assert slept == []

    @pytest.mark.asyncio
    async def test_a_multi_page_reply_spends_at_most_the_budget(self):
        """端到端：把一次 8 页投递的每个间隙加起来，不超过声明的上界。"""
        pacing = SendPacing()
        slept: list[float] = []

        for page_index in range(8):
            await pacing.wait_before_page(
                page_index,
                text_length=1900,
                page_count=8,
                sleep=lambda value: _record(slept, value),
            )

        bound = pacing.total_wait_bound(8)
        assert sum(slept) <= bound + 1e-6, (
            f"8 页共等待 {sum(slept):.1f}s，超出声明的上界 {bound:.1f}s"
        )

    @pytest.mark.asyncio
    async def test_the_reported_reply_shape_is_no_longer_minutes_long(self):
        """现场那条回复分成 3 条时，纯等待从 13.7 秒降到 10 秒以内。"""
        pacing = SendPacing()
        slept: list[float] = []

        for page_index, length in enumerate((2102, 2229, 57)):
            await pacing.wait_before_page(
                page_index,
                text_length=length,
                page_count=3,
                sleep=lambda value: _record(slept, value),
            )

        assert sum(slept) < 10.0, f"仍然要等 {sum(slept):.1f}s"


async def _record(bucket: list[float], value: float) -> None:
    bucket.append(value)
    await asyncio.sleep(0)


class TestBothSendPathsPassThePageCount:
    """两条投递路径都要把总页数传给节流，否则预算形同不存在。

    `page_count` 是可选参数（既有调用方与第三方插件不传它也能工作），
    于是漏传不会报错，只会静默回到「按单页上界累加」——症状正是现场那条
    「系统显示成功，QQ 却要等很久才收到」。
    """

    def test_the_direct_path_passes_the_page_count(self):
        import inspect

        from kirara_ai.plugins.im_onebot_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.OneBotAdapter._send_message_unlocked)

        assert "page_count=" in source, "直发路径没有传总页数，节流预算不生效"

    def test_the_outbox_path_passes_the_page_count(self):
        import inspect

        from kirara_ai.plugins.im_onebot_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.OneBotAdapter._send_via_outbox)

        assert "page_count=" in source, "outbox 路径没有传总页数，节流预算不生效"


class TestConfigExposesTheBudget:
    """预算必须能在界面上调：一个只能改 YAML 的参数等于运维改不到。"""

    def test_the_config_declares_the_budget(self):
        from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

        config = OneBotConfig(websocket_url="ws://127.0.0.1:8080/onebot")

        assert hasattr(config, "send_pacing_maximum_total_seconds")

    def test_the_config_maps_the_budget_onto_the_runtime(self):
        from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

        config = OneBotConfig(
            websocket_url="ws://127.0.0.1:8080/onebot",
            send_pacing_maximum_total_seconds=3.0,
        )

        assert config.build_send_pacing().maximum_total_seconds == 3.0
