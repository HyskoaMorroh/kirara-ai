"""Claude / Gemini / Ollama 也必须实现流式，不能静默回落。

需求 4 要求「必须要实现流式和非流式输出」。此前只有 OpenAI 兼容适配器实现了
`LLMChatStreamProtocol`：配置 Claude / Gemini / Ollama 的部署即使把
`reply_stream_mode` 打开也会静默走非流式，于是流式首字节超时、静默超时与
「首字节之前的故障转移」这三条容错路径对它们全部无效——而用户看不到任何提示。

三家的帧格式互不相同，照搬 OpenAI 的解析会一个分片都读不到：

- Claude：SSE，按 ``event`` 分类型，文本在 ``content_block_delta.delta.text``，
  用量分散在 ``message_start``（input）与 ``message_delta``（output）；
- Gemini：SSE + ``alt=sse``，文本在 ``candidates[0].content.parts[*].text``；
- Ollama：**不是 SSE**，是按行 JSON（NDJSON），最后一行 ``done=true``。

因此每家都要单独覆盖，而不是共用一组断言。
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Iterable

import pytest

from kirara_ai.llm.adapter import LLMChatStreamProtocol
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest


def request() -> LLMChatRequest:
    return LLMChatRequest(
        model="m",
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
    )


class _FakeResponse:
    """A requests-like streaming response."""

    def __init__(self, lines: Iterable[str], status_ok: bool = True):
        self._lines = list(lines)
        self._ok = status_ok
        self.text = "error body"
        #: 建连失败时被关掉的次数。
        #:
        #: 真实的 `requests` 响应有 `close()`，而这个替身此前没有——于是
        #: 「建连失败后释放连接」这一步在测试里不可见，也无法被断言。
        #: 整流重试会重新建连，不关上一条就是每次重试漏一个 socket。
        self.closed = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("HTTP 500")

    def close(self):
        self.closed += 1

    def iter_lines(self, decode_unicode: bool = False):
        return iter(self._lines)


def patch_post(monkeypatch, module, lines, status_ok=True) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        response = _FakeResponse(lines, status_ok)
        # 把响应对象也交出来：否则「建连失败后有没有关掉这条连接」
        # 在测试里无从断言，而漏连接不会有任何症状，只会慢慢耗尽连接池。
        captured.setdefault("responses", []).append(response)
        return response

    monkeypatch.setattr(module.requests, "post", fake_post)
    return captured


def claude_adapter():
    from kirara_ai.plugins.llm_preset_adapters.claude_adapter import ClaudeAdapter, ClaudeConfig

    adapter = ClaudeAdapter(ClaudeConfig(api_key="k"))
    adapter.media_manager = None
    return adapter


def gemini_adapter():
    from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import GeminiAdapter, GeminiConfig

    adapter = GeminiAdapter(GeminiConfig(api_key="k"))
    adapter.media_manager = None
    return adapter


def ollama_adapter():
    from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import OllamaAdapter, OllamaConfig

    adapter = OllamaAdapter(OllamaConfig())
    adapter.media_manager = None
    return adapter


def texts_of(chunks) -> list[str]:
    return [
        part.text
        for chunk in chunks
        for part in chunk.message.content
        if hasattr(part, "text")
    ]


@pytest.mark.parametrize("factory", [claude_adapter, gemini_adapter, ollama_adapter])
def test_each_adapter_declares_the_stream_protocol(factory):
    """协议判定必须在运行时成立：`_stream_chat_available` 用它决定走哪条路。"""
    assert isinstance(factory(), LLMChatStreamProtocol)


def test_claude_stream_yields_deltas_and_final_usage(monkeypatch):
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter as module

    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":11}}}',
        'data: {"type":"content_block_delta","delta":{"text":"你好"}}',
        "",
        'data: {"type":"content_block_delta","delta":{"text":"世界"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":7}}',
        'data: {"type":"message_stop"}',
    ]
    captured = patch_post(monkeypatch, module, lines)

    chunks = list(claude_adapter().stream_chat(request()))

    assert texts_of(chunks) == ["你好", "世界"]
    assert captured["json"]["stream"] is True, "流式路径不得被调用方关掉"
    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.prompt_tokens == 11
    assert final.usage.completion_tokens == 7
    assert final.usage.total_tokens == 18
    assert final.message.finish_reason == "end_turn"


def test_claude_stream_raises_on_an_error_frame(monkeypatch):
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter as module

    patch_post(
        monkeypatch,
        module,
        ['data: {"type":"error","error":{"type":"overloaded_error"}}'],
    )

    with pytest.raises(RuntimeError, match="overloaded_error"):
        list(claude_adapter().stream_chat(request()))


def test_gemini_stream_yields_deltas_and_final_usage(monkeypatch):
    from kirara_ai.plugins.llm_preset_adapters import gemini_adapter as module

    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"模拟"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":"退火"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[]},"finishReason":"STOP"}],'
        '"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":9,'
        '"totalTokenCount":14}}',
    ]
    captured = patch_post(monkeypatch, module, lines)

    chunks = list(gemini_adapter().stream_chat(request()))

    assert texts_of(chunks) == ["模拟", "退火"]
    assert "alt=sse" in captured["url"], "Gemini 必须用 alt=sse 才是 SSE 帧"
    assert "streamGenerateContent" in captured["url"]
    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.prompt_tokens == 5
    assert final.usage.completion_tokens == 9
    assert final.message.finish_reason == "STOP"


def test_ollama_stream_parses_ndjson_not_sse(monkeypatch):
    """Ollama 是按行 JSON；用 `data:` 前缀解析会读到零个分片。"""
    from kirara_ai.plugins.llm_preset_adapters import ollama_adapter as module

    lines = [
        json.dumps({"message": {"content": "第一"}, "done": False}),
        json.dumps({"message": {"content": "第二"}, "done": False}),
        json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 6,
            }
        ),
    ]
    captured = patch_post(monkeypatch, module, lines)

    chunks = list(ollama_adapter().stream_chat(request()))

    assert texts_of(chunks) == ["第一", "第二"]
    assert captured["json"]["stream"] is True
    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.prompt_tokens == 4
    assert final.usage.completion_tokens == 6
    assert final.usage.total_tokens == 10


@pytest.mark.parametrize(
    ("module_name", "factory", "lines"),
    [
        (
            "claude_adapter",
            claude_adapter,
            ["data: {not json", 'data: {"type":"message_stop"}'],
        ),
        ("gemini_adapter", gemini_adapter, ["data: {not json", "data: [DONE]"]),
        ("ollama_adapter", ollama_adapter, ["{not json", '{"done": true}']),
    ],
)
def test_an_unparsable_frame_does_not_kill_the_stream(
    monkeypatch, module_name, factory, lines
):
    """单个坏帧只应被跳过：一条流不能因为一行垃圾而整体失败。"""
    module = importlib.import_module(
        f"kirara_ai.plugins.llm_preset_adapters.{module_name}"
    )
    patch_post(monkeypatch, module, lines)

    # 不抛即通过；产出可以为空或仅含终帧。
    list(factory().stream_chat(request()))


@pytest.mark.parametrize(
    ("module_name", "factory"),
    [
        ("claude_adapter", claude_adapter),
        ("gemini_adapter", gemini_adapter),
        ("ollama_adapter", ollama_adapter),
    ],
)
def test_an_http_error_propagates(monkeypatch, module_name, factory):
    """HTTP 层失败必须抛出，让上层的故障转移看到它。"""
    module = importlib.import_module(
        f"kirara_ai.plugins.llm_preset_adapters.{module_name}"
    )
    captured = patch_post(monkeypatch, module, [], status_ok=False)

    with pytest.raises(RuntimeError):
        list(factory().stream_chat(request()))

    # 建连失败的响应必须被关掉。
    #
    # 只有 Claude 这条路径会在失败后重新建连（整流重试），因此只有它需要显式
    # 关闭；另两家失败即抛，由 `with` 收尾。断言写成「不能留下没关且没被
    # `with` 收走的连接」，而不是逐家硬编码次数——后者会在别家也加上重试时
    # 静默失效。
    responses = captured.get("responses", [])
    assert responses, "没有发出任何请求"
    if len(responses) > 1:
        assert all(r.closed >= 1 for r in responses[:-1]), (
            "重试前的响应没有关闭，每次重试都会漏一条连接"
        )


@pytest.mark.parametrize(
    ("module_name", "factory"),
    [
        ("claude_adapter", claude_adapter),
        ("gemini_adapter", gemini_adapter),
        ("ollama_adapter", ollama_adapter),
    ],
)
def test_no_usage_from_upstream_stays_none(monkeypatch, module_name, factory):
    """上游没给用量就保持 None，交给上层估算器标记，绝不在这里补 0。"""
    module = importlib.import_module(
        f"kirara_ai.plugins.llm_preset_adapters.{module_name}"
    )
    frames = {
        "claude_adapter": [
            'data: {"type":"content_block_delta","delta":{"text":"x"}}',
            'data: {"type":"message_stop"}',
        ],
        "gemini_adapter": [
            'data: {"candidates":[{"content":{"parts":[{"text":"x"}]}}]}',
            "data: [DONE]",
        ],
        "ollama_adapter": [
            '{"message": {"content": "x"}, "done": false}',
            '{"message": {"content": ""}, "done": true}',
        ],
    }[module_name]
    patch_post(monkeypatch, module, frames)

    chunks = list(factory().stream_chat(request()))

    assert chunks, "至少应产出一个文本分片"
    assert all(chunk.usage is None for chunk in chunks)
