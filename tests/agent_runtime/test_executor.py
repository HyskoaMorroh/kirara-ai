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
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError


HASH_PROMPT = "a" * 64
HASH_SKILL = "b" * 64
HASH_MCP = "c" * 64


def make_context(channel: str = "telegram") -> ChannelContext:
    return ChannelContext(
        channel_type=channel,
        adapter_instance=f"{channel}-main",
        account_scope="account-a",
        conversation_scope="c2c:user-a",
        sender_scope="user-a",
    )


def make_message(text: str = "Find the documentation") -> IMMessage:
    sender = ChatSender.from_c2c_chat("user-a", "Researcher")
    return IMMessage(sender, [TextMessage(text)])


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=Function(name=name, arguments=arguments),
    )


class FakeLLMManager:
    def __init__(self, responses: dict[str, list[LLMChatResponse | BaseException]]):
        self.responses = {model: list(values) for model, values in responses.items()}
        self.requests = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        values = self.responses[request.model]
        value = values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return ChatExecutionResult(response=value, trace_id=f"trace-{len(self.requests)}", attempts=[])


class FakeMCPManager:
    def __init__(self, *, confirmation_tools: set[str] | None = None):
        self.confirmation_tools = confirmation_tools or set()
        self.calls: list[tuple[str, dict, dict]] = []
        self.tools = {
            "search": SimpleNamespace(
                server_id="docs-server",
                tool_info=SimpleNamespace(
                    name="search",
                    description="Search documentation",
                    inputSchema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                ),
            ),
            "write": SimpleNamespace(
                server_id="docs-server",
                tool_info=SimpleNamespace(
                    name="write",
                    description="Write a document",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
            ),
        }

    def get_tools(self):
        return self.tools

    def requires_confirmation(self, name: str) -> bool:
        return name in self.confirmation_tools

    async def call_tool(self, name, args, **options):
        self.calls.append((name, args, options))
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"result for {args.get('query', args.get('text', ''))}")],
            isError=False,
        )


def make_agent(*, allowlist: set[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        agent_id="research-agent",
        model_priority=("model-primary", "model-backup"),
        prompt_bindings=(
            ResourceBinding(
                resource_id="prompt-main",
                resource_type="prompt",
                version="1.0.0",
                content_sha256=HASH_PROMPT,
            ),
        ),
        skill_bindings=(
            ResourceBinding(
                resource_id="skill-search",
                resource_type="skill",
                version="2.0.0",
                content_sha256=HASH_SKILL,
            ),
        ),
        mcp_bindings=(
            ResourceBinding(
                resource_id="docs-server",
                resource_type="mcp",
                version="1.0.0",
                content_sha256=HASH_MCP,
            ),
        ),
        mcp_allowlist=allowlist or {"search"},
        max_tool_iterations=3,
    )


def make_executor(llm, mcp, *, agent=None):
    registry = AgentRegistry()
    registry.register(agent or make_agent())
    registry.set_default("research-agent")
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader={
            "prompt-main": "You are a careful research assistant.",
            "skill-search": "Use the documentation search tool when evidence is needed.",
        }.__getitem__,
    )


def make_versioned_executor(llm, mcp, calls, *, agent=None):
    registry = AgentRegistry()
    registry.register(agent or make_agent())
    registry.set_default("research-agent")

    def load(resource_id, version):
        calls.append((resource_id, version))
        return {
            ("prompt-main", "1.0.0"): "version-pinned prompt",
            ("skill-search", "2.0.0"): "version-pinned skill",
        }.get((resource_id, version), "")

    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader=load,
    )


@pytest.mark.asyncio
async def test_runtime_injects_resources_runs_tool_in_same_turn_and_uses_model_fallback():
    llm = FakeLLMManager(
        {
            "model-primary": [
                FailoverExecutionError("primary unavailable", attempts=[])
            ],
            "model-backup": [
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "runtime docs"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="According to the documentation, the answer is ready.")],
                    ),
                ),
            ],
        }
    )
    mcp = FakeMCPManager()

    result = await make_executor(llm, mcp).run(make_context(), make_message())

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "According to the documentation, the answer is ready."
    assert [request.model for request in llm.requests] == [
        "model-primary",
        "model-backup",
        "model-backup",
    ]
    assert "You are a careful research assistant." in llm.requests[1].messages[0].content[0].text
    assert "Use the documentation search tool" in llm.requests[1].messages[0].content[0].text
    assert "search" in {tool.name for tool in llm.requests[1].tools}
    assert any(
        message.role == "tool"
        and "result for runtime docs" in message.content[0].content
        for message in llm.requests[2].messages
    )
    assert mcp.calls[0][0] == "search"
    assert result.snapshot is not None
    assert result.snapshot.resources[0].content_sha256 == HASH_PROMPT


