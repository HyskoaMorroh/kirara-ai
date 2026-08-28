from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    ResourceBinding,
    RuntimeResult,
    RuntimeStatus,
    SessionStore,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent, LLMToolCallContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


HASH_PROMPT = "a" * 64
HASH_MCP = "c" * 64
CREATOR = RuntimePrincipal(subject="session-test-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


class FakeLLMManager:
    def __init__(self, responses: list[LLMChatResponse]):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        return ChatExecutionResult(
            response=self.responses.pop(0),
            trace_id=f"trace-{len(self.requests)}",
            attempts=[],
        )


class FakeMCPManager:
    def __init__(self):
        self.calls = []
        self.tools = {
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
        return name == "write"

    async def call_tool(self, name, args, **options):
        self.calls.append((name, args, options))
        return SimpleNamespace(
            content=[SimpleNamespace(text="tool completed")],
            isError=False,
        )


class FakeMemoryManager:
    def __init__(self, entries: list[MemoryEntry] | None = None):
        self.entries = list(entries or [])
        self.queries = []
        self.stores = []

    def query(self, scope, sender, extra_identifier=None):
        self.queries.append((scope, sender, extra_identifier))
        return list(self.entries)

    def store(self, scope, entry, extra_identifier=None):
        self.stores.append((scope, entry, extra_identifier))
        self.entries.append(entry)


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="account-a",
        conversation_scope="c2c:user-a",
        sender_scope="user-a",
    )


def _other_context() -> ChannelContext:
    return ChannelContext(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="account-a",
        conversation_scope="c2c:user-b",
        sender_scope="user-b",
    )


def _message(text: str) -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat("user-a", "Researcher"),
        [TextMessage(text)],
    )


def _agent() -> AgentDefinition:
    return AgentDefinition(
        agent_id="research-agent",
        model_priority=("model-primary",),
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
        mcp_allowlist={"write"},
        max_tool_iterations=2,
    )


def _executor(
    registry: AgentRegistry,
    llm: FakeLLMManager,
    mcp: FakeMCPManager,
    store: SessionStore,
    memory: FakeMemoryManager | None = None,
) -> AgentRuntimeExecutor:
    return AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader={"prompt-main": "Pinned prompt"}.__getitem__,
        session_store=store,
        memory_manager=memory,
    )


def _response(text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text=text)],
        ),
    )


def _tool_response() -> LLMChatResponse:
    return LLMChatResponse(
        model="model-primary",
        message=Message(
            role="assistant",
            content=[],
            tool_calls=[
                ToolCall(
                    id="call-1",
                    type="function",
                    function=Function(name="write", arguments={"text": "publish"}),
                )
            ],
        ),
    )


def _registry(tmp_path: Path) -> AgentRegistry:
    registry = AgentRegistry(tmp_path)
    registry.register(_agent())
    registry.set_default("research-agent")
    return registry


def test_session_store_round_trips_history_without_raw_session_filename(tmp_path: Path):
    store = SessionStore(tmp_path)
    # The store accepts validated LLM messages, not arbitrary serialized input.
    history = [
        LLMChatMessage(
            role="user",
            content=[LLMChatTextContent(text="hello")],
        ),
        LLMChatMessage(
            role="assistant",
            content=[LLMChatTextContent(text="hi")],
        ),
    ]
    store.save_history(_context().session_key, history)

    restored = SessionStore(tmp_path).load_history(_context().session_key)

    assert [item.model_dump(mode="json") for item in restored] == [
        item.model_dump(mode="json") for item in history
    ]
    assert _context().session_key not in " ".join(
        path.name for path in (tmp_path / "sessions").iterdir()
    )


def test_session_store_keeps_each_agents_history_for_the_same_session(tmp_path: Path):
    store = SessionStore(tmp_path)
    session_key = _context().session_key
    first = [
        LLMChatMessage(
            role="user",
            content=[LLMChatTextContent(text="agent-a history")],
        )
    ]
    second = [
        LLMChatMessage(
            role="user",
            content=[LLMChatTextContent(text="agent-b history")],
        )
    ]

    store.save_history(session_key, first, agent_id="agent-a")
    store.save_history(session_key, second, agent_id="agent-b")

    assert store.load_history(session_key, agent_id="agent-a")[0].content[0].text == (
        "agent-a history"
    )
    assert store.load_history(session_key, agent_id="agent-b")[0].content[0].text == (
        "agent-b history"
    )


