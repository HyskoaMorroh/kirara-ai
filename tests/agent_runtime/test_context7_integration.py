from __future__ import annotations

from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    ResourceBinding,
    RuntimeStatus,
)
from kirara_ai.config.global_config import (
    GlobalConfig,
    MCPConfig,
    MCPServerConfig,
    MCPTransportConfig,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


HASH_PROMPT = "a" * 64
HASH_SKILL = "b" * 64
HASH_MEMORY = "c" * 64
HASH_MCP = "d" * 64
CREATOR = RuntimePrincipal(subject="context7-test-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


class ControlledLLMManager:
    def __init__(self, responses: dict[str, list[LLMChatResponse | BaseException]]):
        self.responses = {model: list(values) for model, values in responses.items()}
        self.requests: list[object] = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        value = self.responses[request.model].pop(0)
        if isinstance(value, BaseException):
            raise value
        return ChatExecutionResult(
            response=value,
            trace_id=f"integration-trace-{len(self.requests)}",
            attempts=[],
        )


def _tool_call(name: str, arguments: dict[str, object]) -> ToolCall:
    return ToolCall(
        id="context7-call-1",
        type="function",
        function=Function(name=name, arguments=arguments),
    )


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="webui",
        adapter_instance="integration",
        account_scope="integration-account",
        conversation_scope="c2c:integration-user",
        sender_scope="integration-user",
    )


def _message() -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat("integration-user", "Integration Test"),
        [TextMessage("Find the current pytest documentation library")],
    )


def _agent() -> AgentDefinition:
    return AgentDefinition(
        agent_id="context7-integration-agent",
        model_priority=("model-primary", "model-backup"),
        prompt_bindings=(
            ResourceBinding(
                resource_id="prompt.integration",
                resource_type="prompt",
                version="1.0.0",
                content_sha256=HASH_PROMPT,
            ),
        ),
        skill_bindings=(
            ResourceBinding(
                resource_id="skill.context7",
                resource_type="skill",
                version="1.0.0",
                content_sha256=HASH_SKILL,
            ),
        ),
        memory_bindings=(
            ResourceBinding(
                resource_id="memory.integration",
                resource_type="memory",
                version="1.0.0",
                content_sha256=HASH_MEMORY,
            ),
        ),
        mcp_bindings=(
            ResourceBinding(
                resource_id="mcp.context7",
                resource_type="mcp",
                version="1.0.0",
                content_sha256=HASH_MCP,
            ),
        ),
        mcp_allowlist={"context7.resolve-library-id"},
        max_tool_iterations=2,
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("creator_principal")
async def test_real_context7_mcp_completes_agent_turn_after_model_failover():
    config = GlobalConfig(
        mcp=MCPConfig(
            servers=[
                MCPServerConfig(
                    id="context7",
                    name="Context7",
                    server=MCPTransportConfig(
                        type="stdio",
                        command="npx",
                        args=["-y", "@upstash/context7-mcp"],
                    ),
                )
            ]
        )
    )
    container = DependencyContainer()
    container.register(GlobalConfig, config)
    manager = MCPServerManager(container)

    try:
        manager.load_servers()
        assert await manager.connect_server("context7") is True
        tools = manager.get_tools()
        assert {"resolve-library-id", "query-docs"}.issubset(tools)

        llm = ControlledLLMManager(
            {
                "model-primary": [
                    FailoverExecutionError("primary model unavailable", attempts=[])
                ],
                "model-backup": [
                    LLMChatResponse(
                        model="model-backup",
                        message=Message(
                            role="assistant",
                            content=[],
                            tool_calls=[
                                _tool_call(
                                    "resolve-library-id",
                                    {
                                        "libraryName": "pytest",
                                        "query": "pytest fixtures documentation",
                                    },
                                )
                            ],
                        ),
                    ),
                    LLMChatResponse(
                        model="model-backup",
                        message=Message(
                            role="assistant",
                            content=[
                                LLMChatTextContent(
                                    text="Context7 documentation lookup completed."
                                )
                            ],
                        ),
                    ),
                ],
            }
        )
        registry = AgentRegistry()
        registry.register(_agent())
        registry.set_default("context7-integration-agent")
        resources = {
            "prompt.integration": "Use current documentation sources.",
            "skill.context7": "Use Context7 to resolve the library before answering.",
            "memory.integration": "Keep the research request focused.",
        }
        executor = AgentRuntimeExecutor(
            agent_registry=registry,
            llm_manager=llm,
            mcp_manager=manager,
            resource_loader=resources.__getitem__,
        )

        result = await executor.run(
            _context(),
            _message(),
            session_mcp_allowlist={"context7.resolve-library-id"},
            workflow_mcp_allowlist={"context7.resolve-library-id"},
        )

        assert result.status is RuntimeStatus.COMPLETED
        assert result.text == "Context7 documentation lookup completed."
        assert [request.model for request in llm.requests] == [
            "model-primary",
            "model-backup",
            "model-backup",
        ]
        assert "resolve-library-id" in {tool.name for tool in llm.requests[1].tools}
        tool_messages = [
            message
            for message in llm.requests[2].messages
            if message.role == "tool"
        ]
        assert tool_messages
        tool_result = tool_messages[-1].content[0]
        assert tool_result.isError is False
        tool_text = str(tool_result.content)
        assert tool_text.strip()
        assert "pytest" in tool_text.lower()

        successful_calls = [
            record
            for record in manager.audit_records
            if record.get("server") == "context7"
            and record.get("operation") == "call_tool"
            and record.get("outcome") == "success"
        ]
        assert successful_calls
    finally:
        await manager.stop_server("context7")