@pytest.mark.asyncio
async def test_runtime_intersects_agent_session_and_workflow_permissions_before_calling_mcp():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("write", {"text": "must not run"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="The requested operation was not permitted.")],
                    ),
                ),
            ]
        }
    )
    mcp = FakeMCPManager()
    executor = make_executor(llm, mcp, agent=make_agent(allowlist={"search", "write"}))

    result = await executor.run(
        make_context("wecom"),
        make_message("write it"),
        session_mcp_allowlist={"search", "write"},
        workflow_mcp_allowlist={"search"},
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert mcp.calls == []
    denied_result = llm.requests[1].messages[-1].content[0]
    assert denied_result.isError is True
    assert denied_result.name == "write"
    assert "permission" in str(denied_result.content)


@pytest.mark.asyncio
async def test_runtime_requires_confirmation_without_executing_external_tool_then_resumes():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("write", {"text": "publish"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="The document was updated.")],
                    ),
                ),
            ]
        }
    )
    mcp = FakeMCPManager(confirmation_tools={"write"})
    executor = make_executor(llm, mcp, agent=make_agent(allowlist={"write"}))

    waiting = await executor.run(make_context(), make_message("publish"))

    assert waiting.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert waiting.confirmation_id
    assert mcp.calls == []

    resumed = await executor.confirm(waiting.confirmation_id)

    assert resumed.status is RuntimeStatus.COMPLETED
    assert resumed.text == "The document was updated."
    assert [call[0] for call in mcp.calls] == ["write"]


@pytest.mark.asyncio
async def test_runtime_stops_after_maximum_tool_rounds_and_does_not_loop_forever():
    responses = [
        LLMChatResponse(
            model="model-primary",
            message=Message(
                role="assistant",
                content=[],
                tool_calls=[tool_call("search", {"query": f"q-{index}",})],
            ),
        )
        for index in range(4)
    ]
    llm = FakeLLMManager({"model-primary": responses})
    mcp = FakeMCPManager()
    agent = make_agent(allowlist={"search"})
    agent = AgentDefinition(
        **{**agent.__dict__, "model_priority": ("model-primary",), "max_tool_iterations": 2}
    )

    result = await make_executor(llm, mcp, agent=agent).run(make_context(), make_message())

    assert result.status is RuntimeStatus.COMPLETED
    assert len(mcp.calls) == 2
    assert len(llm.requests) == 3
    assert llm.requests[-1].tool_choice == "none"


@pytest.mark.asyncio
async def test_one_runtime_shape_is_reusable_across_channels():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="same agent")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final, final]})
    executor = make_executor(llm, FakeMCPManager())

    telegram = await executor.run(make_context("telegram"), make_message())
    qq = await executor.run(make_context("onebot"), make_message())

    assert telegram.text == qq.text == "same agent"
    assert telegram.snapshot.to_dict()["resources"] == qq.snapshot.to_dict()["resources"]
    assert llm.requests[0].messages[-1].content[0].text == llm.requests[1].messages[-1].content[0].text


@pytest.mark.asyncio
async def test_runtime_loads_prompt_and_skill_using_the_bound_versions():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="pinned")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    calls = []

    result = await make_versioned_executor(llm, FakeMCPManager(), calls).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert calls == [("prompt-main", "1.0.0"), ("skill-search", "2.0.0")]
    system_text = llm.requests[0].messages[0].content[0].text
    assert "version-pinned prompt" in system_text
    assert "version-pinned skill" in system_text


@pytest.mark.asyncio
async def test_runtime_accepts_legacy_single_argument_resource_loader():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="legacy")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    result = await make_executor(llm, FakeMCPManager()).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert "You are a careful research assistant." in llm.requests[0].messages[0].content[0].text