def test_session_store_reads_matching_legacy_agent_history(tmp_path: Path):
    store = SessionStore(tmp_path)
    session_key = _context().session_key
    history = [
        LLMChatMessage(
            role="user",
            content=[LLMChatTextContent(text="legacy history")],
        )
    ]
    legacy_payload = {
        "format_version": 1,
        "agent_id": "research-agent",
        "messages": [item.model_dump(mode="json") for item in history],
    }
    store._atomic_write(store._history_path(session_key), legacy_payload)

    restored = store.load_history(session_key, agent_id="research-agent")

    assert restored[0].content[0].text == "legacy history"
    assert store.load_history(session_key, agent_id="other-agent") == []


@pytest.mark.asyncio
async def test_runtime_loads_and_persists_history_across_executor_instances(tmp_path: Path):
    context = _context()
    store = SessionStore(tmp_path)
    registry = _registry(tmp_path)
    first_llm = FakeLLMManager([_response("first reply")])

    first = await _executor(registry, first_llm, FakeMCPManager(), store).run(
        context, _message("first question")
    )

    second_llm = FakeLLMManager([_response("second reply")])
    second = await _executor(registry, second_llm, FakeMCPManager(), store).run(
        context, _message("second question")
    )

    assert first.status is RuntimeStatus.COMPLETED
    assert second.status is RuntimeStatus.COMPLETED
    request_messages = second_llm.requests[0].messages
    assert [item.content[0].text for item in request_messages if item.role == "user"] == [
        "first question",
        "second question",
    ]
    assert [
        item.content[0].text
        for item in store.load_history(context.session_key, agent_id="research-agent")
    ] == [
        "first question",
        "first reply",
        "second question",
        "second reply",
    ]


