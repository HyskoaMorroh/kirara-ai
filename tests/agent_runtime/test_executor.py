from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentHookRuntime,
    AgentRuntimeExecutor,
    ChannelContext,
    ResourceBinding,
    RuntimeStatus,
    HOOK_EVENTS,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


HASH_PROMPT = "a" * 64
HASH_SKILL = "b" * 64
HASH_MCP = "c" * 64
HASH_MEMORY = "d" * 64
HASH_HOOK = "e" * 64
CREATOR = RuntimePrincipal(subject="executor-test-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


def hook_binding(resource_id: str = "hook-main", digest: str = HASH_HOOK) -> ResourceBinding:
    return ResourceBinding(
        resource_id=resource_id,
        resource_type="hook",
        version="1.0.0",
        content_sha256=digest,
    )


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
        self.execute_options = []

    def execute_chat(self, request, **options):
        self.requests.append(request)
        self.execute_options.append(options)
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


class Context7MCPManager(FakeMCPManager):
    """Local Context7-shaped transport used to verify the runtime contract."""

    def __init__(self):
        super().__init__()
        self.tools = {
            "query-docs": SimpleNamespace(
                server_id="context7",
                tool_info=SimpleNamespace(
                    name="query-docs",
                    description="Query current library documentation",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "libraryId": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["libraryId", "query"],
                    },
                ),
            )
        }


class ConflictingMCPManager(FakeMCPManager):
    def __init__(self):
        super().__init__()
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        self.tools = {
            "search": SimpleNamespace(
                server_id="docs-server",
                original_name="search",
                tool_info=SimpleNamespace(
                    name="search",
                    description="Search internal documentation",
                    inputSchema=schema,
                ),
            ),
            "public-docs.search": SimpleNamespace(
                server_id="public-docs",
                original_name="search",
                tool_info=SimpleNamespace(
                    name="search",
                    description="Search public documentation",
                    inputSchema=schema,
                ),
            ),
        }


class QualifiedAliasCollisionMCPManager(FakeMCPManager):
    def __init__(self):
        super().__init__()
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        self.tools = {
            "query-docs": SimpleNamespace(
                server_id="context7",
                original_name="query-docs",
                tool_info=SimpleNamespace(
                    name="query-docs",
                    description="Query Context7 documentation",
                    inputSchema=schema,
                ),
            ),
            "context7.query-docs": SimpleNamespace(
                server_id="other",
                original_name="context7.query-docs",
                tool_info=SimpleNamespace(
                    name="context7.query-docs",
                    description="A different tool with a colliding display name",
                    inputSchema=schema,
                ),
            ),
        }


class CombinedMemoryManager:
    def __init__(self, entries):
        self.entries = list(entries)
        self.queries = []
        self.stores = []

    def query(self, scope, sender, extra_identifier=None):
        self.queries.append((scope, sender, extra_identifier))
        return list(self.entries)

    def store(self, scope, entry, extra_identifier=None):
        self.stores.append((scope, entry, extra_identifier))
        self.entries.append(entry)


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


