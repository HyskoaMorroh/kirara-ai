"""需求 22.1：重试次数与故障转移次数是**两项**，不能合成一个数。

需求把「重试/故障转移次数」列为要记录的项。当前只有一个聚合的 `attempt_count`
（`len(event.attempts)`），于是这两种情况在统计上完全一样：

- 同一个供应商重试 3 次 → `attempt_count = 3`
- 切换了 3 个供应商各试 1 次 → `attempt_count = 3`

而它们的处置完全相反：前者是这家上游慢或抖，该调超时与退避；后者是这家上游
不可用，该查供应商健康与熔断。给出一个分不开的数字，等于把「该查什么」这个
判断留给读者去猜——而他手上没有能猜对的信息。

`attempts_json` 里其实有足够信息（每条 attempt 都带 `provider` 与 `retry_index`），
只是没有任何地方把它拆开。这些用例要求落库时就拆成两列。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestStartEvent
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.resilience import ProviderAttempt
from kirara_ai.tracing.models import LLMRequestTrace


def _attempt(
    provider: str,
    *,
    attempt: int,
    retry_index: int,
    success: bool = False,
) -> ProviderAttempt:
    return ProviderAttempt(
        trace_id="t",
        model="m",
        provider=provider,
        attempt=attempt,
        retry_index=retry_index,
        success=success,
        started_at=0.0,
        completed_at=1.0,
    )


def _trace(attempts: list[ProviderAttempt]) -> LLMRequestTrace:
    record = LLMRequestTrace()
    record.update_from_event(
        LLMRequestStartEvent(
            trace_id="t",
            model_id="m",
            backend_name="b",
            request=LLMChatRequest(model="m", messages=[]),
        )
    )
    record.update_from_event(
        LLMRequestCompleteEvent(
            trace_id="t",
            model_id="m",
            backend_name="b",
            request=LLMChatRequest(model="m", messages=[]),
            response=LLMChatResponse(
                model="m",
                message=Message(role="assistant", content=[LLMChatTextContent(text="x")]),
            ),
            start_time=datetime.now(timezone.utc).timestamp(),
            attempts=attempts,
        )
    )
    return record


def test_retries_on_one_provider_are_counted_as_retries():
    """同一家重试 3 次：重试 2 次、故障转移 0 次。

    `attempt_count` 是 3，但其中只有第一次是「首次尝试」——
    重试次数是 3-1=2，而供应商始终只有一个，没有发生转移。
    """
    record = _trace(
        [
            _attempt("openai", attempt=1, retry_index=0),
            _attempt("openai", attempt=2, retry_index=1),
            _attempt("openai", attempt=3, retry_index=2, success=True),
        ]
    )

    assert record.attempt_count == 3
    assert record.retry_count == 2
    assert record.failover_count == 0


def test_switching_providers_is_counted_as_failover():
    """切换 3 家各试 1 次：重试 0 次、故障转移 2 次。

    与上一个用例的 `attempt_count` 完全相同（都是 3），但处置相反：
    这里要查供应商健康，上面要调超时。
    """
    record = _trace(
        [
            _attempt("openai", attempt=1, retry_index=0),
            _attempt("claude", attempt=2, retry_index=0),
            _attempt("gemini", attempt=3, retry_index=0, success=True),
        ]
    )

    assert record.attempt_count == 3
    assert record.retry_count == 0
    assert record.failover_count == 2


def test_a_mixed_sequence_splits_both_counts():
    """既重试又转移：两个数各算各的。"""
    record = _trace(
        [
            _attempt("openai", attempt=1, retry_index=0),
            _attempt("openai", attempt=2, retry_index=1),
            _attempt("claude", attempt=3, retry_index=0),
            _attempt("claude", attempt=4, retry_index=1, success=True),
        ]
    )

    assert record.attempt_count == 4
    # 两家各重试一次。
    assert record.retry_count == 2
    # 只换过一次供应商。
    assert record.failover_count == 1


def test_a_single_successful_attempt_has_neither():
    record = _trace([_attempt("openai", attempt=1, retry_index=0, success=True)])

    assert record.attempt_count == 1
    assert record.retry_count == 0
    assert record.failover_count == 0


def test_no_attempts_leaves_both_counts_null():
    """没有 attempt 数据时给 NULL，不给 0。

    `retry_count: 0` 是一个论断（「没重试过」）。旧版本记录、第三方调用方或
    未走故障转移路径的请求都可能没有 attempt 列表，那时「不知道」才是真话。
    """
    record = _trace([])

    assert record.retry_count is None
    assert record.failover_count is None


def test_the_same_provider_returning_later_still_counts_as_failover():
    """A → B → A 是两次转移，不是零次。

    只比较「用过几个不同供应商」会把这个序列算成 1 次转移（去重后 2 家 -1），
    但实际发生了两次切换，每次都付了一遍连接与首字节成本。
    """
    record = _trace(
        [
            _attempt("openai", attempt=1, retry_index=0),
            _attempt("claude", attempt=2, retry_index=0),
            _attempt("openai", attempt=3, retry_index=0, success=True),
        ]
    )

    assert record.failover_count == 2
    assert record.retry_count == 0


def test_both_counts_appear_in_the_serialized_record():
    record = _trace(
        [
            _attempt("openai", attempt=1, retry_index=0),
            _attempt("claude", attempt=2, retry_index=0, success=True),
        ]
    )

    payload = record.to_dict()

    assert payload["retry_count"] == 0
    assert payload["failover_count"] == 1
