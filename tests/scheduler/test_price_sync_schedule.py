"""定价同步接进后台调度器。

同步器本身已经能拉目录、能只碰自己写过的版本（见
`tests/llm/test_pricing_upstream_sync.py`），但如果没有调度入口，用户仍要自
己记得点一下，价格照样会过期。这里锁住它在 `TaskScheduler` 里的位置：与自
动检测共用同一套状态文件与到期判定，间隔 0 表示关闭，状态要能报给界面。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMConfig
from kirara_ai.llm.pricing import PriceCatalog
from kirara_ai.scheduler.scheduler import PRICE_SYNC_STATE_KEY, TaskScheduler


class _Container:
    def __init__(self, mapping: dict):
        self._mapping = mapping

    def resolve(self, key):
        return self._mapping[key]

    def has(self, key) -> bool:  # pragma: no cover - 只为兼容生命周期探测
        return key in self._mapping


def _scheduler(tmp_path, monkeypatch, interval_days: int = 7) -> TaskScheduler:
    monkeypatch.setattr(
        "kirara_ai.scheduler.scheduler.STATE_FILE",
        str(tmp_path / "auto_detect_state.json"),
    )
    catalog = PriceCatalog.load_or_create(tmp_path / "pricing.json")
    config = GlobalConfig(llms=LLMConfig(price_sync_interval_days=interval_days))
    container = _Container({PriceCatalog: catalog, GlobalConfig: config})
    scheduler = TaskScheduler(container)
    return scheduler


@pytest.mark.asyncio
async def test_price_sync_runs_when_it_has_never_run_before(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch)
    calls: list[bool] = []

    async def fake_sync(**_kwargs):
        calls.append(True)
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport(imported=3)

    scheduler._run_price_sync = fake_sync  # type: ignore[method-assign]

    await scheduler.run_once()

    assert calls, "从未同步过时应当立刻跑一轮"


@pytest.mark.asyncio
async def test_price_sync_is_skipped_before_the_interval_elapses(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, interval_days=7)
    scheduler._state[PRICE_SYNC_STATE_KEY] = datetime.now().isoformat()
    calls: list[bool] = []

    async def fake_sync(**_kwargs):
        calls.append(True)
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport()

    scheduler._run_price_sync = fake_sync  # type: ignore[method-assign]

    await scheduler.run_once()

    assert not calls, "刚同步过就不该再跑"


@pytest.mark.asyncio
async def test_price_sync_runs_again_once_the_interval_has_passed(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, interval_days=7)
    scheduler._state[PRICE_SYNC_STATE_KEY] = (
        datetime.now() - timedelta(days=8)
    ).isoformat()
    calls: list[bool] = []

    async def fake_sync(**_kwargs):
        calls.append(True)
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport(imported=1)

    scheduler._run_price_sync = fake_sync  # type: ignore[method-assign]

    await scheduler.run_once()

    assert calls, "超过间隔后应当再跑一轮"


@pytest.mark.asyncio
async def test_a_zero_interval_turns_price_sync_off(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, interval_days=0)
    calls: list[bool] = []

    async def fake_sync(**_kwargs):
        calls.append(True)
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport()

    scheduler._run_price_sync = fake_sync  # type: ignore[method-assign]

    await scheduler.run_once()

    assert not calls, "间隔 0 表示关闭自动同步"


@pytest.mark.asyncio
async def test_a_failed_sync_does_not_stamp_the_state(tmp_path, monkeypatch):
    """同步失败不能记成「已同步」，否则要再等一个完整间隔才会重试。"""
    scheduler = _scheduler(tmp_path, monkeypatch)

    async def failing_sync(**_kwargs):
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport(error="upstream unreachable")

    scheduler._run_price_sync = failing_sync  # type: ignore[method-assign]

    await scheduler.run_once()

    assert PRICE_SYNC_STATE_KEY not in scheduler._state


@pytest.mark.asyncio
async def test_status_reports_the_price_sync_schedule(tmp_path, monkeypatch):
    scheduler = _scheduler(tmp_path, monkeypatch, interval_days=7)
    stamp = datetime.now().isoformat()
    scheduler._state[PRICE_SYNC_STATE_KEY] = stamp

    status = scheduler.get_status()

    assert "price_sync" in status, "界面拿不到同步状态就等于没有这个功能"
    assert status["price_sync"]["interval_days"] == 7
    assert status["price_sync"]["last_run"] == stamp


@pytest.mark.asyncio
async def test_price_sync_stays_out_of_the_per_backend_result_map(tmp_path, monkeypatch):
    """run_once 的返回值是「后端名 -> 检测是否成功」，调用方按后端名遍历它。

    定价同步不是后端。把它塞进这份映射，调用方就会把它当成一个真上游去处理
    （在界面上多出一行叫 __price_sync__ 的"模型后端"）。同步结果走 get_status。
    """
    scheduler = _scheduler(tmp_path, monkeypatch)

    async def fake_sync(**_kwargs):
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport(imported=1)

    scheduler._run_price_sync = fake_sync  # type: ignore[method-assign]

    results = await scheduler.run_once(force=True)

    assert PRICE_SYNC_STATE_KEY not in results
    assert scheduler.get_status()["price_sync"]["last_ok"] is True


@pytest.mark.asyncio
async def test_status_separates_never_synced_from_sync_failed(tmp_path, monkeypatch):
    """没同步过和同步失败是两种处境，不能在界面上长成同一个样子。"""
    scheduler = _scheduler(tmp_path, monkeypatch)

    assert scheduler.get_status()["price_sync"]["last_ok"] is None

    async def failing_sync(**_kwargs):
        from kirara_ai.llm.pricing_sync import PriceSyncReport

        return PriceSyncReport(error="upstream unreachable")

    scheduler._run_price_sync = failing_sync  # type: ignore[method-assign]
    await scheduler.run_once(force=True)

    assert scheduler.get_status()["price_sync"]["last_ok"] is False
