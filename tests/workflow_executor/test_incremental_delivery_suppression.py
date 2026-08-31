"""dispatcher 必须尊重「已经增量投递过」这个事实（需求 4）。

`_deliver_runtime_result` 原本无条件调 `source.send_message()`。增量投递成功之后
那条被改写完成的消息已经是完整回复，再发一次等于同一段内容出现两遍——开启
`incremental` 反而比 `off` 更糟。

对称的另一半同样重要：增量没成功（占位失败 / 改写被限流 / 渠道不支持编辑）时
用户屏幕上没有完整回复，整段投递必须照常发生。

时间线仍然要记：`send_succeeded` 这一段本来由适配器在 `send_message` 里补。
跳过整段投递就没人补它，于是投递耗时看板上这一轮凭空消失。因此跳过时要显式
记一条，标明是增量路径完成的。
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.agent_runtime.executor import RuntimeResult, RuntimeStatus
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher


class _RecordingAdapter:
    """记录整段投递被调了几次的假适配器。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, message: IMMessage, recipient: ChatSender) -> Any:
        texts = [
            element.text
            for element in message.message_elements
            if isinstance(element, TextMessage)
        ]
        self.sent.append("".join(texts))


def _dispatcher() -> WorkflowDispatcher:
    """只演练投递分支，不跑 `__init__`（它要求注册好整套工作流依赖）。

    容器仍要挂上：`_persist_delivery_durations` 会问它有没有注册投递耗时存储。
    一个空容器让那一步成为空操作，正是这里想要的。
    """
    dispatcher = object.__new__(WorkflowDispatcher)
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    dispatcher.container = container
    dispatcher.logger = get_logger("TestDispatcher")
    return dispatcher


def _inbound() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat(user_id="u1", display_name="tester"),
        message_elements=[TextMessage("写一个回火算法")],
    )


@pytest.mark.asyncio
async def test_an_incrementally_delivered_reply_is_not_sent_again():
    adapter = _RecordingAdapter()
    result = RuntimeResult(
        status=RuntimeStatus.COMPLETED,
        text="整段回复",
        delivered_incrementally=True,
    )

    await _dispatcher()._deliver_runtime_result(adapter, _inbound(), result)

    assert adapter.sent == [], "增量已经投递过，整段投递又发了一遍"


@pytest.mark.asyncio
async def test_a_reply_that_was_not_delivered_incrementally_is_still_sent():
    """这是 QQ / 企业微信的常态路径，也是增量失败时的兜底。"""
    adapter = _RecordingAdapter()
    result = RuntimeResult(status=RuntimeStatus.COMPLETED, text="整段回复")

    await _dispatcher()._deliver_runtime_result(adapter, _inbound(), result)

    assert adapter.sent == ["整段回复"]


@pytest.mark.asyncio
async def test_skipping_the_bulk_send_still_records_a_delivery_stage():
    """跳过整段投递时没人记 `send_succeeded`，投递耗时看板上这一轮会凭空消失。"""
    adapter = _RecordingAdapter()
    message = _inbound()
    result = RuntimeResult(
        status=RuntimeStatus.COMPLETED,
        text="整段回复",
        delivered_incrementally=True,
    )

    await _dispatcher()._deliver_runtime_result(adapter, message, result)

    stages = [event.stage for event in message.delivery_timeline]
    assert "send_succeeded" in stages
