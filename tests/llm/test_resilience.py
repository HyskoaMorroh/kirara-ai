import threading

from kirara_ai.llm.resilience import CircuitBreaker, CircuitState, ErrorCategory, classify_llm_error


class HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"upstream status {status_code}")
        self.status_code = status_code


def test_classifies_only_explicit_transient_errors_as_retryable():
    assert classify_llm_error(HttpError(429)) == ErrorCategory.RATE_LIMIT
    assert classify_llm_error(HttpError(503)) == ErrorCategory.UPSTREAM
    assert classify_llm_error(HttpError(401)) == ErrorCategory.AUTHENTICATION
    assert classify_llm_error(ValueError("invalid request schema")) == ErrorCategory.INVALID_REQUEST
    assert classify_llm_error(RuntimeError("unexpected adapter failure")) == ErrorCategory.UNKNOWN


def test_circuit_breaker_opens_and_allows_one_half_open_probe():
    breaker = CircuitBreaker(
        failure_threshold=2,
        error_rate_threshold=1.0,
        min_requests=10,
        recovery_timeout_seconds=10,
    )

    assert breaker.acquire(now=0) is True
    breaker.record_failure(now=0)
    assert breaker.state(now=1) == CircuitState.CLOSED
    assert breaker.acquire(now=1) is True
    breaker.record_failure(now=1)
    assert breaker.state(now=1) == CircuitState.OPEN
    assert breaker.acquire(now=2) is False
    assert breaker.acquire(now=11) is True
    assert breaker.acquire(now=11) is False
    breaker.record_success(now=11)
    assert breaker.state(now=11) == CircuitState.CLOSED


def test_circuit_breaker_uses_error_rate_after_minimum_sample_size():
    breaker = CircuitBreaker(
        failure_threshold=99,
        error_rate_threshold=0.5,
        min_requests=4,
        recovery_timeout_seconds=10,
    )

    for timestamp, failed in enumerate((True, False, True, True)):
        assert breaker.acquire(now=timestamp) is True
        if failed:
            breaker.record_failure(now=timestamp)
        else:
            breaker.record_success(now=timestamp)

    assert breaker.state(now=3) == CircuitState.OPEN


def test_error_rate_still_applies_when_min_requests_exceeds_the_default_window():
    """``min_requests`` 大于默认样本窗口时，错误率熔断仍必须生效。

    结果窗口是一个定长 deque。窗口固定 20 而 ``circuit_min_requests`` 只校验
    ``ge=1``，于是 ``len(outcomes) >= min_requests`` 永假：配 30 的用户拿到的是
    「错误率熔断被静默关掉」，只剩连续失败阈值。100% 失败率却保持 closed，
    是比不熔断更糟的状态——面板显示健康，请求继续付超时。
    """
    breaker = CircuitBreaker(
        failure_threshold=1000,   # 抬高到不可达，隔离出错误率分支
        error_rate_threshold=0.5,
        min_requests=50,          # 远大于默认 history_size=20
        recovery_timeout_seconds=10,
    )

    for timestamp in range(50):
        assert breaker.acquire(now=timestamp) is True, (
            f"第 {timestamp} 次请求就被拒绝，说明在攒满 min_requests 前已熔断"
        )
        breaker.record_failure(now=timestamp)

    snapshot = breaker.snapshot(now=50)
    assert snapshot["requests"] >= breaker.min_requests, (
        "样本窗口必须能容纳 min_requests 条结果，否则错误率分支永远不触发"
    )
    assert breaker.state(now=50) == CircuitState.OPEN
    # 熔断已打开：恢复等待期内不再放行任何请求。
    assert breaker.acquire(now=50) is False


def test_half_open_requires_configured_successes_before_closing():
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=10,
        recovery_success_threshold=2,
    )

    assert breaker.acquire(now=0) is True
    breaker.record_failure(now=0)
    assert breaker.state(now=10) == CircuitState.HALF_OPEN

    assert breaker.acquire(now=10) is True
    breaker.record_success(now=10)
    assert breaker.state(now=10) == CircuitState.HALF_OPEN
    assert breaker.snapshot(now=10)["recovery_successes"] == 1

    assert breaker.acquire(now=11) is True
    breaker.record_success(now=11)
    assert breaker.state(now=11) == CircuitState.CLOSED


def test_half_open_allows_only_one_concurrent_probe():
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0)
    breaker.acquire(now=0)
    breaker.record_failure(now=0)

    barrier = threading.Barrier(3)
    results = []

    def acquire_probe():
        barrier.wait()
        results.append(breaker.acquire(now=1))

    threads = [threading.Thread(target=acquire_probe) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
