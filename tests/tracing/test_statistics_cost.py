"""Statistics must aggregate cost and the dimensions the schema already indexes.

`get_statistics` returned request counts, tokens and average duration only. The
trace table already stores `cost_snapshot_json`, `error_category`, `ttft_ms` and
`attempt_count` — and carries indexes on two of them — but nothing summed spend
or grouped by failure category, so "where did the money go" and "what is failing"
had no answer even though the data was there.

`UsageSource.ESTIMATED` also had no producer: a response without provider usage
was stored with no token counts and skipped for costing, producing a row that
looks free rather than one marked unknown.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.tracing import LLMTracer
from kirara_ai.tracing.models import LLMRequestTrace


@pytest.fixture()
def tracer(tmp_path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.system.timezone = "UTC"
    container.register(GlobalConfig, config)
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'trace.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    container.register(EventBus, EventBus())
    instance = LLMTracer(container)
    instance.initialize()
    return instance


def add_trace(
    tracer: LLMTracer,
    *,
    trace_id: str,
    provider: str = "provider-a",
    model_id: str = "model-a",
    status: str = "success",
    total_tokens: int = 100,
    cost: str | None = "0.5",
    usage_source: str = "provider",
    error_category: str | None = None,
    ttft_ms: int | None = 120,
    attempt_count: int = 1,
    minutes_ago: int = 5,
) -> None:
    request_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    with tracer.db_manager.get_session() as session:
        row = LLMRequestTrace(
            trace_id=trace_id,
            model_id=model_id,
            backend_name=provider,
            provider=provider,
            request_time=request_time,
            response_time=request_time + timedelta(seconds=1),
            duration=1.0,
            prompt_tokens=total_tokens // 2,
            completion_tokens=total_tokens - total_tokens // 2,
            total_tokens=total_tokens,
            usage_source=usage_source,
            ttft_ms=ttft_ms,
            attempt_count=attempt_count,
            status=status,
            error_category=error_category,
        )
        if cost is not None:
            row.cost_snapshot_json = (
                '{"currency": "USD", "total_cost": "%s", "price_version_id": "v1"}' % cost
            )
        session.add(row)
        session.commit()


def test_overview_reports_total_cost_and_its_currency(tracer: LLMTracer):
    add_trace(tracer, trace_id="t1", cost="0.5")
    add_trace(tracer, trace_id="t2", cost="1.25")

    stats = tracer.get_statistics()

    assert Decimal(stats["overview"]["total_cost"]) == Decimal("1.75")
    assert stats["overview"]["cost_currency"] == "USD"


def test_a_request_without_a_price_snapshot_is_counted_as_unpriced(tracer: LLMTracer):
    add_trace(tracer, trace_id="priced", cost="0.5")
    add_trace(tracer, trace_id="unpriced", cost=None)

    stats = tracer.get_statistics()

    # The unpriced request must be visible as such, not silently treated as free.
    assert stats["overview"]["unpriced_requests"] == 1
    assert Decimal(stats["overview"]["total_cost"]) == Decimal("0.5")


def test_provider_groups_carry_their_own_cost(tracer: LLMTracer):
    add_trace(tracer, trace_id="a1", provider="provider-a", cost="0.5")
    add_trace(tracer, trace_id="b1", provider="provider-b", cost="2.0")

    stats = tracer.get_statistics()
    by_provider = {row["provider"]: row for row in stats["providers"]}

    assert Decimal(by_provider["provider-b"]["cost"]) == Decimal("2.0")
    assert Decimal(by_provider["provider-a"]["cost"]) == Decimal("0.5")


def test_model_groups_carry_their_own_cost(tracer: LLMTracer):
    add_trace(tracer, trace_id="m1", model_id="model-a", cost="0.25")
    add_trace(tracer, trace_id="m2", model_id="model-b", cost="0.75")

    stats = tracer.get_statistics()
    by_model = {row["model_id"]: row for row in stats["models"]}

    assert Decimal(by_model["model-b"]["cost"]) == Decimal("0.75")


def test_error_categories_are_grouped(tracer: LLMTracer):
    add_trace(tracer, trace_id="e1", status="failed", error_category="rate_limit", cost=None)
    add_trace(tracer, trace_id="e2", status="failed", error_category="rate_limit", cost=None)
    add_trace(tracer, trace_id="e3", status="failed", error_category="authentication", cost=None)

    stats = tracer.get_statistics()
    by_category = {row["error_category"]: row["count"] for row in stats["error_categories"]}

    assert by_category["rate_limit"] == 2
    assert by_category["authentication"] == 1


def test_latency_summary_reports_ttft_and_attempts(tracer: LLMTracer):
    add_trace(tracer, trace_id="l1", ttft_ms=100, attempt_count=1)
    add_trace(tracer, trace_id="l2", ttft_ms=300, attempt_count=3)

    stats = tracer.get_statistics()

    assert stats["latency"]["avg_ttft_ms"] == pytest.approx(200.0)
    assert stats["latency"]["max_ttft_ms"] == 300
    assert stats["latency"]["avg_attempt_count"] == pytest.approx(2.0)


def test_latency_summary_omits_ttft_when_nothing_recorded_it(tracer: LLMTracer):
    add_trace(tracer, trace_id="n1", ttft_ms=None)

    stats = tracer.get_statistics()

    # A non-stream request has no real first byte; reporting 0 would be a lie.
    assert stats["latency"]["avg_ttft_ms"] is None


def test_usage_sources_are_grouped_so_estimates_are_visible(tracer: LLMTracer):
    add_trace(tracer, trace_id="u1", usage_source="provider")
    add_trace(tracer, trace_id="u2", usage_source="estimated")
    add_trace(tracer, trace_id="u3", usage_source="unknown")

    stats = tracer.get_statistics()
    sources = {row["usage_source"]: row["count"] for row in stats["usage_sources"]}

    assert sources == {"provider": 1, "estimated": 1, "unknown": 1}


def test_cost_filters_follow_the_same_filter_contract(tracer: LLMTracer):
    add_trace(tracer, trace_id="f1", provider="provider-a", cost="1.0")
    add_trace(tracer, trace_id="f2", provider="provider-b", cost="4.0")

    stats = tracer.get_statistics(filters={"provider": "provider-b"})

    assert Decimal(stats["overview"]["total_cost"]) == Decimal("4.0")
    assert stats["overview"]["total_requests"] == 1


def test_statistics_on_an_empty_database_reports_zero_not_none(tracer: LLMTracer):
    stats = tracer.get_statistics()

    assert stats["overview"]["total_requests"] == 0
    assert Decimal(stats["overview"]["total_cost"]) == Decimal("0")
    assert stats["error_categories"] == []
