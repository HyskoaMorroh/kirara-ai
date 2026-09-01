"""An open circuit must survive a restart.

The breaker lives in memory, which is right for the hot path. What was missing is
durability around it: a restart wiped every open breaker, so a provider that had
just been isolated for repeated failures was retried immediately as if healthy and
the next request paid the full timeout again.

Two design decisions are pinned here because they are easy to get wrong:

- The outcome ring buffer is **not** persisted. Error *rate* describes recent live
  traffic; replaying a pre-restart window could trip a breaker on a provider that
  is now fine. After a restart the window starts empty and the consecutive-failure
  threshold does the work.
- Recovery time keeps elapsing while the process is down. Otherwise a restart
  loop would reset the wait every time and the breaker would never reach half-open.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.llm.circuit_store import CircuitBreakerStore
from kirara_ai.llm.resilience import CircuitBreaker, CircuitState


def opened_breaker(**overrides) -> CircuitBreaker:
    settings = {"failure_threshold": 1, "recovery_timeout_seconds": 60.0}
    settings.update(overrides)
    breaker = CircuitBreaker(**settings)
    breaker.acquire()
    breaker.record_failure()
    return breaker


def test_a_closed_breaker_has_nothing_worth_persisting():
    breaker = CircuitBreaker(failure_threshold=3)

    assert breaker.durable_state() is None


def test_an_open_breaker_exports_its_state():
    state = opened_breaker().durable_state()

    assert state is not None
    assert state["state"] == CircuitState.OPEN.value
    assert state["consecutive_failures"] >= 1


def test_the_outcome_window_is_not_persisted():
    breaker = CircuitBreaker(failure_threshold=1, min_requests=2, error_rate_threshold=0.5)
    breaker.acquire()
    breaker.record_failure()

    state = breaker.durable_state()

    assert state is not None
    # A persisted rate window could trip a now-healthy provider after restart.
    assert "outcomes" not in state
    assert "requests" not in state


def test_restoring_reopens_the_breaker(tmp_path: Path):
    store = CircuitBreakerStore(tmp_path / "circuit.json")
    store.save({"provider-a": opened_breaker()})

    fresh = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
    assert fresh.state() == CircuitState.CLOSED

    restored = store.restore({"provider-a": fresh})

    assert restored == 1
    assert fresh.state() == CircuitState.OPEN
    assert fresh.acquire() is False


def test_recovery_time_keeps_elapsing_while_the_process_is_down(tmp_path: Path):
    now = [1000.0]
    store = CircuitBreakerStore(tmp_path / "circuit.json", clock=lambda: now[0])
    store.save({"provider-a": opened_breaker(recovery_timeout_seconds=30.0)})

    # The process was down for longer than the recovery wait.
    now[0] += 120.0
    fresh = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30.0)
    store.restore({"provider-a": fresh})

    # The wait already expired, so the breaker must allow one probe rather than
    # starting the countdown over.
    assert fresh.state() == CircuitState.HALF_OPEN
    assert fresh.acquire() is True


def test_a_restart_within_the_recovery_wait_still_blocks(tmp_path: Path):
    now = [1000.0]
    store = CircuitBreakerStore(tmp_path / "circuit.json", clock=lambda: now[0])
    store.save({"provider-a": opened_breaker(recovery_timeout_seconds=300.0)})

    now[0] += 5.0
    fresh = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=300.0)
    store.restore({"provider-a": fresh})

    assert fresh.acquire() is False


def test_an_unknown_provider_is_left_untouched(tmp_path: Path):
    store = CircuitBreakerStore(tmp_path / "circuit.json")
    store.save({"provider-a": opened_breaker()})

    other = CircuitBreaker(failure_threshold=1)
    restored = store.restore({"provider-b": other})

    assert restored == 0
    assert other.state() == CircuitState.CLOSED


def test_a_missing_state_file_restores_nothing(tmp_path: Path):
    store = CircuitBreakerStore(tmp_path / "absent.json")
    breaker = CircuitBreaker(failure_threshold=1)

    assert store.load() == {}
    assert store.restore({"provider-a": breaker}) == 0
    assert breaker.state() == CircuitState.CLOSED


def test_a_corrupt_state_file_is_ignored_rather_than_fatal(tmp_path: Path):
    path = tmp_path / "circuit.json"
    path.write_text("{not json", encoding="utf-8")
    store = CircuitBreakerStore(path)

    assert store.load() == {}


def test_a_stale_state_file_is_ignored(tmp_path: Path):
    now = [1000.0]
    store = CircuitBreakerStore(tmp_path / "circuit.json", clock=lambda: now[0])
    store.save({"provider-a": opened_breaker()})

    # Two days later the stored breaker no longer describes the current upstream.
    now[0] += 48 * 60 * 60
    assert store.load() == {}


def test_saving_only_closed_breakers_does_not_create_a_file(tmp_path: Path):
    path = tmp_path / "circuit.json"
    store = CircuitBreakerStore(path)

    store.save({"provider-a": CircuitBreaker(failure_threshold=3)})

    assert not path.exists()


def test_a_half_open_breaker_is_persisted_too(tmp_path: Path):
    now = [1000.0]
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        clock=lambda: now[0],
    )
    breaker.acquire()
    breaker.record_failure()
    now[0] += 20.0
    assert breaker.state() == CircuitState.HALF_OPEN

    state = breaker.durable_state()

    assert state is not None
    assert state["state"] == CircuitState.HALF_OPEN.value


def test_a_restored_breaker_can_still_close_after_a_success(tmp_path: Path):
    now = [1000.0]
    store = CircuitBreakerStore(tmp_path / "circuit.json", clock=lambda: now[0])
    store.save({"provider-a": opened_breaker(recovery_timeout_seconds=10.0)})
    now[0] += 60.0

    # 恢复阈值显式写成 1：这条用例验的是「恢复后的熔断器仍能闭合」，
    # 而不是「闭合需要几次成功」。依赖构造默认值会让它在默认值改动时
    # 以一个与主题无关的理由失败——默认值现已与配置字段对齐为 2。
    fresh = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        recovery_success_threshold=1,
    )
    store.restore({"provider-a": fresh})
    assert fresh.acquire() is True
    fresh.record_success()

    assert fresh.state() == CircuitState.CLOSED


def test_the_configured_recovery_threshold_is_honoured_after_restore(tmp_path: Path):
    """恢复阈值为 2 时，一次成功**不足以**闭合。

    与上一条配对：那条固定阈值验「能闭合」，这条验「阈值真的被遵守」。
    只留前者时，把阈值读成 1 的实现也能通过。
    """
    now = [1000.0]
    store = CircuitBreakerStore(tmp_path / "circuit.json", clock=lambda: now[0])
    store.save({"provider-a": opened_breaker(recovery_timeout_seconds=10.0)})
    now[0] += 60.0

    fresh = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        recovery_success_threshold=2,
    )
    store.restore({"provider-a": fresh})
    assert fresh.acquire() is True
    fresh.record_success()
    assert fresh.state() == CircuitState.HALF_OPEN

    assert fresh.acquire() is True
    fresh.record_success()
    assert fresh.state() == CircuitState.CLOSED


@pytest.mark.parametrize("bad_state", [{}, {"state": "closed"}, {"state": 5}])
def test_an_unusable_record_is_not_applied(bad_state):
    breaker = CircuitBreaker(failure_threshold=1)

    assert breaker.restore_durable_state(bad_state) is False
    assert breaker.state() == CircuitState.CLOSED
