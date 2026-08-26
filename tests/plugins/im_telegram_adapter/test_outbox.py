from __future__ import annotations

import asyncio

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_telegram_adapter.outbox import (
    TelegramInboundReceipt,
    TelegramOutboxService,
)


def _database(tmp_path):
    database = DatabaseManager(DependencyContainer(), data_dir=tmp_path / "db")
    database.initialize()
    return database


def test_telegram_update_claim_is_durable_and_recoverable(tmp_path):
    database = _database(tmp_path)
    first = TelegramOutboxService(database, lambda _params: None, adapter_instance="bot-a")

    assert first.claim_inbound(42, "chat-1") is True
    assert first.claim_inbound(42, "chat-1") is False
    assert first.recover_inbound() == 1
    assert first.claim_inbound(42, "chat-1") is True

    with database.get_session() as session:
        receipt = session.query(TelegramInboundReceipt).one()
        assert receipt.status == "processing"
        assert receipt.update_id == "42"


@pytest.mark.asyncio
async def test_telegram_outbox_preserves_pages_and_quarantines_unknown_send(tmp_path):
    database = _database(tmp_path)
    calls = []

    async def sender(params):
        calls.append(params)
        raise asyncio.TimeoutError("result unknown")

    outbox = TelegramOutboxService(database, sender, adapter_instance="bot-a")
    queued = outbox.enqueue(
        "delivery-1",
        "chat:1",
        "text",
        {"chat_id": "1", "text": "page 1"},
        page_index=0,
        page_count=2,
    )
    outbox.enqueue(
        "delivery-2",
        "chat:1",
        "text",
        {"chat_id": "1", "text": "page 2"},
        page_index=1,
        page_count=2,
    )

    result = await outbox.deliver(queued.delivery_id)

    assert result.status == "ambiguous"
    assert len(calls) == 1
    assert outbox.pending_delivery_ids() == ["delivery-2"]
    assert outbox.get("delivery-1").page_index == 0
    assert outbox.get("delivery-1").page_count == 2
