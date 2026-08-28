from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.audit_store import MCPAuditStore
from kirara_ai.mcp_module.confirmation_store import MCPConfirmationStore
from kirara_ai.mcp_module.manager import MCPServerManager, ToolCacheEntry
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


CREATOR = RuntimePrincipal(
    subject="creator-subject",
    role="admin",
    scopes=frozenset({"*"}),
    is_creator=True,
)


class _Server:
    state = MCPConnectionState.CONNECTED

    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="ok")],
            isError=False,
        )


def _manager(tmp_path: Path) -> tuple[MCPServerManager, _Server]:
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(
        container,
        confirmation_store=MCPConfirmationStore(tmp_path / "confirmations.db"),
        audit_store=MCPAuditStore(tmp_path / "audit.db"),
    )
    server = _Server()
    manager.servers["files"] = server
    manager.tools_cache["write"] = ToolCacheEntry(
        server_id="files",
        original_name="write",
        tool_info=types.Tool(
            name="write",
            description="Write a file",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
    )
    return manager, server


@pytest.mark.asyncio
async def test_manager_defaults_to_deny_without_explicit_agent_owner_authorization(tmp_path: Path):
    manager, server = _manager(tmp_path)

    with runtime_principal_context(CREATOR):
        denied = await manager.call_tool(
            "write",
            {"text": "payload"},
            agent_allowlist=frozenset({"files.write"}),
            agent_mcp_server_ids=frozenset({"files"}),
        )

    assert denied is None
    assert server.calls == []
    assert manager.audit_records[-1]["outcome"] == "host_unauthorized"


@pytest.mark.asyncio
async def test_manager_allows_creator_only_for_matching_agent_owner(tmp_path: Path):
    manager, server = _manager(tmp_path)

    with runtime_principal_context(CREATOR):
        denied = await manager.call_tool(
            "write",
            {"text": "payload"},
            agent_allowlist=frozenset({"files.write"}),
            agent_mcp_server_ids=frozenset({"files"}),
            agent_owner_subject="different-subject",
        )
        allowed = await manager.call_tool(
            "write",
            {"text": "payload"},
            agent_allowlist=frozenset({"files.write"}),
            agent_mcp_server_ids=frozenset({"files"}),
            agent_owner_subject=CREATOR.subject,
        )

    assert denied is None
    assert allowed is not None
    assert server.calls == [("write", {"text": "payload"})]
