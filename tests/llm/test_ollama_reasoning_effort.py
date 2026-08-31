"""推理强度必须在 Ollama 上也落地，而不是被静默丢掉。

需求 8 点名「最大强度思考」。当前 `reasoning_effort` 只有三个适配器真的翻译它：
OpenAI 系（`reasoning_effort` 字符串）、Claude（`thinking.budget_tokens`）、
Gemini（`thinkingConfig.thinkingBudget`）。Ollama 是第四个**有自己的思考开关**
的适配器，却完全不读这个字段。

这一条的失败形态是需求反复点名的那一类：**界面上配好了，那个值从未生效**。
供应商编辑页允许给 Ollama 后端选「最大强度」，`llm_manager` 也会把它写进请求，
然后 `OllamaAdapter.chat` 把整个字段丢掉——没有报错，没有警告，回复看起来
一切正常，只是模型根本没有进入思考模式。用户唯一能观察到的现象是
「开了最大强度但答案质量没变」，而那无法自查。

Ollama 用**顶层** `think` 字段表达思考（不是 `options` 里的一项），
取值是布尔或 `"low"` / `"medium"` / `"high"`。它没有 `"max"` 这一档，
因此 `max` 映射到 `"high"`——把一个上游不认识的字面量透传过去会被拒，
而降一档仍然是「最高可用强度」这个语义。

三条边界与其余三家一致：
- 未指定时**不出现该键**：不支持思考的模型收到 `think` 会报错；
- 流式与非流式同一口径：两条路径给出不同强度是一个无法自查的差异；
- `options` 里已有的字段一个都不动——`think` 是顶层字段，塞进 `options`
  会被 Ollama 当成未知采样参数忽略，于是又变成一次静默失效。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import (
    OllamaAdapter,
    OllamaConfig,
)


def chat_request(**overrides) -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hello")])
        ],
        model="qwen3",
        **overrides,
    )


OLLAMA_OK = {
    "message": {"role": "assistant", "content": "ok"},
    "done_reason": "stop",
    "prompt_eval_count": 3,
    "eval_count": 2,
}


class _Capture:
    """记录最后一次请求体的 requests 替身。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.body: dict | None = None

    def __call__(self, *_args, **kwargs):
        self.body = kwargs.get("json")
        return self

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload

    @property
    def text(self) -> str:
        return "<capture>"

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_lines(self, *_args, **kwargs):
        """Ollama 走 NDJSON：每行一个 JSON 对象。

        `decode_unicode` 由适配器传入，替身必须接受它——签名不匹配会让
        用例以 TypeError 失败，而那掩盖了真正要验的东西。
        """
        import json as _json

        payload = dict(self.payload)
        payload["done"] = True
        line = _json.dumps(payload)
        yield line if kwargs.get("decode_unicode") else line.encode("utf-8")


def _adapter() -> OllamaAdapter:
    adapter = OllamaAdapter(OllamaConfig(api_base="http://invalid.example"))
    adapter.backend_name = "ollama"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    return adapter


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        # Ollama 没有 `max` 这一档。透传一个它不认识的字面量会被拒，
        # 降到 `high` 仍然是「最高可用强度」这个语义。
        ("max", "high"),
    ],
)
def test_ollama_translates_effort_into_think(
    monkeypatch: pytest.MonkeyPatch, effort: str, expected: str
):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    adapter.chat(chat_request(reasoning_effort=effort))

    assert capture.body is not None
    assert capture.body["think"] == expected


def test_ollama_puts_think_at_the_top_level_not_in_options(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    adapter.chat(chat_request(reasoning_effort="high"))

    assert capture.body is not None
    # 塞进 `options` 会被 Ollama 当成未知采样参数忽略，于是又变成一次静默失效。
    assert "think" not in capture.body.get("options", {})


def test_ollama_omits_think_when_unset(monkeypatch: pytest.MonkeyPatch):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    adapter.chat(chat_request())

    assert capture.body is not None
    # 不支持思考的模型收到 `think` 会报错；未配置时必须逐字节保持旧请求体。
    assert "think" not in capture.body


def test_ollama_keeps_existing_options_untouched(monkeypatch: pytest.MonkeyPatch):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    adapter.chat(
        chat_request(reasoning_effort="high", temperature=0.4, max_tokens=256)
    )

    assert capture.body is not None
    options = capture.body["options"]
    # 新字段不得挤掉既有采样参数。
    assert options["temperature"] == 0.4
    assert options["num_predict"] == 256


def test_ollama_stream_uses_the_same_effort_mapping(monkeypatch: pytest.MonkeyPatch):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    list(adapter.stream_chat(chat_request(reasoning_effort="max")))

    assert capture.body is not None
    # 两条路径给出不同强度是一个无法自查的差异：同一个 Agent 在 `off` 与
    # `aggregate` 两档下会得到不同质量的回答，而配置里看不出任何区别。
    assert capture.body["think"] == "high"


def test_ollama_stream_omits_think_when_unset(monkeypatch: pytest.MonkeyPatch):
    adapter = _adapter()
    capture = _Capture(OLLAMA_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.ollama_adapter.requests.post", capture
    )

    list(adapter.stream_chat(chat_request()))

    assert capture.body is not None
    assert "think" not in capture.body
