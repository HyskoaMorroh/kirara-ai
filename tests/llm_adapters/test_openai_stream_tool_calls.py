"""带工具的请求也必须能走流式（需求 4、21.3）。

`_execute_model` 的流式分支条件是
`self._stream_chat_available(stream_mode) and not candidate_request.tools`。
而 `tools` 在**第 0 轮**就非空——`run()` 先拼好 MCP 工具、队友委派工具与技能工具再
进循环。于是一个绑了任何工具的 Agent，它**绝大多数只需一次回复的对话**从头到尾
没有一次流式请求，因此拿不到：

- `stream_first_byte_timeout_seconds`（等首个数据块的上限）
- `stream_idle_timeout_seconds`（识别中途卡住的流）
- 首字节之前的安全故障转移

21.3 把这三个参数列为必须「集中配置并校验边界」的项，而它们在最常见的部署形态上
根本不生效——绑了 MCP 的 Agent 是本项目的主流用法，不是边缘情况。

原来的理由写在文档里：「聚合文本会丢掉 `tool_calls`」。那句话只对**真的产生了
工具调用**的那一轮成立，被放大成了「凡是带工具的请求」。真正的缺口在适配器：
流式解析只读 `delta.content`，不累积 `delta.tool_calls`。

## 判据

**流式解析必须同时处理两种增量**：文本增量交给上层聚合，工具调用增量在适配器内
按 `index` 累积（`function.name` 一次给全、`function.arguments` 分片拼接），
最后一帧连同 `finish_reason` 一起交出。这是各家流式协议的通行做法。

四条边界：

1. **`arguments` 是分片拼接的**，不是每帧一个完整 JSON。拼错就是工具调用参数错。
2. **`index` 决定归属**：一轮可以并行调多个工具，帧是交错到达的。
3. **`id` 只在第一帧给**，后续帧只有 `index` 与 `arguments` 片段。
4. **没有工具调用时行为逐字不变**：纯文本回复不该因为这条改动多出任何字段。
"""

from __future__ import annotations

from kirara_ai.llm.format.message import LLMToolCallContent
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import (
    accumulate_stream_tool_calls,
    resolve_stream_tool_calls,
)


def _delta(index: int, *, call_id=None, name=None, arguments=None) -> dict:
    call: dict = {"index": index}
    if call_id is not None:
        call["id"] = call_id
    function: dict = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        call["function"] = function
    return call


class TestArgumentFragmentsAreJoined:
    def test_fragments_form_one_json_document(self):
        """`arguments` 按帧分片到达；拼错就是工具调用参数错。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", name="read_file")])
        accumulate_stream_tool_calls(state, [_delta(0, arguments='{"path":')])
        accumulate_stream_tool_calls(state, [_delta(0, arguments=' "a.txt"}')])

        calls = resolve_stream_tool_calls(state)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].parameters == {"path": "a.txt"}

    def test_the_id_from_the_first_frame_is_kept(self):
        """`id` 只在第一帧给；丢了它上游无法把结果对回这次调用。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="call_abc", name="f")])
        accumulate_stream_tool_calls(state, [_delta(0, arguments="{}")])

        assert resolve_stream_tool_calls(state)[0].id == "call_abc"

    def test_a_name_arriving_in_a_later_frame_is_kept(self):
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1")])
        accumulate_stream_tool_calls(state, [_delta(0, name="late_name")])

        assert resolve_stream_tool_calls(state)[0].name == "late_name"


class TestParallelCallsStaySeparate:
    def test_interleaved_frames_are_grouped_by_index(self):
        """一轮可以并行调多个工具，帧是交错到达的。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", name="alpha")])
        accumulate_stream_tool_calls(state, [_delta(1, call_id="c2", name="beta")])
        accumulate_stream_tool_calls(state, [_delta(0, arguments='{"a":1}')])
        accumulate_stream_tool_calls(state, [_delta(1, arguments='{"b":2}')])

        calls = resolve_stream_tool_calls(state)
        assert [call.name for call in calls] == ["alpha", "beta"]
        assert calls[0].parameters == {"a": 1}
        assert calls[1].parameters == {"b": 2}

    def test_calls_are_ordered_by_index_not_arrival(self):
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(1, call_id="c2", name="second")])
        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", name="first")])

        assert [call.name for call in resolve_stream_tool_calls(state)] == [
            "first",
            "second",
        ]

    def test_multiple_calls_in_one_frame_are_handled(self):
        state: dict = {}

        accumulate_stream_tool_calls(
            state,
            [
                _delta(0, call_id="c1", name="alpha", arguments="{}"),
                _delta(1, call_id="c2", name="beta", arguments="{}"),
            ],
        )

        assert len(resolve_stream_tool_calls(state)) == 2


class TestDegenerateInput:
    def test_no_frames_yields_no_calls(self):
        assert resolve_stream_tool_calls({}) == []

    def test_a_call_without_a_name_is_dropped(self):
        """没有函数名的调用无法执行；交出去会在下游变成一次 KeyError。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", arguments="{}")])

        assert resolve_stream_tool_calls(state) == []

    def test_empty_arguments_become_an_empty_object(self):
        """上游只给名字、不给参数是合法的（无参工具）。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", name="ping")])

        calls = resolve_stream_tool_calls(state)
        assert len(calls) == 1
        assert calls[0].parameters == {}

    def test_a_non_list_payload_is_ignored(self):
        state: dict = {}

        accumulate_stream_tool_calls(state, None)  # type: ignore[arg-type]
        accumulate_stream_tool_calls(state, {"index": 0})  # type: ignore[arg-type]

        assert resolve_stream_tool_calls(state) == []

    def test_a_malformed_entry_does_not_break_the_others(self):
        """单个坏帧不该终止整条流——与文本增量同一约定。"""
        state: dict = {}

        accumulate_stream_tool_calls(state, ["not-a-dict", _delta(0, call_id="c1", name="ok")])  # type: ignore[list-item]

        assert [call.name for call in resolve_stream_tool_calls(state)] == ["ok"]

    def test_the_returned_type_matches_the_non_stream_path(self):
        state: dict = {}
        accumulate_stream_tool_calls(state, [_delta(0, call_id="c1", name="f", arguments="{}")])

        assert isinstance(resolve_stream_tool_calls(state)[0], LLMToolCallContent)
