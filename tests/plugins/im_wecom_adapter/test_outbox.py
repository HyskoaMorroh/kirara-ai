import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_wecom_adapter.outbox import (
    WecomDelivery,
    WecomInboundReceipt,
    WecomOutboxService,
)


def make_database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'wecom-outbox.db').as_posix()}",
    )
    database.initialize()
    return database


def delivery(database: DatabaseManager, delivery_id: str) -> WecomDelivery:
    with database.get_session() as session:
        return session.execute(
            select(WecomDelivery).where(WecomDelivery.delivery_id == delivery_id)
        ).scalar_one()


def receipt(database: DatabaseManager, message_id: str) -> WecomInboundReceipt:
    with database.get_session() as session:
        return session.execute(
            select(WecomInboundReceipt).where(
                WecomInboundReceipt.message_id == message_id
            )
        ).scalar_one()


def test_inbound_claim_is_persistent_and_idempotent(tmp_path: Path):
    database = make_database(tmp_path)
    service = WecomOutboxService(database, lambda _params: None)

    assert service.claim_inbound("msg-1", "source-1") is True
    assert service.claim_inbound("msg-1", "source-1") is False
    service.complete_inbound("msg-1", "passive reply")

    with database.get_session() as session:
        assert session.scalar(select(WecomInboundReceipt.passive_reply)) == "passive reply"
    assert receipt(database, "msg-1").status == "completed"


def test_inbound_processing_claims_are_released_after_restart(tmp_path: Path):
    database = make_database(tmp_path)
    service = WecomOutboxService(database, lambda _params: None)

    assert service.claim_inbound("msg-restart", "source-1") is True
    assert service.recover_inbound() == 1
    assert receipt(database, "msg-restart").status == "retryable"
    assert service.claim_inbound("msg-restart", "source-1") is True
    assert receipt(database, "msg-restart").status == "processing"


def test_inbound_retry_does_not_reopen_a_completed_callback(tmp_path: Path):
    database = make_database(tmp_path)
    service = WecomOutboxService(database, lambda _params: None)

    assert service.claim_inbound("msg-complete", "source-1") is True
    service.complete_inbound("msg-complete", "reply")
    service.retry_inbound("msg-complete", "late cancellation")

    assert service.claim_inbound("msg-complete", "source-1") is False
    assert receipt(database, "msg-complete").status == "completed"


@pytest.mark.asyncio
async def test_outbound_is_idempotent_and_marks_upstream_only(tmp_path: Path):
    database = make_database(tmp_path)
    calls = 0

    async def send(params):
        nonlocal calls
        calls += 1
        assert params["text"] == "hello"
        return {"msgid": "wecom-upstream-id"}

    service = WecomOutboxService(database, send)
    service.enqueue("delivery-1", "user-1", "text", {"text": "hello"})
    service.enqueue("delivery-1", "user-1", "text", {"text": "changed"})

    result = await service.deliver("delivery-1")

    assert result.status == "accepted"
    assert result.upstream_accepted is True
    assert result.client_received is None
    assert calls == 1
    assert delivery(database, "delivery-1").status == "accepted"


@pytest.mark.asyncio
async def test_interrupted_send_is_quarantined_and_never_resent(tmp_path: Path):
    database = make_database(tmp_path)
    service = WecomOutboxService(database, lambda _params: None)
    service.enqueue("sending-1", "user-1", "text", {"text": "hello"})
    with database.get_session() as session:
        session.query(WecomDelivery).filter_by(delivery_id="sending-1").update(
            {"status": "sending", "attempt_count": 1}
        )
        session.commit()

    recovered = WecomOutboxService(database, lambda _params: None)
    assert recovered.recover_on_startup() == 1
    assert delivery(database, "sending-1").status == "ambiguous"
    result = await recovered.deliver("sending-1")
    assert result.status == "ambiguous"
