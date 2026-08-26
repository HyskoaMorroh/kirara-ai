from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentHookRuntime,
    AgentRegistry,
    AgentRuntimeExecutor,
    ResourceBinding,
    RuntimeStatus,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult, FailoverExecutionError
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_qqbot_adapter.adapter import QQBotAdapter
from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter
from kirara_ai.plugins.im_wecom_adapter.adapter import WecomAdapter
from kirara_ai.web.api.llm.webui_adapter import WebUIAdapter
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry


HASH_PROMPT = "a" * 64
HASH_SKILL = "b" * 64
HASH_MEMORY = "c" * 64
HASH_MCP = "d" * 64
HASH_HOOK = "e" * 64


class _FailoverToolLLM:
    """Primary failure, then one Context7-shaped call and a final answer."""

    def __init__(self):
        self.requests = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        if request.model == "model-primary":
            raise FailoverExecutionError("primary unavailable", attempts=[])

        latest_user = max(
            (
                index
                for index, message in enumerate(request.messages)
                if message.role == "user"
            ),
            default=-1,
        )
        current_turn = request.messages[latest_user + 1 :]
        if any(message.role == "tool" for message in current_turn):
            query = next(
                (
                    message.content[0].content
                    for message in reversed(current_turn)
                    if message.role == "tool"
                ),
                "unknown",
            )
            response = LLMChatResponse(
                model="model-backup",
                message=Message(
                    role="assistant",
                    content=[LLMChatTextContent(text=f"shared answer: {query}")],
                ),
            )
        else:
            response = LLMChatResponse(
                model="model-backup",
                message=Message(
                    role="assistant",
                    content=[],
                    tool_calls=[
                        ToolCall(
                            id=f"context7-call-{len(self.requests)}",
                            type="function",
                            function=Function(
                                name="query-docs",
                                arguments={
                                    "libraryId": "/shared/research",
                                    "query": request.messages[latest_user].content[0].text,
                                },
                            ),
                        )
                    ],
                ),
            )
        return ChatExecutionResult(
            response=response,
            trace_id=f"unified-trace-{len(self.requests)}",
            attempts=[],
        )


class _Context7MCP:
    def __init__(self):
        self.calls = []
        self.tools = {
            "query-docs": SimpleNamespace(
                server_id="context7",
                original_name="query-docs",
                tool_info=SimpleNamespace(
                    name="query-docs",
                    description="Query shared documentation",
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

    def get_tools(self):
        return self.tools

    def requires_confirmation(self, _name):
        return False

    async def call_tool(self, name, arguments, **options):
        self.calls.append((name, arguments, options))
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=f"Context7 result for {arguments['query']}"
                )
            ],
            isError=False,
        )


class _IsolatedMemory:
    def __init__(self):
        self.entries: dict[str, list[object]] = {}
        self.queries = []
        self.stores = []

    def query(self, scope, sender, extra_identifier=None):
        self.queries.append((scope, sender, extra_identifier))
        return list(self.entries.get(extra_identifier, []))

    def store(self, scope, entry, extra_identifier=None):
        self.stores.append((scope, entry, extra_identifier))
        self.entries.setdefault(extra_identifier, []).append(entry)


def _message(user_id: str, text: str) -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat(user_id, f"User {user_id}"),
        message_elements=[TextMessage(text)],
    )


