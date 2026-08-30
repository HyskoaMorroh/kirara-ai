"""遗留工作流路径的投递耗时也必须落库。

Agent 路径由 dispatcher 统一记录并落库；遗留工作流路径的回复对象由工作流自己
构造，dispatcher 拿不到它。此前这条分支既不记 `workflow_started`，也从不落库，
于是未迁移到 Agent 的部署里投递耗时表永远是空的——「QQ 慢」在这些部署上
根本无法拆开定位，而这正是 1.txt 19.5 要求回答的问题。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher
from kirara_ai.workflow.implementations.blocks.im.messages import SendIMMessage


class RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[IMMessage] = []

    async def send_message(self, message: IMMessage, recipient: Any) -> None:
        message.record_delivery_stage("formatting_started", adapter="legacy")
        message.record_delivery_stage(
            "formatting_completed", adapter="legacy", segment_count=2
        )
        message.record_delivery_stage("send_started", adapter="legacy")
        message.record_delivery_stage("send_succeeded", adapter="legacy", retry_count=1)
        self.sent.append(message)


class SpyDispatcher:
    """只记录调用，避免拉起整个容器与数据库。"""

    def __init__(self) -> None:
        self.logged: list[IMMessage] = []
        self.persisted: list[tuple[Any, IMMessage, IMMessage]] = []

    def _log_delivery_durations(self, message: IMMessage) -> None:
        self.logged.append(message)

    def _persist_delivery_durations(self, source, message, reply) -> None:
        self.persisted.append((source, message, reply))


def inbound() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat("100", "用户"),
        message_elements=[TextMessage("你好")],
    )


def make_block(adapter, dispatcher, inbound_message):
    from kirara_ai.im.adapter import IMAdapter

    container = DependencyContainer()
    container.register(IMMessage, inbound_message)
    container.register(IMAdapter, adapter)
    container.register(WorkflowDispatcher, dispatcher)
    loop = asyncio.new_event_loop()
    container.register(asyncio.AbstractEventLoop, loop)
    block = SendIMMessage()
    block.container = container
    return block, loop


def test_legacy_workflow_send_persists_the_full_timeline():
    adapter = RecordingAdapter()
    dispatcher = SpyDispatcher()
    request = inbound()
    # dispatcher 在遗留分支上记录的两个入站阶段。`_record_stage` 是静态方法。
    WorkflowDispatcher._record_stage(request, "received_event")
    WorkflowDispatcher._record_stage(request, "workflow_started", workflow="r1")

    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    block, loop = make_block(adapter, dispatcher, request)
    try:
        assert block.execute(reply) == {"ok": True}
    finally:
        loop.close()

    stages = [event.stage for event in reply.delivery_timeline]
    assert stages[0] == "received_event", "入站阶段必须被带到回复上"
    assert "workflow_started" in stages
    assert stages[-1] == "send_succeeded"

    durations = reply.delivery_durations()
    assert "queue_seconds" in durations
    assert "total_seconds" in durations

    assert dispatcher.logged == [reply]
    assert len(dispatcher.persisted) == 1
    source, source_message, persisted_reply = dispatcher.persisted[0]
    assert source is adapter
    assert source_message is request
    assert persisted_reply is reply


def test_send_failure_still_persists_the_timings():
    """发送失败同样要落库：send_failed 是需要回查的证据。"""

    class FailingAdapter:
        async def send_message(self, message: IMMessage, recipient: Any) -> None:
            message.record_delivery_stage("send_started", adapter="legacy")
            message.record_delivery_stage(
                "send_failed", adapter="legacy", error_type="ActionFailed"
            )
            raise RuntimeError("upstream refused")

    dispatcher = SpyDispatcher()
    request = inbound()
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    block, loop = make_block(FailingAdapter(), dispatcher, request)
    try:
        with pytest.raises(RuntimeError, match="upstream refused"):
            block.execute(reply)
    finally:
        loop.close()

    assert len(dispatcher.persisted) == 1


def test_observability_failure_never_breaks_delivery():
    """落库本身抛错时，消息仍必须算发送成功。"""

    class HostileDispatcher(SpyDispatcher):
        def _persist_delivery_durations(self, source, message, reply) -> None:
            raise RuntimeError("timing store exploded")

    adapter = RecordingAdapter()
    request = inbound()
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    block, loop = make_block(adapter, HostileDispatcher(), request)
    try:
        assert block.execute(reply) == {"ok": True}
    finally:
        loop.close()

    assert adapter.sent == [reply]


def test_send_without_a_dispatcher_registered_still_works():
    """容器里没有 dispatcher（单元测试与部分插件如此）时静默跳过观测。"""
    from kirara_ai.im.adapter import IMAdapter

    adapter = RecordingAdapter()
    request = inbound()
    container = DependencyContainer()
    container.register(IMMessage, request)
    container.register(IMAdapter, adapter)
    loop = asyncio.new_event_loop()
    container.register(asyncio.AbstractEventLoop, loop)
    block = SendIMMessage()
    block.container = container

    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    try:
        assert block.execute(reply) == {"ok": True}
    finally:
        loop.close()
