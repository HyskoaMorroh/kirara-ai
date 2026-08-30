"""The delivery timing API must be queryable and must not leak chat content."""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.fixture
def api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.system.timezone = "UTC"
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService())
    container.register(EventBus, EventBus())
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'timings.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    store = DeliveryTimingStore(database)
    container.register(DeliveryTimingStore, store)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), store


@pytest.fixture
def api_without_store(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService())
    container.register(EventBus, EventBus())

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client()


def add(store: DeliveryTimingStore, **overrides) -> None:
    payload = {
        "channel": "onebot",
        "adapter_instance": "onebot-1",
        "durations": {
            "queue_seconds": 0.2,
            "llm_first_byte_seconds": 3.0,
            "llm_generation_seconds": 5.0,
            "formatting_seconds": 0.1,
            "send_seconds": 0.3,
            "total_seconds": 8.6,
        },
        "conversation_key": "c2c:一位真实用户",
    }
    payload.update(overrides)
    store.record(**payload)


@pytest.mark.asyncio
async def test_the_summary_requires_authentication(api):
    client, _ = api

    response = await client.get("/api/tracing/delivery/summary")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_summary_reports_per_phase_averages_and_samples(api):
    client, store = api
    add(store)
    add(store)

    response = await client.get("/api/tracing/delivery/summary", headers=headers())
    payload = await response.get_json()

    assert response.status_code == 200
    assert payload["deliveries"] == 2
    assert payload["phases"]["total_seconds"]["samples"] == 2
    assert payload["phases"]["total_seconds"]["avg_seconds"] == pytest.approx(8.6)


@pytest.mark.asyncio
async def test_an_unmeasured_phase_reports_null_with_zero_samples(api):
    client, store = api
    add(
        store,
        durations={
            "queue_seconds": 0.1,
            "llm_first_byte_seconds": None,
            "llm_generation_seconds": None,
            "formatting_seconds": 0.1,
            "send_seconds": 0.2,
            "total_seconds": 0.4,
        },
    )

    payload = await (
        await client.get("/api/tracing/delivery/summary", headers=headers())
    ).get_json()

    first_byte = payload["phases"]["llm_first_byte_seconds"]
    assert first_byte["avg_seconds"] is None
    assert first_byte["samples"] == 0


@pytest.mark.asyncio
async def test_the_summary_can_be_scoped_to_one_channel(api):
    client, store = api
    add(store, channel="onebot")
    add(store, channel="telegram")

    payload = await (
        await client.get(
            "/api/tracing/delivery/summary?channel=telegram", headers=headers()
        )
    ).get_json()

    assert payload["deliveries"] == 1
    assert "onebot" in payload["channels"]
    assert "telegram" in payload["channels"]


@pytest.mark.asyncio
async def test_an_invalid_time_range_is_rejected(api):
    client, store = api
    add(store)

    response = await client.get(
        "/api/tracing/delivery/summary?start_time=not-a-date", headers=headers()
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_naive_time_range_is_rejected(api):
    """Without a timezone the boundary is ambiguous, so it must not be accepted."""
    client, store = api
    add(store)

    response = await client.get(
        "/api/tracing/delivery/summary?start_time=2026-08-28T00:00:00", headers=headers()
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_recent_returns_rows_without_any_message_content(api):
    client, store = api
    add(store)

    payload = await (
        await client.get("/api/tracing/delivery/recent", headers=headers())
    ).get_json()

    assert len(payload["items"]) == 1
    assert "真实用户" not in str(payload)
    assert payload["items"][0]["total_seconds"] == pytest.approx(8.6)


@pytest.mark.asyncio
async def test_recent_rejects_an_invalid_limit(api):
    client, _ = api

    response = await client.get("/api/tracing/delivery/recent?limit=0", headers=headers())

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_recent_rejects_a_non_numeric_limit(api):
    client, _ = api

    response = await client.get(
        "/api/tracing/delivery/recent?limit=many", headers=headers()
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_the_endpoints_report_503_when_the_store_is_absent(api_without_store):
    client = api_without_store

    summary = await client.get("/api/tracing/delivery/summary", headers=headers())
    recent = await client.get("/api/tracing/delivery/recent", headers=headers())

    assert summary.status_code == 503
    assert recent.status_code == 503
