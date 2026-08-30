"""非流式适配器的用量归属契约（需求 22.1）。

需求 22.1 要求区分「真实 / 供应商返回 / 估算 / 未知」四类 Token，并且
「缺少供应商 usage 时必须明确标记估算或未知」。危险的失败形态不是缺数据，
而是**伪造数据再打上「供应商返回」的标签**：

`mark_provider_usage` 只在 `usage.source == UNKNOWN` 时改标记，
`attach_estimated_usage` 只在 `usage is None` 时补估算值。因此适配器一旦
把缺失字段兜底成 `0` 并构造出一个 `Usage` 对象，这条请求就会被标记成
`provider`、参与成本统计、并显示为一次「0 Token 的免费请求」——
既绕过了估算器，又把断言（「就是 0」）冒充成了观测。

这些用例钉住三条：
1. 上游没给 usage 时，适配器必须交出 `usage=None`（交由上层估算/标记未知）；
2. 上游给了 usage 时，字段必须按各家真实的键名读取；
3. 缓存维度（已计价）必须真的有生产者，不能是永远为空的列。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import UsageSource
from kirara_ai.plugins.llm_preset_adapters.claude_adapter import ClaudeAdapter, ClaudeConfig
from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import GeminiAdapter, GeminiConfig
from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import OllamaAdapter, OllamaConfig
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import OpenAIAdapter, OpenAIConfig


def chat_request(model: str = "mock-model") -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hello")])
        ],
        model=model,
    )


class _FakeResponse:
    """最小 requests.Response 替身：只提供适配器实际用到的三个成员。"""

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.text = "<fake response>"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _openai_adapter(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> OpenAIAdapter:
    adapter = OpenAIAdapter(OpenAIConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "openai"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        adapter._session, "post", lambda *args, **kwargs: _FakeResponse(payload)
    )
    return adapter


def _claude_adapter(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> ClaudeAdapter:
    adapter = ClaudeAdapter(ClaudeConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "claude"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


def _gemini_adapter(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> GeminiAdapter:
    adapter = GeminiAdapter(GeminiConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "gemini"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.gemini_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


def _ollama_adapter(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> OllamaAdapter:
    adapter = OllamaAdapter(OllamaConfig(api_base="http://invalid.example"))
    adapter.backend_name = "ollama"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


OPENAI_TEXT_CHOICE = {
    "choices": [
        {"message": {"role": "assistant", "content": "mock_response"}, "finish_reason": "stop"}
    ]
}

CLAUDE_TEXT_CONTENT = {
    "role": "assistant",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "mock_response"}],
}

GEMINI_TEXT_CANDIDATE = {
    "candidates": [
        {
            "content": {"role": "model", "parts": [{"text": "mock_response"}]},
            "finishReason": "STOP",
        }
    ]
}

OLLAMA_TEXT_MESSAGE = {"message": {"role": "assistant", "content": "mock_response"}}


def test_openai_reports_unknown_usage_rather_than_zero(monkeypatch: pytest.MonkeyPatch):
    """上游省略 usage 时不得构造一份全 0 的 Usage。

    全 0 的 Usage 会被 `mark_provider_usage` 标成「供应商返回」，
    并让 `attach_estimated_usage` 直接跳过——这条请求于是永久记为
    0 Token、0 成本，且看起来像是上游亲口说的。适配器交出 `None`
    之后，装饰器里的估算器才能接手并如实标记为 `ESTIMATED`。
    """
    adapter = _openai_adapter(monkeypatch, dict(OPENAI_TEXT_CHOICE))

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.source is UsageSource.ESTIMATED
    assert response.usage.prompt_tokens != 0


def test_openai_reads_provider_usage_including_cached_prompt_tokens(
    monkeypatch: pytest.MonkeyPatch,
):
    """OpenAI 的缓存命中量在 `prompt_tokens_details.cached_tokens`。

    `cache_write_per_million` / 缓存命中价格都已在定价表里计费，
    却没有任何生产者填 `cached_tokens`，缓存维度的成本因此恒为 0。
    """
    adapter = _openai_adapter(
        monkeypatch,
        {
            **OPENAI_TEXT_CHOICE,
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        },
    )

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 30
    assert response.usage.completion_tokens == 12
    assert response.usage.total_tokens == 42
    assert response.usage.cached_tokens == 20


def test_claude_reports_unknown_usage_rather_than_zero(monkeypatch: pytest.MonkeyPatch):
    adapter = _claude_adapter(monkeypatch, dict(CLAUDE_TEXT_CONTENT))

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.source is UsageSource.ESTIMATED
    assert response.usage.prompt_tokens != 0


def test_claude_reads_cache_read_and_cache_write_tokens(monkeypatch: pytest.MonkeyPatch):
    """Claude 把缓存拆成读（`cache_read_input_tokens`）与写（`cache_creation_input_tokens`）。

    两者单价不同且都已在 `PriceCatalog` 里建模，因此必须分别落库，
    不能把缓存写入静默算成普通输入。
    """
    adapter = _claude_adapter(
        monkeypatch,
        {
            **CLAUDE_TEXT_CONTENT,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_read_input_tokens": 40,
                "cache_creation_input_tokens": 60,
            },
        },
    )

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 100
    assert response.usage.completion_tokens == 25
    assert response.usage.total_tokens == 125
    assert response.usage.cached_tokens == 40
    assert response.usage.cache_write_tokens == 60


def test_gemini_reports_unknown_usage_rather_than_zero(monkeypatch: pytest.MonkeyPatch):
    adapter = _gemini_adapter(monkeypatch, dict(GEMINI_TEXT_CANDIDATE))

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.source is UsageSource.ESTIMATED
    assert response.usage.prompt_tokens != 0


def test_gemini_non_stream_output_tokens_come_from_candidates_token_count(
    monkeypatch: pytest.MonkeyPatch,
):
    """输出 Token 是 `usageMetadata.candidatesTokenCount`。

    非流式分支此前读的是顶层 `promptTokensDetails`——一个并不存在的键，
    于是 `completion_tokens` 恒为 0，而同一个适配器的流式分支读的是正确字段。
    同一家上游给出两套口径，成本统计只会在其中一条路径上正确。
    """
    adapter = _gemini_adapter(
        monkeypatch,
        {
            **GEMINI_TEXT_CANDIDATE,
            "usageMetadata": {
                "promptTokenCount": 514,
                "candidatesTokenCount": 114,
                "cachedContentTokenCount": 1919,
                "totalTokenCount": 2547,
            },
        },
    )

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 514
    assert response.usage.completion_tokens == 114
    assert response.usage.cached_tokens == 1919
    assert response.usage.total_tokens == 2547


def test_ollama_reports_unknown_usage_when_counters_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """Ollama 缺统计字段时不得抛 KeyError，也不得记成 0。

    旧实现用 `response_data['prompt_eval_count']` 直接下标：字段缺失
    会让整条请求以 KeyError 失败，而这本该只是「用量未知」，
    由估算器接手并标记为 `ESTIMATED`。
    """
    adapter = _ollama_adapter(monkeypatch, dict(OLLAMA_TEXT_MESSAGE))

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.source is UsageSource.ESTIMATED
    assert response.usage.prompt_tokens != 0


def test_ollama_reads_provider_counters(monkeypatch: pytest.MonkeyPatch):
    adapter = _ollama_adapter(
        monkeypatch,
        {**OLLAMA_TEXT_MESSAGE, "prompt_eval_count": 18, "eval_count": 7},
    )

    response = adapter.chat(chat_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 18
    assert response.usage.completion_tokens == 7
    assert response.usage.total_tokens == 25


def test_openai_stream_reads_cached_prompt_tokens(monkeypatch: pytest.MonkeyPatch):
    """流式分支的缓存维度必须与非流式一致。

    两条路径读同一家上游却各读一套字段，会让「同一个模型的缓存成本」
    取决于用户是否开了流式——统计口径必须在两条路径上相同（需求 22.1）。
    """
    frames = [
        'data: {"choices":[{"delta":{"content":"hi"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":30,"completion_tokens":12,"total_tokens":42,'
        '"prompt_tokens_details":{"cached_tokens":20}}}',
        "data: [DONE]",
    ]

    class _StreamResponse:
        text = "<fake stream>"

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self, decode_unicode: bool = False):
            return iter(frames)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    adapter = OpenAIAdapter(OpenAIConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "openai"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        adapter._session, "post", lambda *args, **kwargs: _StreamResponse()
    )

    usages = [
        chunk.usage for chunk in adapter.stream_chat(chat_request()) if chunk.usage
    ]

    assert len(usages) == 1
    assert usages[0].cached_tokens == 20
    assert usages[0].prompt_tokens == 30
    assert usages[0].completion_tokens == 12


def test_claude_stream_reads_cache_dimensions(monkeypatch: pytest.MonkeyPatch):
    """Claude 流式同样要报缓存读/写，口径与非流式一致。"""
    frames = [
        'data: {"type":"message_start","message":{"usage":'
        '{"input_tokens":100,"cache_read_input_tokens":40,'
        '"cache_creation_input_tokens":60}}}',
        'data: {"type":"content_block_delta","delta":{"text":"hi"}}',
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        '"usage":{"output_tokens":25}}',
        'data: {"type":"message_stop"}',
    ]

    class _StreamResponse:
        text = "<fake stream>"

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self, decode_unicode: bool = False):
            return iter(frames)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    adapter = ClaudeAdapter(ClaudeConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "claude"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter.requests.post",
        lambda *args, **kwargs: _StreamResponse(),
    )

    usages = [
        chunk.usage for chunk in adapter.stream_chat(chat_request()) if chunk.usage
    ]

    assert len(usages) == 1
    assert usages[0].prompt_tokens == 100
    assert usages[0].completion_tokens == 25
    assert usages[0].cached_tokens == 40
    assert usages[0].cache_write_tokens == 60


