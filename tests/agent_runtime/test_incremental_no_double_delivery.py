"""增量投递成功之后不能再发一条完整消息（需求 4）。

`incremental` 档在 Telegram 上先发一条占位消息，随生成不断改写它，最后
`complete()` 把它改写成最终文本。此后 `_deliver_runtime_result`
（`dispatcher.py`）**无条件**再调一次 `source.send_message()`——于是同一条回复
在用户屏幕上出现两遍：一条是被改写完成的那条，一条是新发的。

这不是「多一条日志」级别的问题：用户看到机器人把同一段几千字的内容说了两次，
而两次内容逐字相同。开了 `incremental` 反而比 `off` 更糟。

## 为什么不能简单地「走过增量就不再整段投递」

整段投递是增量失败时的兜底，这一点必须保住：

* 占位消息发不出去（`begin` 返回 `None`）；
* 中途某次改写被平台拒绝（限流、消息过长）；
* 渠道压根不支持编辑（QQ / 企业微信）。

以上任何一种情况下，用户此刻屏幕上**没有**完整回复，整段投递必须照常发生。
因此判据只能是「最终文本已经确实写到平台上了」，而不是「本轮尝试过增量」。

## 四条边界

1. 增量收尾成功 → 不再整段投递（否则重复）。
2. 增量任何一步失败 → 照常整段投递（否则用户什么都收不到）。
3. 渠道不支持增量 → 照常整段投递（这是 QQ / 企业微信的常态路径）。
4. 判据由运行时结果携带，而不是让 dispatcher 去问适配器：dispatcher 拿不到
   那个 sink，而适配器不知道本轮走了哪一档。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from kirara_ai.agent_runtime.executor import (
    IncrementalReplyDelivery,
    RuntimeResult,
    RuntimeStatus,
)
from kirara_ai.im.adapter import IncrementalReplyHandle


class _EditingChannel:
    """具备编辑能力的假渠道（Telegram 的形态）。"""

    def __init__(self, *, fail_on_begin: bool = False, fail_on_finish: bool = False):
        self.fail_on_begin = fail_on_begin
        self.fail_on_finish = fail_on_finish
        self.updates: list[str] = []
        self.finished: list[str] = []

    async def begin_incremental_reply(
        self, recipient: Any
    ) -> Optional[IncrementalReplyHandle]:
        if self.fail_on_begin:
            return None
        return IncrementalReplyHandle(message_id="1", chat_id="c")

    async def update_incremental_reply(
        self, handle: IncrementalReplyHandle, text: str
    ) -> None:
        self.updates.append(text)

    async def finish_incremental_reply(
        self, handle: IncrementalReplyHandle, text: str
    ) -> None:
        if self.fail_on_finish:
            raise RuntimeError("edit rejected")
        self.finished.append(text)


class _PlainChannel:
    """没有编辑能力的渠道（QQ / 企业微信的形态）。"""


class TestTheSinkReportsWhetherItDelivered:
    @pytest.mark.asyncio
    async def test_a_successful_finish_reports_delivered(self):
        channel = _EditingChannel()
        sink = IncrementalReplyDelivery(channel, recipient=object())

        await sink.start()
        await sink.push("半句")
        await sink.complete("整段回复")

        assert channel.finished == ["整段回复"]
        assert sink.delivered is True

    @pytest.mark.asyncio
    async def test_a_failed_finish_does_not_report_delivered(self):
        """收尾被平台拒绝时屏幕上停在半句话，整段投递必须补上。"""
        channel = _EditingChannel(fail_on_finish=True)
        sink = IncrementalReplyDelivery(channel, recipient=object())

        await sink.start()
        await sink.push("半句")
        await sink.complete("整段回复")

        assert sink.delivered is False

    @pytest.mark.asyncio
    async def test_a_channel_without_editing_never_reports_delivered(self):
        sink = IncrementalReplyDelivery(_PlainChannel(), recipient=object())

        await sink.start()
        await sink.push("半句")
        await sink.complete("整段回复")

        assert sink.delivered is False

    @pytest.mark.asyncio
    async def test_a_failed_placeholder_never_reports_delivered(self):
        sink = IncrementalReplyDelivery(
            _EditingChannel(fail_on_begin=True), recipient=object()
        )

        await sink.start()
        await sink.complete("整段回复")

        assert sink.delivered is False

    @pytest.mark.asyncio
    async def test_a_turn_that_never_streamed_does_not_report_delivered(self):
        """工具轮不走增量；此时不该以为屏幕上已经有回复了。"""
        sink = IncrementalReplyDelivery(_EditingChannel(), recipient=object())

        await sink.complete("整段回复")

        assert sink.delivered is False


class TestTheResultCarriesTheFlag:
    def test_the_default_is_not_delivered(self):
        """默认必须是「没投递过」：默认反了会让所有非增量回复静默消失。"""
        assert RuntimeResult(status=RuntimeStatus.COMPLETED).delivered_incrementally is False

    def test_the_flag_can_be_set(self):
        result = RuntimeResult(
            status=RuntimeStatus.COMPLETED, delivered_incrementally=True
        )

        assert result.delivered_incrementally is True
