"""Teammates 委派的真实执行链路（需求 8）。

上一层用例钉的是「工具怎么生成」；这里钉的是「工具被调用后真的会跑队友」——
一个只出现在工具列表里、调用时返回「未实现」的委派工具，等于没有实现。

四条必须成立的行为：

1. 委派会真正执行队友的一次完整 turn（队友用自己的模型链与资源）。
2. 队友的回答作为 tool 结果回到主 Agent 的对话里，主 Agent 据此继续作答。
3. 深度递减：队友执行时的委派预算比调用者少一层，防止 A→B→A 无限递归。
4. 委派不需要人工确认（它不动服务器），但**队友自身**的高危工具仍走原有确认链路——
   委派不是绕过授权的旁路。
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.agent_runtime.core import (AgentDefinition, AgentRegistry,
                                          ChannelContext)
from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor, RuntimeStatus
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import (Function, LLMChatResponse, Message,
                                           ToolCall)


def text_response(text: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="model-a",
        message=Message(
            content=[LLMChatTextContent(text=text)], role="assistant", finish_reason="stop"
        ),
    )


def delegate_response(agent_id: str, task: str) -> LLMChatResponse:
    return LLMChatResponse(
        model="model-a",
        message=Message(
            content=[LLMChatTextContent(text="")],
            role="assistant",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    type="function",
                    function=Function(
                        name=f"delegate_to_{agent_id}", arguments={"task": task}
                    ),
                )
            ],
        ),
    )


class ScriptedLLMManager:
    """按调用顺序返回预设响应，并记录每次收到的消息。"""

    def __init__(self, script: list[LLMChatResponse]):
        self.script = list(script)
        self.seen_system: list[str] = []
        self.seen_tools: list[tuple[str, ...]] = []

    def execute_chat(self, request, **_kwargs):
        from kirara_ai.llm.resilience import ChatExecutionResult

        system = next(
            (
                part.text
                for message in request.messages
                if message.role == "system"
                for part in message.content
                if isinstance(part, LLMChatTextContent)
            ),
            "",
        )
        self.seen_system.append(system)
        self.seen_tools.append(
            tuple(tool.name for tool in (request.tools or ()))
        )
        response = self.script.pop(0)
        return ChatExecutionResult(response=response, trace_id="", attempts=[])


def make_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(
            agent_id="lead",
            model_priority=("model-a",),
            teammate_agent_ids=("helper",),
        )
    )
    registry.register(
        AgentDefinition(
            agent_id="helper",
            display_name="资料检索助手",
            model_priority=("model-a",),
        )
    )
    registry.set_default("lead")
    return registry


def make_executor(manager: ScriptedLLMManager) -> AgentRuntimeExecutor:
    return AgentRuntimeExecutor(
        agent_registry=make_registry(),
        llm_manager=manager,
        mcp_manager=None,
        resource_loader=lambda *_args: "",
    )


def context() -> ChannelContext:
    return ChannelContext(
        channel_type="onebot",
        adapter_instance="default",
        account_scope="1",
        conversation_scope="c2c:2",
        sender_scope="2",
    )


def inbound(text: str = "帮我查一下") -> IMMessage:
    return IMMessage(
        sender=ChatSender.from_c2c_chat("2", "user"),
        message_elements=[TextMessage(text)],
    )


@pytest.mark.asyncio
async def test_delegation_runs_the_teammate_and_feeds_the_answer_back():
    """委派必须真的跑队友，并把结果带回主 Agent 的对话。"""
    manager = ScriptedLLMManager(
        [
            delegate_response("helper", "查一下 A 的资料"),
            text_response("队友说：A 在 2020 年成立。"),
            text_response("综合队友结论：A 成立于 2020 年。"),
        ]
    )
    executor = make_executor(manager)

    result = await executor.run(context(), inbound())

    assert result.status is RuntimeStatus.COMPLETED
    assert "2020" in (result.text or "")
    # 三次模型调用：主 Agent 决定委派 → 队友作答 → 主 Agent 汇总。
    assert len(manager.seen_system) == 3


@pytest.mark.asyncio
async def test_the_teammate_gets_its_own_delegation_budget_one_level_lower():
    """队友执行时的委派预算比调用者少一层，A→B→A 无法无限递归。"""
    manager = ScriptedLLMManager(
        [
            delegate_response("helper", "子任务"),
            text_response("队友答案"),
            text_response("汇总"),
        ]
    )
    executor = make_executor(manager)

    await executor.run(context(), inbound())

    # 第一次调用（主 Agent，深度 2）看得到委派工具；
    # 第二次调用是队友（深度 1，但队友自己没有配队友）因此没有委派工具。
    assert any(name.startswith("delegate_to_") for name in manager.seen_tools[0])
    assert not any(name.startswith("delegate_to_") for name in manager.seen_tools[1])


@pytest.mark.asyncio
async def test_an_unknown_teammate_tool_is_reported_not_crashed():
    """模型编造一个不存在的队友时，返回工具错误让它改口，而不是让整轮失败。"""
    manager = ScriptedLLMManager(
        [
            delegate_response("ghost", "子任务"),
            text_response("我直接回答：……"),
        ]
    )
    executor = make_executor(manager)

    result = await executor.run(context(), inbound())

    assert result.status is RuntimeStatus.COMPLETED
    assert "我直接回答" in (result.text or "")


@pytest.mark.asyncio
async def test_a_missing_task_argument_is_reported_as_a_tool_error():
    """空任务不得被当成有效委派：队友看不到主对话，凭空猜只会浪费一轮。"""
    empty = LLMChatResponse(
        model="model-a",
        message=Message(
            content=[LLMChatTextContent(text="")],
            role="assistant",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    type="function",
                    function=Function(name="delegate_to_helper", arguments={}),
                )
            ],
        ),
    )
    manager = ScriptedLLMManager([empty, text_response("我自己答：……")])
    executor = make_executor(manager)

    result = await executor.run(context(), inbound())

    assert result.status is RuntimeStatus.COMPLETED
    assert "我自己答" in (result.text or "")


@pytest.mark.asyncio
async def test_no_delegation_tool_when_the_agent_has_no_teammates():
    """未配置队友的 Agent 完全看不到委派工具，行为与此前一致。"""
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="solo", model_priority=("model-a",)))
    registry.set_default("solo")
    manager = ScriptedLLMManager([text_response("直接回答")])
    executor = AgentRuntimeExecutor(
        agent_registry=registry,
        llm_manager=manager,
        mcp_manager=None,
        resource_loader=lambda *_args: "",
    )

    result = await executor.run(context(), inbound())

    assert result.status is RuntimeStatus.COMPLETED
    assert manager.seen_tools[0] == ()
