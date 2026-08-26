from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentHookRuntime,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    ResourceBinding,
    RuntimeStatus,
    SessionStore,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


HASH_PROMPT = "a" * 64
HASH_MCP = "b" * 64
HASH_HOOK = "c" * 64


class CorrelatedLLM:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.requests = []
        self.options = []

    def execute_chat(self, request, **options):
        self.requests.append(request)
        self.options.append(options)
        value = self.responses[request.model].pop(0)
        if isinstance(value, BaseException):
            raise value
        return ChatExecutionResult(
            response=value,
            trace_id=f"trace-{len(self.requests)}",
            attempts=[],
        )


class CorrelatedMCP:
    def __init__(self, audit):
        self.audit = audit
        self.calls = []
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
                        "properties": {"token": {"type": "string"}},
                        "required": ["token"],
                    },
                ),
            ),
        }

    def get_tools(self):
        return self.tools

    def requires_confirmation(self, name):
        return name == "write"

    async def call_tool(self, name, arguments, **options):
        self.calls.append((name, arguments, options))
        self.audit.append(
            {
                "component": "mcp",
                "operation": "call_tool",
                "outcome": "success",
                "correlation_id": options.get("correlation_id"),
            }
        )
        return SimpleNamespace(
            content=[SimpleNamespace(text="documentation result")],
            isError=False,
        )


class PersistingAudit(list):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def append(self, record):
        super().append(record)
        self.sink(record)


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="account-a",
        conversation_scope="c2c:user-a",
        sender_scope="user-a",
    )


def _message(text: str) -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat("user-a", "Researcher"),
        [TextMessage(text)],
    )


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=Function(name=name, arguments=arguments),
    )


def _agent(*, write: bool = False) -> AgentDefinition:
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
        mcp_bindings=(
            ResourceBinding(
                resource_id="docs-server",
                resource_type="mcp",
                version="1.0.0",
                content_sha256=HASH_MCP,
            ),
        ),
        hook_bindings=(
            ResourceBinding(
                resource_id="hook-main",
                resource_type="hook",
                version="1.0.0",
                content_sha256=HASH_HOOK,
            ),
        ),
        mcp_allowlist={"write" if write else "search"},
        max_tool_iterations=2,
    )


def _executor(agent, llm, mcp, audit, *, store=None):
    registry = AgentRegistry()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    resources = {
        "prompt-main": "A research prompt containing no audit data.",
        "hook-main": json.dumps(
            {
                "events": {
                    "SessionStart": {"handler": "record"},
                    "UserPromptSubmit": {"handler": "record"},
                    "PreToolUse": {"handler": "record"},
                    "PermissionRequest": {"handler": "record"},
                    "PostToolUse": {"handler": "record"},
                    "Stop": {"handler": "record"},
                }
            }
        ),
    }
    hook = AgentHookRuntime(
        resource_loader=resources.__getitem__,
        handlers={"record": lambda _payload: None},
        audit_sink=audit.append,
    )
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader=resources.__getitem__,
        hook_runtime=hook,
        audit_sink=audit.append,
        session_store=store,
    )


@pytest.mark.asyncio
async def test_one_agent_turn_uses_one_correlation_id_across_failover_hooks_mcp_and_audit():
    audit = []
    llm = CorrelatedLLM(
        {
            "model-primary": [
                FailoverExecutionError("primary unavailable", attempts=[]),
            ],
            "model-backup": [
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[
                            _tool_call(
                                "search",
                                {"query": "secret prompt should stay out of audit"},
                            )
                        ],
                    ),
                ),
                LLMChatResponse(
                    model="model-backup",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="done")],
                    ),
                ),
            ],
        }
    )
    mcp = CorrelatedMCP(audit)
    result = await _executor(_agent(), llm, mcp, audit).run(
        _context(),
        _message("secret prompt should stay out of audit"),
        session_mcp_allowlist={"search"},
        workflow_mcp_allowlist={"search"},
    )

    assert result.status is RuntimeStatus.COMPLETED
    assert result.correlation_id
    assert len({item.get("correlation_id") for item in audit if "correlation_id" in item}) == 1
    assert all(
        item.get("correlation_id") == result.correlation_id
        for item in audit
        if "correlation_id" in item
    )
    assert all(
        options.get("correlation_id") == result.correlation_id
        for options in llm.options
    )
    assert mcp.calls[0][2]["correlation_id"] == result.correlation_id
    serialized_audit = json.dumps(audit, ensure_ascii=True)
    assert "secret prompt should stay out of audit" not in serialized_audit
    assert "user-a" not in serialized_audit


@pytest.mark.asyncio
async def test_one_agent_turn_persists_agent_hook_and_mcp_under_one_correlation_id(tmp_path):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    audit = PersistingAudit(lifecycle.append_runtime_audit)
    llm = CorrelatedLLM(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[_tool_call("search", {"query": "documentation"})],
                    ),
                ),
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="done")],
                    ),
                ),
            ],
            "model-backup": [],
        }
    )

    result = await _executor(
        _agent(), llm, CorrelatedMCP(audit), audit
    ).run(
        _context(),
        _message("find documentation"),
        session_mcp_allowlist={"search"},
        workflow_mcp_allowlist={"search"},
    )

    page = lifecycle.list_audit(correlation_id=result.correlation_id, limit=200)
    assert {record["component"] for record in page["items"]} >= {
        "agent_runtime",
        "agent_hook",
        "mcp",
    }
    assert {record["correlation_id"] for record in page["items"]} == {
        result.correlation_id
    }


@pytest.mark.asyncio
async def test_confirmation_resume_keeps_the_original_correlation_id_after_executor_restart(
    tmp_path,
):
    audit = []
    store = SessionStore(tmp_path)
    first_llm = CorrelatedLLM(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[],
                        tool_calls=[
                            _tool_call("write", {"token": "secret-tool-value"})
                        ],
                    ),
                )
            ],
            "model-backup": [],
        }
    )
    mcp = CorrelatedMCP(audit)
    waiting = await _executor(
        _agent(write=True), first_llm, mcp, audit, store=store
    ).run(_context(), _message("request requiring confirmation"))

    assert waiting.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert waiting.correlation_id
    persisted = store.get_confirmation(waiting.confirmation_id)
    assert persisted["correlation_id"] == waiting.correlation_id

    resumed_llm = CorrelatedLLM(
        {
            "model-primary": [
                LLMChatResponse(
                    model="model-primary",
                    message=Message(
                        role="assistant",
                        content=[LLMChatTextContent(text="published")],
                    ),
                )
            ],
            "model-backup": [],
        }
    )
    resumed = await _executor(
        _agent(write=True), resumed_llm, mcp, audit, store=SessionStore(tmp_path)
    ).confirm(waiting.confirmation_id, _context())

    assert resumed.status is RuntimeStatus.COMPLETED
    assert resumed.correlation_id == waiting.correlation_id
    assert resumed_llm.options[0]["correlation_id"] == waiting.correlation_id
    assert mcp.calls[-1][2]["correlation_id"] == waiting.correlation_id
    assert {
        item["correlation_id"]
        for item in audit
        if item.get("correlation_id") is not None
    } == {waiting.correlation_id}
    serialized = json.dumps(audit, ensure_ascii=True)
    assert "secret-tool-value" not in serialized
    assert "request requiring confirmation" not in serialized
