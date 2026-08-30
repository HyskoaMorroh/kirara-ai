"""The dispatcher must stitch the inbound half of the timeline onto the reply.

Adapters record their own four stages on the *reply* object they are handed.
Without the dispatcher carrying `received_event` / `workflow_started` /
`llm_completed` across, the reply's timeline started at "formatting_started" and
the wait users actually complain about — queueing plus model generation — was
invisible.

These tests also pin that observability can never become a new failure mode: a
third-party adapter passing a message object without the timeline API must still
get its reply delivered.
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher


class RecordingAdapter:
    def __init__(self) -> None:
        self.sent: list[IMMessage] = []

    async def send_message(self, message: IMMessage, recipient: Any) -> None:
        # Adapters append their own stages to the reply they receive.
        message.record_delivery_stage("formatting_started", adapter="test")
        message.record_delivery_stage("formatting_completed", adapter="test", segment_count=1)
        message.record_delivery_stage("send_started", adapter="test")
        message.record_delivery_stage("send_succeeded", adapter="test", retry_count=0)
        self.sent.append(message)


class TimelinelessMessage:
    """A message object from an adapter built before the timeline API existed."""

    def __init__(self, sender: ChatSender, content: str) -> None:
        self.sender = sender
        self.content = content
        self.message_elements = [TextMessage(content)]
        self.raw_message = None


def bare_dispatcher() -> WorkflowDispatcher:
    """A dispatcher instance without container wiring; only helpers are exercised."""
    return object.__new__(WorkflowDispatcher)


def inbound() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat("100", "用户"),
        message_elements=[TextMessage("你好")],
    )


def test_inbound_stages_are_carried_onto_the_reply():
    dispatcher = bare_dispatcher()
    request = inbound()
    dispatcher._record_stage(request, "received_event")
    dispatcher._record_stage(request, "workflow_started", agent_id="a1")
    dispatcher._record_stage(request, "llm_completed", total_tokens=42)

    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    dispatcher._carry_timeline(request, reply)

    assert [event.stage for event in reply.delivery_timeline] == [
        "received_event",
        "workflow_started",
        "llm_completed",
    ]
    # Timestamps must be the originals, not the moment of copying.
    assert reply.delivery_timeline[0].timestamp == request.delivery_timeline[0].timestamp
    assert reply.delivery_timeline[2].details["total_tokens"] == 42


@pytest.mark.asyncio
async def test_end_to_end_timeline_covers_queue_model_and_send():
    dispatcher = bare_dispatcher()
    adapter = RecordingAdapter()
    request = inbound()
    dispatcher._record_stage(request, "received_event")
    dispatcher._record_stage(request, "workflow_started")
    dispatcher._record_stage(request, "llm_first_byte")
    dispatcher._record_stage(request, "llm_completed")

    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )
    dispatcher._carry_timeline(request, reply)
    await adapter.send_message(reply, request.sender)

    stages = [event.stage for event in reply.delivery_timeline]
    assert stages[0] == "received_event"
    assert stages[-1] == "send_succeeded"
    durations = reply.delivery_durations()
    for key in (
        "queue_seconds",
        "llm_first_byte_seconds",
        "llm_generation_seconds",
        "formatting_seconds",
        "send_seconds",
        "total_seconds",
    ):
        assert key in durations


def test_record_stage_tolerates_a_message_without_the_timeline_api():
    dispatcher = bare_dispatcher()
    legacy = TimelinelessMessage(ChatSender.from_c2c_chat("1", "u"), "hi")

    # Must not raise: losing observability is acceptable, dropping the reply is not.
    dispatcher._record_stage(legacy, "received_event")


def test_carry_timeline_tolerates_a_reply_without_the_timeline_api():
    dispatcher = bare_dispatcher()
    request = inbound()
    dispatcher._record_stage(request, "received_event")
    legacy = TimelinelessMessage(ChatSender.get_bot_sender(), "hi")

    dispatcher._carry_timeline(request, legacy)


def test_carry_timeline_is_a_noop_without_recorded_stages():
    dispatcher = bare_dispatcher()
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    dispatcher._carry_timeline(inbound(), reply)

    assert reply.delivery_timeline == ()


def test_record_stage_swallows_a_recorder_that_raises():
    class HostileMessage(TimelinelessMessage):
        def record_delivery_stage(self, *_args, **_kwargs):
            raise RuntimeError("observability backend exploded")

    dispatcher = bare_dispatcher()
    hostile = HostileMessage(ChatSender.from_c2c_chat("1", "u"), "hi")

    dispatcher._record_stage(hostile, "received_event")


class _RuntimeResultStub:
    """The subset of ``RuntimeResult`` that `_record_model_stages` reads."""

    def __init__(self, *, first_byte_at=None, trace_ids=()):
        self.response = None
        self.trace_ids = trace_ids
        self.llm_first_byte_at = first_byte_at


def test_streamed_first_byte_reaches_the_delivery_timeline():
    """流式请求测到的首字节必须进入时间线，否则那两列永远是 NULL。

    ``llm_first_byte`` 阶段名、``llm_first_byte_seconds`` 列和文档三处都早已存在，
    但生产代码从来没有写过这个阶段——只有测试自己手工记录。于是真实部署里
    「模型首字节」与「生成耗时」永远缺失，而缺失和为零在排查时含义完全不同。
    """
    from datetime import datetime, timedelta, timezone

    dispatcher = bare_dispatcher()
    request = inbound()
    dispatcher._record_stage(request, "received_event")
    dispatcher._record_stage(request, "workflow_started")

    first_byte_at = datetime.now(timezone.utc) + timedelta(milliseconds=5)
    dispatcher._record_model_stages(
        request,
        _RuntimeResultStub(first_byte_at=first_byte_at, trace_ids=("trace-1",)),
    )

    stages = [event.stage for event in request.delivery_timeline]
    assert stages == [
        "received_event",
        "workflow_started",
        "llm_first_byte",
        "llm_completed",
    ], "首字节必须记在 llm_completed 之前，否则按顺序读取的消费者看到倒序链路"

    recorded = {event.stage: event for event in request.delivery_timeline}
    assert recorded["llm_first_byte"].timestamp == first_byte_at
    # trace id 要同时出现在两个模型阶段上，便于把这段耗时接回 LLM 追踪表。
    assert recorded["llm_first_byte"].details["trace_id"] == "trace-1"

    durations = request.delivery_durations()
    assert "llm_first_byte_seconds" in durations
    assert "llm_generation_seconds" in durations


def test_non_stream_result_records_no_fabricated_first_byte():
    """非流式请求没有可测的首字节，绝不能补一个。"""
    dispatcher = bare_dispatcher()
    request = inbound()
    dispatcher._record_stage(request, "workflow_started")

    dispatcher._record_model_stages(request, _RuntimeResultStub())

    stages = [event.stage for event in request.delivery_timeline]
    assert "llm_first_byte" not in stages
    assert "llm_first_byte_seconds" not in request.delivery_durations()
