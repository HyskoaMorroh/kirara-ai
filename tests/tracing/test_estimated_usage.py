"""The tracing decorator must estimate usage rather than record a free request."""

from __future__ import annotations

import pytest

from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage, UsageSource
from kirara_ai.tracing.decorator import attach_estimated_usage, mark_provider_usage


def request() -> LLMChatRequest:
    return LLMChatRequest(
        model="model-a",
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="模拟退火算法")])
        ],
    )


def response(usage: Usage | None = None) -> LLMChatResponse:
    return LLMChatResponse(
        model="model-a",
        usage=usage,
        message=Message(role="assistant", content=[LLMChatTextContent(text="以下是实现")]),
    )


def test_a_missing_usage_is_filled_in_as_an_estimate():
    filled = attach_estimated_usage(request(), response())

    assert filled.usage is not None
    assert filled.usage.source is UsageSource.ESTIMATED
    assert filled.usage.total_tokens and filled.usage.total_tokens > 0


def test_provider_reported_usage_is_never_overwritten():
    provided = Usage(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    filled = attach_estimated_usage(request(), response(provided))

    assert filled.usage is not None
    assert filled.usage.prompt_tokens == 11
    assert filled.usage.completion_tokens == 7


def test_a_zero_valued_provider_usage_is_still_respected():
    """Zero from the provider is a measurement; it must not be replaced by a guess."""
    provided = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)

    filled = attach_estimated_usage(request(), response(provided))

    assert filled.usage is not None
    assert filled.usage.total_tokens == 0
    assert filled.usage.source is not UsageSource.ESTIMATED


def test_an_unmeasurable_response_keeps_usage_unknown():
    empty_request = LLMChatRequest(model="model-a", messages=[])
    empty_response = LLMChatResponse(
        model="model-a",
        message=Message(role="assistant", content=[]),
    )

    filled = attach_estimated_usage(empty_request, empty_response)

    # No evidence at all: staying None keeps the row marked unknown rather than free.
    assert filled.usage is None


def test_mark_provider_usage_still_promotes_unknown_to_provider():
    """上游回报过的用量不能停在 UNKNOWN——它会被读成「这条没有用量」。

    档位取决于**维度是否齐全**（见 `mark_provider_usage`）：这里只报了三维，
    缓存两维缺失，因此是 `PROVIDER_PARTIAL`。本用例钉住的是「不再是 UNKNOWN
    且被认定为上游回报」，而不是具体落在哪一档——后者由
    `tests/llm/test_usage_source_partial.py` 逐档覆盖。
    """
    reported = Usage(prompt_tokens=5, completion_tokens=5, total_tokens=10)
    assert reported.source is UsageSource.UNKNOWN

    marked = mark_provider_usage(response(reported))

    assert marked.usage is not None
    assert marked.usage.source in {
        UsageSource.PROVIDER,
        UsageSource.PROVIDER_PARTIAL,
    }
    assert marked.usage.source is UsageSource.PROVIDER_PARTIAL


def test_a_fully_reported_usage_is_provider_not_partial():
    """四维齐全时必须是 `PROVIDER`：那份账单可以直接采信。"""
    reported = Usage(
        prompt_tokens=5,
        completion_tokens=5,
        total_tokens=10,
        cached_tokens=0,
        cache_write_tokens=0,
    )

    marked = mark_provider_usage(response(reported))

    assert marked.usage is not None
    assert marked.usage.source is UsageSource.PROVIDER


def test_mark_provider_usage_does_not_relabel_an_estimate():
    estimated = Usage(prompt_tokens=5, total_tokens=5, source=UsageSource.ESTIMATED)

    marked = mark_provider_usage(response(estimated))

    assert marked.usage is not None
    assert marked.usage.source is UsageSource.ESTIMATED


def test_the_original_response_is_not_mutated():
    original = response()

    attach_estimated_usage(request(), original)

    assert original.usage is None
