"""整流器必须真的接在请求链路上（需求 8）。

`tests/llm/test_rectifier.py` 只证明那些函数会算出正确的改写。函数正确而链路
不调用，正是本轮反复在修的那类缺陷——有定义、有测试、主链路零调用
（`UsageSource.ESTIMATED` 曾经就是这样，表现为一批「0 token、0 成本」的假免费请求）。

因此这里从**适配器**这一层验证：让上游第一次返回参数约束错误，
断言适配器改一处之后重试并成功，且重试请求体里改的正是那一处。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.rectifier import (
    RECTIFIED_MAX_TOKENS,
    RECTIFIED_THINKING_BUDGET,
    UNSUPPORTED_IMAGE_PLACEHOLDER,
)
from kirara_ai.plugins.llm_preset_adapters.claude_adapter import ClaudeAdapter, ClaudeConfig

BUDGET_ERROR_BODY = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": (
                "max_tokens must be greater than thinking.budget_tokens; "
                "budget_tokens must be at least 1024"
            ),
        },
    }
)

SIGNATURE_ERROR_BODY = json.dumps(
    {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Invalid 'signature' in 'thinking' block at messages.1.content.0",
        },
    }
)

RATE_LIMIT_BODY = json.dumps(
    {"type": "error", "error": {"type": "rate_limit_error", "message": "rate limited"}}
)

SUCCESS_BODY = {
    "content": [{"type": "text", "text": "ok"}],
    "role": "assistant",
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 3},
}


class _Response:
    """`requests` 响应替身。"""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers: dict[str, str] = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} Client Error", response=self)

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    def close(self):
        return None


class _Transport:
    """记录每一次 POST 的请求体，按脚本返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies: list[dict] = []

    def __call__(self, *_args, **kwargs):
        self.bodies.append(kwargs.get("json"))
        return self.responses.pop(0)


def _adapter(monkeypatch, transport) -> ClaudeAdapter:
    adapter = ClaudeAdapter(
        ClaudeConfig(api_key="k", api_base="https://example.invalid/v1")
    )
    adapter.media_manager = MagicMock()
    # `@trace_llm_chat` 会读 `self.tracer` 与 `self.backend_name`；
    # 这两个字段由容器注入，单测里要自己给。
    adapter.tracer = MagicMock()
    adapter.backend_name = "claude"
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter.requests.post", transport
    )
    return adapter


def _request(**overrides) -> LLMChatRequest:
    payload = {
        "messages": [
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hello")])
        ],
        "model": "claude-sonnet-5",
        "max_tokens": 2000,
    }
    payload.update(overrides)
    return LLMChatRequest(**payload)


def test_a_budget_violation_is_rectified_and_retried(monkeypatch):
    """第一次失败在参数上，改一处就能成功——不该让用户看到一次硬失败。"""
    transport = _Transport(
        [_Response(400, BUDGET_ERROR_BODY), _Response(200, SUCCESS_BODY)]
    )
    adapter = _adapter(monkeypatch, transport)

    response = adapter.chat(_request(reasoning_effort="max"))

    assert response.message.content[0].text == "ok"
    assert len(transport.bodies) == 2, "没有重试，整流器没有接上"
    retried = transport.bodies[1]
    assert retried["thinking"]["budget_tokens"] == RECTIFIED_THINKING_BUDGET
    assert retried["max_tokens"] == RECTIFIED_MAX_TOKENS
    # 第一次请求保持原样：整流不能就地改掉调用方的请求体。
    assert transport.bodies[0]["max_tokens"] == 2000


def test_an_unrelated_error_is_raised_without_retrying(monkeypatch):
    """限流不是参数问题。对它整流只会掩盖真正的原因并多花一次配额。"""
    transport = _Transport([_Response(429, RATE_LIMIT_BODY)])
    adapter = _adapter(monkeypatch, transport)

    with pytest.raises(Exception):
        adapter.chat(_request())

    assert len(transport.bodies) == 1