def make_hook_executor(
    llm,
    mcp,
    hook_content,
    *,
    agent=None,
    handlers=None,
    audit=None,
    context_char_threshold=None,
    compactor=None,
):
    base = agent or make_agent()
    if not base.hook_bindings:
        base = AgentDefinition(**{**base.__dict__, "hook_bindings": (hook_binding(),)})
    registry = AgentRegistry()
    registry.register(base)
    registry.set_default(base.agent_id)
    runtime = AgentHookRuntime(
        resource_loader={
            "prompt-main": "prompt",
            "skill-search": "skill",
            "hook-main": hook_content,
        }.__getitem__,
        handlers=handlers,
        audit_sink=audit.append if audit is not None else None,
    )
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader={
            "prompt-main": "prompt",
            "skill-search": "skill",
            "hook-main": hook_content,
        }.__getitem__,
        audit_sink=audit.append if audit is not None else None,
        hook_runtime=runtime,
        context_char_threshold=context_char_threshold,
        compactor=compactor,
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
@pytest.mark.usefixtures("creator_principal")
async def test_one_agent_turn_composes_prompt_skill_memory_context7_failover_and_hooks():
    prompt_text = "prompt body for the research agent"
    skill_text = "skill body: use Context7 when documentation evidence is needed"
    memory_resource_text = "memory resource policy: preserve the research thread"
    hook_text = json_hook(
        {
            "SessionStart": "record",
            "UserPromptSubmit": "record",
            "PreToolUse": "record",
            "PostToolUse": "record",
            "Stop": "record",
        }
    )
    audit = []
    memory = CombinedMemoryManager(
        [
            MemoryEntry(
                sender=ChatSender.from_c2c_chat("old-user", "Researcher"),
                content="old question",
                metadata={
                    "agent_runtime": {
                        "version": 1,
                        "agent_id": "research-agent",
                        "message": LLMChatMessage(
                            role="user",
                            content=[LLMChatTextContent(text="old question")],
                        ).model_dump(mode="json"),
                    }
                },
            ),
            MemoryEntry(
                sender=ChatSender.get_bot_sender(),
                content="old answer",
                metadata={
                    "agent_runtime": {
                        "version": 1,
                        "agent_id": "research-agent",
                        "message": LLMChatMessage(
                            role="assistant",
                            content=[LLMChatTextContent(text="old answer")],
                        ).model_dump(mode="json"),
                    }
                },
            ),
        ]
    )
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
                        tool_calls=[
                            tool_call(
                                "query-docs",
                                {
                                    "libraryId": "/vercel/next.js",
                                    "query": "route handlers",
                                },
                            )
                        ],
                    ),
                ),
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="documented answer")],
                    ),
                ),
            ],
        }
    )
    agent = AgentDefinition(
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
        memory_bindings=(
            ResourceBinding(
                resource_id="memory-main",
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
        hook_bindings=(hook_binding(),),
        mcp_allowlist={"query-docs"},
        max_tool_iterations=2,
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    handlers = {"record": lambda payload: None}
    resources = {
        "prompt-main": prompt_text,
        "skill-search": skill_text,
        "memory-main": memory_resource_text,
        "hook-main": hook_text,
    }
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=Context7MCPManager(),
        resource_loader=resources.__getitem__,
        memory_manager=memory,
        audit_sink=audit.append,
        hook_runtime=AgentHookRuntime(
            resource_loader=resources.__getitem__,
            handlers=handlers,
            audit_sink=audit.append,
        ),
    )

    result = await executor.run(
        make_context("telegram"),
        make_message("find the current route handler documentation"),
        session_mcp_allowlist={"query-docs"},
        workflow_mcp_allowlist={"query-docs"},
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "documented answer"
    assert [request.model for request in llm.requests] == [
        "model-primary",
        "model-backup",
        "model-backup",
    ]
    first_system_text = llm.requests[1].messages[0].content[0].text
    assert prompt_text in first_system_text
    assert skill_text in first_system_text
    assert memory_resource_text in first_system_text
    assert [
        item.content[0].text
        for item in llm.requests[1].messages
        if item.role in {"user", "assistant"}
    ] == ["old question", "old answer", "find the current route handler documentation"]
    assert "query-docs" in {tool.name for tool in llm.requests[1].tools}
    assert len(executor.mcp_manager.calls) == 1
    tool_name, tool_args, options = executor.mcp_manager.calls[0]
    assert tool_name == "query-docs"
    assert tool_args["libraryId"] == "/vercel/next.js"
    assert options["agent_allowlist"] == frozenset({"query-docs"})
    assert options["agent_mcp_server_ids"] == frozenset({"context7"})
    assert options["session_allowlist"] == frozenset({"query-docs"})
    assert options["workflow_allowlist"] == frozenset({"query-docs"})
    assert [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ] == ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    serialized_audit = json.dumps(audit, ensure_ascii=True)
    assert "user-a" not in serialized_audit
    assert prompt_text not in serialized_audit
    assert result.snapshot is not None
    assert result.snapshot.agent_id == "research-agent"
    assert result.snapshot.model_priority == ("model-primary", "model-backup")
    assert [item.resource_id for item in result.snapshot.resources] == [
        "prompt-main",
        "skill-search",
        "memory-main",
        "mcp.context7",
        "hook-main",
    ]
    assert len(memory.queries) == 1
    assert len(memory.stores) == 4
    assert [
        call[1].metadata["agent_runtime"]["message"]["role"]
        for call in memory.stores
    ] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
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
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_resolves_stable_server_qualified_tool_policy_to_cache_name():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[
                            tool_call(
                                "query-docs",
                                {"libraryId": "/pytest-dev/pytest", "query": "fixtures"},
                            )
                        ],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="qualified policy worked")],
                    ),
                ),
            ]
        }
    )
    base = make_agent(allowlist={"context7.query-docs"})
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "mcp_bindings": (
                ResourceBinding(
                    resource_id="mcp.context7",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
            ),
        }
    )
    mcp = Context7MCPManager()

    result = await make_executor(llm, mcp, agent=agent).run(
        make_context(),
        make_message(),
        session_mcp_allowlist={"query-docs"},
        workflow_mcp_allowlist={"context7.query-docs"},
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert {tool.name for tool in llm.requests[0].tools} == {"query-docs"}
    assert [call[0] for call in mcp.calls] == ["query-docs"]
    assert mcp.calls[0][2]["agent_allowlist"] == frozenset(
        {"context7.query-docs"}
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_qualified_policy_selects_the_bound_server_during_name_conflict():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="ready")],
        ),
    )
    base = make_agent(allowlist={"public-docs.search"})
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "mcp_bindings": (
                ResourceBinding(
                    resource_id="mcp.public-docs",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
            ),
        }
    )
    llm = FakeLLMManager({"model-primary": [final]})

    result = await make_executor(llm, ConflictingMCPManager(), agent=agent).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert {tool.name for tool in llm.requests[0].tools} == {"public-docs.search"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_qualified_policy_takes_precedence_over_a_cache_name_alias():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="ready")],
        ),
    )
    base = make_agent(allowlist={"context7.query-docs"})
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "mcp_bindings": (
                ResourceBinding(
                    resource_id="mcp.context7",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
                ResourceBinding(
                    resource_id="mcp.other",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
            ),
        }
    )
    llm = FakeLLMManager({"model-primary": [final]})

    result = await make_executor(
        llm,
        QualifiedAliasCollisionMCPManager(),
        agent=agent,
    ).run(make_context(), make_message())

    assert result.status is RuntimeStatus.COMPLETED
    assert {tool.name for tool in llm.requests[0].tools} == {"query-docs"}


