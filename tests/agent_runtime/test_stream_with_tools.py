"""带工具的请求走流式后，工具调用必须完好地到达执行层（需求 4、21.3）。

适配器侧的累积逻辑在 `tests/llm_adapters/test_openai_stream_tool_calls.py`。
这里钉的是上层：`_execute_model` 原来的流式分支条件含
`and not candidate_request.tools`，于是一个绑了 MCP / 技能 / 队友的 Agent，
它**绝大多数只需一次回复的对话**从头到尾没有一次流式请求，因此拿不到
`stream_first_byte_timeout_seconds`、`stream_idle_timeout_seconds` 与首字节前的
安全故障转移——而 21.3 把这三项列为必须集中配置并生效的参数。

放开那个条件的前提是**聚合器不能把工具调用丢掉**：
`_execute_model_streaming` 此前只拼文本，工具调用即使被适配器解析出来也会在
聚合这一步消失，上层于是把「模型想调工具」当成「模型答完了」。
那比一刀切成非流式更糟：前者是能力缺失，后者是静默错误。

## 四条边界

1. **工具调用与文本一起聚合。** 一轮里两者可以同时存在（模型先说一句再调工具）。
2. **`finish_reason` 必须透传。** 执行层靠它判断这一轮是不是要进工具循环。
3. **没有工具调用时形状逐字不变。** 纯文本回复不该因为这条改动多出任何字段。
4. **适配器不支持流式工具调用时不能硬上。** Claude / Gemini / Ollama 的流式解析
   目前只处理文本增量；对它们必须保持非流式，否则工具调用会静默消失——
   那正是本条要避免的失败形态。
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.format.message import LLMChatTextContent, LLMToolCallContent
from kirara_ai.llm.format.request import Tool, ToolParameters
from kirara_ai.llm.format.response import LLMChatResponse, Message
from tests.agent_runtime.test_stream_reply_mode import (
    FakeStream,
    chunk,
    make_executor,
    request,
)


def tool_chunk(
    *calls: LLMToolCallContent, text: str = "", finish: str = "tool_calls"
) -> LLMChatResponse:
    content: list = [LLMChatTextContent(text=text)] if text else []
    content.extend(calls)
    return LLMChatResponse(
        model="model-a",
        message=Message(role="assistant", content=content, finish_reason=finish),
    )


def tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="读文件",
            parameters=ToolParameters(properties={}, required=[]),
        )
    ]


def _calls(response: LLMChatResponse) -> list[LLMToolCallContent]:
    return [
        part
        for part in response.message.content
        if isinstance(part, LLMToolCallContent)
    ]


class TestTheAggregatorKeepsToolCalls:
    @pytest.mark.asyncio
    async def test_a_tool_call_survives_aggregation(self, tmp_path):
        call = LLMToolCallContent(id="c1", name="read_file", parameters={"path": "a"})
        stream = FakeStream([chunk("我来看一下。"), tool_chunk(call)])
        executor, manager = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

        response, _model_id, _trace = await executor._execute_model(
            request(tools=tools()), ("model-a",), "model-a"
        )

        assert manager.execute_stream.call_count == 1, "带工具的请求仍然走了非流式"
        assert [item.name for item in _calls(response)] == ["read_file"]
        assert _calls(response)[0].parameters == {"path": "a"}

    @pytest.mark.asyncio
    async def test_text_and_tool_calls_coexist(self, tmp_path):
        """模型可以先说一句再调工具；两者都要留下。"""
        call = LLMToolCallContent(id="c1", name="read_file", parameters={})
        stream = FakeStream([chunk("我来"), chunk("看一下。"), tool_chunk(call)])
        executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

        response, _model_id, _trace = await executor._execute_model(
            request(tools=tools()), ("model-a",), "model-a"
        )

        text = "".join(
            part.text
            for part in response.message.content
            if isinstance(part, LLMChatTextContent)
        )
        assert text == "我来看一下。"
        assert _calls(response)

    @pytest.mark.asyncio
    async def test_the_finish_reason_is_preserved(self, tmp_path):
        """执行层靠它判断这一轮要不要进工具循环。"""
        call = LLMToolCallContent(id="c1", name="read_file", parameters={})
        stream = FakeStream([tool_chunk(call)])
        executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

        response, _model_id, _trace = await executor._execute_model(
            request(tools=tools()), ("model-a",), "model-a"
        )

        assert response.message.finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_all_survive(self, tmp_path):
        first = LLMToolCallContent(id="c1", name="alpha", parameters={})
        second = LLMToolCallContent(id="c2", name="beta", parameters={})
        stream = FakeStream([tool_chunk(first, second)])
        executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

        response, _model_id, _trace = await executor._execute_model(
            request(tools=tools()), ("model-a",), "model-a"
        )

        assert [item.name for item in _calls(response)] == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_a_pure_text_reply_is_unchanged(self, tmp_path):
        """没有工具调用时形状逐字不变。"""
        stream = FakeStream([chunk("答案"), chunk("就这样。")])
        executor, _ = make_executor("aggregate", stream=stream, tmp_path=tmp_path)

        response, _model_id, _trace = await executor._execute_model(
            request(), ("model-a",), "model-a"
        )

        assert len(response.message.content) == 1
        assert response.message.content[0].text == "答案就这样。"


class TestOffModeStillNeverStreams:
    @pytest.mark.asyncio
    async def test_a_tool_request_in_off_mode_uses_execute_chat(self, tmp_path):
        stream = FakeStream([])
        executor, manager = make_executor("off", stream=stream, tmp_path=tmp_path)

        await executor._execute_model(
            request(tools=tools()), ("model-a",), "model-a"
        )

        assert manager.execute_chat.call_count == 1
        assert manager.execute_stream.call_count == 0
