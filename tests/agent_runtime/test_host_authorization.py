from __future__ import annotations

import json
import sys
from pathlib import Path
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
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


CREATOR = RuntimePrincipal(
    subject="creator-subject",
    role="admin",
    scopes=frozenset({"*"}),
    is_creator=True,
)
OTHER_ADMIN = RuntimePrincipal(
    subject="other-admin",
    role="admin",
    scopes=frozenset({"*"}),
    is_creator=False,
)
HASH_MCP = "c" * 64
HASH_HOOK = "e" * 64


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="webui",
        adapter_instance="web-main",
        account_scope="account",
        conversation_scope="conversation",
        sender_scope="sender",
    )


def _message(text: str = "write it") -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat("user", "User"),
        [TextMessage(text)],
    )


def _tool_response() -> LLMChatResponse:
    return LLMChatResponse(
        model="model",
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall(
                    id="call-1",
                    type="function",
                    function=Function(name="write", arguments={"text": "payload"}),
                )
            ],
        ),
    )


def _text_response(text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="model",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text=text)],
        ),
    )


class _LLM:
    def __init__(self, responses: list[LLMChatResponse]):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        return ChatExecutionResult(
            response=self.responses.pop(0),
            trace_id=None,
            attempts=[],
        )


class _MCP:
    def __init__(self, *, confirmation: bool = False):
        self.confirmation = confirmation
        self.calls = []
        self.tools = {
            "write": SimpleNamespace(
                server_id="files",
                original_name="write",
                tool_info=SimpleNamespace(
                    name="write",
                    description="Write a file",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
            )
        }

    def get_tools(self):
        return self.tools

    def requires_confirmation(self, name: str) -> bool:
        return self.confirmation and name == "write"

    async def call_tool(self, name, arguments, **options):
        self.calls.append((name, arguments, options))
        return SimpleNamespace(content=[SimpleNamespace(text="written")], isError=False)


def _agent(
    *,
    agent_id: str = "agent",
    owner_subject: str | None = None,
    hook: bool = False,
) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        owner_subject=owner_subject,
        model_priority=("model",),
        capabilities=frozenset({"process.execute"}) if hook else frozenset(),
        mcp_bindings=(
            ResourceBinding(
                resource_id="mcp.files",
                resource_type="mcp",
                version="1",
                content_sha256=HASH_MCP,
            ),
        ),
        hook_bindings=(
            ResourceBinding(
                resource_id="hook.command",
                resource_type="hook",
                version="1",
                content_sha256=HASH_HOOK,
                permissions=("process.execute",),
            ),
        )
        if hook
        else (),
        mcp_allowlist=frozenset({"files.write"}),
    )


def _executor(
    agent: AgentDefinition,
    llm: _LLM,
    mcp: _MCP,
    *,
    store: SessionStore | None = None,
) -> AgentRuntimeExecutor:
    registry = AgentRegistry()
    principal = (
        RuntimePrincipal(subject=agent.owner_subject, is_creator=True)
        if agent.owner_subject is not None
        else None
    )
    with runtime_principal_context(principal):
        registry.register(agent)
    registry.set_default(agent.agent_id)
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        session_store=store,
    )


def test_registry_binds_only_creator_at_creation_and_persists_owner(tmp_path: Path):
    creator_registry = AgentRegistry(tmp_path / "creator")
    with runtime_principal_context(CREATOR):
        creator_registry.register(_agent())
        creator_registry.configure(
            _agent(agent_id="configured"),
            create=True,
        )

    assert creator_registry.get("agent").owner_subject == CREATOR.subject
    assert creator_registry.get("configured").owner_subject == CREATOR.subject
    assert AgentRegistry(tmp_path / "creator").get("agent").owner_subject == CREATOR.subject

    admin_registry = AgentRegistry(tmp_path / "admin")
    with runtime_principal_context(OTHER_ADMIN):
        admin_registry.register(_agent())
    assert admin_registry.get("agent").owner_subject is None

    anonymous_registry = AgentRegistry(tmp_path / "anonymous")
    anonymous_registry.register(_agent())
    assert anonymous_registry.get("agent").owner_subject is None


def test_registry_legacy_owner_is_unbound_and_existing_owner_cannot_change(tmp_path: Path):
    registry = AgentRegistry(tmp_path)
    with runtime_principal_context(CREATOR):
        registry.register(_agent())

    registry.update(_agent(owner_subject=None))
    assert registry.get("agent").owner_subject == CREATOR.subject

    with pytest.raises(ValueError, match="owner"):
        registry.update(_agent(owner_subject="different-subject"))

    path = tmp_path / "agents" / "registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["agents"][0].pop("owner_subject")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert AgentRegistry(tmp_path).get("agent").owner_subject is None


