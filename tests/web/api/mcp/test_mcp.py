from pathlib import Path
import time
from unittest.mock import AsyncMock, patch

import pytest
from mcp import types

from kirara_ai.config.global_config import GlobalConfig, MCPServerConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.agent_runtime import AgentDefinition, AgentRegistry, ResourceBinding
from kirara_ai.mcp_module.audit_store import MCPAuditStore
from kirara_ai.mcp_module.manager import MCPServerManager, ToolCacheEntry
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


@pytest.fixture
def mcp_api(tmp_path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService())
    container.register(AgentRegistry, AgentRegistry())
    manager = MCPServerManager(
        container,
        audit_store=MCPAuditStore(tmp_path / "mcp-audit.db"),
    )
    container.register(MCPServerManager, manager)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), config, manager


def _headers():
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_mcp_audit_api_returns_redacted_runtime_page_and_filters(mcp_api):
    client, _, manager = mcp_api
    manager._audit_operation("docs", "connect", time.monotonic(), "success")
    manager._audit_operation(
        "docs",
        "call_tool",
        time.monotonic(),
        "error",
        RuntimeError("transport secret must not escape"),
    )
    manager._audit_operation("other", "connect", time.monotonic(), "success")

    response = await client.get(
        "/api/mcp/audit?server_id=docs&operation=call_tool&limit=1",
        headers=_headers(),
    )

    assert response.status_code == 200
    body = await response.get_json()
    assert body["persistent"] is True
    assert body["total"] == 1
    assert body["has_more"] is False
    assert body["items"][0]["server"] == "docs"
    assert body["items"][0]["outcome"] == "error"
    assert body["items"][0]["error"] == {
        "type": "RuntimeError",
        "message": "operation failed",
    }
    assert "transport secret" not in str(body)

    invalid = await client.get(
        "/api/mcp/audit?offset=-1",
        headers=_headers(),
    )
    assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_mcp_lifecycle_operations_are_audited(mcp_api):
    _, _, manager = mcp_api
    assert await manager.connect_server("missing") is False

    manager.load_server(
        MCPServerConfig.model_validate(
            {"id": "lifecycle", "server": {"type": "http", "url": "https://example.invalid/mcp"}}
        )
    )
    assert await manager.stop_server("lifecycle") is True

    operations = [
        (record["operation"], record["outcome"])
        for record in manager.audit_records
    ]
    assert ("connect", "not_found") in operations
    assert ("disconnect", "already_disconnected") in operations


@pytest.mark.asyncio
async def test_mcp_api_create_and_list_use_canonical_shape_and_redact_secrets(mcp_api):
    client, config, _ = mcp_api
    payload = {
        "id": "docs",
        "name": "Documentation",
        "server": {
            "type": "http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "real-token", "X-Trace": "trace"},
        },
        "apps": {"codex": True, "claude-desktop": True},
        "tags": ["docs"],
        "metadata": {"owner": "team", "api_token": "private"},
    }
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        created = await client.post("/api/mcp/servers", headers=_headers(), json=payload)

    assert created.status_code == 200
    body = await created.get_json()
    assert body["server"]["type"] == "http"
    assert body["server"]["url"] == payload["server"]["url"]
    assert body["server"]["headers"] == {
        "Authorization": "********",
        "X-Trace": "********",
    }
    assert body["metadata"]["api_token"] == "********"
    assert "connection_type" not in body
    assert "command" not in body
    assert "args" not in body
    assert config.mcp.servers[0].server.headers["Authorization"] == "real-token"

    listed = await client.get("/api/mcp/servers", headers=_headers())
    assert listed.status_code == 200
    listed_body = await listed.get_json()
    assert listed_body["items"][0]["server"]["headers"]["Authorization"] == "********"
    assert "connection_type" not in listed_body["items"][0]


@pytest.mark.asyncio
async def test_mcp_api_rejects_string_args_and_accepts_http_filter(mcp_api):
    client, _, _ = mcp_api
    invalid = await client.post(
        "/api/mcp/servers",
        headers=_headers(),
        json={
            "id": "bad",
            "server": {"type": "stdio", "command": "node", "args": "--flag value"},
        },
    )
    assert invalid.status_code == 400

    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        created = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "remote",
                "server": {"type": "http", "url": "https://example.invalid/mcp"},
            },
        )
    assert created.status_code == 200
    filtered = await client.get("/api/mcp/servers?type=http", headers=_headers())
    assert filtered.status_code == 200
    assert [item["id"] for item in (await filtered.get_json())["items"]] == ["remote"]


@pytest.mark.asyncio
async def test_mcp_api_does_not_overwrite_existing_disconnected_server(mcp_api):
    client, config, manager = mcp_api
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        first = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "stable-id",
                "name": "Original",
                "server": {"type": "http", "url": "https://example.invalid/original"},
            },
        )
        assert first.status_code == 200
        original = config.mcp.servers[0]
        assert manager.get_server("stable-id") is not None
        assert manager.get_server("stable-id").state is MCPConnectionState.DISCONNECTED

        duplicate = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "stable-id",
                "name": "Replacement",
                "server": {"type": "http", "url": "https://example.invalid/replacement"},
            },
        )

    assert duplicate.status_code == 409
    assert len(config.mcp.servers) == 1
    assert config.mcp.servers[0] is original
    assert config.mcp.servers[0].name == "Original"
    assert config.mcp.servers[0].server.url == "https://example.invalid/original"


