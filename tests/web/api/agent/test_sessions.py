"""Session and confirmation management must be reachable over the API."""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.agent_runtime import AgentRegistry, SessionStore
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def message(role: str, text: str) -> LLMChatMessage:
    return LLMChatMessage(role=role, content=[LLMChatTextContent(text=text)])


@pytest.fixture
def session_api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService())
    container.register(EventBus, EventBus())
    container.register(AgentRegistry, AgentRegistry(tmp_path / "data"))
    store = SessionStore(tmp_path / "data")
    container.register(SessionStore, store)

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
    container.register(AgentRegistry, AgentRegistry(tmp_path / "data"))

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.asyncio
async def test_listing_sessions_requires_authentication(session_api):
    client, _ = session_api

    response = await client.get("/api/agents/sessions")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_saved_session_is_listed_with_counts_only(session_api):
    client, store = session_api
    store.save_history("onebot:acct:c2c:100", [message("user", "机密内容")], agent_id="a1")

    response = await client.get("/api/agents/sessions", headers=headers())
    payload = await response.get_json()

    assert response.status_code == 200
    assert len(payload["items"]) == 1
    assert payload["items"][0]["message_count"] == 1
    # Conversation text must never cross this boundary.
    assert "机密内容" not in str(payload)


@pytest.mark.asyncio
async def test_a_session_can_be_deleted_over_the_api(session_api):
    client, store = session_api
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    listing = await (await client.get("/api/agents/sessions", headers=headers())).get_json()
    session_id = listing["items"][0]["session_id"]

    response = await client.delete(f"/api/agents/sessions/{session_id}", headers=headers())

    assert response.status_code == 200
    assert store.list_sessions() == []


@pytest.mark.asyncio
async def test_deleting_an_unknown_session_returns_404(session_api):
    client, _ = session_api

    response = await client.delete(f"/api/agents/sessions/{'0' * 64}", headers=headers())

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_traversal_session_id_is_refused(session_api):
    client, store = session_api
    store.save_history("k", [message("user", "hi")], agent_id="a1")

    response = await client.delete("/api/agents/sessions/..", headers=headers())

    assert response.status_code in (404, 405)
    assert len(store.list_sessions()) == 1


@pytest.mark.asyncio
async def test_history_can_be_cleared_without_removing_the_session(session_api):
    client, store = session_api
    store.save_history("k", [message("user", "hi")], agent_id="a1")
    listing = await (await client.get("/api/agents/sessions", headers=headers())).get_json()
    session_id = listing["items"][0]["session_id"]

    response = await client.delete(
        f"/api/agents/sessions/{session_id}/history", headers=headers()
    )

    assert response.status_code == 200
    remaining = store.list_sessions()
    assert len(remaining) == 1
    assert remaining[0]["message_count"] == 0


@pytest.mark.asyncio
async def test_an_invalid_limit_is_rejected(session_api):
    client, _ = session_api

    response = await client.get("/api/agents/sessions?limit=0", headers=headers())

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_non_numeric_limit_is_rejected(session_api):
    client, _ = session_api

    response = await client.get("/api/agents/sessions?limit=abc", headers=headers())

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_pending_confirmations_are_listable_without_tool_arguments(session_api):
    client, store = session_api
    store.save_pending(
        {
            "confirmation_id": "c" * 32,
            "agent_id": "a1",
            "tool_name": "Bash",
            "arguments": {"command": "rm -rf /"},
        },
        session_key="k",
    )

    response = await client.get("/api/agents/confirmations", headers=headers())
    payload = await response.get_json()

    assert response.status_code == 200
    assert payload["items"][0]["confirmation_id"] == "c" * 32
    assert payload["items"][0]["status"] == "awaiting_confirmation"
    # The queued arguments stay server-side.
    assert "rm -rf" not in str(payload)


@pytest.mark.asyncio
async def test_session_endpoints_report_503_when_the_runtime_is_absent(api_without_store):
    client = api_without_store

    listing = await client.get("/api/agents/sessions", headers=headers())
    confirmations = await client.get("/api/agents/confirmations", headers=headers())

    assert listing.status_code == 503
    assert confirmations.status_code == 503
