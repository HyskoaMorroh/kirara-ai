"""Streaming reply mode must be reachable and must not change what users get.

`execute_stream` had no production caller: every path went through
`execute_chat`, so the stream-first-byte timeout, the stream idle timeout and
"failover is safe before the first visible byte" were all dead configuration.

`reply_stream_mode: aggregate` turns the request into a stream and delivers the
aggregated text. It deliberately does not push token-by-token — QQ, Telegram and
WeCom cannot edit a sent message in place, so per-token delivery would become
dozens of fragment messages. The gain is the resilience path, not the typing
animation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kirara_ai.agent_runtime.core import AgentRegistry
from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest, Tool, ToolParameters
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.llm.resilience import ChatExecutionResult


def chunk(text: str, *, usage: Usage | None = None, finish: str = "") -> LLMChatResponse:
    return LLMChatResponse(
        model="model-a",
        usage=usage,
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text=text)] if text else [],
            finish_reason=finish,
        ),
    )


class FakeStream:
    def __init__(self, chunks, trace_id: str = "trace-stream"):
        self._chunks = list(chunks)
        self.trace_id = trace_id
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self):
        self.closed = True


def make_executor(mode: str, *, stream=None, chat_response=None, tmp_path=None):
    manager = MagicMock()
    if stream is not None:
        manager.execute_stream = MagicMock(return_value=stream)
    else:
        del manager.execute_stream
    manager.execute_chat = MagicMock(
        return_value=ChatExecutionResult(
            response=chat_response or chunk("non-stream"),
            trace_id="trace-chat",
            attempts=[],
        )
    )
    return AgentRuntimeExecutor(
        agent_registry=AgentRegistry(tmp_path),
        llm_manager=manager,
        mcp_manager=MagicMock(),
        reply_stream_mode=mode,
    ), manager


def request(tools=None) -> LLMChatRequest:
    return LLMChatRequest(
        model="model-a",
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        tools=tools,
    )


def test_an_invalid_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="reply_stream_mode"):
        AgentRuntimeExecutor(
            agent_registry=AgentRegistry(tmp_path),
            llm_manager=MagicMock(),
            mcp_manager=MagicMock(),
            reply_stream_mode="token-by-token",
        )


def test_the_default_mode_keeps_using_execute_chat(tmp_path):
    executor, manager = make_executor("off", stream=FakeStream([]), tmp_path=tmp_path)

    assert executor._stream_chat_available() is False
    assert manager.execute_stream.call_count == 0


@pytest.mark.asyncio
async def test_aggregate_mode_uses_execute_stream(tmp_path):
    stream = FakeStream([chunk("Hello"), chunk(" world")])
    executor, manager = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    response, model_id, trace_id = await executor._execute_model(
        request(), ("model-a",), "model-a"
    )

    assert manager.execute_stream.call_count == 1
    assert manager.execute_chat.call_count == 0
    assert model_id == "model-a"
    assert trace_id == "trace-stream"


@pytest.mark.asyncio
async def test_the_aggregated_text_is_the_concatenation_of_every_chunk(tmp_path):
    stream = FakeStream([chunk("模拟"), chunk("退火"), chunk("算法")])
    executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    response, _, _ = await executor._execute_model(request(), ("model-a",), "model-a")

    text = "".join(
        part.text
        for part in response.message.content
        if isinstance(part, LLMChatTextContent)
    )
    assert text == "模拟退火算法"


@pytest.mark.asyncio
async def test_usage_from_the_final_frame_is_kept(tmp_path):
    stream = FakeStream(
        [chunk("hi"), chunk("", usage=Usage(prompt_tokens=3, total_tokens=5))]
    )
    executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    response, _, _ = await executor._execute_model(request(), ("model-a",), "model-a")

    assert response.usage is not None
    assert response.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_the_finish_reason_is_preserved(tmp_path):
    stream = FakeStream([chunk("hi"), chunk("", finish="stop")])
    executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    response, _, _ = await executor._execute_model(request(), ("model-a",), "model-a")

    assert response.message.finish_reason == "stop"


@pytest.mark.asyncio
async def test_the_stream_is_closed_even_when_it_raises(tmp_path):
    class ExplodingStream(FakeStream):
        def __iter__(self):
            def generate():
                yield chunk("partial")
                raise RuntimeError("upstream died")

            return generate()

    stream = ExplodingStream([])
    executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    with pytest.raises(RuntimeError, match="upstream died"):
        await executor._execute_model(request(), ("model-a",), "model-a")

    assert stream.closed is True


@pytest.mark.asyncio
async def test_a_tool_request_still_uses_the_non_stream_path(tmp_path):
    """Tool rounds need structured tool_calls; aggregated text would lose them."""
    stream = FakeStream([chunk("ignored")])
    executor, manager = make_executor("aggregate", stream=stream, tmp_path=tmp_path)
    tool = Tool(
        name="read_file",
        description="read",
        parameters=ToolParameters(properties={}, required=[]),
    )

    await executor._execute_model(request(tools=[tool]), ("model-a",), "model-a")

    assert manager.execute_chat.call_count == 1
    assert manager.execute_stream.call_count == 0


@pytest.mark.asyncio
async def test_a_manager_without_execute_stream_falls_back(tmp_path):
    executor, manager = make_executor("aggregate", stream=None, tmp_path=tmp_path)

    await executor._execute_model(request(), ("model-a",), "model-a")

    assert manager.execute_chat.call_count == 1


@pytest.mark.asyncio
async def test_an_empty_stream_yields_an_empty_reply_not_a_crash(tmp_path):
    stream = FakeStream([])
    executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

    response, _, _ = await executor._execute_model(request(), ("model-a",), "model-a")

    text = "".join(
        part.text
        for part in response.message.content
        if isinstance(part, LLMChatTextContent)
    )
    assert text == ""
