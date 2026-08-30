"""需求 21.3：熔断三态的**触发与恢复证据**必须可回溯。

三态本身早就有（`CircuitState.CLOSED/OPEN/HALF_OPEN`），`snapshot()` 也能回答
「现在是什么状态」。缺的是「**什么时候**、**因为什么**变成这个状态的」：

- `resilience.py` 与 `circuit_store.py` 内没有任何 logger，也不写审计；
- `get_resilience_status` 只给当前快照，轮询间隔内发生的 open → half-open →
  closed 全部不可见。

于是「昨天下午 P1 被隔离过吗、隔了多久、是自己恢复的还是一直开着」只能靠
恰好抓到那一次轮询——那不是证据。这些用例要求每次状态迁移留下一条有界记录。
"""

from __future__ import annotations

from kirara_ai.llm.resilience import CircuitBreaker, CircuitState


def _breaker(**overrides) -> CircuitBreaker:
    settings = {
        "failure_threshold": 2,
        "error_rate_threshold": 1.0,
        "min_requests": 10,
        "recovery_timeout_seconds": 10,
        "recovery_success_threshold": 1,
    }
    settings.update(overrides)
    return CircuitBreaker(**settings)


def test_opening_records_a_transition_with_its_trigger():
    breaker = _breaker()

    breaker.acquire(now=0)
    breaker.record_failure(now=0)
    # 一次失败还不到阈值：没有迁移，就不该有记录。
    assert breaker.transitions() == ()

    breaker.acquire(now=1)
    breaker.record_failure(now=1)

    transitions = breaker.transitions()
    assert len(transitions) == 1
    event = transitions[0]
    assert event["from_state"] == CircuitState.CLOSED.value
    assert event["to_state"] == CircuitState.OPEN.value
    assert event["reason"] == "failure_threshold"
    assert event["at"] == 1
    # 触发时的连续失败数是判断「阈值配得合不合理」的唯一依据。
    assert event["failure_count"] == 2


def test_error_rate_and_threshold_are_distinguishable_triggers():
    """两种打开原因的处置不同：一个调阈值，一个查上游稳定性。"""
    breaker = _breaker(failure_threshold=1000, error_rate_threshold=0.5, min_requests=4)

    for timestamp, failed in enumerate((True, False, True, True)):
        breaker.acquire(now=timestamp)
        if failed:
            breaker.record_failure(now=timestamp)
        else:
            breaker.record_success(now=timestamp)

    transitions = breaker.transitions()
    assert [event["reason"] for event in transitions] == ["error_rate"]
    assert transitions[0]["error_rate"] >= 0.5


def test_recovery_path_records_half_open_and_close():
    breaker = _breaker()

    breaker.acquire(now=0)
    breaker.record_failure(now=0)
    breaker.acquire(now=1)
    breaker.record_failure(now=1)
    assert breaker.state(now=1) == CircuitState.OPEN

    # 恢复等待时间走完：open → half-open 由时间驱动，不是由某次调用驱动，
    # 因此这条迁移必须在状态刷新时就记下来，否则没有任何调用方会记录它。
    assert breaker.acquire(now=11) is True
    breaker.record_success(now=11)

    reasons = [event["reason"] for event in breaker.transitions()]
    assert reasons == ["failure_threshold", "recovery_timeout", "recovery_success"]
    assert breaker.transitions()[-1]["to_state"] == CircuitState.CLOSED.value


def test_a_failed_probe_reopens_and_says_so():
    breaker = _breaker()

    breaker.acquire(now=0)
    breaker.record_failure(now=0)
    breaker.acquire(now=1)
    breaker.record_failure(now=1)
    breaker.acquire(now=11)
    breaker.record_failure(now=11)

    reasons = [event["reason"] for event in breaker.transitions()]
    # 半开探测失败重新打开，与「首次因阈值打开」是两件事：前者说明上游还没好，
    # 后者说明刚开始出问题。混成同一个原因会让恢复过程无法复盘。
    assert reasons == ["failure_threshold", "recovery_timeout", "half_open_probe_failed"]


def test_transition_history_is_bounded():
    """迁移记录不能无界增长——它活在每个 Provider 的内存里。"""
    breaker = _breaker(failure_threshold=1, recovery_timeout_seconds=0)

    for cycle in range(200):
        breaker.acquire(now=cycle)
        breaker.record_failure(now=cycle)

    transitions = breaker.transitions()
    assert 0 < len(transitions) <= 64
    # 保留的必须是**最近**的，不是最早的：排查看的是刚才发生了什么。
    assert transitions[-1]["at"] == 199


def test_transitions_never_carry_credentials_or_upstream_text():
    """迁移记录会经 `/llm/resilience/status` 出到面板，只能有固定字段。"""
    breaker = _breaker()
    breaker.acquire(now=0)
    breaker.record_failure(now=0)
    breaker.acquire(now=1)
    breaker.record_failure(now=1)

    event = breaker.transitions()[0]
    assert set(event) == {
        "from_state",
        "to_state",
        "reason",
        "at",
        "failure_count",
        "error_rate",
    }
    for value in event.values():
        assert isinstance(value, (str, int, float))
