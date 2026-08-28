"""Reply latency must be queryable after the fact, without storing chat content.

The in-memory timeline answers "why was *this* reply slow" while it is in flight.
It cannot answer the question an operator actually asks a week later: "QQ felt
slow last Tuesday — was it the model or the send?" That needs rows.

Three properties are pinned here because getting them wrong would make the table
either useless or dangerous:

- a phase that was never measured stays NULL, never 0 (0 reads as "instant");
- averages are computed only over rows that measured that phase, and each phase
  reports its sample count;
- no message content is stored — the conversation key is hashed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.im.delivery_timing_store import DeliveryTiming, DeliveryTimingStore
from kirara_ai.ioc.container import DependencyContainer


@pytest.fixture()
def store(tmp_path: Path) -> DeliveryTimingStore:
    container = DependencyContainer()
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'timings.db').as_posix()}",
    )
    database.initialize()
    return DeliveryTimingStore(database)


def durations(**overrides) -> dict:
    base = {
        "queue_seconds": 0.2,
        "llm_first_byte_seconds": 3.8,
        "llm_generation_seconds": 5.0,
        "formatting_seconds": 0.1,
        "send_seconds": 0.3,
        "total_seconds": 9.4,
    }
    base.update(overrides)
    return base


def record(store: DeliveryTimingStore, **overrides) -> bool:
    payload = {
        "channel": "onebot",
        "adapter_instance": "onebot-1",
        "durations": durations(),
        "conversation_key": "c2c:100",
        "segment_count": 2,
        "retry_count": 0,
    }
    payload.update(overrides)
    return store.record(**payload)


def test_a_delivery_is_persisted(store: DeliveryTimingStore):
    assert record(store) is True

    rows = store.recent()
    assert len(rows) == 1
    assert rows[0]["channel"] == "onebot"
    assert rows[0]["total_seconds"] == pytest.approx(9.4)


def test_no_message_content_is_stored(store: DeliveryTimingStore):
    record(store, conversation_key="c2c:一位真实用户")

    rows = store.recent()

    payload = str(rows)
    assert "真实用户" not in payload
    # The conversation key is hashed so "is this chat always slow" still works.
    with store.database.get_session() as session:
        row = session.query(DeliveryTiming).one()
        assert row.conversation_digest is not None
        assert len(row.conversation_digest) == 64


def test_an_unmeasured_phase_stays_null_not_zero(store: DeliveryTimingStore):
    """A non-stream request has no first byte; 0 would read as 'instant'."""
    record(store, durations=durations(llm_first_byte_seconds=None))

    rows = store.recent()

    assert rows[0]["llm_first_byte_seconds"] is None
    assert rows[0]["llm_generation_seconds"] == pytest.approx(5.0)


def test_a_delivery_with_no_measurements_is_not_stored(store: DeliveryTimingStore):
    stored = record(
        store,
        durations={
            "queue_seconds": None,
            "llm_first_byte_seconds": None,
            "llm_generation_seconds": None,
            "formatting_seconds": None,
            "send_seconds": None,
            "total_seconds": None,
        },
    )

    assert stored is False
    assert store.recent() == []


def test_a_negative_duration_is_discarded(store: DeliveryTimingStore):
    record(store, durations=durations(send_seconds=-1.0))

    assert store.recent()[0]["send_seconds"] is None


def test_a_non_numeric_duration_is_discarded(store: DeliveryTimingStore):
    record(store, durations=durations(send_seconds="fast"))

    assert store.recent()[0]["send_seconds"] is None


def test_summary_averages_only_rows_that_measured_the_phase(store: DeliveryTimingStore):
    record(store, durations=durations(llm_first_byte_seconds=2.0))
    record(store, durations=durations(llm_first_byte_seconds=4.0))
    # A non-stream reply: no first byte at all.
    record(store, durations=durations(llm_first_byte_seconds=None))

    summary = store.summarize()

    first_byte = summary["phases"]["llm_first_byte_seconds"]
    # Counting the third row as 0 would give 2.0; the honest average is 3.0.
    assert first_byte["avg_seconds"] == pytest.approx(3.0)
    assert first_byte["samples"] == 2
    assert summary["deliveries"] == 3


def test_summary_reports_the_max_per_phase(store: DeliveryTimingStore):
    record(store, durations=durations(send_seconds=0.3))
    record(store, durations=durations(send_seconds=12.0))

    summary = store.summarize()

    assert summary["phases"]["send_seconds"]["max_seconds"] == pytest.approx(12.0)


def test_summary_can_be_scoped_to_one_channel(store: DeliveryTimingStore):
    record(store, channel="onebot", durations=durations(total_seconds=10.0))
    record(store, channel="telegram", durations=durations(total_seconds=1.0))

    onebot = store.summarize(channel="onebot")
    telegram = store.summarize(channel="telegram")

    assert onebot["phases"]["total_seconds"]["avg_seconds"] == pytest.approx(10.0)
    assert telegram["phases"]["total_seconds"]["avg_seconds"] == pytest.approx(1.0)


def test_summary_counts_failed_deliveries_separately(store: DeliveryTimingStore):
    record(store)
    record(store, status="failed")

    summary = store.summarize()

    assert summary["deliveries"] == 2
    assert summary["failed_deliveries"] == 1


def test_summary_on_an_empty_table_reports_zero_and_no_samples(store: DeliveryTimingStore):
    summary = store.summarize()

    assert summary["deliveries"] == 0
    assert summary["phases"]["total_seconds"]["avg_seconds"] is None
    assert summary["phases"]["total_seconds"]["samples"] == 0


def test_channels_are_listable(store: DeliveryTimingStore):
    record(store, channel="onebot")
    record(store, channel="telegram")
    record(store, channel="onebot")

    assert store.list_channels() == ["onebot", "telegram"]


def test_recent_is_bounded(store: DeliveryTimingStore):
    for _ in range(10):
        record(store)

    assert len(store.recent(limit=3)) == 3


@pytest.mark.parametrize("limit", [0, -1, 100_000])
def test_an_invalid_limit_is_rejected(store: DeliveryTimingStore, limit: int):
    with pytest.raises(ValueError):
        store.recent(limit=limit)


def test_cleanup_removes_rows_beyond_the_retention_window(store: DeliveryTimingStore):
    record(store)
    with store.database.get_session() as session:
        row = session.query(DeliveryTiming).one()
        row.recorded_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=60)
        session.commit()

    deleted = store.cleanup()

    assert deleted == 1
    assert store.recent() == []


def test_cleanup_keeps_rows_inside_the_window(store: DeliveryTimingStore):
    record(store)

    assert store.cleanup() == 0
    assert len(store.recent()) == 1


def test_an_invalid_retention_window_is_rejected(store: DeliveryTimingStore):
    with pytest.raises(ValueError):
        DeliveryTimingStore(store.database, retention_days=0)