@pytest.mark.asyncio
async def test_runtime_rejects_ambiguous_legacy_name_across_bound_servers():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="ready")],
        ),
    )
    base = make_agent(allowlist={"search"})
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "mcp_bindings": (
                ResourceBinding(
                    resource_id="mcp.docs-server",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
                ResourceBinding(
                    resource_id="mcp.public-docs",
                    resource_type="mcp",
                    version="1.0.0",
                    content_sha256=HASH_MCP,
                ),
            ),
        }
    )
    llm = FakeLLMManager({"model-primary": [final]})

    result = await make_executor(llm, ConflictingMCPManager(), agent=agent).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert llm.requests[0].tools == []


@pytest.mark.asyncio
async def test_runtime_passes_agent_provider_allowlist_to_llm_manager():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="provider constrained")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    base = make_agent()
    agent = AgentDefinition(
        **{**base.__dict__, "provider_allowlist": frozenset({"openai"})}
    )

    result = await make_executor(llm, FakeMCPManager(), agent=agent).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert llm.execute_options[0]["provider_allowlist"] == frozenset({"openai"})


@pytest.mark.asyncio
async def test_runtime_does_not_switch_models_after_non_retryable_failure():
    primary_error = RuntimeError("invalid request: unsupported model parameter")
    llm = FakeLLMManager(
        {
            "model-primary": [primary_error],
            "model-backup": [
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="must not run")],
                    ),
                )
            ],
        }
    )

    result = await make_executor(llm, FakeMCPManager()).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.FAILED
    assert [request.model for request in llm.requests] == ["model-primary"]
    assert result.error is not None
    assert result.error["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_runtime_switches_models_after_transient_failure():
    llm = FakeLLMManager(
        {
            "model-primary": [TimeoutError("upstream timed out")],
            "model-backup": [
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="backup used")],
                    ),
                )
            ],
        }
    )

    result = await make_executor(llm, FakeMCPManager()).run(
        make_context(), make_message()
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "backup used"
    assert [request.model for request in llm.requests] == [
        "model-primary",
        "model-backup",
    ]


@pytest.mark.asyncio
async def test_runtime_injects_memory_as_context_but_never_hook_content_into_model_messages():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="context loaded")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    base = make_agent()
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "memory_bindings": (
                ResourceBinding(
                    resource_id="memory-main",
                    resource_type="memory",
                    version="1.0.0",
                    content_sha256=HASH_MEMORY,
                ),
            ),
            "hook_bindings": (
                ResourceBinding(
                    resource_id="hook-main",
                    resource_type="hook",
                    version="1.0.0",
                    content_sha256=HASH_HOOK,
                ),
            ),
        }
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=FakeMCPManager(),
        resource_loader={
            "prompt-main": "prompt",
            "skill-search": "skill",
            "memory-main": "memory context",
            "hook-main": "hook implementation must stay outside model messages",
        }.__getitem__,
    )

    result = await executor.run(make_context(), make_message())

    assert result.status is RuntimeStatus.COMPLETED
    system_text = llm.requests[0].messages[0].content[0].text
    assert "[memory:memory-main]" in system_text
    assert "memory context" in system_text
    assert "hook implementation" not in system_text


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
@pytest.mark.usefixtures("creator_principal")
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

    resumed = await executor.confirm(waiting.confirmation_id, make_context())

    assert resumed.status is RuntimeStatus.COMPLETED
    assert resumed.text == "The document was updated."
    assert [call[0] for call in mcp.calls] == ["write"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_confirmation_expires_when_tool_signature_changes_while_waiting():
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
                )
            ]
        }
    )
    mcp = FakeMCPManager(confirmation_tools={"write"})
    executor = make_executor(llm, mcp, agent=make_agent(allowlist={"write"}))

    waiting = await executor.run(make_context(), make_message("publish"))
    mcp.tools["write"] = SimpleNamespace(
        server_id="docs-server",
        tool_info=SimpleNamespace(
            name="write",
            description="Write a document using a changed contract",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "format": {"type": "string"},
                },
                "required": ["text", "format"],
            },
        ),
    )

    resumed = await executor.confirm(waiting.confirmation_id, make_context())

    assert resumed.status is RuntimeStatus.FAILED
    assert resumed.error is not None
    assert resumed.error["type"] == "ConfirmationExpired"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_hook_runtime_exposes_the_complete_agent_lifecycle_event_set():
    assert HOOK_EVENTS == frozenset(
        {
            "PreToolUse",
            "PermissionRequest",
            "PostToolUse",
            "PreCompact",
            "PostCompact",
            "SessionStart",
            "SessionEnd",
            "UserPromptSubmit",
            "SubagentStart",
            "SubagentStop",
            "Stop",
        }
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_dispatches_permission_request_before_waiting_and_stop_after_waiting():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[tool_call("write", {"text": "publish"})],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(confirmation_tools={"write"}),
        json_hook(
            {
                "PreToolUse": "pre_tool",
                "PermissionRequest": "permission",
                "Stop": "stop",
            }
        ),
        agent=make_agent(allowlist={"write"}),
        handlers={
            "pre_tool": lambda payload: None,
            "permission": lambda payload: None,
            "stop": lambda payload: None,
        },
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("publish"))

    assert result.status is RuntimeStatus.AWAITING_CONFIRMATION
    events = [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ]
    assert events == ["PreToolUse", "PermissionRequest", "Stop"]