@pytest.mark.asyncio
async def test_mcp_api_update_preserves_masked_credentials_and_partial_fields(mcp_api):
    client, config, manager = mcp_api
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        created = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "remote",
                "description": "before",
                "server": {
                    "type": "http",
                    "url": "https://example.invalid/mcp",
                    "headers": {"X-Token": "real-token"},
                },
            },
        )
        assert created.status_code == 200
        updated = await client.put(
            "/api/mcp/servers/remote",
            headers=_headers(),
            json={
                "description": "after",
                "server": {
                    "type": "http",
                    "url": "https://example.invalid/other",
                    "headers": {"X-Token": "********"},
                },
            },
        )

    assert updated.status_code == 200
    assert config.mcp.servers[0].description == "after"
    assert config.mcp.servers[0].server.url == "https://example.invalid/other"
    assert config.mcp.servers[0].server.headers["X-Token"] == "real-token"
    assert (await updated.get_json())["server"]["headers"]["X-Token"] == "********"
    assert manager.get_server("remote") is not None


@pytest.mark.asyncio
async def test_mcp_api_tool_call_uses_manager_policy_and_confirmation(mcp_api):
    client, _, manager = mcp_api
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        response = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "tools",
                "server": {"type": "stdio", "command": "node", "args": []},
            },
        )
    assert response.status_code == 200
    server = manager.get_server("tools")
    assert server is not None
    server.state = MCPConnectionState.CONNECTED
    server.call_tool = AsyncMock(
        return_value=types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")], isError=False
        )
    )
    tool = types.Tool(
        name="search",
        description="Search",
        inputSchema={"type": "object"},
        annotations=types.ToolAnnotations(destructiveHint=True),
    )
    manager.tools_cache["search"] = ToolCacheEntry("tools", "search", tool)

    registry = manager.container.resolve(AgentRegistry)
    registry.register(
        AgentDefinition(
            agent_id="tool-admin",
            model_priority=("test-model",),
            mcp_bindings=(
                ResourceBinding(
                    resource_id="mcp.tools",
                    resource_type="mcp",
                    version="1",
                    content_sha256="0" * 64,
                ),
            ),
            mcp_allowlist={"tools.search"},
        )
    )
    registry.register(
        AgentDefinition(
            agent_id="unbound-agent",
            model_priority=("test-model",),
            mcp_allowlist={"tools.search"},
        )
    )

    denied = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={"toolName": "search", "params": {}, "agent_id": "tool-admin"},
    )
    assert denied.status_code == 409
    confirmation_id = (await denied.get_json())["confirmation_id"]
    server.call_tool.assert_not_awaited()

    # A client-provided boolean or allowlist is not a confirmation context and
    # must not widen the Agent's policy.
    allowed = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={
            "toolName": "search",
            "params": {},
            "agent_id": "tool-admin",
            "confirmed": True,
            "agent_allowlist": ["tools.search", "tools.other"],
        },
    )
    assert allowed.status_code == 409
    server.call_tool.assert_not_awaited()

    confirmed = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={
            "toolName": "search",
            "params": {},
            "agent_id": "tool-admin",
            "confirmation_id": confirmation_id,
        },
    )
    assert confirmed.status_code == 200
    assert (await confirmed.get_json())["result"]["content"][0]["text"] == "ok"
    server.call_tool.assert_awaited_once_with("search", {})

    replay = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={
            "toolName": "search",
            "params": {},
            "agent_id": "tool-admin",
            "confirmation_id": confirmation_id,
        },
    )
    assert replay.status_code == 403
    server.call_tool.assert_awaited_once_with("search", {})

    unbound = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={
            "toolName": "search",
            "params": {},
            "agent_id": "unbound-agent",
        },
    )
    assert unbound.status_code == 403
    server.call_tool.assert_awaited_once_with("search", {})


@pytest.mark.asyncio
async def test_mcp_api_tool_call_requires_a_real_agent(mcp_api):
    client, _, manager = mcp_api
    manager.load_server(
        MCPServerConfig.model_validate(
            {"id": "tools", "server": {"type": "stdio", "command": "node", "args": []}}
        )
    )
    manager.tools_cache["search"] = ToolCacheEntry(
        "tools",
        "search",
        types.Tool(name="search", inputSchema={"type": "object"}),
    )

    response = await client.post(
        "/api/mcp/servers/tools/tools/call",
        headers=_headers(),
        json={"toolName": "search", "params": {}, "confirmed": True},
    )

    assert response.status_code == 400
    assert (await response.get_json())["message"] == "agent_id is required"


