"""需求 10(b)：Hook 的 `additionalContext` 不能只在两个事件上生效。

`_inject_hook_context` 只在 `SessionStart` + `UserPromptSubmit` 之后被调用一次
（`executor.py:248`）。其余 10 个事件的 `HookOutcome` 也带着
`additional_context` / `system_messages` 字段，解析层照样填充它们——
然后被丢掉。

最明显的一处是 `PostToolUse`：一个 Hook 看到工具返回结果后想告诉模型
「这个结果的单位是分而不是元」，它按协议写进 `additionalContext`，
解析成功、审计记录里显示 `status: ok`，而模型永远看不到那句话。

这比「不支持」更糟：协议里有、解析通过、审计说成功，唯独不起作用。
Hook 作者会以为自己写错了业务逻辑，而实际是这条通路不存在。

刻意保留的边界：**不是所有事件都该注入**。`Stop`、`SessionEnd` 之后已经没有
下一次模型调用，往哪注入都无意义；那两个事件的 `additionalContext`
按「无处可去」处理，而不是硬塞进一个不会被读的地方。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentHookRuntime,
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
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


CREATOR = RuntimePrincipal(
    subject="hook-context-creator",
    scopes=frozenset({"*"}),
    is_creator=True,
)
HASH_HOOK = "d" * 64
HASH_MCP = "e" * 64


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="webui",
        adapter_instance="web",
        account_scope="account",
        conversation_scope="conversation",
        sender_scope="sender",
    )


def _message() -> IMMessage:
    return IMMessage(ChatSender.from_c2c_chat("user", "User"), [TextMessage("go")])


class _LLM:
    """Return a tool call first, then a plain answer; record every request."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def execute_chat(self, request, **_options):
        self.requests.append(request)
        if len(self.requests) == 1:
            response = LLMChatResponse(
                model="m",
                message=Message(
                    role="assistant",
                    content=[],
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            type="function",
                            function=Function(name="read", arguments={}),
                        )
                    ],
                ),
            )
        else:
            response = LLMChatResponse(
                model="m",
                message=Message(
                    role="assistant", content=[LLMChatTextContent(text="done")]
                ),
            )
        return ChatExecutionResult(response=response, trace_id="t", attempts=[])


class _MCP:
    def __init__(self) -> None:
        self.tools = {
            "read": SimpleNamespace(
                server_id="srv",
                original_name="read",
                tool_info=SimpleNamespace(
                    name="read",
                    description="read a value",
                    inputSchema={"type": "object", "properties": {}},
                ),
            )
        }

    def get_tools(self) -> dict[str, Any]:
        return self.tools

    def requires_confirmation(self, _name: str) -> bool:
        return False

    async def call_tool(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="42")],
            isError=False,
        )


def _executor(tmp_path, *, handlers: dict[str, Any], events: dict[str, Any]):
    agent = AgentDefinition(
        agent_id="agent",
        owner_subject=CREATOR.subject,
        model_priority=("m",),
        allow_tools=True,
        mcp_allowlist=frozenset({"read"}),
        mcp_bindings=(
            ResourceBinding(
                resource_id="mcp.srv",
                resource_type="mcp",
                version="1.0.0",
                content_sha256=HASH_MCP,
            ),
        ),
        hook_bindings=(
            ResourceBinding(
                resource_id="hook.ctx",
                resource_type="hook",
                version="1.0.0",
                content_sha256=HASH_HOOK,
            ),
        ),
    )
    registry = AgentRegistry(tmp_path / "agents")
    with runtime_principal_context(CREATOR):
        registry.register(agent)
    registry.set_default(agent.agent_id)

    llm = _LLM()
    hooks = AgentHookRuntime(
        resource_loader=lambda _resource_id, _version=None: json.dumps(
            {"events": events}
        ),
        handlers=handlers,
    )
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=llm,
        mcp_manager=_MCP(),
        hook_runtime=hooks,
    )
    return executor, llm


def _system_text(request) -> str:
    return "\n".join(
        part.text
        for message in request.messages
        if message.role == "system"
        for part in message.content
        if isinstance(part, LLMChatTextContent)
    )


@pytest.mark.asyncio
async def test_post_tool_use_context_reaches_the_next_model_request(tmp_path):
    """`PostToolUse` 的 `additionalContext` 必须进入下一次请求。

    这是最能说明问题的一个事件：Hook 看到工具结果后想补一句
    「这个数的单位是分」，协议里写得对、解析通过、审计说成功，
    而模型永远看不到。Hook 作者只能怀疑自己的业务逻辑。
    """
    executor, llm = _executor(
        tmp_path,
        events={"PostToolUse": {"handler": "post"}},
        handlers={
            "post": lambda _payload: {
                "hookSpecificOutput": {"additionalContext": "单位是分而不是元"}
            }
        },
    )

    with runtime_principal_context(CREATOR):
        result = await executor.run(_context(), _message())

    assert result.status is RuntimeStatus.COMPLETED
    assert len(llm.requests) == 2
    assert "单位是分而不是元" in _system_text(llm.requests[1])


@pytest.mark.asyncio
async def test_pre_tool_use_context_reaches_the_next_model_request(tmp_path):
    executor, llm = _executor(
        tmp_path,
        events={"PreToolUse": {"handler": "pre"}},
        handlers={
            "pre": lambda _payload: {
                "hookSpecificOutput": {"additionalContext": "该工具只读"}
            }
        },
    )

    with runtime_principal_context(CREATOR):
        await executor.run(_context(), _message())

    assert "该工具只读" in _system_text(llm.requests[1])


@pytest.mark.asyncio
async def test_a_system_message_from_a_tool_hook_also_reaches_the_model(tmp_path):
    executor, llm = _executor(
        tmp_path,
        events={"PostToolUse": {"handler": "post"}},
        handlers={"post": lambda _payload: {"systemMessage": "结果已脱敏"}},
    )

    with runtime_principal_context(CREATOR):
        await executor.run(_context(), _message())

    assert "结果已脱敏" in _system_text(llm.requests[1])


@pytest.mark.asyncio
async def test_the_context_is_not_repeated_on_every_later_request(tmp_path):
    """同一条上下文不能每轮重复注入。

    重复注入会让一段文本在长对话里出现十几次，白花 token，还可能让模型
    把它当成被反复强调的重点。
    """
    executor, llm = _executor(
        tmp_path,
        events={"PostToolUse": {"handler": "post"}},
        handlers={
            "post": lambda _payload: {
                "hookSpecificOutput": {"additionalContext": "只说一次"}
            }
        },
    )

    with runtime_principal_context(CREATOR):
        await executor.run(_context(), _message())

    assert _system_text(llm.requests[1]).count("只说一次") == 1


@pytest.mark.asyncio
async def test_a_hook_without_context_changes_nothing(tmp_path):
    """不返回上下文的 Hook 不得改变消息结构。"""
    executor, llm = _executor(
        tmp_path,
        events={"PostToolUse": {"handler": "post"}},
        handlers={"post": lambda _payload: {}},
    )

    with runtime_principal_context(CREATOR):
        await executor.run(_context(), _message())

    first_roles = [message.role for message in llm.requests[0].messages]
    second_roles = [message.role for message in llm.requests[1].messages]
    # 第二次请求只多出 assistant(tool_call) 与 tool 两条，没有额外 system。
    assert second_roles == [*first_roles, "assistant", "tool"]
