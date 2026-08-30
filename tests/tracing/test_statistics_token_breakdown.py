"""聚合统计必须给出输入/输出/缓存四类 Token，而不只有一个总数（需求 9、22.1）。

22.1 逐项点名了「输入/输出/缓存 Token」。这四个数字**每一行都记着**
（`prompt_tokens`、`completion_tokens`、`cached_tokens`、`cache_write_tokens`），
请求详情页也四个都显示。但 `get_statistics` 的 `overview` 只 `SUM(total_tokens)`，
分组统计只给 `tokens`，日/时分桶也只有 `tokens`——聚合这一跳把四类合成了一个数。

这跟本轮反复在修的是同一种缺陷：数据在库里、类型里有、详情页看得到，
**唯独聚合出口没有**。后果不是「少一个装饰性数字」：

- 缓存命中率算不出来。输入 Token 的价格通常是缓存读取的 5~10 倍，
  一份「总 Token 没变」的账单，缓存命中从 80% 掉到 0% 时成本会翻几倍，
  而当前的统计页在这两种情况下显示的数字**完全一样**。
- 「输出涨了」和「输入涨了」处置相反（前者查 prompt 与 max_tokens，
  后者查上下文与历史长度），合成一个总数就把该查什么留给读者猜。

NULL 与 0 的区分必须保持：`cached_tokens IS NULL` 是「这家上游没报缓存」，
`0` 是「报了、确实没命中」。把前者当 0 会让命中率凭空变低，
进而把一次「上游不报数」误判成「缓存失效」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    provider: str = "provider-a",
    model_id: str = "model-a",
    status: str = "success",
    prompt_tokens: int | None = 60,
    completion_tokens: int | None = 40,
    cached_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    minutes_ago: int = 5,
) -> None:
    request_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=minutes_ago
    )
    total = (prompt_tokens or 0) + (completion_tokens or 0)
    with tracer.db_manager.get_session() as session:
        session.add(
            LLMRequestTrace(
                trace_id=trace_id,
                model_id=model_id,
                backend_name=provider,
                provider=provider,
                request_time=request_time,
                response_time=request_time + timedelta(seconds=1),
                duration=1.0,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
                cached_tokens=cached_tokens,
                cache_write_tokens=cache_write_tokens,
                usage_source="provider",
                status=status,
            )
        )
        session.commit()


class TestOverviewTokenBreakdown:
    def test_overview_splits_input_output_and_cache_tokens(self, tracer: LLMTracer):
        add_trace(
            tracer,
            trace_id="t1",
            prompt_tokens=100,
            completion_tokens=20,
            cached_tokens=80,
            cache_write_tokens=10,
        )
        add_trace(
            tracer,
            trace_id="t2",
            prompt_tokens=50,
            completion_tokens=30,
            cached_tokens=40,
            cache_write_tokens=5,
        )

        overview = tracer.get_statistics()["overview"]

        assert overview["total_prompt_tokens"] == 150
        assert overview["total_completion_tokens"] == 50
        assert overview["total_cached_tokens"] == 120
        assert overview["total_cache_write_tokens"] == 15

    def test_total_tokens_is_unchanged(self, tracer: LLMTracer):
        """加了拆分不能改动既有那个总数的含义，否则历史看板会前后不一致。"""
        add_trace(tracer, trace_id="t1", prompt_tokens=100, completion_tokens=20)

        overview = tracer.get_statistics()["overview"]

        assert overview["total_tokens"] == 120

    def test_cache_hit_rate_is_reported(self, tracer: LLMTracer):
        """命中率是这批数字的用途所在：总 Token 相同、命中率不同，账单差几倍。"""
        add_trace(
            tracer,
            trace_id="t1",
            prompt_tokens=100,
            completion_tokens=0,
            cached_tokens=75,
            cache_write_tokens=25,
        )

        overview = tracer.get_statistics()["overview"]

        # 命中率 = 缓存读取 / (输入 + 缓存写入 + 缓存读取)
        assert overview["cache_hit_rate"] == pytest.approx(75 / (100 + 25 + 75))

    def test_cache_hit_rate_is_none_when_no_upstream_reported_cache(
        self, tracer: LLMTracer
    ):
        """没有任何上游报缓存时，命中率是「未知」而不是 0%。

        报 0% 会让运维去查一个并不存在的缓存失效问题。
        """
        add_trace(tracer, trace_id="t1", cached_tokens=None, cache_write_tokens=None)

        overview = tracer.get_statistics()["overview"]

        assert overview["cache_hit_rate"] is None
        assert overview["total_cached_tokens"] is None

    def test_a_reported_zero_is_not_the_same_as_no_report(self, tracer: LLMTracer):
        """上游报了 0（确实没命中）必须与「没报」区分开。"""
        add_trace(tracer, trace_id="t1", prompt_tokens=100, completion_tokens=0, cached_tokens=0)

        overview = tracer.get_statistics()["overview"]

        assert overview["total_cached_tokens"] == 0
        assert overview["cache_hit_rate"] == pytest.approx(0.0)

    def test_empty_database_reports_zero_tokens_not_none(self, tracer: LLMTracer):
        overview = tracer.get_statistics()["overview"]

        assert overview["total_prompt_tokens"] == 0
        assert overview["total_completion_tokens"] == 0
        # 一条记录都没有时缓存命中率无从谈起。
        assert overview["cache_hit_rate"] is None


class TestGroupTokenBreakdown:
    def test_provider_groups_carry_the_split(self, tracer: LLMTracer):
        add_trace(
            tracer,
            trace_id="a1",
            provider="provider-a",
            prompt_tokens=90,
            completion_tokens=10,
            cached_tokens=45,
        )
        add_trace(
            tracer,
            trace_id="b1",
            provider="provider-b",
            prompt_tokens=10,
            completion_tokens=90,
            cached_tokens=0,
        )

        by_provider = {
            row["provider"]: row for row in tracer.get_statistics()["providers"]
        }

        assert by_provider["provider-a"]["prompt_tokens"] == 90
        assert by_provider["provider-a"]["completion_tokens"] == 10
        assert by_provider["provider-a"]["cached_tokens"] == 45
        # 同一个 100 总量，一家几乎全是输入、一家几乎全是输出——
        # 这正是「合成一个数字就看不出来」的那件事。
        assert by_provider["provider-b"]["prompt_tokens"] == 10
        assert by_provider["provider-b"]["completion_tokens"] == 90

    def test_model_groups_carry_the_split(self, tracer: LLMTracer):
        add_trace(
            tracer,
            trace_id="m1",
            model_id="model-a",
            prompt_tokens=70,
            completion_tokens=30,
            cache_write_tokens=12,
        )

        by_model = {row["model_id"]: row for row in tracer.get_statistics()["models"]}

        assert by_model["model-a"]["prompt_tokens"] == 70
        assert by_model["model-a"]["completion_tokens"] == 30
        assert by_model["model-a"]["cache_write_tokens"] == 12


class TestTrendTokenBreakdown:
    def test_daily_buckets_carry_the_split(self, tracer: LLMTracer):
        """趋势图要能回答「涨的是输入还是输出」，否则看到总量上升也不知查哪。"""
        add_trace(
            tracer,
            trace_id="d1",
            prompt_tokens=80,
            completion_tokens=20,
            cached_tokens=30,
            cache_write_tokens=5,
        )

        daily = tracer.get_statistics()["daily_stats"]

        assert daily, "最近 30 天应至少有一个分桶"
        bucket = daily[-1]
        assert bucket["prompt_tokens"] == 80
        assert bucket["completion_tokens"] == 20
        assert bucket["cached_tokens"] == 30
        assert bucket["cache_write_tokens"] == 5
        # 既有键不能改名，前端图表还在读它。
        assert bucket["tokens"] == 100

    def test_hourly_buckets_carry_the_split(self, tracer: LLMTracer):
        add_trace(tracer, trace_id="h1", prompt_tokens=60, completion_tokens=40, minutes_ago=10)

        hourly = tracer.get_statistics()["hourly_stats"]

        assert hourly
        bucket = hourly[-1]
        assert bucket["prompt_tokens"] == 60
        assert bucket["completion_tokens"] == 40
        assert bucket["tokens"] == 100

    def test_null_token_rows_do_not_break_the_buckets(self, tracer: LLMTracer):
        """旧记录四列都是 NULL；分桶必须照常出数，而不是抛异常或算成负数。"""
        add_trace(
            tracer,
            trace_id="old",
            prompt_tokens=None,
            completion_tokens=None,
            cached_tokens=None,
            cache_write_tokens=None,
        )

        daily = tracer.get_statistics()["daily_stats"]

        assert daily
        assert daily[-1]["prompt_tokens"] == 0
        assert daily[-1]["completion_tokens"] == 0