@pytest.mark.asyncio
async def test_session_and_prompt_hook_context_are_visible_to_the_next_model_request():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="context consumed")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json.dumps(
            {
                "events": {
                    "SessionStart": {
                        "handler": "session",
                    },
                    "UserPromptSubmit": {
                        "handler": "prompt",
                    },
                }
            }
        ),
        handlers={
            "session": lambda _payload: {
                "systemMessage": "session policy",
                "hookSpecificOutput": {
                    "additionalContext": "session context",
                },
            },
            "prompt": lambda _payload: {
                "systemMessage": "prompt policy",
                "hookSpecificOutput": {
                    "additionalContext": "prompt context",
                },
            },
        },
    )

    result = await executor.run(make_context(), make_message("use the context"))

    assert result.status is RuntimeStatus.COMPLETED
    system_text = "\n".join(
        executor._message_text(message)
        for message in llm.requests[0].messages
        if message.role == "system"
    )
    assert "session context" in system_text
    assert "prompt context" in system_text
    assert "session policy" in system_text
    assert "prompt policy" in system_text


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_pretool_updated_input_changes_only_arguments_before_mcp_execution():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "original"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="updated")],
                    ),
                ),
            ]
        }
    )
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"PreToolUse": "rewrite"}),
        handlers={
            "rewrite": lambda _payload: {
                "hookSpecificOutput": {
                    "permissionDecision": "allow",
                    "updatedInput": {"query": "rewritten"},
                }
            }
        },
    )

    result = await executor.run(make_context(), make_message("rewrite"))

    assert result.status is RuntimeStatus.COMPLETED
    assert executor.mcp_manager.calls[0][0] == "search"
    assert executor.mcp_manager.calls[0][1] == {"query": "rewritten"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_pretool_updated_input_cannot_expand_tool_schema_or_server_scope():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "original"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="identity preserved")],
                    ),
                ),
            ]
        }
    )
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"PreToolUse": "rewrite"}),
        handlers={
            "rewrite": lambda _payload: {
                "hookSpecificOutput": {
                    "permissionDecision": "allow",
                    "updatedInput": {
                        "name": "write",
                        "server_id": "unbound-server",
                        "query": "still allowed input",
                    },
                }
            }
        },
    )

    result = await executor.run(make_context(), make_message("preserve identity"))

    assert result.status is RuntimeStatus.COMPLETED
    assert executor.mcp_manager.calls == []
    tool_message = next(
        message
        for message in llm.requests[1].messages
        if message.role == "tool"
    )
    assert tool_message.content[0].name == "search"
    assert tool_message.content[0].isError is True
    assert "invalid arguments" in str(tool_message.content[0].content)


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_permission_request_hook_deny_does_not_create_usable_confirmation():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[tool_call("write", {"text": "publish"})],
        ),
    )
    llm = FakeLLMManager(
        {
            "model-primary": [
                final,
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="approval denied")],
                    ),
                ),
            ]
        }
    )
    executor = make_hook_executor(
        llm,
        FakeMCPManager(confirmation_tools={"write"}),
        json.dumps(
            {
                "events": {
                    "PermissionRequest": {"handler": "deny"},
                }
            }
        ),
        agent=make_agent(allowlist={"write"}),
        handlers={
            "deny": lambda _payload: {
                "hookSpecificOutput": {
                    "decision": {"behavior": "deny", "message": "policy"},
                }
            }
        },
    )

    result = await executor.run(make_context(), make_message("publish"))

    assert result.status is RuntimeStatus.COMPLETED
    assert result.confirmation_id is None
    assert not executor._pending
    assert executor.mcp_manager.calls == []
    assert any(
        message.role == "tool" and "policy" in str(message.content)
        for message in llm.requests[-1].messages
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_posttool_block_returns_model_feedback_without_replacing_tool_output():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "docs"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="feedback handled")],
                    ),
                ),
            ]
        }
    )
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json.dumps(
            {
                "events": {
                    "PostToolUse": {"handler": "block"},
                }
            }
        ),
        handlers={
            "block": lambda _payload: {
                "decision": "block",
                "reason": "result requires review",
            }
        },
    )

    result = await executor.run(make_context(), make_message("review"))

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "feedback handled"
    tool_message = next(
        message
        for message in llm.requests[1].messages
        if message.role == "tool"
    )
    assert tool_message.content[0].isError is True
    assert "result requires review" in str(tool_message.content[0].content)
    assert "result for docs" not in str(tool_message.content[0].content)


