"""发送节流：避免触发 QQ 风控（需求 11）。

被融入的 OneBot 适配器项目在每次 flush 之前主动等一段时间：

    duration = max(text_length * 0.1, 1) + random.uniform(0.5, 1.5)
    await asyncio.sleep(duration)

那不是重试退避，而是**主动限速**——QQ 对短时间内连发多条消息有风控，
命中之后账号会被限制发言，而这跟「消息发失败」是完全不同的故障：
接口全部返回成功，消息却没有到达对方，且要等很久才恢复。

本项目的 outbox 只有**失败重试**退避（`retry_delay_seconds`、
`retry_backoff_seconds`），那是「发失败了等一会儿再试」；节流是「发成功了也要等，
下一条才不会被判定为刷屏」。两者方向相反，无法互相替代——一个从 C 迁过来的用户
会撞上 C 专门规避掉的那个风控。

三条边界写进实现：

* **按文本长度算**，与原实现一致：长文本本身已经占用了对方的阅读时间，
  短文本连发才是风控最敏感的形态。
* **带随机抖动**：固定间隔本身就是一种可识别的机器特征。
* **可关闭且默认保守**：本地自建、私有部署或压测场景不需要它；
  但默认必须开着——默认关掉等于让每个新部署自己踩一次风控才知道要开。

节流只发生在**同一次投递的页与页之间**，不在第一页之前：让用户多等一秒才看到
第一个字，是把一个不存在的问题加到每一次正常对话上。
"""

from __future__ import annotations

import asyncio

import pytest

from kirara_ai.plugins.im_onebot_adapter.pacing import (
    SendPacing,
    resolve_pacing_delay,
)


class TestDelayCalculation:
    def test_longer_text_waits_longer(self):
        pacing = SendPacing(enabled=True, jitter_seconds=0.0)

        short = resolve_pacing_delay(pacing, text_length=0)
        long = resolve_pacing_delay(pacing, text_length=200)

        assert long > short

    def test_there_is_a_floor_so_short_bursts_still_pause(self):
        """短文本连发正是风控最敏感的形态，不能因为长度是 0 就不等。"""
        pacing = SendPacing(enabled=True, jitter_seconds=0.0)

        assert resolve_pacing_delay(pacing, text_length=0) >= pacing.minimum_seconds

    def test_the_delay_is_capped(self):
        """没有上界时，一条很长的消息会把整轮对话拖成分钟级。

        风控看的是频率，不是「等得够不够久」；超过某个点再等只是在惩罚用户。
        """
        pacing = SendPacing(enabled=True, jitter_seconds=0.0, maximum_seconds=3.0)

        assert resolve_pacing_delay(pacing, text_length=100000) == pytest.approx(3.0)

    def test_jitter_stays_inside_its_declared_range(self):
        """固定间隔本身就是可识别的机器特征，所以要抖动；
        但抖动不能把延迟推到上界之外。"""
        pacing = SendPacing(
            enabled=True, minimum_seconds=1.0, jitter_seconds=1.0, maximum_seconds=3.0
        )

        samples = [resolve_pacing_delay(pacing, text_length=0) for _ in range(200)]

        assert all(1.0 <= value <= 3.0 for value in samples)
        # 抖动必须真的在动：全部相同说明随机没接上。
        assert len(set(samples)) > 1

    def test_disabled_pacing_is_exactly_zero(self):
        """关掉之后不能只是「等得少一点」——压测与自建部署要的是完全不等。"""
        pacing = SendPacing(enabled=False)

        assert resolve_pacing_delay(pacing, text_length=5000) == 0.0

    def test_it_is_enabled_by_default(self):
        """默认关掉等于让每个新部署自己踩一次风控才知道要开。"""
        assert SendPacing().enabled is True

    def test_a_negative_configuration_is_refused(self):
        """负延迟会变成 `asyncio.sleep(-x)`，那是 ValueError，
        而它会在一次正常发送里冒出来。"""
        with pytest.raises(ValueError):
            SendPacing(minimum_seconds=-1.0)
        with pytest.raises(ValueError):
            SendPacing(jitter_seconds=-0.5)
        with pytest.raises(ValueError):
            SendPacing(per_character_seconds=-0.1)

    def test_a_maximum_below_the_minimum_is_refused(self):
        """这组配置无解，静默取其中一个会让另一个设置永远不生效。"""
        with pytest.raises(ValueError):
            SendPacing(minimum_seconds=5.0, maximum_seconds=2.0)


