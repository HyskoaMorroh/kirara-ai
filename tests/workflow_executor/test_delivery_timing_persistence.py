"""The dispatcher must persist reply timings without ever storing chat content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher


class FakeAdapter:
    channel_type = "onebot"
    adapter_instance = "onebot-1"


def dispatcher_with_store(tmp_path: Path) -> tuple[WorkflowDispatcher, DeliveryTimingStore]:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(EventBus, EventBus())
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'timings.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    store = DeliveryTimingStore(database)
    container.register(DeliveryTimingStore, store)

    dispatcher = object.__new__(WorkflowDispatcher)
    dispatcher.container = container
    dispatcher.logger = _SilentLogger()
    return dispatcher, store


class _SilentLogger:
    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


def inbound() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat("100", "用户"),
        message_elements=[TextMessage("问题内容")],
    )


def reply_with_timeline(*, failed: bool = False) -> IMMessage:
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复内容")],
    )
    reply.record_delivery_stage("received_event")
    reply.record_delivery_stage("workflow_started")
    reply.record_delivery_stage("llm_first_byte")
    reply.record_delivery_stage("llm_completed")
    reply.record_delivery_stage("formatting_started")
    reply.record_delivery_stage("formatting_completed", segment_count=3)
    reply.record_delivery_stage("send_started")
    if failed:
        reply.record_delivery_stage("send_failed", retry_count=2, error_type="Timeout")
    else:
        reply.record_delivery_stage("send_succeeded", retry_count=1)
    return reply


def test_a_delivered_reply_is_persisted(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), reply_with_timeline())

    rows = store.recent()
    assert len(rows) == 1
    assert rows[0]["channel"] == "onebot"
    assert rows[0]["adapter_instance"] == "onebot-1"


def test_the_segment_and_retry_counts_are_carried_over(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), reply_with_timeline())

    row = store.recent()[0]
    assert row["segment_count"] == 3
    assert row["retry_count"] == 1


def test_a_failed_send_is_recorded_as_failed(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    dispatcher._persist_delivery_durations(
        FakeAdapter(), inbound(), reply_with_timeline(failed=True)
    )

    summary = store.summarize()
    assert summary["failed_deliveries"] == 1


def test_no_message_content_reaches_the_table(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), reply_with_timeline())

    payload = str(store.recent())
    assert "回复内容" not in payload
    assert "问题内容" not in payload


def test_a_reply_without_a_timeline_is_not_persisted(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)
    bare = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), bare)

    assert store.recent() == []


def test_persistence_is_skipped_when_the_store_is_absent(tmp_path: Path):
    """Observability must never become a new failure mode."""
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    dispatcher = object.__new__(WorkflowDispatcher)
    dispatcher.container = container
    dispatcher.logger = _SilentLogger()

    # Must not raise even though no store is registered.
    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), reply_with_timeline())


def test_a_store_failure_does_not_propagate(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    def explode(**_kwargs):
        raise RuntimeError("disk on fire")

    store.record = explode  # type: ignore[method-assign]

    # The reply was already delivered; a metrics failure must not surface.
    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), reply_with_timeline())


def test_a_legacy_message_without_the_timeline_api_is_tolerated(tmp_path: Path):
    dispatcher, store = dispatcher_with_store(tmp_path)

    class LegacyReply:
        sender = ChatSender.get_bot_sender()
        message_elements: list[Any] = []
        raw_message = None

    dispatcher._persist_delivery_durations(FakeAdapter(), inbound(), LegacyReply())

    assert store.recent() == []