@pytest.mark.asyncio
async def test_run_hook_preserves_all_hook_outcome_fields():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="done")],
        ),
    )
    executor = make_hook_executor(
        FakeLLMManager({"model-primary": [final]}),
        FakeMCPManager(),
        json_hook({"SessionStart": "full"}),
        handlers={
            "full": lambda _payload: {
                "continue": False,
                "stopReason": "stop here",
                "suppressOutput": True,
                "systemMessage": "system",
                "hookSpecificOutput": {
                    "additionalContext": "context",
                },
            }
        },
    )

    outcome = await executor._run_hook(
        "SessionStart",
        agent=executor.agent_registry.get("research-agent"),
        context=make_context(),
        snapshot=executor.agent_registry.get("research-agent").snapshot(),
        payload={},
    )

    assert outcome.continue_execution is False
    assert outcome.stop_reason == "stop here"
    assert outcome.suppress_output is True
    assert outcome.additional_context == ("context",)
    assert outcome.system_messages == ("system",)


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_dispatches_stop_after_a_normal_tool_turn():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "docs"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="done")],
                    ),
                ),
            ]
        }
    )
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook(
            {
                "PreToolUse": "pre_tool",
                "PostToolUse": "post_tool",
                "Stop": "stop",
            }
        ),
        handlers={
            "pre_tool": lambda payload: None,
            "post_tool": lambda payload: None,
            "stop": lambda payload: None,
        },
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("search"))

    assert result.status is RuntimeStatus.COMPLETED
    events = [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ]
    assert events == ["PreToolUse", "PostToolUse", "Stop"]


@pytest.mark.asyncio
async def test_hook_snapshot_survives_mid_turn_disable_but_next_turn_skips_hook():
    hook_content = json_hook(
        {
            "SessionStart": "record",
            "UserPromptSubmit": "record",
            "Stop": "record",
        }
    )
    hook_events = []

    class MutableHookService:
        def __init__(self):
            self.globally_enabled = True

        def resolve_binding(
            self,
            resource_id,
            resource_type,
            *,
            version=None,
            enabled=True,
            version_policy="fixed",
        ):
            if enabled and not self.globally_enabled:
                raise RuntimeError("hook is globally disabled")
            return ResourceBinding(
                resource_id=resource_id,
                resource_type=resource_type,
                version=version or "1.0.0",
                content_sha256=HASH_HOOK,
                enabled=enabled,
                version_policy=version_policy,
            )

        def read_entry(self, resource_id, version=None):
            assert resource_id == "hook-main"
            assert version == "1.0.0"
            return hook_content

    service = MutableHookService()
    agent = AgentDefinition(
        agent_id="research-agent",
        model_priority=("model-primary",),
        hook_bindings=(hook_binding(),),
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="done")],
        ),
    )

    class DisableHookDuringModel(FakeLLMManager):
        def execute_chat(self, request, **options):
            response = super().execute_chat(request, **options)
            if len(self.requests) == 1:
                service.globally_enabled = False
                registry.update(
                    AgentDefinition(
                        **{
                            **agent.__dict__,
                            "hook_bindings": (
                                ResourceBinding(
                                    **{
                                        **hook_binding().__dict__,
                                        "enabled": False,
                                    }
                                ),
                            ),
                        }
                    )
                )
            return response

    llm = DisableHookDuringModel({"model-primary": [final, final]})
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=FakeMCPManager(),
        resource_service=service,
        hook_runtime=AgentHookRuntime(
            resource_service=service,
            handlers={"record": lambda payload: hook_events.append(payload)},
        ),
    )

    first = await executor.run(make_context(), make_message("first"))
    first_event_count = len(hook_events)
    second = await executor.run(make_context(), make_message("second"))

    assert first.status is RuntimeStatus.COMPLETED
    assert second.status is RuntimeStatus.COMPLETED
    assert first_event_count == 3
    assert len(hook_events) == first_event_count


@pytest.mark.asyncio
async def test_runtime_dispatches_postcompact_after_compaction_and_stop_after_completion():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="compacted")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook(
            {
                "PreCompact": "precompact",
                "PostCompact": "postcompact",
                "Stop": "stop",
            }
        ),
        handlers={
            "precompact": lambda payload: None,
            "postcompact": lambda payload: None,
            "stop": lambda payload: None,
        },
        audit=audit,
        context_char_threshold=20,
    )

    result = await executor.run(make_context(), make_message("a very long current request"))

    assert result.status is RuntimeStatus.COMPLETED
    events = [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ]
    assert events == ["PreCompact", "PostCompact", "Stop"]


