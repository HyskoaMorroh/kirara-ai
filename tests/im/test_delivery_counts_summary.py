"""需求 19.5：分段数量与重试次数必须能被回查，不只是记在日志里。

19.5 要求记录九项：收到事件、工作流开始、LLM 首字节、LLM 完成、格式化完成、
发送开始、发送成功/失败、**分段数量**、**重试次数**。

前七项是时间戳，`delivery_durations()` 把它们折算成各阶段耗时并落库、汇总接口
按阶段给出平均值与样本数。后两项不是时间戳：它们被四个适配器写进
`record_delivery_stage(..., segment_count=/retry_count=)` 的 details 里，
`dispatcher._persist_delivery_durations` 扫 timeline 把它们取出来存进两个列——
**但汇总接口不聚合它们**。

于是「上周二那批慢投递是不是因为分了很多页」这个问题回答不了：单条记录里有
`segment_count`，可 `/tracing/delivery/summary` 只给阶段耗时。落库了却不出现在
汇总里的字段，实际等于只能靠逐条翻。

这些用例要求汇总把这两个计数也报出来，并且遵守与阶段耗时同一条口径：
**只对测到该值的行求平均，并给出样本数**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.ioc.container import DependencyContainer


@pytest.fixture()
def store(tmp_path: Path) -> DeliveryTimingStore:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'timing.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    return DeliveryTimingStore(database)


def _record(
    store: DeliveryTimingStore,
    *,
    channel: str = "onebot",
    segment_count: int | None = None,
    retry_count: int | None = None,
    status: str = "succeeded",
) -> None:
    store.record(
        channel=channel,
        adapter_instance="main",
        durations={"total_seconds": 1.0},
        status=status,
        conversation_key="conv",
        segment_count=segment_count,
        retry_count=retry_count,
    )


def test_the_summary_reports_average_segment_count(store: DeliveryTimingStore):
    _record(store, segment_count=1)
    _record(store, segment_count=5)

    summary = store.summarize()

    assert summary["counts"]["segment_count"]["avg"] == pytest.approx(3.0)
    assert summary["counts"]["segment_count"]["max"] == 5
    assert summary["counts"]["segment_count"]["samples"] == 2


def test_the_summary_reports_average_retry_count(store: DeliveryTimingStore):
    _record(store, retry_count=0)
    _record(store, retry_count=2)

    summary = store.summarize()

    assert summary["counts"]["retry_count"]["avg"] == pytest.approx(1.0)
    assert summary["counts"]["retry_count"]["max"] == 2
    assert summary["counts"]["retry_count"]["samples"] == 2


def test_rows_without_a_count_are_excluded_from_its_average(store: DeliveryTimingStore):
    """没测到的行不参与平均——与阶段耗时同一条口径。

    第三方适配器可能不带 details，那时 `segment_count` 为 NULL。把 NULL 按 0
    计入会把平均分段数拉低成一个不存在的数字，而读者无从察觉。
    """
    _record(store, segment_count=4)
    _record(store, segment_count=None)

    summary = store.summarize()

    assert summary["counts"]["segment_count"]["avg"] == pytest.approx(4.0)
    # 样本数必须暴露出来：1 次里 4 页和 100 次里平均 4 页是完全不同的证据强度。
    assert summary["counts"]["segment_count"]["samples"] == 1
    assert summary["deliveries"] == 2


def test_a_count_nobody_recorded_is_null_rather_than_zero(store: DeliveryTimingStore):
    """一个都没测到时给 `None`，不给 0。

    `retry_count: 0` 是一个论断（「都没重试过」），`None` 才是「没有数据」。
    前者会让人以为投递链路一切正常，而实际上只是没人记录。
    """
    _record(store, segment_count=2)

    summary = store.summarize()

    assert summary["counts"]["retry_count"]["avg"] is None
    assert summary["counts"]["retry_count"]["max"] is None
    assert summary["counts"]["retry_count"]["samples"] == 0


def test_counts_follow_the_same_channel_and_time_filters(store: DeliveryTimingStore):
    _record(store, channel="onebot", segment_count=2)
    _record(store, channel="telegram", segment_count=10)

    summary = store.summarize(channel="onebot")

    assert summary["counts"]["segment_count"]["avg"] == pytest.approx(2.0)
    assert summary["counts"]["segment_count"]["samples"] == 1


def test_an_empty_range_reports_no_counts_without_failing(store: DeliveryTimingStore):
    summary = store.summarize(channel="nonexistent")

    assert summary["deliveries"] == 0
    assert summary["counts"]["segment_count"]["samples"] == 0
    assert summary["counts"]["segment_count"]["avg"] is None
