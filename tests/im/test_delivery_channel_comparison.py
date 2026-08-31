"""跨渠道的可比链路耗时（需求 19.5）。

19.5 的最后一句是硬要求：「应给出 Telegram、WeCom 与 QQ 的**可比**链路耗时」。
此前 `summarize()` 只接受**一个** `channel`，界面上是一个下拉筛选器——要比较三个
渠道，运维得切三次下拉框，然后靠记忆对比六个阶段的数字。那不是可比，那是把对比
这件事推给人的短期记忆。而 19.5 要回答的问题恰恰是对比式的：
「QQ 慢，是 QQ 这条链路慢，还是模型本来就慢（三个渠道一样慢）」。

一次查询完成分组，不是每个渠道各查一遍：六个阶段 × N 个渠道 = 6N 次查询，
而这张表在长期运行的部署上是会长的（默认保留 30 天）。`idx_im_delivery_channel_time`
覆盖 `(channel, recorded_at)`，分组聚合正好走它。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.ioc.container import DependencyContainer


@pytest.fixture()
def store(tmp_path) -> DeliveryTimingStore:
    container = DependencyContainer()
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'timings.db').as_posix()}",
    )
    database.initialize()
    return DeliveryTimingStore(database)


def _record(
    store: DeliveryTimingStore,
    channel: str,
    *,
    queue: float,
    generation: float,
    send: float,
    status: str = "succeeded",
    recorded_at: datetime | None = None,
) -> None:
    store.record(
        channel=channel,
        adapter_instance=f"{channel}-1",
        conversation_key=f"{channel}/session",
        status=status,
        durations={
            "queue_seconds": queue,
            "llm_generation_seconds": generation,
            "send_seconds": send,
            "total_seconds": queue + generation + send,
        },
        segment_count=2,
        retry_count=0,
    )
    if recorded_at is not None:
        # `record()` 刻意不接受外部时间戳（记录时刻由它自己定，避免调用方写入
        # 一个编出来的时间）。回填历史行只在测试里需要，因此直接改库。
        from kirara_ai.im.delivery_timing_store import DeliveryTiming

        with store.database.get_session() as session:
            row = (
                session.query(DeliveryTiming)
                .order_by(DeliveryTiming.id.desc())
                .first()
            )
            row.recorded_at = recorded_at
            session.commit()


def test_compare_returns_one_row_per_channel(store):
    _record(store, "onebot", queue=0.1, generation=8.0, send=4.0)
    _record(store, "telegram", queue=0.1, generation=8.0, send=0.4)
    _record(store, "wecom", queue=0.1, generation=8.0, send=0.6)

    rows = store.compare()

    assert [row["channel"] for row in rows] == ["onebot", "telegram", "wecom"]
    assert all(row["deliveries"] == 1 for row in rows)


def test_compare_makes_the_slow_stage_attributable(store):
    """这就是 19.5 要回答的问题。

    三个渠道的模型生成耗时相同、只有发送段差 10 倍——单渠道视图里这个事实
    看不出来，因为运维没有对照组。
    """
    _record(store, "onebot", queue=0.1, generation=8.0, send=4.0)
    _record(store, "telegram", queue=0.1, generation=8.0, send=0.4)

    rows = {row["channel"]: row for row in store.compare()}

    assert rows["onebot"]["phases"]["llm_generation_seconds"]["avg_seconds"] == pytest.approx(8.0)
    assert rows["telegram"]["phases"]["llm_generation_seconds"]["avg_seconds"] == pytest.approx(8.0)
    assert rows["onebot"]["phases"]["send_seconds"]["avg_seconds"] == pytest.approx(4.0)
    assert rows["telegram"]["phases"]["send_seconds"]["avg_seconds"] == pytest.approx(0.4)


def test_an_unmeasured_phase_stays_null_per_channel(store):
    """未测到的阶段必须是 `null`，不是 0。

    非流式请求没有首字节。写 0 会被读成「极快」——与事实正好相反，
    而在**对比**视图里这个错误更严重：一个渠道显示 0ms 首字节、另一个显示 2s，
    看起来是前者快得多。
    """
    _record(store, "onebot", queue=0.1, generation=8.0, send=1.0)

    row = store.compare()[0]

    assert row["phases"]["llm_first_byte_seconds"]["avg_seconds"] is None
    assert row["phases"]["llm_first_byte_seconds"]["samples"] == 0
    assert row["phases"]["llm_generation_seconds"]["samples"] == 1


def test_every_average_carries_its_sample_count(store):
    """一次 30 秒和一百次里一次 30 秒，平均值可能相同、处置完全不同。"""
    for _ in range(3):
        _record(store, "onebot", queue=0.1, generation=1.0, send=1.0)

    row = store.compare()[0]

    assert row["phases"]["send_seconds"]["samples"] == 3
    assert row["deliveries"] == 3


def test_compare_counts_failed_deliveries_per_channel(store):
    _record(store, "onebot", queue=0.1, generation=1.0, send=1.0, status="failed")
    _record(store, "onebot", queue=0.1, generation=1.0, send=1.0)
    _record(store, "telegram", queue=0.1, generation=1.0, send=1.0)

    rows = {row["channel"]: row for row in store.compare()}

    assert rows["onebot"]["failed_deliveries"] == 1
    assert rows["onebot"]["deliveries"] == 2
    assert rows["telegram"]["failed_deliveries"] == 0


def test_compare_reports_segment_and_retry_counts(store):
    """分段数量与重试次数是 19.5 九项里的后两项，对比时同样要在场。

    「QQ 慢是不是因为它分了 8 页而 Telegram 只发 1 条」——这个问题只有把
    分段数放在同一张表里才回答得了。
    """
    _record(store, "onebot", queue=0.1, generation=1.0, send=1.0)

    row = store.compare()[0]

    assert row["counts"]["segment_count"]["avg"] == pytest.approx(2.0)
    assert row["counts"]["retry_count"]["avg"] == pytest.approx(0.0)
    assert row["counts"]["retry_count"]["samples"] == 1


def test_compare_honours_the_time_range(store):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    _record(store, "onebot", queue=0.1, generation=1.0, send=9.0, recorded_at=old)
    _record(store, "onebot", queue=0.1, generation=1.0, send=1.0)

    recent = store.compare(
        start_time=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    )

    assert recent[0]["deliveries"] == 1
    assert recent[0]["phases"]["send_seconds"]["avg_seconds"] == pytest.approx(1.0)


def test_compare_on_an_empty_table_returns_no_rows(store):
    """空表返回空列表，而不是一行全 0 的假数据。"""
    assert store.compare() == []


def test_compare_uses_one_query_per_aggregate_not_one_per_channel(store):
    """分组聚合，不是逐渠道循环。

    六个阶段 × N 个渠道 = 6N 次查询。这张表默认保留 30 天，长期运行的部署上
    它会长；N+1 查询在这种表上是一个会随时间变慢的设计，而且慢下来的时候
    正好是最需要它的时候。
    """
    from sqlalchemy import event

    statements: list[str] = []

    @event.listens_for(store.database.engine, "before_cursor_execute")
    def _record_statement(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith("SELECT"):
            statements.append(statement)

    for channel in ("onebot", "telegram", "wecom", "qqbot", "http"):
        _record(store, channel, queue=0.1, generation=1.0, send=1.0)
    statements.clear()

    store.compare()

    # 一个渠道数上限即可：与渠道数无关的常数次查询。5 个渠道时若是 N+1，
    # 这里会是 30 次以上。
    assert len(statements) <= 12, f"查询次数随渠道数增长：{len(statements)} 次"
