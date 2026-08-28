"""OneBot and QQBot must process one upstream event exactly once."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(tmp_path: Path) -> tuple[OneBotAdapter, AsyncMock]:
    container = DependencyContainer()
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'inbound.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    container.register(OneBotConfig, OneBotConfig())
    adapter = Inject(container).create(OneBotAdapter)()
    dispatcher = AsyncMock()
    adapter.dispatcher = dispatcher
    return adapter, dispatcher


def event(message_id: int = 1, **overrides) -> dict:
    payload = {
        "self_id": 10001,
        "user_id": 200,
        "message_id": message_id,
        "time": 1700000000,
        "post_type": "message",
        "message_type": "private",
        "raw_message": "hello",
        "message": "hello",
        "sender": {"nickname": "用户"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_a_new_event_is_dispatched(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)

    await adapter._handle_message(event())

    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_a_redelivered_event_is_not_dispatched_twice(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)

    await adapter._handle_message(event())
    await adapter._handle_message(event())

    # The upstream replays after a dropped reverse WebSocket post; the workflow
    # (and its model bill) must run once.
    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_two_different_events_both_run(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)

    await adapter._handle_message(event(message_id=1))
    await adapter._handle_message(event(message_id=2))

    assert dispatcher.dispatch.await_count == 2


@pytest.mark.asyncio
async def test_the_same_message_id_from_another_account_is_a_different_event(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)

    await adapter._handle_message(event(message_id=1, self_id=10001))
    await adapter._handle_message(event(message_id=1, self_id=10002))

    assert dispatcher.dispatch.await_count == 2


@pytest.mark.asyncio
async def test_a_failed_dispatch_can_be_retried_by_a_redelivery(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)
    dispatcher.dispatch.side_effect = [RuntimeError("workflow exploded"), None]

    with pytest.raises(RuntimeError):
        await adapter._handle_message(event())
    await adapter._handle_message(event())

    # The first attempt failed, so the receipt was released and the redelivery
    # is allowed to run — this is the one case where reprocessing is correct.
    assert dispatcher.dispatch.await_count == 2


@pytest.mark.asyncio
async def test_an_event_without_an_identity_is_still_processed(tmp_path: Path):
    """Losing a message is worse than a rare duplicate."""
    adapter, dispatcher = make_adapter(tmp_path)

    unidentifiable = event()
    unidentifiable.pop("message_id")
    unidentifiable.pop("user_id")
    unidentifiable.pop("time")

    await adapter._handle_message(unidentifiable)

    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_an_event_without_message_id_falls_back_to_a_composite_key(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)
    without_id = event()
    without_id.pop("message_id")

    await adapter._handle_message(dict(without_id))
    await adapter._handle_message(dict(without_id))

    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_startup_recovery_reopens_an_interrupted_event(tmp_path: Path):
    adapter, dispatcher = make_adapter(tmp_path)
    receipts = adapter._ensure_inbound_receipts()
    assert receipts is not None
    # Simulate a crash between claim and complete.
    receipts.claim("10001:1", "c2c:200")

    receipts.recover_on_startup()
    await adapter._handle_message(event(message_id=1))

    assert dispatcher.dispatch.await_count == 1


@pytest.mark.asyncio
async def test_dedup_is_skipped_without_a_database(tmp_path: Path):
    """An adapter wired without persistence must still deliver messages."""
    container = DependencyContainer()
    container.register(OneBotConfig, OneBotConfig())
    adapter = Inject(container).create(OneBotAdapter)()
    dispatcher = AsyncMock()
    adapter.dispatcher = dispatcher

    await adapter._handle_message(event())
    await adapter._handle_message(event())

    assert dispatcher.dispatch.await_count == 2
