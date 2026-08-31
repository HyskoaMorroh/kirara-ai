"""上游限额余量：把「离上限还有多远」变成可见的（需求 9）。

桌面端参考实现的额度面板回答的是「这个上游还剩多少可用」，
带进度条与重置倒计时。它读的是各家订阅/计划的专有接口，本项目不能照搬那些接口。

但同一个用户意图在本项目里有确切落点：**上游在每个响应里就带着限额余量**
（`x-ratelimit-remaining-requests` / `-tokens`、`-reset`、`retry-after`）。
此前这些响应头被完整丢弃——适配器里 `response.headers` 零次读取。

丢掉它们的后果不是少一个图表，而是**限流只能事后发现**：请求开始报 429 才知道
撞了上限，而那时排队与重试已经在发生。余量是唯一能在撞上之前给出信号的东西。

三条边界写进实现：

* **缺头就是 `None`，不是 0。** 很多兼容端点根本不返回这些头。0 表示「余量用完」，
  是最该报警的状态；把「没上报」显示成 0 会造出一个不存在的紧急情况。
* **不猜上限。** 只有 remaining 而没有 limit 时不去反推百分比——百分比需要分母，
  编一个分母出来会得到一个看起来精确的错数字。
* **各供应商独立。** 限额是按 key/账户算的，不同供应商之间没有可比性，
  也不能相加。
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.rate_limit import (
    RateLimitSnapshot,
    parse_rate_limit_headers,
)


class TestParsing:
    def test_openai_style_headers_are_read(self):
        snapshot = parse_rate_limit_headers(
            {
                "x-ratelimit-limit-requests": "5000",
                "x-ratelimit-remaining-requests": "4987",
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3912004",
                "x-ratelimit-reset-requests": "12s",
            }
        )

        assert snapshot is not None
        assert snapshot.limit_requests == 5000
        assert snapshot.remaining_requests == 4987
        assert snapshot.limit_tokens == 4000000
        assert snapshot.remaining_tokens == 3912004
        assert snapshot.reset_requests_seconds == pytest.approx(12.0)

    def test_anthropic_style_headers_are_read(self):
        """Anthropic 用 RFC3339 时刻而不是相对秒数表达重置时间。"""
        snapshot = parse_rate_limit_headers(
            {
                "anthropic-ratelimit-requests-limit": "1000",
                "anthropic-ratelimit-requests-remaining": "999",
                "anthropic-ratelimit-tokens-limit": "80000",
                "anthropic-ratelimit-tokens-remaining": "79000",
            }
        )

        assert snapshot is not None
        assert snapshot.limit_requests == 1000
        assert snapshot.remaining_requests == 999
        assert snapshot.remaining_tokens == 79000

    def test_header_lookup_is_case_insensitive(self):
        """HTTP 头大小写不敏感，而 `dict` 是敏感的。"""
        snapshot = parse_rate_limit_headers(
            {"X-RateLimit-Remaining-Requests": "42"}
        )

        assert snapshot is not None
        assert snapshot.remaining_requests == 42

    def test_a_response_without_any_of_these_headers_yields_none(self):
        """很多兼容端点根本不返回限额头。返回一个全 None 的快照
        会让界面显示一堆「0」，而那是「余量用完」这个最该报警的状态。"""
        assert parse_rate_limit_headers({"content-type": "application/json"}) is None
        assert parse_rate_limit_headers({}) is None
        assert parse_rate_limit_headers(None) is None

    def test_garbage_values_are_ignored_rather_than_crashing(self):
        """限额头是上游给的，不能假定它可解析。一个解析异常会让整条本已成功的
        请求失败——那比少一个数字严重得多。"""
        snapshot = parse_rate_limit_headers(
            {
                "x-ratelimit-remaining-requests": "not-a-number",
                "x-ratelimit-remaining-tokens": "77",
            }
        )

        assert snapshot is not None
        assert snapshot.remaining_requests is None
        assert snapshot.remaining_tokens == 77

    def test_retry_after_seconds_is_read(self):
        snapshot = parse_rate_limit_headers({"retry-after": "30"})

        assert snapshot is not None
        assert snapshot.retry_after_seconds == pytest.approx(30.0)

    def test_various_reset_formats(self):
        assert parse_rate_limit_headers(
            {"x-ratelimit-reset-tokens": "1.5s"}
        ).reset_tokens_seconds == pytest.approx(1.5)
        assert parse_rate_limit_headers(
            {"x-ratelimit-reset-tokens": "6m0s"}
        ).reset_tokens_seconds == pytest.approx(360.0)
        assert parse_rate_limit_headers(
            {"x-ratelimit-reset-tokens": "2h"}
        ).reset_tokens_seconds == pytest.approx(7200.0)
        assert parse_rate_limit_headers(
            {"x-ratelimit-reset-tokens": "250ms"}
        ).reset_tokens_seconds == pytest.approx(0.25)


class TestHeadroom:
    def test_headroom_needs_both_limit_and_remaining(self):
        """只有 remaining 时不反推百分比：百分比需要分母，
        编一个分母会得到一个看起来精确的错数字。"""
        assert RateLimitSnapshot(remaining_requests=10).request_headroom is None
        assert RateLimitSnapshot(limit_requests=100).request_headroom is None
        assert RateLimitSnapshot(
            limit_requests=100, remaining_requests=25
        ).request_headroom == pytest.approx(0.25)

    def test_a_zero_limit_does_not_divide_by_zero(self):
        assert (
            RateLimitSnapshot(limit_requests=0, remaining_requests=0).request_headroom
            is None
        )

    def test_token_headroom_is_separate_from_request_headroom(self):
        """两者会分别见底，且处置不同：请求数见底要降频，
        Token 见底要缩短上下文。合成一个数就分不开。"""
        snapshot = RateLimitSnapshot(
            limit_requests=100,
            remaining_requests=99,
            limit_tokens=1000,
            remaining_tokens=20,
        )

        assert snapshot.request_headroom == pytest.approx(0.99)
        assert snapshot.token_headroom == pytest.approx(0.02)

    def test_the_snapshot_serializes_only_what_it_knows(self):
        payload = RateLimitSnapshot(remaining_requests=7).to_dict()

        assert payload["remaining_requests"] == 7
        assert payload["limit_requests"] is None
        # 派生值也必须是 None，而不是 0——0 会被读成「已用尽」。
        assert payload["request_headroom"] is None

    def test_an_empty_snapshot_is_falsy(self):
        """全 None 的快照不该被当成「上游报了限额」。"""
        assert not RateLimitSnapshot()
        assert RateLimitSnapshot(remaining_requests=0)