def _agent() -> AgentDefinition:
    return AgentDefinition(
        agent_id="unified-research-agent",
        model_priority=("model-primary", "model-backup"),
        prompt_bindings=(
            ResourceBinding(
                resource_id="prompt.office-research",
                resource_type="prompt",
                version="1.0.0",
                content_sha256=HASH_PROMPT,
            ),
        ),
        skill_bindings=(
            ResourceBinding(
                resource_id="skill.context7-research",
                resource_type="skill",
                version="1.0.0",
                content_sha256=HASH_SKILL,
            ),
        ),
        memory_bindings=(
            ResourceBinding(
                resource_id="memory.research-context",
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
        hook_bindings=(
            ResourceBinding(
                resource_id="hook.runtime-debug",
                resource_type="hook",
                version="1.0.0",
                content_sha256=HASH_HOOK,
            ),
        ),
        mcp_allowlist={"context7.query-docs"},
        max_tool_iterations=2,
    )


def _build_runtime(tmp_path: Path):
    resources = {
        "prompt.office-research": "Prompt: explain evidence in plain language.",
        "skill.context7-research": "Skill: use Context7 for current documentation.",
        "memory.research-context": "Memory policy: preserve each channel thread.",
        "hook.runtime-debug": json.dumps(
            {
                "events": {
                    event: {"handler": f"record-{event}"}
                    for event in (
                        "SessionStart",
                        "UserPromptSubmit",
                        "PreToolUse",
                        "PostToolUse",
                        "Stop",
                    )
                }
            }
        ),
    }
    hook_events = []
    handlers = {
        f"record-{event}": (lambda _payload, event=event: hook_events.append(event))
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        )
    }
    llm = _FailoverToolLLM()
    mcp = _Context7MCP()
    memory = _IsolatedMemory()
    registry = AgentRegistry()
    unified = _agent()
    registry.register(unified)
    registry.register(
        AgentDefinition(
            agent_id="wrong-default-agent",
            model_priority=("model-backup",),
        )
    )
    registry.set_default("wrong-default-agent")

    # Account bindings force every adapter's normalized identity through the
    # same Agent relation, while the unrelated default catches bad identity
    # extraction.
    for channel, adapter_instance, account_scope in (
        ("webui", "webui", "webui"),
        ("onebot", "onebot-main", "onebot-account"),
        ("qqbot", "qq-main", "qq-account"),
        ("telegram", "telegram-main", "telegram-account"),
        ("wecom", "wecom-main", "wecom-account"),
    ):
        registry.bind_account(
            channel,
            adapter_instance,
            account_scope,
            unified.agent_id,
        )

    hook_runtime = AgentHookRuntime(
        resource_loader=resources.__getitem__,
        handlers=handlers,
    )
    runtime = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=mcp,
        resource_loader=resources.__getitem__,
        memory_manager=memory,
        session_store=__import__(
            "kirara_ai.agent_runtime.session_store",
            fromlist=["SessionStore"],
        ).SessionStore(tmp_path),
        hook_runtime=hook_runtime,
    )

    container = DependencyContainer()
    workflow_registry = WorkflowRegistry(container)
    container.register(WorkflowRegistry, workflow_registry)
    container.register(
        DispatchRuleRegistry,
        DispatchRuleRegistry(container),
    )
    container.register(AgentRegistry, registry)
    container.register(AgentRuntimeExecutor, runtime)
    return WorkflowDispatcher(container), runtime, llm, mcp, memory, hook_events


async def _run_onebot(dispatcher, user_id: str, text: str):
    adapter = object.__new__(OneBotAdapter)
    adapter.adapter_instance = "onebot-main"
    adapter.logger = MagicMock()
    adapter.dispatcher = dispatcher
    adapter.send_message = AsyncMock()
    await adapter._handle_message(
        {
            "self_id": "onebot-account",
            "user_id": user_id,
            "message": [{"type": "text", "data": {"text": text}}],
        }
    )
    return adapter


async def _run_qq(dispatcher, user_id: str, text: str):
    adapter = object.__new__(QQBotAdapter)
    adapter.adapter_instance = "qq-main"
    adapter.account_scope = "qq-account"
    adapter.logger = MagicMock()
    adapter.dispatcher = dispatcher
    adapter.send_message = AsyncMock()
    adapter.convert_to_message = AsyncMock(
        return_value=_message(user_id, text)
    )
    await adapter.on_c2c_message_create(object())
    return adapter


async def _run_telegram(dispatcher, user_id: str, text: str):
    adapter = object.__new__(TelegramAdapter)
    adapter.adapter_instance = "telegram-main"
    adapter.account_scope = "telegram-account"
    adapter.dispatcher = dispatcher
    adapter.send_message = AsyncMock()
    adapter.convert_to_message = AsyncMock(
        return_value=_message(user_id, text)
    )
    await adapter.handle_message(SimpleNamespace(message=object()), SimpleNamespace())
    return adapter


