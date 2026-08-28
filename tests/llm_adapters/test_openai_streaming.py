"""The OpenAI-compatible adapter must actually implement streaming.

`LLMChatStreamProtocol` had no implementer, so `LLMManager.execute_stream` was
only ever reached from tests and "streaming vs non-streaming reply mode" was not a
choice a user could make. These tests pin the SSE parsing contract, including the
parts that are easy to get wrong: a bad frame must not kill the stream, heartbeat
frames must not surface as empty replies, and usage must stay `None` when the
upstream does not report it rather than being fabricated here.
"""

from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.adapter import LLMChatStreamProtocol
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import (
    OpenAIAdapter,
    OpenAIConfig,
)


def make_adapter(lines: list[str], *, status_error: Exception | None = None) -> OpenAIAdapter:
    adapter = OpenAIAdapter(OpenAIConfig(api_key="test-key"))
    adapter.media_manager = MagicMock()

    response = MagicMock()
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.iter_lines = MagicMock(return_value=iter(lines))
    response.text = "error body"
    if status_error is not None:
        response.raise_for_status = MagicMock(side_effect=status_error)
    else:
        response.raise_for_status = MagicMock()

    adapter._session = MagicMock()
    adapter._session.post = MagicMock(return_value=response)
    return adapter


def request() -> LLMChatRequest:
    return LLMChatRequest(
        model="gpt-test",
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
    )


def collect(adapter: OpenAIAdapter) -> list[str]:
    texts: list[str] = []
    for chunk in adapter.stream_chat(request()):
        for part in chunk.message.content:
            if isinstance(part, LLMChatTextContent):
                texts.append(part.text)
    return texts


def frame(content: str) -> str:
    return 'data: {"choices":[{"delta":{"content":"%s"}}]}' % content


def test_the_adapter_declares_the_stream_protocol():
    adapter = make_adapter([])

    assert isinstance(adapter, LLMChatStreamProtocol)


def test_deltas_are_yielded_in_order():
    adapter = make_adapter([frame("Hello"), frame(" "), frame("world"), "data: [DONE]"])

    assert collect(adapter) == ["Hello", " ", "world"]


def test_the_request_forces_stream_true():
    adapter = make_adapter([frame("x"), "data: [DONE]"])

    list(adapter.stream_chat(request()))

    _, kwargs = adapter._session.post.call_args
    assert kwargs["json"]["stream"] is True
    assert kwargs["stream"] is True


def test_usage_is_reported_when_the_upstream_sends_it():
    adapter = make_adapter(
        [
            frame("hi"),
            'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
            "data: [DONE]",
        ]
    )

    chunks = list(adapter.stream_chat(request()))

    usages = [chunk.usage for chunk in chunks if chunk.usage is not None]
    assert len(usages) == 1
    assert usages[0].total_tokens == 5


def test_usage_stays_none_when_the_upstream_omits_it():
    """Fabricating a number here would make an estimate look like a measurement."""
    adapter = make_adapter([frame("hi"), "data: [DONE]"])

    chunks = list(adapter.stream_chat(request()))

    assert all(chunk.usage is None for chunk in chunks)


def test_a_single_unparsable_frame_does_not_kill_the_stream():
    adapter = make_adapter([frame("a"), "data: {not json", frame("b"), "data: [DONE]"])

    assert collect(adapter) == ["a", "b"]


def test_heartbeat_and_role_only_frames_are_skipped():
    adapter = make_adapter(
        [
            "",
            ": keepalive",
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            frame("real"),
            "data: [DONE]",
        ]
    )

    assert collect(adapter) == ["real"]


def test_done_terminates_the_stream():
    adapter = make_adapter([frame("a"), "data: [DONE]", frame("never")])

    assert collect(adapter) == ["a"]


def test_a_finish_reason_frame_is_delivered():
    adapter = make_adapter(
        ['data: {"choices":[{"delta":{},"finish_reason":"stop"}]}', "data: [DONE]"]
    )

    chunks = list(adapter.stream_chat(request()))

    assert chunks[-1].message.finish_reason == "stop"


def test_a_list_shaped_delta_is_flattened():
    adapter = make_adapter(
        ['data: {"choices":[{"delta":{"content":[{"text":"part"}]}}]}', "data: [DONE]"]
    )

    assert collect(adapter) == ["part"]


def test_an_http_error_propagates():
    adapter = make_adapter([], status_error=RuntimeError("HTTP 500"))

    with pytest.raises(RuntimeError, match="HTTP 500"):
        list(adapter.stream_chat(request()))


def test_a_business_error_frame_raises():
    adapter = make_adapter(
        ['data: {"error":{"message":"quota exceeded","type":"insufficient_quota"}}']
    )

    with pytest.raises(Exception):
        list(adapter.stream_chat(request()))