@pytest.mark.asyncio
async def test_postcompact_handler_failure_does_not_change_completed_result():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="compacted")],
        ),
    )
    audit = []

    def fail_postcompact(_payload):
        raise RuntimeError("post compact audit unavailable")

    executor = make_hook_executor(
        FakeLLMManager({"model-primary": [final]}),
        FakeMCPManager(),
        json_hook({"PostCompact": "postcompact", "Stop": "stop"}),
        handlers={
            "postcompact": fail_postcompact,
            "stop": lambda payload: None,
        },
        audit=audit,
        context_char_threshold=20,
    )

    result = await executor.run(make_context(), make_message("a very long current request"))

    assert result.status is RuntimeStatus.COMPLETED
    assert any(
        item.get("event") == "PostCompact" and item.get("outcome") == "error"
        for item in audit
    )
    assert any(
        item.get("event") == "Stop" and item.get("outcome") == "success"
        for item in audit
    )


@pytest.mark.asyncio
async def test_stop_handler_failure_does_not_change_original_result():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="done")],
        ),
    )
    audit = []

    def fail_stop(_payload):
        raise RuntimeError("stop audit unavailable")

    executor = make_hook_executor(
        FakeLLMManager({"model-primary": [final]}),
        FakeMCPManager(),
        json_hook({"Stop": "stop"}),
        handlers={"stop": fail_stop},
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("complete"))

    assert result.status is RuntimeStatus.COMPLETED
    assert any(
        item.get("event") == "Stop" and item.get("outcome") == "error"
        for item in audit
    )


@pytest.mark.asyncio
async def test_runtime_dispatches_stop_when_model_execution_fails():
    llm = FakeLLMManager({"model-primary": [RuntimeError("provider unavailable")]})
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"Stop": "stop"}),
        handlers={"stop": lambda payload: None},
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("fail"))

    assert result.status is RuntimeStatus.FAILED
    assert [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ] == ["Stop"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_non_pretool_hook_denial_is_observational_only():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "docs"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="done")],
                    ),
                )
            ]
        }
    )
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"PostToolUse": "deny_post"}),
        handlers={"deny_post": lambda payload: False},
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("complete"))

    assert result.status is RuntimeStatus.COMPLETED
    assert [call[0] for call in executor.mcp_manager.calls] == ["search"]
    assert any(
        item.get("event") == "PostToolUse" and item.get("outcome") == "success"
        for item in audit
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_confirm_dispatches_only_resume_tool_events_and_stop():
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
                        content=[LLMChatTextContent(text="published")],
                    ),
                ),
            ]
        }
    )
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(confirmation_tools={"write"}),
        json_hook(
            {
                "PreToolUse": "pre_tool",
                "PermissionRequest": "permission",
                "PostToolUse": "post_tool",
                "Stop": "stop",
            }
        ),
        agent=make_agent(allowlist={"write"}),
        handlers={
            "pre_tool": lambda payload: None,
            "permission": lambda payload: None,
            "post_tool": lambda payload: None,
            "stop": lambda payload: None,
        },
        audit=audit,
    )

    waiting = await executor.run(make_context(), make_message("publish"))
    first_event_count = len(audit)
    resumed = await executor.confirm(waiting.confirmation_id, make_context())

    assert resumed.status is RuntimeStatus.COMPLETED
    resume_events = [
        item["event"]
        for item in audit[first_event_count:]
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ]
    assert resume_events == ["PreToolUse", "PostToolUse", "Stop"]


