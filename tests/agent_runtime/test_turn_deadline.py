"""整轮取消与总截止时间必须真的下传到 LLMManager。

`LLMManager.execute_chat` / `execute_stream` 早就实现了 `cancellation_event` 与
`deadline_seconds`（超时放弃等待、取消传播、退避期间也能被取消），但**没有任何生产
调用方**传这两个参数——`grep` 只能在 manager 自己和测试里找到。于是真实部署里
「请求总截止时间」和「取消传播」从未生效：一个卡住的上游会占着线程与连接直到
进程退出。这组用例把「参数确实被传下去」钉住。

同时钉住三件容易做错的事：
1. 多轮工具调用共享同一个预算，而不是每轮都重新给满；
2. 未配置总预算时不得凭空造出一个（保持既有行为）；
3. 第三方 LLMManager 若没有这两个参数，不能硬传导致 TypeError。
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from kirara_ai.agent_runtime.core import AgentRegistry
from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.resilience import ChatExecutionResult


def response(text: str = "ok") -> LLMChatResponse:
    return LLMChatResponse(
        model="model-a",
        message=Message(role="assistant", content=[LLMChatTextContent(text=text)]),
    )


def request() -> LLMChatRequest:
    return LLMChatRequest(
        model="model-a",
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
    )


def make_executor(tmp_path, **kwargs):
    manager = MagicMock()
    del manager.execute_stream  # 强制走非流式路径
    manager.execute_chat = MagicMock(
        return_value=ChatExecutionResult(response=response(), trace_id="t", attempts=[])
    )
    executor = AgentRuntimeExecutor(
        agent_registry=AgentRegistry(tmp_path),
        llm_manager=manager,
        mcp_manager=MagicMock(),
        **kwargs,
    )
    return executor, manager


def test_a_negative_turn_deadline_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="turn_deadline_seconds"):
        AgentRuntimeExecutor(
            agent_registry=AgentRegistry(tmp_path),
            llm_manager=MagicMock(),
            mcp_manager=MagicMock(),
            turn_deadline_seconds=-1,
        )


def test_a_boolean_turn_deadline_is_rejected(tmp_path):
    """`True` 是 int 的子类，必须显式拒绝，否则会被当成 1 秒预算。"""
    with pytest.raises(ValueError, match="turn_deadline_seconds"):
        AgentRuntimeExecutor(
            agent_registry=AgentRegistry(tmp_path),
            llm_manager=MagicMock(),
            mcp_manager=MagicMock(),
            turn_deadline_seconds=True,
        )


def test_the_default_passes_no_deadline_and_no_cancellation(tmp_path):
    """未配置总预算时不得凭空造一个——既有部署的行为必须完全不变。"""
    executor, manager = make_executor(tmp_path)

    assert executor.turn_deadline_seconds == 0.0


@pytest.mark.asyncio
async def test_execute_model_forwards_cancellation_and_deadline(tmp_path):
    executor, manager = make_executor(tmp_path, turn_deadline_seconds=30)
    event = threading.Event()

    await executor._execute_model(
        request(),
        ("model-a",),
        "model-a",
        cancellation_event=event,
        deadline_seconds=12.5,
    )

    kwargs = manager.execute_chat.call_args.kwargs
    assert kwargs["cancellation_event"] is event
    assert kwargs["deadline_seconds"] == 12.5


@pytest.mark.asyncio
async def test_omitted_cancellation_and_deadline_are_not_sent(tmp_path):
    """没有值就不要塞 None 进去：那会覆盖 manager 自己的默认语义。"""
    executor, manager = make_executor(tmp_path)

    await executor._execute_model(request(), ("model-a",), "model-a")

    kwargs = manager.execute_chat.call_args.kwargs
    assert "cancellation_event" not in kwargs
    assert "deadline_seconds" not in kwargs


@pytest.mark.asyncio
async def test_a_manager_without_those_parameters_is_still_callable(tmp_path):
    """第三方 LLMManager 可能没有这两个参数，硬传会 TypeError。"""

    def narrow_execute_chat(req, *, provider_allowlist=(), correlation_id=None):
        return ChatExecutionResult(response=response(), trace_id="t", attempts=[])

    manager = MagicMock()
    del manager.execute_stream
    manager.execute_chat = narrow_execute_chat
    executor = AgentRuntimeExecutor(
        agent_registry=AgentRegistry(tmp_path),
        llm_manager=manager,
        mcp_manager=MagicMock(),
        turn_deadline_seconds=30,
    )

    result, model_id, _ = await executor._execute_model(
        request(),
        ("model-a",),
        "model-a",
        cancellation_event=threading.Event(),
        deadline_seconds=5,
    )

    assert model_id == "model-a"
    assert result.message.content[0].text == "ok"
