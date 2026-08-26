from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.database.manager import Base
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.tracing import LLMTracer
from kirara_ai.tracing.manager import TracingManager
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


@pytest.fixture
def tracing_api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())

    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'tracing.db').as_posix()}",
        is_debug=True,
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
    yield app.test_client(), tracer

    tracer.shutdown()
    database.shutdown()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _request() -> LLMChatRequest:
    return LLMChatRequest(
        model="research-model",
        messages=[
            LLMChatMessage(
                role="user",
                content=[LLMChatTextContent(text="lookup documentation")],
            )
        ],
    )


@pytest.mark.asyncio
async def test_trace_api_filters_searches_and_returns_correlation_id(tracing_api):
    client, tracer = tracing_api
    matching_trace = tracer.start_request_tracking(
        "primary-provider",
        _request(),
        correlation_id="turn-api-Exact-123",
    )
    tracer.start_request_tracking(
        "primary-provider",
        _request(),
        correlation_id="turn-api-other-456",
    )

    filtered_response = await client.post(
        "/api/tracing/llm/traces",
        headers=_headers(),
        json={"correlation_id": "turn-api-Exact-123"},
    )
    searched_response = await client.post(
        "/api/tracing/llm/traces",
        headers=_headers(),
        json={"query": "api-exact-123"},
    )
    detail_response = await client.get(
        f"/api/tracing/llm/detail/{matching_trace}",
        headers=_headers(),
    )

    assert filtered_response.status_code == 200
    filtered = await filtered_response.get_json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["trace_id"] == matching_trace
    assert filtered["items"][0]["correlation_id"] == "turn-api-Exact-123"

    assert searched_response.status_code == 200
    searched = await searched_response.get_json()
    assert searched["total"] == 1
    assert searched["items"][0]["trace_id"] == matching_trace

    assert detail_response.status_code == 200
    detail = await detail_response.get_json()
    assert detail["trace_id"] == matching_trace
    assert detail["correlation_id"] == "turn-api-Exact-123"
