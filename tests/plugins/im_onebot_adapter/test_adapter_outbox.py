import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from kirara_ai.database import DatabaseManager
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import (
    OneBotActionTimeoutError,
    OneBotAdapter,
)
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig
from kirara_ai.plugins.im_onebot_adapter.outbox import OneBotDelivery


def make_persistent_adapter(tmp_path: Path) -> tuple[OneBotAdapter, DatabaseManager]:
    container = DependencyContainer()
    config = OneBotConfig(action_timeout_seconds=0.01, outbox_retry_delay_seconds=0)
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'adapter.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    container.register(OneBotConfig, config)
    return Inject(container).create(OneBotAdapter)(), database


@pytest.mark.asyncio
async def test_partial_page_success_does_not_resend_an_accepted_page(tmp_path: Path):
    adapter, database = make_persistent_adapter(tmp_path)
    calls = 0

    async def first_accepts_second_times_out(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"message_id": 1}
        await asyncio.sleep(1)

    adapter.bot.call_action = AsyncMock(side_effect=first_accepts_second_times_out)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("内容。" * 3000)],
    )
    recipient = ChatSender.from_c2c_chat("100", "用户")

    with pytest.raises(OneBotActionTimeoutError):
        await adapter.send_message(message, recipient, delivery_id="logical-1")

    with database.get_session() as session:
        deliveries = list(
            session.execute(
                select(OneBotDelivery)
                .where(OneBotDelivery.logical_delivery_id == "logical-1")
                .order_by(OneBotDelivery.page_index)
            ).scalars()
        )
    assert len(deliveries) > 1
    assert deliveries[0].status == "accepted"
    assert deliveries[1].status == "ambiguous"
    assert all(item.status == "queued" for item in deliveries[2:])
    assert calls == 2

    with pytest.raises(OneBotActionTimeoutError):
        await adapter.send_message(message, recipient, delivery_id="logical-1")

    assert calls == 2


def test_health_snapshot_exposes_layered_status_without_account_ids(tmp_path: Path):
    adapter, _database = make_persistent_adapter(tmp_path)
    adapter._started = True
    adapter.connections["account-id-is-not-exposed"] = {"last_heartbeat": 10.0}
    adapter._external_login_status = "upstream_reported_online"
    adapter._ensure_outbox().enqueue(
        "health-1",
        "account:c2c:recipient",
        "send_private_msg",
        {"user_id": 100, "message": []},
    )

    snapshot = adapter.get_health_snapshot(now=10.0)
    payload = snapshot.model_dump()

    assert payload["adapter_started"] is True
    assert payload["websocket_connected"] is True
    assert payload["external_login_status"] == "upstream_reported_online"
    assert payload["outbox"]["queued"] == 1
    assert "account-id-is-not-exposed" not in str(payload)


@pytest.mark.asyncio
async def test_onebot_records_local_delivery_stages(tmp_path: Path):
    adapter, _database = make_persistent_adapter(tmp_path)
    adapter.bot.call_action = AsyncMock(return_value={"message_id": 1})
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("hello")],
    )

    await adapter.send_message(
        message,
        ChatSender.from_c2c_chat("100", "用户"),
        delivery_id="logical-timeline",
    )

    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]
    assert message.delivery_timeline[1].details["segment_count"] == 1
    assert message.delivery_timeline[-1].details["retry_count"] == 0


@pytest.mark.asyncio
async def test_onebot_records_send_failure_stage(tmp_path: Path):
    adapter, _database = make_persistent_adapter(tmp_path)

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(1)

    adapter.bot.call_action = AsyncMock(side_effect=hangs)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("hello")],
    )

    with pytest.raises(OneBotActionTimeoutError):
        await adapter.send_message(
            message,
            ChatSender.from_c2c_chat("100", "用户"),
            delivery_id="logical-timeline-failure",
        )

    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_failed",
    ]
    failed = message.delivery_timeline[-1]
    assert failed.details["error_type"] == "OneBotActionTimeoutError"
    assert failed.details["retry_count"] == 0