@pytest.mark.asyncio
async def test_mcp_tool_lists_identify_the_owning_server(mcp_api):
    client, _, manager = mcp_api
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        created = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "docs",
                "server": {"type": "stdio", "command": "node", "args": []},
            },
        )
    assert created.status_code == 200

    server = manager.get_server("docs")
    assert server is not None
    server.state = MCPConnectionState.CONNECTED
    tool = types.Tool(
        name="search",
        description="Search documentation",
        inputSchema={"type": "object"},
    )
    manager.tools_cache["docs.search"] = ToolCacheEntry("docs", "search", tool)

    all_tools = await client.get("/api/mcp/tools", headers=_headers())
    server_tools = await client.get("/api/mcp/servers/docs/tools", headers=_headers())

    assert all_tools.status_code == 200
    assert await all_tools.get_json() == [
        {
            "name": "docs.search",
            "description": "Search documentation",
            "input_schema": {"type": "object"},
            "server_id": "docs",
        }
    ]
    assert server_tools.status_code == 200
    assert await server_tools.get_json() == [
        {
            "name": "search",
            "description": "Search documentation",
            "input_schema": {"type": "object"},
            "server_id": "docs",
        }
    ]


@pytest.mark.asyncio
async def test_refresh_replaces_changed_server_without_preserving_old_cache(mcp_api):
    _, config, manager = mcp_api
    first = GlobalConfig.MCPConfig.ServerConfig if False else None
    from kirara_ai.config.global_config import MCPServerConfig

    config.mcp.servers = [
        MCPServerConfig.model_validate(
            {
                "id": "docs",
                "server": {"type": "http", "url": "https://example.invalid/old"},
            }
        )
    ]
    manager.load_server(config.mcp.servers[0])
    old_server = manager.get_server("docs")
    old_server.state = MCPConnectionState.ERROR
    manager.tools_cache["old-search"] = ToolCacheEntry("docs", "old-search", types.Tool(name="old-search", inputSchema={"type": "object"}))
    manager.prompts_cache["docs"] = []
    manager.resources_cache["docs"] = []
    config.mcp.servers = [
        MCPServerConfig.model_validate(
            {
                "id": "docs",
                "server": {"type": "http", "url": "https://example.invalid/new"},
            }
        )
    ]

    await manager.refresh_managed_servers(connect=False)

    assert manager.get_server("docs") is not old_server
    assert manager.get_server("docs").server_config.server.url == "https://example.invalid/new"
    assert "old-search" not in manager.tools_cache
    assert "docs" not in manager.prompts_cache
    assert "docs" not in manager.resources_cache


@pytest.mark.asyncio
async def test_mcp_prompt_sample_validates_and_forwards_declared_arguments(mcp_api):
    client, _, manager = mcp_api
    with patch("kirara_ai.web.api.mcp.routes.ConfigLoader.save_config_with_backup"):
        created = await client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "id": "prompts",
                "server": {"type": "stdio", "command": "node", "args": []},
            },
        )
    assert created.status_code == 200
    server = manager.get_server("prompts")
    assert server is not None
    server.state = MCPConnectionState.CONNECTED
    manager.prompts_cache["prompts"] = [
        types.Prompt(
            name="research-brief",
            arguments=[
                types.PromptArgument(name="topic", required=True),
                types.PromptArgument(name="audience", required=False),
                types.PromptArgument(name="tone", required=False),
            ],
        )
    ]
    manager.get_prompt = AsyncMock(
        return_value=types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="ready"),
                )
            ]
        )
    )

    missing = await client.post(
        "/api/mcp/servers/prompts/prompts/sample",
        headers=_headers(),
        json={"promptId": "research-brief", "arguments": {"audience": "students"}},
    )
    assert missing.status_code == 400
    assert "topic" in (await missing.get_json())["message"]
    manager.get_prompt.assert_not_awaited()

    sampled = await client.post(
        "/api/mcp/servers/prompts/prompts/sample",
        headers=_headers(),
        json={
            "promptId": "research-brief",
            "arguments": {
                "topic": "retrieval",
                "audience": "students",
                "tone": "concise",
            },
        },
    )

    assert sampled.status_code == 200
    assert (await sampled.get_json())["text"] == "ready"
    manager.get_prompt.assert_awaited_once_with(
        "prompts",
        "research-brief",
        {"topic": "retrieval", "audience": "students", "tone": "concise"},
    )


@pytest.mark.asyncio
async def test_mcp_unexpected_errors_do_not_expose_internal_details(mcp_api):
    client, _, manager = mcp_api
    manager.get_all_servers = lambda: (_ for _ in ()).throw(
        RuntimeError("Authorization: Bearer private-token internal-host")
    )

    response = await client.get("/api/mcp/servers", headers=_headers())

    assert response.status_code == 500
    body = await response.get_json()
    assert body == {"message": "MCP operation failed"}
    assert "private-token" not in str(body)
    assert "internal-host" not in str(body)


def test_mcp_routes_do_not_return_raw_exception_text():
    routes_path = Path(__file__).parents[4] / "kirara_ai" / "web" / "api" / "mcp" / "routes.py"
    source = routes_path.read_text(encoding="utf-8")

    assert 'jsonify({"message": str(e)})' not in source
    assert "_public_error(str(e))" not in source
