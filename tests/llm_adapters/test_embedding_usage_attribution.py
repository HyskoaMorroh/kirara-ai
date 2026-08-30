"""嵌入与重排的用量也不得把「未知」写成 0（需求 22.1）。

聊天适配器刚修掉「缺失字段兜底成 0」这个缺陷，但嵌入 / 多模态嵌入 / 重排三条路径
仍在用 `.get("...", 0)`。它们不经过 `trace_llm_chat`，所以不会被错标成「供应商返回」，
但「0 tokens」依然是一个**断言**，而正确答案是「不知道」——同一个缺陷类别。

这里刻意只覆盖那三条路径。它们的失败形态比聊天路径更隐蔽：嵌入常用于记忆检索，
一次调用可能处理上千条文本，把它记成 0 token 会让「记忆功能不花钱」这个错误结论
看起来有数据支撑。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.embedding import LLMEmbeddingRequest
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import OllamaAdapter, OllamaConfig
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import OpenAIAdapter, OpenAIConfig
from kirara_ai.plugins.llm_preset_adapters.voyage_adapter import VoyageAdapter, VoyageConfig


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.text = "<fake response>"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def embedding_request(model: str = "mock-embedding") -> LLMEmbeddingRequest:
    return LLMEmbeddingRequest(
        inputs=[LLMChatTextContent(text="hello world")],
        model=model,
    )


def _openai(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> OpenAIAdapter:
    adapter = OpenAIAdapter(OpenAIConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "openai"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.openai_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


def _ollama(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> OllamaAdapter:
    adapter = OllamaAdapter(OllamaConfig(api_base="http://invalid.example"))
    adapter.backend_name = "ollama"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


def _voyage(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> VoyageAdapter:
    adapter = VoyageAdapter(VoyageConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "voyage"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.voyage_adapter.requests.post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )
    return adapter


def test_openai_embedding_without_usage_reports_unknown(monkeypatch: pytest.MonkeyPatch):
    """上游省略 usage 时不得记成 0 token。"""
    adapter = _openai(monkeypatch, {"data": [{"embedding": [0.1, 0.2]}]})

    response = adapter.embed(embedding_request())

    assert response.usage is None or response.usage.total_tokens is None


def test_openai_embedding_reads_reported_usage(monkeypatch: pytest.MonkeyPatch):
    adapter = _openai(
        monkeypatch,
        {
            "data": [{"embedding": [0.1, 0.2]}],
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        },
    )

    response = adapter.embed(embedding_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 7
    assert response.usage.total_tokens == 7


def test_openai_embedding_partial_usage_keeps_the_missing_field_unknown(
    monkeypatch: pytest.MonkeyPatch,
):
    """只给一半字段时，另一半保持未知而不是 0。"""
    adapter = _openai(
        monkeypatch,
        {"data": [{"embedding": [0.1]}], "usage": {"total_tokens": 11}},
    )

    response = adapter.embed(embedding_request())

    assert response.usage is not None
    assert response.usage.total_tokens == 11
    assert response.usage.prompt_tokens is None


def test_ollama_embedding_without_counters_reports_unknown(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _ollama(monkeypatch, {"embeddings": [[0.1, 0.2]]})

    response = adapter.embed(embedding_request())

    assert response.usage is None or response.usage.prompt_tokens is None


def test_ollama_embedding_reads_reported_counter(monkeypatch: pytest.MonkeyPatch):
    adapter = _ollama(
        monkeypatch, {"embeddings": [[0.1, 0.2]], "prompt_eval_count": 5}
    )

    response = adapter.embed(embedding_request())

    assert response.usage is not None
    assert response.usage.prompt_tokens == 5


def test_voyage_embedding_without_usage_reports_unknown(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _voyage(monkeypatch, {"data": [{"embedding": [0.1, 0.2]}]})

    response = adapter.embed(embedding_request())

    assert response.usage is None or response.usage.total_tokens is None


def test_voyage_embedding_reads_reported_usage(monkeypatch: pytest.MonkeyPatch):
    adapter = _voyage(
        monkeypatch,
        {"data": [{"embedding": [0.1, 0.2]}], "usage": {"total_tokens": 9}},
    )

    response = adapter.embed(embedding_request())

    assert response.usage is not None
    assert response.usage.total_tokens == 9


def test_voyage_rerank_without_usage_reports_unknown(monkeypatch: pytest.MonkeyPatch):
    from kirara_ai.llm.format.rerank import LLMReRankRequest

    adapter = _voyage(
        monkeypatch,
        {"data": [{"document": "a", "relevance_score": 0.9, "index": 0}]},
    )

    response = adapter.rerank(
        LLMReRankRequest(query="q", documents=["a"], model="mock-rerank")
    )

    assert response.usage is None or response.usage.total_tokens is None


def test_voyage_rerank_reads_reported_usage(monkeypatch: pytest.MonkeyPatch):
    from kirara_ai.llm.format.rerank import LLMReRankRequest

    adapter = _voyage(
        monkeypatch,
        {
            "data": [{"document": "a", "relevance_score": 0.9, "index": 0}],
            "usage": {"total_tokens": 13},
        },
    )

    response = adapter.rerank(
        LLMReRankRequest(query="q", documents=["a"], model="mock-rerank")
    )

    assert response.usage is not None
    assert response.usage.total_tokens == 13