@pytest.mark.asyncio
async def test_runtime_loads_memory_and_writes_completed_turn_with_isolated_key(
    tmp_path: Path,
):
    context = _context()
    memory = FakeMemoryManager(
        [
            MemoryEntry(
                sender=_message("old question").sender,
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
    llm = FakeLLMManager([_response("new answer")])
    runtime = _executor(
        _registry(tmp_path),
        llm,
        FakeMCPManager(),
        SessionStore(tmp_path),
        memory,
    )

    result = await runtime.run(context, _message("new question"))

    assert result.status is RuntimeStatus.COMPLETED
    assert [
        item.content[0].text
        for item in llm.requests[0].messages
        if item.role in {"user", "assistant"}
    ] == ["old question", "old answer", "new question"]
    assert len(memory.queries) == 1
    memory_key = memory.queries[0][2]
    assert memory_key.startswith("agent-runtime:")
    assert context.session_key not in memory_key
    assert context.sender_scope not in memory_key
    stored = [call[1] for call in memory.stores]
    assert [entry.metadata["agent_runtime"]["message"]["role"] for entry in stored] == [
        "user",
        "assistant",
    ]
    assert [entry.content for entry in stored] == ["new question", "new answer"]
    assert all(call[2] == memory_key for call in memory.stores)


@pytest.mark.asyncio
async def test_session_history_wins_over_mirrored_memory_without_duplicates(
    tmp_path: Path,
):
    context = _context()
    store = SessionStore(tmp_path)
    session_history = [
        LLMChatMessage(
            role="user",
            content=[LLMChatTextContent(text="session question")],
        ),
        LLMChatMessage(
            role="assistant",
            content=[LLMChatTextContent(text="session answer")],
        ),
    ]
    memory = FakeMemoryManager(
        [
            MemoryEntry(
                sender=_message("memory duplicate").sender,
                content="memory duplicate",
                metadata={
                    "agent_runtime": {
                        "version": 1,
                        "agent_id": "research-agent",
                        "message": LLMChatMessage(
                            role="user",
                            content=[LLMChatTextContent(text="memory duplicate")],
                        ).model_dump(mode="json"),
                    }
                },
            )
        ]
    )
    runtime = _executor(
        _registry(tmp_path),
        FakeLLMManager([_response("new answer")]),
        FakeMCPManager(),
        store,
        memory,
    )
    store.save_history(
        runtime._history_key(context, "research-agent"),
        session_history,
        agent_id="research-agent",
    )

    await runtime.run(context, _message("new question"))

    request = runtime.llm_manager.requests[0]
    texts = [
        item.content[0].text
        for item in request.messages
        if item.role in {"user", "assistant"}
    ]
    assert texts == ["session question", "session answer", "new question"]
    assert len(memory.queries) == 1


def test_memory_scope_key_changes_for_every_channel_and_agent_dimension(tmp_path: Path):
    runtime = _executor(
        _registry(tmp_path),
        FakeLLMManager([]),
        FakeMCPManager(),
        SessionStore(tmp_path),
        FakeMemoryManager(),
    )
    base = _context()
    variants = [
        ChannelContext(**{**base.__dict__, "channel_type": "wecom"}),
        ChannelContext(**{**base.__dict__, "adapter_instance": "telegram-backup"}),
        ChannelContext(**{**base.__dict__, "account_scope": "account-b"}),
        ChannelContext(**{**base.__dict__, "conversation_scope": "group:research"}),
        ChannelContext(**{**base.__dict__, "sender_scope": "user-b"}),
    ]

    keys = {
        runtime._memory_key(base, "research-agent"),
        runtime._memory_key(base, "other-agent"),
        *(runtime._memory_key(context, "research-agent") for context in variants),
    }

    assert len(keys) == 7
    assert all(base.session_key not in key for key in keys)


def test_memory_sender_matches_the_runtime_conversation_scope(tmp_path: Path):
    runtime = _executor(
        _registry(tmp_path),
        FakeLLMManager([]),
        FakeMCPManager(),
        SessionStore(tmp_path),
        FakeMemoryManager(),
    )

    private_sender = runtime._memory_sender(_context())
    group_sender = runtime._memory_sender(
        ChannelContext(
            channel_type="telegram",
            adapter_instance="telegram-main",
            account_scope="account-a",
            conversation_scope="group:research",
            sender_scope="user-a",
        )
    )

    assert str(private_sender) == "c2c:user-a"
    assert str(group_sender) == "research:user-a"
    assert group_sender.group_id == "research"


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_confirmation_persists_tool_call_result_and_final_reply_to_memory(
    tmp_path: Path,
):
    context = _context()
    memory = FakeMemoryManager()
    store = SessionStore(tmp_path)
    mcp = FakeMCPManager()
    registry = _registry(tmp_path)
    waiting = await _executor(
        registry,
        FakeLLMManager([_tool_response()]),
        mcp,
        store,
        memory,
    ).run(context, _message("publish"))

    resumed = await _executor(
        registry,
        FakeLLMManager([_response("published")]),
        mcp,
        store,
        memory,
    ).confirm(waiting.confirmation_id, context)

    assert resumed.status is RuntimeStatus.COMPLETED
    assert [
        call[1].metadata["agent_runtime"]["message"]["role"]
        for call in memory.stores
    ] == ["user", "assistant", "tool", "assistant"]


def test_memory_persistence_failure_is_audited_without_leaking_error_details(
    tmp_path: Path,
):
    class FailingMemoryManager(FakeMemoryManager):
        def store(self, *args, **kwargs):
            raise RuntimeError("provider credential must not be logged")

    audit = []
    runtime = AgentRuntimeExecutor(
        agent_registry=_registry(tmp_path),
        llm_manager=FakeLLMManager([]),
        mcp_manager=FakeMCPManager(),
        memory_manager=FailingMemoryManager(),
        audit_sink=audit.append,
    )
    result = RuntimeResult(
        status=RuntimeStatus.COMPLETED,
        context=_context(),
        response=_response("reply"),
        messages=[
            LLMChatMessage(
                role="user",
                content=[LLMChatTextContent(text="question")],
            )
        ],
    )

    runtime._persist_memory(_context(), "research-agent", result)

    assert audit[-1]["component"] == "agent_runtime_memory"
    assert audit[-1]["operation"] == "store"
    assert audit[-1]["error_type"] == "RuntimeError"
    assert "credential" not in json.dumps(audit[-1])


def test_memory_message_redacts_sensitive_tool_parameters(tmp_path: Path):
    runtime = _executor(
        _registry(tmp_path),
        FakeLLMManager([]),
        FakeMCPManager(),
        SessionStore(tmp_path),
        FakeMemoryManager(),
    )
    message = LLMChatMessage(
        role="assistant",
        content=[
            LLMToolCallContent(
                id="call-1",
                name="write",
                parameters={
                    "token": "secret-token",
                    "nested": {"api_key": "secret-key", "text": "keep"},
                },
            )
        ],
    )

    redacted = runtime._redact_memory_message(message).model_dump(mode="json")
    serialized = json.dumps(redacted)

    assert "secret-token" not in serialized
    assert "secret-key" not in serialized
    assert "keep" in serialized


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_pending_confirmation_survives_executor_restart(tmp_path: Path):
    context = _context()
    store = SessionStore(tmp_path)
    registry = _registry(tmp_path)
    mcp = FakeMCPManager()
    waiting = await _executor(
        registry,
        FakeLLMManager([_tool_response()]),
        mcp,
        store,
    ).run(context, _message("publish"))

    restarted_llm = FakeLLMManager([_response("published")])
    restarted = _executor(registry, restarted_llm, mcp, store)
    resumed = await restarted.confirm(waiting.confirmation_id, context)

    assert waiting.status is RuntimeStatus.AWAITING_CONFIRMATION
    assert resumed.status is RuntimeStatus.COMPLETED
    assert resumed.text == "published"
    assert [call[0] for call in mcp.calls] == ["write"]
    assert store.load_pending() == []
    audit_record = store.get_confirmation(waiting.confirmation_id)
    assert audit_record is not None
    assert audit_record["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_confirmation_can_only_be_claimed_by_its_original_session(tmp_path: Path):
    context = _context()
    store = SessionStore(tmp_path)
    registry = _registry(tmp_path)
    mcp = FakeMCPManager()
    waiting = await _executor(
        registry,
        FakeLLMManager([_tool_response()]),
        mcp,
        store,
    ).run(context, _message("publish"))
    restarted = _executor(
        registry,
        FakeLLMManager([_response("published")]),
        mcp,
        SessionStore(tmp_path),
    )

    denied = await restarted.confirm(waiting.confirmation_id, _other_context())
    resumed = await restarted.confirm(waiting.confirmation_id, context)

    assert denied.status is RuntimeStatus.FAILED
    assert denied.error["type"] == "ConfirmationSessionMismatch"
    assert resumed.status is RuntimeStatus.COMPLETED
    assert [call[0] for call in mcp.calls] == ["write"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_two_runtime_instances_cannot_execute_one_confirmation_twice(tmp_path: Path):
    context = _context()
    store = SessionStore(tmp_path)
    registry = _registry(tmp_path)
    mcp = FakeMCPManager()
    waiting = await _executor(
        registry,
        FakeLLMManager([_tool_response()]),
        mcp,
        store,
    ).run(context, _message("publish"))
    first = _executor(
        registry,
        FakeLLMManager([_response("first")]),
        mcp,
        SessionStore(tmp_path),
    )
    second = _executor(
        registry,
        FakeLLMManager([_response("second")]),
        mcp,
        SessionStore(tmp_path),
    )

    results = await asyncio.gather(
        first.confirm(waiting.confirmation_id, context),
        second.confirm(waiting.confirmation_id, context),
    )

    assert sum(result.status is RuntimeStatus.COMPLETED for result in results) == 1
    rejected = next(result for result in results if result.status is RuntimeStatus.FAILED)
    assert rejected.error["type"] in {
        "ConfirmationInProgress",
        "ConfirmationAlreadyProcessed",
    }
    assert [call[0] for call in mcp.calls] == ["write"]
    assert store.get_confirmation(waiting.confirmation_id)["status"] == "succeeded"


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_failed_tool_confirmation_is_terminal_and_is_not_retried(tmp_path: Path):
    class FailingMCPManager(FakeMCPManager):
        async def call_tool(self, name, args, **options):
            self.calls.append((name, args, options))
            raise RuntimeError("provider details must not be persisted")

    context = _context()
    store = SessionStore(tmp_path)
    registry = _registry(tmp_path)
    mcp = FailingMCPManager()
    waiting = await _executor(
        registry,
        FakeLLMManager([_tool_response(), _response("operation failed")]),
        mcp,
        store,
    ).run(context, _message("publish"))

    first = await _executor(
        registry,
        FakeLLMManager([_response("operation failed")]),
        mcp,
        SessionStore(tmp_path),
    ).confirm(waiting.confirmation_id, context)
    duplicate = await _executor(
        registry,
        FakeLLMManager([]),
        mcp,
        SessionStore(tmp_path),
    ).confirm(waiting.confirmation_id, context)

    assert first.status is RuntimeStatus.COMPLETED
    assert duplicate.status is RuntimeStatus.FAILED
    assert duplicate.error["type"] == "ConfirmationAlreadyProcessed"
    assert len(mcp.calls) == 1
    audit_record = store.get_confirmation(waiting.confirmation_id)
    assert audit_record["status"] == "failed"
    assert audit_record["error_type"] == "ToolExecutionFailed"
    assert "provider details" not in json.dumps(audit_record)


def test_expired_confirmation_is_terminal_and_cannot_be_claimed(tmp_path: Path):
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    current = [now]
    store = SessionStore(
        tmp_path,
        confirmation_ttl_seconds=30,
        clock=lambda: current[0],
    )
    store.save_pending(
        {"confirmation_id": "confirm-expired", "payload": "opaque"},
        session_key=_context().session_key,
    )
    current[0] = now + timedelta(seconds=31)

    outcome, record = store.claim_pending(
        "confirm-expired", _context().session_key
    )

    assert outcome == "expired"
    assert record is not None
    assert record["status"] == "expired"
    assert store.load_pending() == []
    assert store.get_confirmation("confirm-expired")["status"] == "expired"


def test_pre_correlation_confirmation_backfills_one_stable_id_before_session_check(
    tmp_path: Path,
):
    store = SessionStore(tmp_path)
    store.save_pending(
        {"confirmation_id": "confirm-legacy", "payload": "opaque"},
        session_key=_context().session_key,
    )

    mismatch_outcome, mismatch = store.claim_pending(
        "confirm-legacy", _other_context().session_key
    )
    persisted_after_mismatch = store.get_confirmation("confirm-legacy")
    executing_outcome, executing = store.claim_pending(
        "confirm-legacy", _context().session_key
    )
    completed = store.complete_pending("confirm-legacy", "succeeded")
    duplicate_outcome, duplicate = store.claim_pending(
        "confirm-legacy", _context().session_key
    )

    assert mismatch_outcome == "session_mismatch"
    assert mismatch is not None
    correlation_id = mismatch["correlation_id"]
    assert correlation_id
    assert persisted_after_mismatch["correlation_id"] == correlation_id
    assert executing_outcome == "executing"
    assert executing["correlation_id"] == correlation_id
    assert completed["correlation_id"] == correlation_id
    assert duplicate_outcome == "succeeded"
    assert duplicate["correlation_id"] == correlation_id


def test_pre_correlation_expired_confirmation_keeps_one_id_across_repeated_claims(
    tmp_path: Path,
):
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    current = [now]
    store = SessionStore(
        tmp_path,
        confirmation_ttl_seconds=30,
        clock=lambda: current[0],
    )
    store.save_pending(
        {"confirmation_id": "confirm-legacy-expired", "payload": "opaque"},
        session_key=_context().session_key,
    )
    current[0] = now + timedelta(seconds=31)

    first_outcome, first = store.claim_pending(
        "confirm-legacy-expired", _context().session_key
    )
    second_outcome, second = store.claim_pending(
        "confirm-legacy-expired", _context().session_key
    )
    missing_outcome, missing = store.claim_pending(
        "confirm-does-not-exist", _context().session_key
    )

    assert first_outcome == "expired"
    assert second_outcome == "expired"
    assert first["correlation_id"]
    assert second["correlation_id"] == first["correlation_id"]
    assert missing_outcome == "not_found"
    assert missing is None


def test_confirmation_store_rejects_corrupt_records(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.pending_path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "items": [
                    {
                        "confirmation_id": "broken",
                        "status": "unknown-state",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="record"):
        store.load_confirmations()


def test_confirmation_store_does_not_persist_raw_channel_identity(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.save_pending(
        {"confirmation_id": "confirm-private", "payload": "opaque"},
        session_key=_context().session_key,
    )

    serialized = store.pending_path.read_text(encoding="utf-8")

    assert _context().session_key not in serialized
    assert _context().sender_scope not in serialized
    assert _context().conversation_scope not in serialized
