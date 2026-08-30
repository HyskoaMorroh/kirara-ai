"""分组统计必须给出成功率，否则「哪家供应商在失败」没有聚合答案（需求 9、22.1）。

`error_categories` 回答「在失败什么」，但回答不了「谁在失败」：它按错误类型分组，
一个 `timeout` 分组里可能混着三家供应商。而 `providers` / `models` 分组只有
`count`（总请求数），没有成功与失败的拆分。

于是这两个问题在统计页上都没有出口：

- **该把哪家降级或摘掉。** 故障转移的优先级队列按 `priority` 排，但「谁该排后面」
  的依据是成功率。没有它，调整队列只能靠翻请求日志人工计数。
- **一家供应商是慢还是坏。** `avg_duration` 高可能是慢，也可能是大量快速失败
  把均值拉低而慢请求超时被算作失败——两者的处置相反。

`success_rate` 在没有请求时是 `None` 而不是 0.0：分母为 0 时「成功率 0%」
是编出来的数字，会让一家从未被调用过的供应商看起来是最差的那一个。
`pending` 不计入分母：请求还在跑，它既不是成功也不是失败，
把它算作失败会让正在进行的长请求把成功率压下去。
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
) -> None:
    request_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
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
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                usage_source="provider",
                status=status,
            )
        )
        session.commit()


class TestProviderSuccessRate:
    def test_provider_groups_report_success_and_failure_counts(self, tracer: LLMTracer):
        add_trace(tracer, trace_id="a1", provider="provider-a", status="success")
        add_trace(tracer, trace_id="a2", provider="provider-a", status="success")
        add_trace(tracer, trace_id="a3", provider="provider-a", status="failed")
        add_trace(tracer, trace_id="b1", provider="provider-b", status="failed")

        by_provider = {
            row["provider"]: row for row in tracer.get_statistics()["providers"]
        }

        assert by_provider["provider-a"]["success_requests"] == 2
        assert by_provider["provider-a"]["failed_requests"] == 1
        assert by_provider["provider-a"]["success_rate"] == pytest.approx(2 / 3)
        # 一家全失败：这正是「该把谁摘掉」的答案，而它此前在统计页上没有出口。
        assert by_provider["provider-b"]["success_rate"] == pytest.approx(0.0)

    def test_pending_requests_are_not_counted_as_failures(self, tracer: LLMTracer):
        """还在跑的请求既不是成功也不是失败，算作失败会压低正在进行的长请求。"""
        add_trace(tracer, trace_id="p1", status="success")
        add_trace(tracer, trace_id="p2", status="pending")

        row = tracer.get_statistics()["providers"][0]

        assert row["count"] == 2
        assert row["pending_requests"] == 1
        # 分母是「已有结论的请求数」= 1，不是总数 2。
        assert row["success_rate"] == pytest.approx(1.0)

    def test_success_rate_is_none_when_nothing_has_concluded(self, tracer: LLMTracer):
        """一条都没跑完时成功率是未知，不是 0%。

        报 0% 会让一家刚配好、只有一条在途请求的供应商看起来是最差的那个。
        """
        add_trace(tracer, trace_id="only-pending", status="pending")

        row = tracer.get_statistics()["providers"][0]

        assert row["success_rate"] is None

    def test_model_groups_carry_the_same_split(self, tracer: LLMTracer):
        add_trace(tracer, trace_id="m1", model_id="model-a", status="success")
        add_trace(tracer, trace_id="m2", model_id="model-a", status="failed")

        by_model = {row["model_id"]: row for row in tracer.get_statistics()["models"]}

        assert by_model["model-a"]["success_rate"] == pytest.approx(0.5)

    def test_existing_group_keys_are_unchanged(self, tracer: LLMTracer):
        """新增键不能挤掉既有键，前端图表还在读它们。"""
        add_trace(tracer, trace_id="k1")

        row = tracer.get_statistics()["providers"][0]

        for key in ("count", "tokens", "avg_duration", "cost", "unpriced_requests"):
            assert key in row
