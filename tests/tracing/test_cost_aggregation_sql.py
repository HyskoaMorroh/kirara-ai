"""需求 22.2：成本汇总必须在 SQL 侧完成，不能把每一行拉回 Python。

成本存在 `cost_snapshot_json` 这个 Text 列里，于是 `get_statistics` 与
`_group_statistics` 都得**取出筛选后的每一行**、逐条 `json.loads`、再累加。
六个复合索引在这条路径上帮不上任何忙：它们能加速筛选，但结果集有多大就要
搬多少行进内存。请求日志有分页保护，统计页没有——一年几十万条追踪时，
打开统计页就是一次全表物化。

修法不能是「重算」：历史账单必须沿用请求当时的定价快照，拿现价重算是错的。
所以在**写入时**把该快照里的总成本与币种落成两个专用列（快照仍是权威来源，
列只是它的投影，写一次不再改），汇总就能用 `SUM` / `GROUP BY`。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect

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


def _add(tracer: LLMTracer, trace_id: str, *, cost: str | None, provider: str = "p") -> None:
    request_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    with tracer.db_manager.get_session() as session:
        row = LLMRequestTrace(
            trace_id=trace_id,
            model_id="m",
            backend_name=provider,
            provider=provider,
            request_time=request_time,
            response_time=request_time + timedelta(seconds=1),
            duration=1.0,
            total_tokens=10,
            usage_source="provider",
            status="success",
        )
        if cost is not None:
            row.cost_snapshot_json = (
                '{"currency": "USD", "total_cost": "%s", "price_version_id": "v1"}' % cost
            )
            # 赋值即投影：`cost_snapshot_json` 的 `@validates` 钩子负责把
            # 快照里的总成本与币种写进两个可 SUM 的列，不需要调用方记得调。
        session.add(row)
        session.commit()


def test_the_trace_table_carries_indexed_cost_columns(tracer: LLMTracer):
    columns = {
        column["name"]
        for column in inspect(tracer.db_manager.engine).get_columns("llm_request_traces")
    }

    assert "total_cost" in columns, "成本汇总需要一个可 SUM 的数值列"
    assert "cost_currency" in columns, "多币种下 SUM 必须按币种分组，否则是把两种钱相加"


def test_cost_projection_is_written_from_the_snapshot_not_recomputed(tracer: LLMTracer):
    _add(tracer, "t1", cost="0.5")

    with tracer.db_manager.get_session() as session:
        row = session.query(LLMRequestTrace).filter_by(trace_id="t1").one()
        # 列的值必须与快照一致：它是投影，不是第二个真相来源。
        assert Decimal(str(row.total_cost)) == Decimal("0.5")
        assert row.cost_currency == "USD"
        assert row.cost_snapshot_json is not None


def test_a_request_without_a_snapshot_leaves_the_cost_column_null(tracer: LLMTracer):
    _add(tracer, "unpriced", cost=None)

    with tracer.db_manager.get_session() as session:
        row = session.query(LLMRequestTrace).filter_by(trace_id="unpriced").one()

    # NULL 与 0 必须区分：前者是「没有定价证据」，后者是「定价过且确实免费」。
    # 写成 0 会让账单凭空变小，而那是最难发现的一类错误。
    assert row.total_cost is None
    assert row.cost_currency is None


def test_overview_cost_matches_the_snapshot_sum(tracer: LLMTracer):
    _add(tracer, "a", cost="0.5")
    _add(tracer, "b", cost="1.25")
    _add(tracer, "c", cost=None)

    stats = tracer.get_statistics()

    assert Decimal(stats["overview"]["total_cost"]) == Decimal("1.75")
    assert stats["overview"]["unpriced_requests"] == 1
    assert stats["overview"]["cost_currency"] == "USD"


def test_statistics_does_not_materialize_every_row_to_sum_cost(tracer: LLMTracer):
    """汇总不得逐行取出 `cost_snapshot_json`。

    这条用例直接盯住那个行为：把快照列的读取换成一个会计数的属性，
    统计一次之后计数必须为 0。否则「大数据量下的索引性能」只是口头上的。
    """
    for index in range(25):
        _add(tracer, f"row-{index}", cost="0.1")

    loads = {"count": 0}
    original = LLMTracer._snapshot_cost

    def counting_snapshot_cost(snapshot_json):
        loads["count"] += 1
        return original(snapshot_json)

    LLMTracer._snapshot_cost = staticmethod(counting_snapshot_cost)  # type: ignore[assignment]
    try:
        stats = tracer.get_statistics()
    finally:
        LLMTracer._snapshot_cost = staticmethod(original)  # type: ignore[assignment]

    assert Decimal(stats["overview"]["total_cost"]) == Decimal("2.5")
    assert loads["count"] == 0, (
        f"统计路径仍然逐行解析了 {loads['count']} 次价格快照；"
        "成本汇总应当由 SQL 完成"
    )


def test_grouped_costs_also_come_from_sql(tracer: LLMTracer):
    _add(tracer, "pa", cost="0.5", provider="provider-a")
    _add(tracer, "pb", cost="2.0", provider="provider-b")
    _add(tracer, "pn", cost=None, provider="provider-b")

    stats = tracer.get_statistics()
    by_provider = {row["provider"]: row for row in stats["providers"]}

    assert Decimal(by_provider["provider-a"]["cost"]) == Decimal("0.5")
    assert Decimal(by_provider["provider-b"]["cost"]) == Decimal("2.0")
    assert by_provider["provider-b"]["unpriced_requests"] == 1


def test_mixed_currencies_are_never_added_into_one_number(tracer: LLMTracer):
    """两种货币不能相加。

    这是 `SUM(total_cost)` 最容易犯的错：数字类型相同，加起来不报错，
    得到的却是一个没有单位的数。总额只保留金额最大的那个币种，
    其余在 `cost_by_currency` 里逐一列出。
    """
    _add(tracer, "usd1", cost="1.0")
    _add(tracer, "usd2", cost="2.0")

    request_time = datetime.now(timezone.utc).replace(tzinfo=None)
    with tracer.db_manager.get_session() as session:
        row = LLMRequestTrace(
            trace_id="eur1",
            model_id="m",
            backend_name="p",
            provider="p",
            request_time=request_time,
            duration=1.0,
            total_tokens=10,
            usage_source="provider",
            status="success",
        )
        row.cost_snapshot_json = (
            '{"currency": "EUR", "total_cost": "10.0", "price_version_id": "v2"}'
        )
        session.add(row)
        session.commit()

    stats = tracer.get_statistics()

    # EUR 金额更大，因此它是主币种；USD 不会被加进那个数字。
    assert stats["overview"]["cost_currency"] == "EUR"
    assert Decimal(stats["overview"]["total_cost"]) == Decimal("10.0")
    # 金额按 Decimal 比较：列的标度是 8 位小数（与 `_cost` 的量化一致），
    # 字符串形态会带尾随零，那是精度而不是差异。
    by_currency = {
        currency: Decimal(amount)
        for currency, amount in stats["overview"]["cost_by_currency"].items()
    }
    assert by_currency == {"EUR": Decimal("10.0"), "USD": Decimal("3.0")}
    assert stats["overview"]["unpriced_requests"] == 0