@pytest.mark.asyncio
async def test_runtime_does_not_emit_session_end_or_subagent_events_without_lifecycle_actions():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="done")],
        ),
    )
    audit = []
    executor = make_hook_executor(
        FakeLLMManager({"model-primary": [final]}),
        FakeMCPManager(),
        json_hook(
            {
                "SessionStart": "record",
                "SessionEnd": "record",
                "SubagentStart": "record",
                "SubagentStop": "record",
                "UserPromptSubmit": "record",
                "Stop": "record",
            }
        ),
        handlers={"record": lambda payload: None},
        audit=audit,
    )

    result = await executor.run(make_context(), make_message("complete"))

    assert result.status is RuntimeStatus.COMPLETED
    events = [
        item["event"]
        for item in audit
        if item.get("component") == "agent_hook" and item.get("outcome") == "success"
    ]
    assert events == ["SessionStart", "UserPromptSubmit", "Stop"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
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
@pytest.mark.usefixtures("creator_principal")
async def test_mcp_tool_collection_is_frozen_for_a_turn_and_refreshes_next_turn():
    mcp = FakeMCPManager()
    mcp.tools = {"search": mcp.tools["search"]}

    class ReplaceToolsDuringModel(FakeLLMManager):
        def execute_chat(self, request, **options):
            response = super().execute_chat(request, **options)
            if len(self.requests) == 1:
                mcp.tools = {
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
                    )
                }
            return response

    llm = ReplaceToolsDuringModel(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "snapshot"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="first done")],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="second done")],
                    ),
                ),
            ]
        }
    )
    agent = AgentDefinition(
        **{
            **make_agent(allowlist={"search", "write"}).__dict__,
            "model_priority": ("model-primary",),
        }
    )
    executor = make_executor(llm, mcp, agent=agent)

    first = await executor.run(make_context(), make_message("first"))
    second = await executor.run(make_context(), make_message("second"))

    assert first.status is RuntimeStatus.COMPLETED
    assert second.status is RuntimeStatus.COMPLETED
    assert {tool.name for tool in llm.requests[0].tools} == {"search"}
    assert {tool.name for tool in llm.requests[1].tools} == {"search"}
    assert {tool.name for tool in llm.requests[2].tools} == {"write"}


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
async def test_current_binding_refreshes_between_turns_but_keeps_each_turn_snapshot_fixed():
    class MutableResourceService:
        def __init__(self):
            self.current_version = "1.0.0"

        def resolve_binding(
            self,
            resource_id,
            resource_type,
            *,
            version=None,
            enabled=True,
            version_policy="fixed",
        ):
            selected = version or self.current_version
            digests = {"1.0.0": "1" * 64, "2.0.0": "2" * 64}
            return ResourceBinding(
                resource_id=resource_id,
                resource_type=resource_type,
                version=selected,
                content_sha256=digests.get(selected, "3" * 64),
                enabled=enabled,
                version_policy=version_policy,
            )

    service = MutableResourceService()
    agent = make_agent()
    agent = AgentDefinition(
        **{
            **agent.__dict__,
            "prompt_bindings": (
                ResourceBinding(
                    resource_id="prompt-main",
                    resource_type="prompt",
                    version="1.0.0",
                    content_sha256="1" * 64,
                    version_policy="current",
                ),
            ),
        }
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    loaded = []

    def load(resource_id, version):
        loaded.append((resource_id, version))
        if resource_id == "prompt-main" and version == "1.0.0":
            service.current_version = "2.0.0"
        return f"{resource_id}@{version}"

    final = LLMChatResponse(
        model="model-primary",
        message=Message(role="assistant", content=[LLMChatTextContent(text="ok")]),
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=FakeLLMManager({"model-primary": [final, final]}),
        mcp_manager=FakeMCPManager(),
        resource_loader=load,
        resource_service=service,
    )

    first = await executor.run(make_context(), make_message("first"))
    second = await executor.run(make_context(), make_message("second"))

    assert first.status is RuntimeStatus.COMPLETED
    assert second.status is RuntimeStatus.COMPLETED
    assert first.snapshot.resources[0].version == "1.0.0"
    assert first.snapshot.resources[0].version_policy == "current"
    assert second.snapshot.resources[0].version == "2.0.0"
    assert second.snapshot.resources[0].version_policy == "current"
    assert loaded[0] == ("prompt-main", "1.0.0")
    assert ("prompt-main", "2.0.0") in loaded


@pytest.mark.asyncio
async def test_prompt_skill_and_memory_content_stay_fixed_during_turn_then_refresh():
    resource_ids = ("prompt-main", "skill-search", "memory-main")

    class MutableResourceService:
        def __init__(self):
            self.versions = {resource_id: "1.0.0" for resource_id in resource_ids}

        def resolve_binding(
            self,
            resource_id,
            resource_type,
            *,
            version=None,
            enabled=True,
            version_policy="fixed",
        ):
            selected = version or self.versions.get(resource_id, "1.0.0")
            return ResourceBinding(
                resource_id=resource_id,
                resource_type=resource_type,
                version=selected,
                content_sha256=("1" if selected == "1.0.0" else "2") * 64,
                enabled=enabled,
                version_policy=version_policy,
            )

    service = MutableResourceService()
    base = make_agent()
    current = lambda binding: ResourceBinding(
        **{**binding.__dict__, "version_policy": "current"}
    )
    agent = AgentDefinition(
        **{
            **base.__dict__,
            "model_priority": ("model-primary",),
            "prompt_bindings": (current(base.prompt_bindings[0]),),
            "skill_bindings": (current(base.skill_bindings[0]),),
            "memory_bindings": (
                ResourceBinding(
                    resource_id="memory-main",
                    resource_type="memory",
                    version="1.0.0",
                    content_sha256="1" * 64,
                    version_policy="current",
                ),
            ),
        }
    )

    def load(resource_id, version):
        return f"{resource_id} content from {version}"

    class UpdateResourcesDuringModel(FakeLLMManager):
        def execute_chat(self, request, **options):
            response = super().execute_chat(request, **options)
            if len(self.requests) == 1:
                service.versions = {
                    resource_id: "2.0.0" for resource_id in resource_ids
                }
            return response

    llm = UpdateResourcesDuringModel(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "versions"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="first done")],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="second done")],
                    ),
                ),
            ]
        }
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=FakeMCPManager(),
        resource_loader=load,
        resource_service=service,
    )

    first = await executor.run(make_context(), make_message("first"))
    second = await executor.run(make_context(), make_message("second"))

    assert first.status is RuntimeStatus.COMPLETED
    assert second.status is RuntimeStatus.COMPLETED
    for request in llm.requests[:2]:
        system_text = request.messages[0].content[0].text
        assert "content from 1.0.0" in system_text
        assert "content from 2.0.0" not in system_text
    next_system_text = llm.requests[2].messages[0].content[0].text
    assert next_system_text.count("content from 2.0.0") == 3
    assert {item.version for item in first.snapshot.resources[:3]} == {"1.0.0"}
    assert {item.version for item in second.snapshot.resources[:3]} == {"2.0.0"}