def test_the_same_violation_is_only_rectified_once(monkeypatch):
    """改完仍失败就抛原始错误，不能一直转。"""
    transport = _Transport(
        [_Response(400, BUDGET_ERROR_BODY), _Response(400, BUDGET_ERROR_BODY)]
    )
    adapter = _adapter(monkeypatch, transport)

    with pytest.raises(Exception):
        adapter.chat(_request(reasoning_effort="max"))

    assert len(transport.bodies) == 2, "应当只重试一次"


def test_a_stale_thinking_signature_is_removed_and_retried(monkeypatch):
    """换模型或换供应商之后，上一轮回传的思考签名不再有效。"""
    transport = _Transport(
        [_Response(400, SIGNATURE_ERROR_BODY), _Response(200, SUCCESS_BODY)]
    )
    adapter = _adapter(monkeypatch, transport)

    def fake_convert(_messages, _media_manager):
        async def _inner():
            return [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "...", "signature": "stale"},
                        {"type": "text", "text": "previous answer"},
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": "follow up"}]},
            ]

        return _inner()

    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter."
        "convert_llm_chat_message_to_claude_message",
        fake_convert,
    )

    response = adapter.chat(_request())

    assert response.message.content[0].text == "ok"
    assert len(transport.bodies) == 2
    retried_blocks = transport.bodies[1]["messages"][0]["content"]
    assert all(block.get("type") != "thinking" for block in retried_blocks)
    # 正文保留：删它等于把一次参数修复变成一次内容删改。
    assert any(block.get("text") == "previous answer" for block in retried_blocks)


def test_an_image_rejection_falls_back_to_a_visible_placeholder(monkeypatch):
    """不支持图片的模型收到图片会拒绝整个请求；降级让对话不中断。"""
    transport = _Transport(
        [
            _Response(400, json.dumps({"error": {"message": "model does not support image input"}})),
            _Response(200, SUCCESS_BODY),
        ]
    )
    adapter = _adapter(monkeypatch, transport)

    def fake_convert(_messages, _media_manager):
        async def _inner():
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {"type": "image", "source": {"data": "..."}},
                    ],
                }
            ]

        return _inner()

    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter."
        "convert_llm_chat_message_to_claude_message",
        fake_convert,
    )

    response = adapter.chat(_request())

    assert response.message.content[0].text == "ok"
    retried = transport.bodies[1]["messages"][0]["content"]
    # 占位可见：用户问「这张图里是什么」时模型能说没收到图，而不是编一个答案。
    assert any(block.get("text") == UNSUPPORTED_IMAGE_PLACEHOLDER for block in retried)


class _StreamResponse(_Response):
    """流式响应替身：支持 `with` 与 `iter_lines`。"""

    def __init__(self, status_code: int, body, lines=()):
        super().__init__(status_code, body)
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line


_STREAM_OK_LINES = [
    'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}',
    'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}',
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":2}}',
    "data: [DONE]",
]


def test_the_streaming_path_rectifies_too(monkeypatch):
    """只修非流式是半个修复。

    `reply_stream_mode=aggregate` 的部署走的是 `stream_chat`；参数约束错误
    在两条路径上完全一样，只在一条上整流意味着同一个部署换个开关就又会硬失败。
    """
    transport = _Transport(
        [
            _StreamResponse(400, BUDGET_ERROR_BODY),
            _StreamResponse(200, SUCCESS_BODY, _STREAM_OK_LINES),
        ]
    )
    adapter = _adapter(monkeypatch, transport)

    chunks = list(adapter.stream_chat(_request(reasoning_effort="max")))

    assert chunks, "重试后没有产出任何分片"
    assert len(transport.bodies) == 2, "流式路径没有整流重试"
    retried = transport.bodies[1]
    assert retried["thinking"]["budget_tokens"] == RECTIFIED_THINKING_BUDGET
    assert retried["max_tokens"] == RECTIFIED_MAX_TOKENS


def test_the_streaming_path_raises_unrelated_errors_without_retrying(monkeypatch):
    transport = _Transport([_StreamResponse(429, RATE_LIMIT_BODY)])
    adapter = _adapter(monkeypatch, transport)

    with pytest.raises(Exception):
        list(adapter.stream_chat(_request()))

    assert len(transport.bodies) == 1
