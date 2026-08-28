"""Inbound dedup: the same upstream event must run the workflow exactly once.

Telegram and WeCom had receipts; OneBot and QQBot did not. An OneBot
implementation whose reverse WebSocket drops mid-post cannot know whether we
processed the event, so replaying is the only safe thing *it* can do — which
means the dedup has to live on our side. Without it the workflow runs twice:
the model is billed twice and the user gets two replies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.im.inbound_receipts import (
    INBOUND_RETRYABLE_STATUS,
    InboundReceiptService,
)
from kirara_ai.ioc.container import DependencyContainer


@pytest.fixture()
def database(tmp_path: Path) -> DatabaseManager:
    container = DependencyContainer()
    manager = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'inbound.db').as_posix()}",
    )
    manager.initialize()
    return manager


@pytest.fixture()
def receipts(database: DatabaseManager) -> InboundReceiptService:
    return InboundReceiptService(database, channel="onebot", adapter_instance="onebot-1")


def test_the_first_claim_wins(receipts: InboundReceiptService):
    assert receipts.claim("event-1", "c2c:100") is True


def test_a_redelivered_event_is_refused(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")

    assert receipts.claim("event-1", "c2c:100") is False


def test_a_completed_event_is_still_refused(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")
    receipts.complete("event-1")

    assert receipts.claim("event-1", "c2c:100") is False


def test_completing_an_event_drops_its_stored_payload(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100", payload={"raw_message": "机密内容"})
    receipts.complete("event-1")

    counts = receipts.status_counts()
    assert counts["completed"] == 1
    # The payload only exists to allow a retry; a finished event must not keep it.
    with receipts.database.get_session() as session:
        from kirara_ai.im.inbound_receipts import InboundReceipt
        from sqlalchemy import select

        row = session.execute(select(InboundReceipt)).scalar_one()
        assert row.payload_json is None


def test_a_failed_event_can_be_claimed_again(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")
    receipts.retry("event-1")

    assert receipts.claim("event-1", "c2c:100") is True


def test_retry_does_not_reopen_a_completed_event(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")
    receipts.complete("event-1")
    receipts.retry("event-1")

    assert receipts.claim("event-1", "c2c:100") is False


def test_startup_recovery_reopens_events_left_mid_processing(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")
    receipts.claim("event-2", "c2c:100")
    receipts.complete("event-2")

    reopened = receipts.recover_on_startup()

    assert reopened == 1
    assert receipts.claim("event-1", "c2c:100") is True
    assert receipts.claim("event-2", "c2c:100") is False


def test_startup_recovery_is_idempotent(receipts: InboundReceiptService):
    receipts.claim("event-1", "c2c:100")
    receipts.recover_on_startup()

    assert receipts.recover_on_startup() == 0


def test_two_adapter_instances_do_not_share_receipts(database: DatabaseManager):
    first = InboundReceiptService(database, channel="onebot", adapter_instance="onebot-1")
    second = InboundReceiptService(database, channel="onebot", adapter_instance="onebot-2")

    assert first.claim("event-1", "c2c:100") is True
    # A second configured instance is a different bot; the same upstream id there
    # is a different event.
    assert second.claim("event-1", "c2c:100") is True


def test_two_channels_do_not_share_receipts(database: DatabaseManager):
    onebot = InboundReceiptService(database, channel="onebot", adapter_instance="shared")
    qqbot = InboundReceiptService(database, channel="qqbot", adapter_instance="shared")

    assert onebot.claim("event-1", "c2c:100") is True
    assert qqbot.claim("event-1", "c2c:100") is True


def test_status_counts_track_the_lifecycle(receipts: InboundReceiptService):
    receipts.claim("a", "c2c:1")
    receipts.claim("b", "c2c:1")
    receipts.complete("b")
    receipts.claim("c", "c2c:1")
    receipts.retry("c")

    counts = receipts.status_counts()

    assert counts == {"processing": 1, INBOUND_RETRYABLE_STATUS: 1, "completed": 1}


@pytest.mark.parametrize("event_key", ["", "   ", None, "x" * 129])
def test_an_invalid_event_key_is_rejected(receipts: InboundReceiptService, event_key):
    with pytest.raises(ValueError):
        receipts.claim(event_key, "c2c:100")


def test_a_missing_channel_or_instance_is_rejected(database: DatabaseManager):
    with pytest.raises(ValueError):
        InboundReceiptService(database, channel="", adapter_instance="x")
    with pytest.raises(ValueError):
        InboundReceiptService(database, channel="onebot", adapter_instance="  ")


def test_completing_an_unknown_event_is_a_noop(receipts: InboundReceiptService):
    receipts.complete("never-seen")

    assert receipts.status_counts() == {
        "processing": 0,
        INBOUND_RETRYABLE_STATUS: 0,
        "completed": 0,
    }