@pytest.mark.asyncio
@pytest.mark.parametrize("principal", [None, OTHER_ADMIN])
async def test_unbound_or_noncreator_agent_cannot_run_mcp_but_still_gets_ai_text(principal):
    llm = _LLM([_tool_response(), _text_response("I cannot perform that operation.")])
    mcp = _MCP()
    executor = _executor(_agent(owner_subject=CREATOR.subject), llm, mcp)

    with runtime_principal_context(principal):
        result = await executor.run(_context(), _message())

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "I cannot perform that operation."
    assert mcp.calls == []
    assert llm.requests[0].tools == []
    denied = llm.requests[1].messages[-1].content[0]
    assert denied.isError is True
    assert "permission" in str(denied.content)


@pytest.mark.asyncio
async def test_creator_can_run_mcp_only_through_its_owned_agent():
    llm = _LLM([_tool_response(), _text_response("Done.")])
    mcp = _MCP()
    executor = _executor(_agent(owner_subject=CREATOR.subject), llm, mcp)

    with runtime_principal_context(CREATOR):
        result = await executor.run(_context(), _message())

    assert result.status is RuntimeStatus.COMPLETED
    assert result.text == "Done."
    assert [call[0] for call in mcp.calls] == ["write"]
    assert mcp.calls[0][2]["agent_owner_subject"] == CREATOR.subject


@pytest.mark.asyncio
async def test_confirmation_revalidates_principal_after_executor_restart(tmp_path: Path):
    store = SessionStore(tmp_path)
    mcp = _MCP(confirmation=True)
    with runtime_principal_context(CREATOR):
        executor = _executor(
            _agent(owner_subject=CREATOR.subject),
            _LLM([_tool_response()]),
            mcp,
            store=store,
        )
        waiting = await executor.run(_context(), _message())

    restarted = _executor(
        _agent(owner_subject=CREATOR.subject),
        _LLM([_text_response("Done.")]),
        mcp,
        store=SessionStore(tmp_path),
    )
    with runtime_principal_context(OTHER_ADMIN):
        denied = await restarted.confirm(waiting.confirmation_id, _context())

    assert waiting.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert denied.status is RuntimeStatus.FAILED
    assert denied.error is not None
    assert denied.error["type"] == "ConfirmationPrincipalMismatch"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_confirmation_record_uses_digest_and_legacy_record_fails_closed(tmp_path: Path):
    context = _context()
    store = SessionStore(tmp_path)
    mcp = _MCP(confirmation=True)
    with runtime_principal_context(CREATOR):
        executor = _executor(
            _agent(owner_subject=CREATOR.subject),
            _LLM([_tool_response()]),
            mcp,
            store=store,
        )
        waiting = await executor.run(context, _message())

    serialized = store.pending_path.read_text(encoding="utf-8")
    assert CREATOR.subject not in serialized
    assert CREATOR.subject_digest in serialized

    payload = json.loads(serialized)
    payload["items"][0].pop("principal_subject_digest")
    store.pending_path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = _executor(
        _agent(owner_subject=CREATOR.subject),
        _LLM([_text_response("must not run")]),
        mcp,
        store=SessionStore(tmp_path),
    )
    with runtime_principal_context(CREATOR):
        denied = await restarted.confirm(waiting.confirmation_id, context)

    assert denied.status is RuntimeStatus.FAILED
    assert denied.error is not None
    assert denied.error["type"] == "ValueError"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_command_hook_requires_creator_and_owned_agent(tmp_path: Path):
    marker = tmp_path / "ran"
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    declaration = json.dumps(
        {
            "events": {
                "UserPromptSubmit": {
                    "type": "command",
                    "command": [sys.executable, "-c", script],
                    "required_permissions": ["process.execute"],
                }
            }
        }
    )
    runtime = AgentHookRuntime(
        resource_loader={"hook.command": declaration}.__getitem__,
    )
    agent = _agent(owner_subject=CREATOR.subject, hook=True)

    with runtime_principal_context(OTHER_ADMIN):
        denied = await runtime.run_event(
            "UserPromptSubmit",
            agent=agent,
            context=_context(),
            snapshot=agent.snapshot(),
            payload={},
        )
    assert denied.status == "error"
    assert not marker.exists()

    with runtime_principal_context(CREATOR):
        allowed = await runtime.run_event(
            "UserPromptSubmit",
            agent=agent,
            context=_context(),
            snapshot=agent.snapshot(),
            payload={},
        )
    assert allowed.status == "success"
    assert marker.read_text(encoding="utf-8") == "ran"


@pytest.mark.asyncio
async def test_command_hook_denies_creator_when_agent_is_unbound(tmp_path: Path):
    marker = tmp_path / "ran"
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    declaration = json.dumps(
        {
            "events": {
                "UserPromptSubmit": {
                    "type": "command",
                    "command": [sys.executable, "-c", script],
                    "required_permissions": ["process.execute"],
                }
            }
        }
    )
    runtime = AgentHookRuntime(
        resource_loader={"hook.command": declaration}.__getitem__,
    )
    agent = _agent(hook=True)

    with runtime_principal_context(CREATOR):
        denied = await runtime.run_event(
            "UserPromptSubmit",
            agent=agent,
            context=_context(),
            snapshot=agent.snapshot(),
            payload={},
        )

    assert denied.status == "error"
    assert not marker.exists()