@pytest.mark.asyncio
async def test_fixed_binding_remains_on_selected_version_after_resource_update():
    class UpdatedResourceService:
        def resolve_binding(
            self,
            resource_id,
            resource_type,
            *,
            version=None,
            enabled=True,
            version_policy="fixed",
        ):
            selected = version or "2.0.0"
            return ResourceBinding(
                resource_id=resource_id,
                resource_type=resource_type,
                version=selected,
                content_sha256=("1" if selected == "1.0.0" else "2") * 64,
                enabled=enabled,
                version_policy=version_policy,
            )

    agent = make_agent()
    agent = AgentDefinition(
        **{
            **agent.__dict__,
            "prompt_bindings": (
                ResourceBinding(
                    resource_id="prompt-main",
                    resource_type="prompt",
                    version="1.0.0",
                    content_sha256="1" * 64,
                    version_policy="fixed",
                ),
            ),
        }
    )
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    loaded = []

    def load(resource_id, version):
        loaded.append((resource_id, version))
        return f"{resource_id}@{version}"

    final = LLMChatResponse(
        model="model-primary",
        message=Message(role="assistant", content=[LLMChatTextContent(text="ok")]),
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=FakeLLMManager({"model-primary": [final, final]}),
        mcp_manager=FakeMCPManager(),
        resource_loader=load,
        resource_service=UpdatedResourceService(),
    )

    first = await executor.run(make_context(), make_message("first"))
    second = await executor.run(make_context(), make_message("second"))

    assert first.snapshot.resources[0].version == "1.0.0"
    assert second.snapshot.resources[0].version == "1.0.0"
    assert all(version != "2.0.0" for resource_id, version in loaded if resource_id == "prompt-main")


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


@pytest.mark.asyncio
async def test_runtime_does_not_run_precompact_when_context_is_below_threshold():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="no compaction")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    audit = []
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"PreCompact": "precompact"}),
        handlers={"precompact": lambda payload: {"note": "must stay in audit"}},
        audit=audit,
        context_char_threshold=10_000,
    )

    result = await executor.run(make_context(), make_message("short"))

    assert result.status is RuntimeStatus.COMPLETED
    assert not any(item.get("event") == "PreCompact" for item in audit)


@pytest.mark.asyncio
async def test_runtime_runs_precompact_without_leaking_hook_output_and_audits_counts():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="compacted")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    audit = []
    hook_output = "hook output must never become model context"
    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({"PreCompact": "precompact"}),
        handlers={"precompact": lambda payload: {"output": hook_output}},
        audit=audit,
        context_char_threshold=20,
    )

    result = await executor.run(make_context(), make_message("a very long current request"))

    assert result.status is RuntimeStatus.COMPLETED
    assert len(llm.requests) == 1
    model_text = "\n".join(
        executor._message_text(message)
        for message in llm.requests[0].messages
    )
    assert hook_output not in model_text
    compact_audits = [
        item for item in audit
        if item.get("operation") == "compact"
    ]
    assert len(compact_audits) == 1
    assert compact_audits[0]["event"] == "PreCompact"
    assert compact_audits[0]["message_count_after"] <= compact_audits[0]["message_count_before"]
    assert compact_audits[0]["estimated_chars_after"] >= 0


@pytest.mark.asyncio
async def test_runtime_compactor_failure_falls_back_without_failing_agent_turn():
    final = LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="fallback compacted")],
        ),
    )
    llm = FakeLLMManager({"model-primary": [final]})
    audit = []

    def broken_compactor(messages):
        raise RuntimeError("compactor failure")

    executor = make_hook_executor(
        llm,
        FakeMCPManager(),
        json_hook({}),
        audit=audit,
        context_char_threshold=1,
        compactor=broken_compactor,
    )

    result = await executor.run(make_context(), make_message("current"))

    assert result.status is RuntimeStatus.COMPLETED
    compact_audit = next(item for item in audit if item.get("operation") == "compact")
    assert compact_audit["status"] == "fallback"
    assert compact_audit["compactor_error_type"] == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_runtime_compaction_preserves_current_tool_chain():
    llm = FakeLLMManager(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[tool_call("search", {"query": "compact"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="tool chain preserved")],
                    ),
                ),
            ]
        }
    )
    executor = AgentRuntimeExecutor(
        agent_registry=(registry := AgentRegistry()),
        llm_manager=llm,
        mcp_manager=FakeMCPManager(),
        resource_loader={"prompt-main": "p", "skill-search": "s"}.__getitem__,
        context_char_threshold=100,
    )
    registry.register(make_agent())
    registry.set_default("research-agent")

    result = await executor.run(make_context(), make_message("tool chain"))

    assert result.status is RuntimeStatus.COMPLETED
    second_request = llm.requests[1]
    roles = [message.role for message in second_request.messages]
    assert roles[-3:] == ["user", "assistant", "tool"]
    assert second_request.messages[-2].content[0].name == "search"
    assert second_request.messages[-1].content[0].name == "search"


def json_hook(events: dict[str, str]) -> str:
    return json.dumps(
        {
            "events": {
                event: {"handler": handler}
                for event, handler in events.items()
            }
        }
    )
