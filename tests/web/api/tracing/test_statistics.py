from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from sqlalchemy import inspect

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.database.manager import Base
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.tracing import LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.resilience import ProviderAttempt
from kirara_ai.tracing import LLMTracer
from kirara_ai.tracing.manager import TracingManager
from kirara_ai.tracing.models import LLMRequestTrace
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


@pytest.fixture
def tracing_statistics_api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())

    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'tracing-statistics.db').as_posix()}",
        is_debug=False,
    )
    database.initialize()
    Base.metadata.create_all(database.engine)
    container.register(DatabaseManager, database)

    tracer = LLMTracer(container)
    tracer.initialize()
    manager = TracingManager(container)
    manager.register_tracer("llm", tracer)
    container.register(TracingManager, manager)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    yield app.test_client(), tracer, database

    tracer.shutdown()
    database.shutdown()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _request(model: str = "research-model") -> LLMChatRequest:
    return LLMChatRequest(
        model=model,
        messages=[
            LLMChatMessage(
                role="user",
                content=[LLMChatTextContent(text="lookup documentation")],
            )
        ],
    )


def _insert_trace(
    database: DatabaseManager,
    *,
    trace_id: str,
    request_time: datetime,
    model_id: str = "research-model",
    backend_name: str = "router-backend",
    provider: str = "primary-provider",
    status: str = "success",
    error_category: str | None = None,
    usage_source: str = "provider",
    total_tokens: int | None = 10,
) -> None:
    with database.get_session() as session:
        session.add(
            LLMRequestTrace(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=backend_name,
                provider=provider,
                request_time=request_time,
                status=status,
                error_category=error_category,
                usage_source=usage_source,
                total_tokens=total_tokens,
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_trace_log_filters_every_statistics_dimension_and_time_range(
    tracing_statistics_api,
):
    client, _, database = tracing_statistics_api
    _insert_trace(
        database,
        trace_id="matching-trace",
        request_time=datetime(2026, 8, 27, 10, 0),
        model_id="model-alpha",
        backend_name="router-a",
        provider="provider-a",
        status="failed",
        error_category="timeout",
        usage_source="estimated",
    )
    _insert_trace(
        database,
        trace_id="wrong-provider",
        request_time=datetime(2026, 8, 27, 10, 30),
        model_id="model-alpha",
        backend_name="router-a",
        provider="provider-b",
        status="failed",
        error_category="timeout",
        usage_source="estimated",
    )
    _insert_trace(
        database,
        trace_id="outside-range",
        request_time=datetime(2026, 8, 28, 10, 0),
        model_id="model-alpha",
        backend_name="router-a",
        provider="provider-a",
        status="failed",
        error_category="timeout",
        usage_source="estimated",
    )

    response = await client.post(
        "/api/tracing/llm/traces",
        headers=_headers(),
        json={
            "provider": "provider-a",
            "backend": "router-a",
            "model": "model-alpha",
            "status": "failed",
            "error_category": "timeout",
            "usage_source": "estimated",
            "start_time": "2026-08-27T00:00:00+08:00",
            "end_time": "2026-08-28T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["total"] == 1
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["items"][0]["trace_id"] == "matching-trace"
    assert payload["items"][0]["provider"] == "provider-a"
    assert payload["items"][0]["error_category"] == "timeout"

    statistics_query = urlencode(
        {
            "provider": "provider-a",
            "backend": "router-a",
            "model": "model-alpha",
            "status": "failed",
            "error_category": "timeout",
            "usage_source": "estimated",
            "start_time": "2026-08-27T00:00:00+08:00",
            "end_time": "2026-08-28T00:00:00+08:00",
            "timezone": "Asia/Shanghai",
        }
    )
    statistics_response = await client.get(
        f"/api/tracing/llm/statistics?{statistics_query}",
        headers=_headers(),
    )

    assert statistics_response.status_code == 200
    statistics = await statistics_response.get_json()
    assert statistics["overview"]["total_requests"] == 1
    assert statistics["models"][0]["model_id"] == "model-alpha"
    assert statistics["backends"][0]["backend_name"] == "router-a"
    assert statistics["providers"][0]["provider"] == "provider-a"
    assert statistics["usage_sources"][0]["usage_source"] == "estimated"


@pytest.mark.asyncio
async def test_trace_log_uses_stable_strict_pagination_for_equal_timestamps(
    tracing_statistics_api,
):
    client, _, database = tracing_statistics_api
    timestamp = datetime(2026, 8, 27, 12, 0)
    for index in range(1, 6):
        _insert_trace(
            database,
            trace_id=f"trace-{index}",
            request_time=timestamp,
        )

    pages = []
    for page in (1, 2, 3, 4):
        response = await client.post(
            "/api/tracing/llm/traces",
            headers=_headers(),
            json={"page": page, "page_size": 2},
        )
        assert response.status_code == 200
        pages.append(await response.get_json())

    assert [item["trace_id"] for item in pages[0]["items"]] == ["trace-5", "trace-4"]
    assert [item["trace_id"] for item in pages[1]["items"]] == ["trace-3", "trace-2"]
    assert [item["trace_id"] for item in pages[2]["items"]] == ["trace-1"]
    assert pages[3]["items"] == []
    assert all(page["total"] == 5 for page in pages)
    assert all(page["total_pages"] == 3 for page in pages)


@pytest.mark.asyncio
async def test_statistics_groups_daily_trend_in_the_requested_timezone(
    tracing_statistics_api,
):
    client, _, database = tracing_statistics_api
    _insert_trace(
        database,
        trace_id="before-shanghai-midnight",
        request_time=datetime(2026, 8, 26, 23, 30),
        total_tokens=10,
    )
    _insert_trace(
        database,
        trace_id="after-shanghai-midnight",
        request_time=datetime(2026, 8, 27, 0, 30),
        status="failed",
        error_category="timeout",
        total_tokens=20,
    )
    _insert_trace(
        database,
        trace_id="outside-statistics-range",
        request_time=datetime(2026, 8, 27, 9, 0),
    )

    query = urlencode(
        {
            "timezone": "UTC",
            "start_time": "2026-08-26T15:00:00+00:00",
            "end_time": "2026-08-26T17:00:00+00:00",
        }
    )
    response = await client.get(
        f"/api/tracing/llm/statistics?{query}",
        headers=_headers(),
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["timezone"] == "UTC"
    assert payload["overview"]["total_requests"] == 2
    assert payload["overview"]["total_tokens"] == 30
    assert payload["daily_stats"] == [
        {
            "date": "2026-08-26",
            "requests": 2,
            "tokens": 30,
            "success": 1,
            "failed": 1,
        }
    ]
    assert {"overview", "daily_stats", "hourly_stats", "models", "backends"} <= payload.keys()


@pytest.mark.asyncio
async def test_trace_export_supports_bounded_json_and_csv_downloads(
    tracing_statistics_api,
):
    client, _, database = tracing_statistics_api
    for index in range(1, 4):
        _insert_trace(
            database,
            trace_id=f"export-{index}",
            request_time=datetime(2026, 8, 27, 10, index),
        )

    json_response = await client.post(
        "/api/tracing/llm/export",
        headers=_headers(),
        json={"format": "json", "limit": 2, "timezone": "UTC"},
    )
    csv_response = await client.post(
        "/api/tracing/llm/export",
        headers=_headers(),
        json={"format": "csv", "limit": 2, "timezone": "UTC"},
    )
    excessive_response = await client.post(
        "/api/tracing/llm/export",
        headers=_headers(),
        json={"format": "json", "limit": 10001},
    )

    assert json_response.status_code == 200
    assert json_response.headers["Content-Disposition"].endswith('"llm-traces.json"')
    json_payload = await json_response.get_json()
    assert json_payload["exported"] == 2
    assert json_payload["total"] == 3
    assert json_payload["truncated"] is True
    assert json_payload["timezone"] == "UTC"

    assert csv_response.status_code == 200
    assert csv_response.headers["Content-Disposition"].endswith('"llm-traces.csv"')
    rows = list(csv.DictReader(io.StringIO((await csv_response.get_data()).decode("utf-8-sig"))))
    assert len(rows) == 2
    assert rows[0]["trace_id"] == "export-3"
    assert {"provider", "backend_name", "model_id", "status", "error_category"} <= rows[0].keys()

    assert excessive_response.status_code == 400
    assert await excessive_response.get_json() == {
        "error": "limit must be an integer between 1 and 10000"
    }


@pytest.mark.asyncio
async def test_tracing_api_rejects_ambiguous_times_and_unknown_timezones(
    tracing_statistics_api,
):
    client, _, _ = tracing_statistics_api

    ambiguous = await client.post(
        "/api/tracing/llm/traces",
        headers=_headers(),
        json={"start_time": "2026-08-27T00:00:00"},
    )
    invalid_zone = await client.get(
        "/api/tracing/llm/statistics?timezone=Mars%2FOlympus",
        headers=_headers(),
    )
    invalid_zone_type = await client.post(
        "/api/tracing/llm/traces",
        headers=_headers(),
        json={"timezone": True},
    )

    assert ambiguous.status_code == 400
    assert await ambiguous.get_json() == {
        "error": "start_time must be an ISO-8601 datetime with a timezone"
    }
    assert invalid_zone.status_code == 400
    assert await invalid_zone.get_json() == {"error": "Unknown timezone: Mars/Olympus"}
    assert invalid_zone_type.status_code == 400
    assert await invalid_zone_type.get_json() == {"error": "timezone must be a valid IANA timezone name"}


def test_trace_events_persist_provider_and_error_category():
    trace = LLMRequestTrace()
    request = _request("model-alpha")
    trace.update_from_event(
        LLMRequestStartEvent(
            trace_id="classified-trace",
            model_id="model-alpha",
            backend_name="router-a",
            request=request,
        )
    )
    trace.update_from_event(
        LLMRequestFailEvent(
            trace_id="classified-trace",
            model_id="model-alpha",
            backend_name="router-a",
            request=request,
            error="upstream timed out",
            start_time=datetime.now().timestamp(),
            attempts=[
                ProviderAttempt(
                    trace_id="classified-trace",
                    model="model-alpha",
                    provider="provider-a",
                    attempt=1,
                    retry_index=0,
                    success=False,
                    error_category="timeout",
                )
            ],
        )
    )

    assert trace.provider == "provider-a"
    assert trace.error_category == "timeout"
    assert trace.to_dict()["provider"] == "provider-a"
    assert trace.to_dict()["error_category"] == "timeout"


def test_llm_trace_database_has_filter_and_time_indexes(tracing_statistics_api):
    _, _, database = tracing_statistics_api
    index_names = {
        index["name"]
        for index in inspect(database.engine).get_indexes("llm_request_traces")
    }

    assert {
        "idx_request_model",
        "idx_backend_time",
        "idx_status_time",
        "idx_provider_time",
        "idx_error_category_time",
        "idx_usage_source_time",
    } <= index_names
