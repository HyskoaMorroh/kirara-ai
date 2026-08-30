"""成本必须有趋势，而不只有一个 30 天总额（需求 9、22.2）。

22.2 要求「统计页面要支持趋势」，而当前只有请求数与 Token 有日/时分桶，
成本只在 `overview` 里给一个合计。于是这个问题没有出口：

**「这个月贵了三倍，是哪天开始的？」**

没有日成本曲线，只能把时间范围手工二分、反复改筛选条件重查——而账单异常
恰恰是最需要快速定位到某一天（换了模型、上了新流量、缓存失效）的场景。

两条必须守住的口径：

1. **不同货币不相加。** 与 `overview.cost_by_currency` 同理：每个分桶各自按币种
   分组给出，把两种货币加进同一个数字得到的是一个没有单位的数，而它不会报错。
2. **未定价请求单列。** 按 0 元并入当天合计会让那一天看起来更便宜，
   而「便宜了」与「有请求没匹配到价格版本」是两个完全不同的结论。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
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
    cost: str | None = "0.5",
    currency: str = "USD",
    days_ago: int = 0,
) -> None:
    request_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=days_ago, minutes=5
    )
    with tracer.db_manager.get_session() as session:
        row = LLMRequestTrace(
            trace_id=trace_id,
            model_id="model-a",
            backend_name="provider-a",
            provider="provider-a",
            request_time=request_time,
            response_time=request_time + timedelta(seconds=1),
            duration=1.0,
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            usage_source="provider",
            status="success",
        )
        if cost is not None:
            row.cost_snapshot_json = (
                '{"currency": "%s", "total_cost": "%s", "price_version_id": "v1"}'
                % (currency, cost)
            )
        session.add(row)
        session.commit()


def _bucket(daily: list[dict], date: str) -> dict:
    for row in daily:
        if row["date"] == date:
            return row
    raise AssertionError(f"没有 {date} 的分桶：{[row['date'] for row in daily]}")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


class TestDailyCostTrend:
    def test_daily_buckets_carry_cost(self, tracer: LLMTracer):
        add_trace(tracer, trace_id="t1", cost="0.5")
        add_trace(tracer, trace_id="t2", cost="1.25")

        daily = tracer.get_statistics()["daily_stats"]

        assert Decimal(_bucket(daily, _today())["cost"]) == Decimal("1.75")

    def test_a_spike_is_visible_as_a_different_day(self, tracer: LLMTracer):
        """这是这条曲线存在的理由：贵了三倍，要能看出是哪天开始的。"""
        add_trace(tracer, trace_id="cheap", cost="1.0", days_ago=1)
        add_trace(tracer, trace_id="spike-a", cost="3.0", days_ago=0)

        daily = tracer.get_statistics()["daily_stats"]

        assert Decimal(_bucket(daily, _yesterday())["cost"]) == Decimal("1.0")
        assert Decimal(_bucket(daily, _today())["cost"]) == Decimal("3.0")

    def test_currencies_are_not_added_together(self, tracer: LLMTracer):
        """两种货币相加得到的是一个没有单位的数字，而它不会报错。"""
        add_trace(tracer, trace_id="usd", cost="2.0", currency="USD")
        add_trace(tracer, trace_id="cny", cost="7.0", currency="CNY")

        bucket = _bucket(tracer.get_statistics()["daily_stats"], _today())

        # 按 Decimal 比较而不是字符串：金额列是定点 Numeric，`2.0` 读回来是
        # `2.00000000`。断言字符串形态等于把数据库的标度写进测试，
        # 那会在与本条要验证的「币种不相加」毫无关系的地方失败。
        assert {
            currency: Decimal(amount)
            for currency, amount in bucket["cost_by_currency"].items()
        } == {"USD": Decimal("2.0"), "CNY": Decimal("7.0")}
        # `cost` 只是主币种（金额最大者）的合计，与 overview 同一口径。
        assert Decimal(bucket["cost"]) == Decimal("7.0")
        assert bucket["cost_currency"] == "CNY"

    def test_unpriced_requests_are_counted_not_treated_as_free(self, tracer: LLMTracer):
        """按 0 元并入当天合计，会把「没匹配到价格版本」显示成「这天便宜」。"""
        add_trace(tracer, trace_id="priced", cost="1.0")
        add_trace(tracer, trace_id="unpriced", cost=None)

        bucket = _bucket(tracer.get_statistics()["daily_stats"], _today())

        assert bucket["unpriced_requests"] == 1
        assert Decimal(bucket["cost"]) == Decimal("1.0")

    def test_a_day_without_any_priced_request_reports_zero_and_no_currency(
        self, tracer: LLMTracer
    ):
        add_trace(tracer, trace_id="only-unpriced", cost=None)

        bucket = _bucket(tracer.get_statistics()["daily_stats"], _today())

        assert Decimal(bucket["cost"]) == Decimal("0")
        # 没有任何定价证据时不编一个币种出来。
        assert bucket["cost_currency"] is None
        assert bucket["cost_by_currency"] == {}

    def test_existing_daily_keys_are_unchanged(self, tracer: LLMTracer):
        """前端图表还在读这些键，新增不能挤掉它们。"""
        add_trace(tracer, trace_id="k1")

        bucket = _bucket(tracer.get_statistics()["daily_stats"], _today())

        for key in ("requests", "tokens", "success", "failed", "prompt_tokens"):
            assert key in bucket

    def test_hourly_buckets_carry_cost_too(self, tracer: LLMTracer):
        """24 小时视图同理：一次夜间批量任务要能在小时粒度上看出来。"""
        add_trace(tracer, trace_id="h1", cost="0.75")

        hourly = tracer.get_statistics()["hourly_stats"]

        assert hourly
        assert Decimal(hourly[-1]["cost"]) == Decimal("0.75")
