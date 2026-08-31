"""成本汇总必须覆盖每一类供应商的 usage 形态。

`calculate_cost_snapshot` 曾要求四个计费维度**全部**可定价才产出 `total_cost`，
但 `cache_write_tokens` 只有 Claude 适配器会填：`openai_adapter`、`gemini_adapter`、
`ollama_adapter` 都不填，Gemini/Ollama 连 `cached_tokens` 也没有。于是

- OpenAI 形态：`total_cost` 恒为 `None`；
- Gemini/Ollama 形态：同样恒为 `None`。

`LLMRequestTrace.apply_cost_projection` 读不到 `total_cost` 就把 `total_cost` 与
`cost_currency` 两列都留 `NULL`，统计接口于是把这些请求全部算进
`unpriced_requests`，页面显示「合计 0」——而 Token 数看起来是对的。
用户配好了定价、跑了一万次请求，账单是零。

这一类缺陷最难自查：定价编辑、快照、历史不改写全部正确，只有「维度没上报」
这一条路径把结果清零，而 Claude 用户永远遇不到。

修法的边界很重要：**没上报**与**上报为零**必须继续可区分。快照里的
per-dimension `None` 保持原样，只有 `total_cost` 改成「对已上报的维度求和」；
四个维度都没上报时仍然是 `None`——那才是真的「没有定价证据」。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kirara_ai.llm.format.response import Usage, UsageSource
from kirara_ai.llm.pricing import PriceVersion, calculate_cost_snapshot


def price() -> PriceVersion:
    return PriceVersion(
        version_id="v1",
        provider="openai",
        model="gpt-4o",
        currency="USD",
        input_per_million=Decimal("2.5"),
        output_per_million=Decimal("10"),
        cache_read_per_million=Decimal("1.25"),
        cache_write_per_million=Decimal("3.75"),
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def snapshot(usage: Usage):
    return calculate_cost_snapshot(
        usage,
        price(),
        requested_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        provider="openai",
        model="gpt-4o",
    )


def test_openai_shaped_usage_produces_a_total_cost():
    """OpenAI 不上报任何缓存维度，成本仍然必须算得出来。"""
    result = snapshot(
        Usage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            source=UsageSource.PROVIDER,
        )
    )

    assert result.input_cost == Decimal("0.00250000")
    assert result.output_cost == Decimal("0.00500000")
    # 未上报的维度保持 None：那是「没这项数据」，不是「这项是 0」。
    assert result.cache_read_cost is None
    assert result.cache_write_cost is None
    # 合计只累加已上报的维度。
    assert result.total_cost == Decimal("0.00750000")


def test_usage_reporting_only_cache_reads_still_totals():
    """Gemini 只上报 cachedContentTokenCount，没有写入缓存这一项。"""
    result = snapshot(
        Usage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cached_tokens=200,
            source=UsageSource.PROVIDER,
        )
    )

    assert result.cache_read_cost == Decimal("0.00025000")
    assert result.cache_write_cost is None
    assert result.total_cost == (
        result.input_cost + result.output_cost + result.cache_read_cost
    )


def test_claude_shaped_usage_keeps_its_previous_total():
    """四个维度齐全时结果不变——这是修复前唯一能算出成本的形态。"""
    result = snapshot(
        Usage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cached_tokens=200,
            cache_write_tokens=100,
            source=UsageSource.PROVIDER,
        )
    )

    assert result.total_cost == Decimal("0.00737500")


def test_a_reported_zero_is_priced_as_zero_not_as_missing():
    """上报为 0 与没上报必须区分：前者算 0 元，后者不参与合计。"""
    result = snapshot(
        Usage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cached_tokens=0,
            source=UsageSource.PROVIDER,
        )
    )

    assert result.cache_read_cost == Decimal("0")
    assert result.cache_write_cost is None
    assert result.total_cost == result.input_cost + result.output_cost


def test_output_only_usage_still_totals():
    """只拿到输出 Token（少数流式实现）时也要有合计。"""
    result = snapshot(Usage(completion_tokens=500, source=UsageSource.PROVIDER))

    assert result.input_cost is None
    assert result.total_cost == Decimal("0.00500000")


@pytest.mark.parametrize(
    "usage",
    [
        Usage(source=UsageSource.UNKNOWN),
        Usage(total_tokens=1500, source=UsageSource.UNKNOWN),
    ],
)
def test_no_reported_dimension_stays_unpriced(usage: Usage):
    """一个计费维度都没有时仍然是 `None`——绝不能变成 0 元。"""
    result = snapshot(usage)

    assert result.total_cost is None
