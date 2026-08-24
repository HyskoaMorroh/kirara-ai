from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import types

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.workflow.implementations.blocks.mcp.tool import MCPToolProvider


@pytest.mark.asyncio
async def test_mcp_tool_provider_executes_through_manager_policy_boundary():
    container = DependencyContainer()
    manager = MagicMock(spec=MCPServerManager)
    manager.call_tool = AsyncMock(
        return_value=types.CallToolResult(
            content=[types.TextContent(type="text", text="result")],
            isError=False,
        )
    )
    container.register(MCPServerManager, manager)

    block = MCPToolProvider(enabled_tools=["search"])
    block.container = container

    result = await block._call_tool(
        ToolCall(
            id="call-1",
            function=Function(name="search", arguments={"query": "docs"}),
        )
    )

    manager.call_tool.assert_awaited_once_with(
        "search",
        {"query": "docs"},
        agent_allowlist=frozenset({"search"}),
        session_allowlist=frozenset({"search"}),
        workflow_allowlist=frozenset({"search"}),
        confirmed=False,
    )
    assert result.content[0].text == "result"
    assert result.isError is False
