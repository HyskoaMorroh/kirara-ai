"""Local token estimation for responses whose provider reports no usage.

Two facts drive the design:

1. Character count is not a token count. Using it directly (as many quick
   implementations do) overstates English by ~4x and understates CJK, so a
   "cost" derived from it is not merely imprecise — it is wrong in a direction
   that depends on the language.
2. An estimate must never be mistaken for a measurement. Anything produced here
   is stamped ``UsageSource.ESTIMATED``, so statistics can separate it from
   provider-reported usage, and a request with no usable evidence stays
   ``UNKNOWN`` rather than being given a fabricated number.

The estimator is deliberately dependency-free: pulling in a provider-specific
tokenizer would tie token accounting to one vendor's vocabulary and still be
wrong for every other model. What it does instead is count script-aware
character classes, which is stable across models and honest about being an
approximation.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from kirara_ai.llm.format.message import (
    LLMChatImageContent,
    LLMChatMessage,
    LLMChatTextContent,
)
from kirara_ai.llm.format.response import Usage, UsageSource

#: CJK 与全角字符：主流 BPE 词表里通常 1 字符 ≈ 1 token，偶尔 2。
_CJK_PATTERN = re.compile(
    r"[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]"
)
#: 拉丁字母/数字连续段：按约 4 个字符 1 token 估算。
_WORDISH_PATTERN = re.compile(r"[A-Za-z0-9]+")

#: 每个非空白、非 CJK、非字母数字字符（标点、符号）大致各占 1 token。
_PUNCT_TOKEN_WEIGHT = 1.0
#: 拉丁字符与 token 的经验比值。
_CHARS_PER_LATIN_TOKEN = 4.0
#: 每条消息的固定结构开销（role、分隔符等）。
_PER_MESSAGE_OVERHEAD = 4
#: 图片按内容块计一个保守的固定值；真实值随分辨率与供应商差异极大，
#: 因此这里只保证「不是 0」，并由 ESTIMATED 标记提醒它不可当账单依据。
_IMAGE_TOKEN_ESTIMATE = 85


def estimate_text_tokens(text: str) -> int:
    """Estimate the token count of one string.

    脚本感知的近似：CJK 按字符计，拉丁按约 4 字符 1 token，标点各计 1。
    这不是任何具体词表的精确复现，也不假装是——它的用途是让「没有 usage 的请求」
    有一个数量级正确、且被明确标记为估算的数字。
    """
    if not text:
        return 0

    cjk_count = len(_CJK_PATTERN.findall(text))
    latin_chars = sum(len(match) for match in _WORDISH_PATTERN.findall(text))
    other = 0
    for char in text:
        if char.isspace():
            continue
        if _CJK_PATTERN.match(char):
            continue
        if char.isalnum() and char.isascii():
            continue
        other += 1

    tokens = cjk_count + latin_chars / _CHARS_PER_LATIN_TOKEN + other * _PUNCT_TOKEN_WEIGHT
    # 非空输入至少 1 token：返回 0 会被下游当成「没有内容」。
    return max(1, int(round(tokens)))


def estimate_message_tokens(messages: Iterable[LLMChatMessage]) -> int:
    """Estimate the token count of a message list, including per-message overhead.

    没有任何可测内容的消息计 0，而不是只计结构开销：否则「空响应」会拿到一个
    非零 token 数，进而被当成一次有内容的请求计费。
    """
    total = 0
    for message in messages or ():
        content = getattr(message, "content", None)
        if isinstance(content, str):
            body = estimate_text_tokens(content)
        else:
            body = 0
            for part in content or ():
                if isinstance(part, LLMChatTextContent):
                    body += estimate_text_tokens(part.text)
                elif isinstance(part, LLMChatImageContent):
                    body += _IMAGE_TOKEN_ESTIMATE
                else:
                    text = getattr(part, "text", None)
                    if isinstance(text, str):
                        body += estimate_text_tokens(text)
        if body <= 0:
            continue
        total += _PER_MESSAGE_OVERHEAD + body
    return total


def estimate_usage(
    *,
    request_messages: Optional[Iterable[LLMChatMessage]],
    response_message: Optional[LLMChatMessage],
) -> Optional[Usage]:
    """Build an ``ESTIMATED`` usage, or ``None`` when there is nothing to measure.

    返回 ``None``（而不是零值 Usage）是有意的：没有任何可测内容时，
    「未知」是唯一诚实的答案，硬造一个 0 会让统计把它当成一次免费请求。
    """
    prompt_tokens = (
        estimate_message_tokens(request_messages) if request_messages else 0
    )
    completion_tokens = (
        estimate_message_tokens([response_message]) if response_message is not None else 0
    )
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return None
    return Usage(
        prompt_tokens=prompt_tokens or None,
        completion_tokens=completion_tokens or None,
        total_tokens=(prompt_tokens + completion_tokens) or None,
        source=UsageSource.ESTIMATED,
    )
