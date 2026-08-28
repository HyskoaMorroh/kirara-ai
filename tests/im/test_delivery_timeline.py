"""End-to-end delivery timeline: enough stages to answer "why was that slow".

The shipped timeline had five adapter-owned stages (formatting_started,
formatting_completed, send_started, send_succeeded/send_failed). That covers the
adapter but not the part users actually wait on, so "QQ is slow" could not be
separated from "the model was slow": there was no timestamp for when the event
was received, when the workflow/agent started, or when the model produced its
first token and finished.

The timeline was also in-memory only and excluded from `to_dict()`, so nothing
could be inspected after the fact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender


def message() -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat("100", "用户"),
        message_elements=[TextMessage("你好")],
    )


def test_every_documented_stage_is_recordable():
    item = message()

    for stage in (
        "received_event",
        "workflow_started",
        "llm_first_byte",
        "llm_completed",
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ):
        item.record_delivery_stage(stage, adapter="test")

    assert [event.stage for event in item.delivery_timeline] == [
        "received_event",
        "workflow_started",
        "llm_first_byte",
        "llm_completed",
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]


def test_timeline_is_serialized_so_it_can_be_inspected_afterwards():
    item = message()
    item.record_delivery_stage("received_event", adapter="onebot")
    item.record_delivery_stage("send_succeeded", adapter="onebot", retry_count=2)

    payload = item.to_dict()

    assert "delivery_timeline" in payload
    stages = [entry["stage"] for entry in payload["delivery_timeline"]]
    assert stages == ["received_event", "send_succeeded"]
    # Timestamps must be ISO-8601 strings, not datetime objects, so the payload
    # survives JSON serialization for a log line or an API response.
    assert isinstance(payload["delivery_timeline"][0]["timestamp"], str)
    assert payload["delivery_timeline"][1]["details"]["retry_count"] == 2


def test_delivery_durations_attribute_each_wait_to_one_phase():
    item = message()
    base = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)
    offsets = {
        "received_event": 0.0,
        "workflow_started": 0.2,
        "llm_first_byte": 4.0,
        "llm_completed": 9.0,
        "formatting_started": 9.1,
        "formatting_completed": 9.2,
        "send_started": 9.3,
        "send_succeeded": 9.6,
    }
    for stage, offset in offsets.items():
        item.record_delivery_stage_at(stage, base + timedelta(seconds=offset))

    durations = item.delivery_durations()

    assert durations["queue_seconds"] == pytest.approx(0.2)
    assert durations["llm_first_byte_seconds"] == pytest.approx(3.8)
    assert durations["llm_generation_seconds"] == pytest.approx(5.0)
    assert durations["formatting_seconds"] == pytest.approx(0.1)
    assert durations["send_seconds"] == pytest.approx(0.3)
    assert durations["total_seconds"] == pytest.approx(9.6)


def test_delivery_durations_omits_phases_with_no_evidence():
    item = message()
    base = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)
    item.record_delivery_stage_at("received_event", base)
    item.record_delivery_stage_at("send_succeeded", base + timedelta(seconds=2))

    durations = item.delivery_durations()

    # No model stages were recorded, so no model timing may be reported; an
    # absent measurement must never be presented as zero.
    assert "llm_first_byte_seconds" not in durations
    assert "llm_generation_seconds" not in durations
    assert durations["total_seconds"] == pytest.approx(2.0)


def test_delivery_durations_is_empty_without_a_timeline():
    assert message().delivery_durations() == {}


def test_failed_send_is_still_measurable():
    item = message()
    base = datetime(2026, 8, 28, 6, 0, 0, tzinfo=timezone.utc)
    item.record_delivery_stage_at("received_event", base)
    item.record_delivery_stage_at("send_started", base + timedelta(seconds=1))
    item.record_delivery_stage_at("send_failed", base + timedelta(seconds=4))

    durations = item.delivery_durations()

    assert durations["send_seconds"] == pytest.approx(3.0)
    assert durations["total_seconds"] == pytest.approx(4.0)


def test_recording_an_empty_stage_is_rejected():
    with pytest.raises(ValueError):
        message().record_delivery_stage("")


def test_recorded_details_cannot_be_mutated_afterwards():
    item = message()
    event = item.record_delivery_stage("send_started", adapter="onebot")

    with pytest.raises(TypeError):
        event.details["adapter"] = "tampered"  # type: ignore[index]


def test_a_naive_timestamp_is_rejected():
    """A naive timestamp would make cross-stage arithmetic silently wrong."""
    with pytest.raises(ValueError):
        message().record_delivery_stage_at("received_event", datetime(2026, 8, 28, 6, 0, 0))