class TestPageToPageOnly:
    @pytest.mark.asyncio
    async def test_the_first_page_is_not_delayed(self):
        """让用户多等一秒才看到第一个字，是把一个不存在的问题
        加到每一次正常对话上。"""
        pacing = SendPacing(enabled=True, minimum_seconds=1.0, jitter_seconds=0.0)
        slept: list[float] = []

        async def _sleep(value):
            slept.append(value)

        await pacing.wait_before_page(0, text_length=100, sleep=_sleep)

        assert slept == []

    @pytest.mark.asyncio
    async def test_later_pages_are_delayed(self):
        pacing = SendPacing(enabled=True, minimum_seconds=1.0, jitter_seconds=0.0)
        slept: list[float] = []

        async def _sleep(value):
            slept.append(value)

        await pacing.wait_before_page(1, text_length=100, sleep=_sleep)

        assert len(slept) == 1
        assert slept[0] >= 1.0

    @pytest.mark.asyncio
    async def test_disabled_pacing_never_sleeps(self):
        pacing = SendPacing(enabled=False)
        slept: list[float] = []

        async def _sleep(value):
            slept.append(value)

        for index in range(4):
            await pacing.wait_before_page(index, text_length=500, sleep=_sleep)

        assert slept == []

    @pytest.mark.asyncio
    async def test_it_uses_asyncio_sleep_by_default(self):
        """默认必须真的让出事件循环，否则「节流」只是算了个数字。"""
        pacing = SendPacing(
            enabled=True, minimum_seconds=0.01, jitter_seconds=0.0, per_character_seconds=0.0
        )

        started = asyncio.get_event_loop().time()
        await pacing.wait_before_page(1, text_length=0)
        elapsed = asyncio.get_event_loop().time() - started

        assert elapsed >= 0.005


class TestTheAdapterActuallyPaces:
    """节流函数正确但无人调用，等于没做——这正是本轮反复在修的那类缺陷。

    这里从适配器层验证：多页投递时，页与页之间真的等过。
    """

    def test_the_adapter_exposes_pacing_from_its_config(self):
        from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

        config = OneBotConfig(websocket_url="ws://127.0.0.1:8080/onebot")

        assert hasattr(config, "send_pacing_enabled")
        assert config.send_pacing_enabled is True, (
            "默认关掉等于让每个新部署自己踩一次风控才知道要开"
        )

    def test_pacing_can_be_turned_off_for_local_deployments(self):
        from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

        config = OneBotConfig(
            websocket_url="ws://127.0.0.1:8080/onebot", send_pacing_enabled=False
        )

        assert config.build_send_pacing().enabled is False

    def test_the_config_maps_onto_the_runtime_pacing(self):
        """两处各写一份参数名，迟早出现「界面调了但运行时没变」，
        而那种不一致没有任何症状。"""
        from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig

        config = OneBotConfig(
            websocket_url="ws://127.0.0.1:8080/onebot",
            send_pacing_minimum_seconds=2.0,
            send_pacing_jitter_seconds=0.0,
        )
        pacing = config.build_send_pacing()

        assert isinstance(pacing, SendPacing)
        assert pacing.minimum_seconds == 2.0
        assert resolve_pacing_delay(pacing, text_length=0) == pytest.approx(2.0)

    def test_both_send_paths_consult_the_pacing(self):
        """直发与 outbox 两条路径都要节流。

        只修一条是半个修复：风控与走哪条投递路径无关，而部署有没有配数据库
        决定走哪条——于是同一个账号换个部署形态就又会被限制发言。
        """
        import inspect

        from kirara_ai.plugins.im_onebot_adapter import adapter as adapter_module

        direct = inspect.getsource(adapter_module.OneBotAdapter._send_message_unlocked)
        outbox = inspect.getsource(adapter_module.OneBotAdapter._send_via_outbox)

        assert "wait_before_page" in direct, "直发路径没有节流"
        assert "wait_before_page" in outbox, "outbox 路径没有节流"