async def _run_wecom(dispatcher, user_id: str, text: str):
    adapter = object.__new__(WecomAdapter)
    adapter.adapter_instance = "wecom-main"
    adapter.account_scope = "wecom-account"
    adapter.dispatcher = dispatcher
    adapter.send_message = AsyncMock()
    normalized = await adapter.convert_to_message(
        SimpleNamespace(source=user_id, type="text", content=text, __dict__={})
    )
    await dispatcher.dispatch(adapter, normalized, require_agent=True)
    return adapter


@pytest.mark.asyncio
async def test_webui_onebot_qq_telegram_wecom_share_one_agent_runtime(tmp_path: Path):
    (
        dispatcher,
        runtime,
        llm,
        mcp,
        memory,
        hook_events,
    ) = _build_runtime(tmp_path)
    entries = (
        ("webui", WebUIAdapter(), _message("web-user", "web research")),
        ("onebot", None, "onebot research"),
        ("qqbot", None, "qq research"),
        ("telegram", None, "telegram research"),
        ("wecom", None, "wecom research"),
    )

    webui = entries[0][1]
    await dispatcher.dispatch(webui, entries[0][2], require_agent=True)
    onebot = await _run_onebot(dispatcher, "onebot-user", entries[1][2])
    qq = await _run_qq(dispatcher, "qq-user", entries[2][2])
    telegram = await _run_telegram(
        dispatcher, "telegram-user", entries[3][2]
    )
    wecom = await _run_wecom(dispatcher, "wecom-user", entries[4][2])

    adapters = (onebot, qq, telegram, wecom)
    assert webui.reply is not None
    assert all(adapter.send_message.await_count == 1 for adapter in adapters)
    assert [webui.reply.content, *(
        adapter.send_message.await_args.args[0].content for adapter in adapters
    )] == [
        "shared answer: Context7 result for web research",
        "shared answer: Context7 result for onebot research",
        "shared answer: Context7 result for qq research",
        "shared answer: Context7 result for telegram research",
        "shared answer: Context7 result for wecom research",
    ]

    # Dispatcher selected the account-bound Agent, and every result came from
    # the same runtime snapshot policy.
    assert runtime.agent_registry.default_agent_id == "wrong-default-agent"
    assert len(llm.requests) == 15
    assert all(
        request.messages[0].content[0].text.count("Prompt:") == 1
        and "Skill: use Context7" in request.messages[0].content[0].text
        and "Memory policy:" in request.messages[0].content[0].text
        for request in llm.requests
    )
    assert [request.model for request in llm.requests[0:3]] == [
        "model-primary",
        "model-backup",
        "model-backup",
    ]
    assert all(
        any(message.role == "tool" for message in request.messages)
        for request in llm.requests[2::3]
    )
    assert len(mcp.calls) == 5
    assert all(call[0] == "query-docs" for call in mcp.calls)
    assert all(
        call[2]["agent_mcp_server_ids"] == frozenset({"context7"})
        for call in mcp.calls
    )

    assert hook_events == [
        event
        for _ in range(5)
        for event in (
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
        )
    ]
    assert len(memory.queries) == 5
    assert len({query[2] for query in memory.queries}) == 5
    assert len(memory.stores) == 20


@pytest.mark.asyncio
async def test_shared_runtime_keeps_history_and_memory_isolated_by_channel_identity(
    tmp_path: Path,
):
    dispatcher, _runtime, llm, _mcp, memory, _hooks = _build_runtime(tmp_path)
    first = WebUIAdapter()
    second = WebUIAdapter()

    await dispatcher.dispatch(
        first,
        _message("same-user", "first channel thread"),
        require_agent=True,
    )
    await dispatcher.dispatch(
        second,
        IMMessage(
            sender=ChatSender.from_group_chat(
                "same-user", "different-group", "Same User"
            ),
            message_elements=[TextMessage("second channel thread")],
        ),
        require_agent=True,
    )

    # The first WebUI session is replayed. Its next model request may contain
    # only its own prior turn, never the other conversation's text.
    await dispatcher.dispatch(
        first,
        _message("same-user", "follow up in first thread"),
        require_agent=True,
    )
    follow_up_request = llm.requests[-3]
    serialized = "\n".join(
        message.content[0].text
        for message in follow_up_request.messages
        if message.content and hasattr(message.content[0], "text")
    )
    assert "first channel thread" in serialized
    assert "second channel thread" not in serialized
    assert len({query[2] for query in memory.queries}) == 2
