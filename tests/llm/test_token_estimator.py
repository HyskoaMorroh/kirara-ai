"""An estimate must be usable and must never look like a measurement.

`UsageSource.ESTIMATED` existed in the enum but had no producer: a response whose
provider reported no usage was stored with no token counts at all and skipped for
costing, producing a row that reads as a free request. That is worse than an
approximation, because "0" is a claim and "unknown" is not.

These tests pin two things: the estimate is script-aware (character count alone
overstates English ~4x and understates CJK), and nothing here can ever be
mistaken for provider-reported usage.
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.format.message import (
    LLMChatImageContent,
    LLMChatMessage,
    LLMChatTextContent,
)
from kirara_ai.llm.format.response import UsageSource
from kirara_ai.llm.token_estimator import (
    estimate_message_tokens,
    estimate_text_tokens,
    estimate_usage,
)


def text_message(role: str, text: str) -> LLMChatMessage:
    return LLMChatMessage(role=role, content=[LLMChatTextContent(text=text)])


def test_empty_text_is_zero_tokens():
    assert estimate_text_tokens("") == 0


def test_any_non_empty_text_is_at_least_one_token():
    assert estimate_text_tokens("a") >= 1
    assert estimate_text_tokens(".") >= 1


def test_latin_text_is_not_counted_one_token_per_character():
    text = "the quick brown fox jumps over the lazy dog"

    tokens = estimate_text_tokens(text)

    # Naive character counting would give 43 here; a real tokenizer lands near 9.
    assert tokens < len(text) / 2
    assert tokens >= 5


def test_cjk_text_is_counted_near_one_token_per_character():
    text = "模拟退火算法"

    tokens = estimate_text_tokens(text)

    assert tokens >= len(text)


def test_cjk_and_latin_are_weighted_differently():
    cjk = estimate_text_tokens("模拟退火算法演示")
    latin = estimate_text_tokens("simulated annealing demo")

    # Same rough information content, very different character counts; the
    # estimate must not simply mirror len().
    assert cjk > latin


def test_whitespace_alone_does_not_inflate_the_estimate():
    assert estimate_text_tokens("   \n\t  ") <= 1


def test_message_estimate_includes_per_message_overhead():
    single = estimate_message_tokens([text_message("user", "hi")])
    double = estimate_message_tokens(
        [text_message("user", "hi"), text_message("assistant", "hi")]
    )

    assert double > single * 1.5


def test_an_image_content_block_is_not_free():
    with_image = estimate_message_tokens(
        [
            LLMChatMessage(
                role="user",
                content=[
                    LLMChatTextContent(text="看图"),
                    LLMChatImageContent(media_id="media-1"),
                ],
            )
        ]
    )
    without_image = estimate_message_tokens([text_message("user", "看图")])

    assert with_image > without_image


def test_estimated_usage_is_always_labelled_as_an_estimate():
    usage = estimate_usage(
        request_messages=[text_message("user", "你好")],
        response_message=text_message("assistant", "你好"),
    )

    assert usage is not None
    assert usage.source is UsageSource.ESTIMATED
    # It must never claim to be provider-reported.
    assert usage.source is not UsageSource.PROVIDER


def test_estimated_usage_sums_prompt_and_completion():
    usage = estimate_usage(
        request_messages=[text_message("user", "hello world")],
        response_message=text_message("assistant", "hi"),
    )

    assert usage is not None
    assert usage.total_tokens == (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)


def test_nothing_measurable_yields_none_not_a_zero_usage():
    """A zero-valued Usage would be read as a free request; unknown must stay unknown."""
    assert estimate_usage(request_messages=[], response_message=None) is None
    assert estimate_usage(request_messages=None, response_message=None) is None


def test_a_response_only_estimate_still_works():
    usage = estimate_usage(
        request_messages=None,
        response_message=text_message("assistant", "结果如下"),
    )

    assert usage is not None
    assert usage.completion_tokens is not None
    assert usage.prompt_tokens is None


@pytest.mark.parametrize("text", ["x" * 4000, "中" * 2000])
def test_the_estimate_scales_without_overflow(text: str):
    tokens = estimate_text_tokens(text)

    assert tokens > 0
    assert tokens <= len(text) + 1
