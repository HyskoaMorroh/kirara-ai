"""Bounded provider failover primitives for synchronous and streaming LLM adapters."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable, Deque, Iterable, Iterator, Mapping, Optional


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class ErrorCategory(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UPSTREAM = "upstream"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    POLICY_REJECTION = "policy_rejection"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


RETRYABLE_ERROR_CATEGORIES = frozenset(
    {
        ErrorCategory.NETWORK,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.UPSTREAM,
    }
)

#: 每个熔断器保留的状态迁移条数。
#:
#: 需求 21.3 要求熔断「记录触发与恢复证据」。快照只能回答「现在是什么状态」，
#: 回答不了「什么时候、因为什么变成这个状态」——而后者才是复盘一次隔离所需的。
#: 上界是刻意的：这份历史活在每个 Provider 的内存里，无界增长会让一个持续抖动的
#: 上游把内存吃掉。64 条足够覆盖一次完整的 open → half-open → closed 反复。
CIRCUIT_TRANSITION_HISTORY = 64


def _status_code(error: BaseException) -> Optional[int]:
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def classify_llm_error(error: BaseException) -> ErrorCategory:
    """Classify an error conservatively before any request is replayed."""

    if isinstance(error, RequestCancelledError):
        return ErrorCategory.CANCELLED

    status_code = _status_code(error)
    if status_code in (401, 403):
        return ErrorCategory.AUTHENTICATION
    if status_code == 408:
        return ErrorCategory.TIMEOUT
    if status_code == 429:
        return ErrorCategory.RATE_LIMIT
    if status_code is not None and 500 <= status_code <= 599:
        return ErrorCategory.UPSTREAM

    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ErrorCategory.TIMEOUT
    if isinstance(error, (ConnectionError, OSError)):
        return ErrorCategory.NETWORK

    message = str(error).lower()
    if any(token in message for token in ("content policy", "safety refusal", "safety filter", "policy rejection")):
        return ErrorCategory.POLICY_REJECTION
    if any(token in message for token in ("unauthorized", "forbidden", "invalid api key", "authentication failed")):
        return ErrorCategory.AUTHENTICATION
    if any(token in message for token in ("invalid request", "validation error", "invalid parameter", "bad request")):
        return ErrorCategory.INVALID_REQUEST
    if any(token in message for token in ("timeout", "timed out", "deadline exceeded")):
        return ErrorCategory.TIMEOUT
    if any(token in message for token in ("connection refused", "connection reset", "name or service not known")):
        return ErrorCategory.NETWORK
    return ErrorCategory.UNKNOWN


def sanitize_error_summary(error: BaseException) -> str:
    """Return a bounded error summary without credentials or full payloads."""

    summary = str(error).strip() or error.__class__.__name__
    summary = re.sub(r"(?i)(authorization|x-api-key|api[-_ ]?key|token|cookie)\s*[:=]\s*[^,;\s]+", r"\1=[redacted]", summary)
    summary = re.sub(r"(?i)bearer\s+[^\s]+", "Bearer [redacted]", summary)
    return summary[:240]


class CircuitBreaker:
    """A three-state breaker with one permit for a half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        error_rate_threshold: float = 0.5,
        min_requests: int = 10,
        recovery_timeout_seconds: float = 30.0,
        recovery_success_threshold: int = 1,
        history_size: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.error_rate_threshold = min(1.0, max(0.0, error_rate_threshold))
        self.min_requests = max(1, min_requests)
        self.recovery_timeout_seconds = max(0.0, recovery_timeout_seconds)
        self.recovery_success_threshold = max(1, recovery_success_threshold)
        self._clock = clock
        self._lock = threading.RLock()
        # 样本窗口至少要能容纳 min_requests 条结果：_should_open 的错误率分支
        # 判断 len(outcomes) >= min_requests，窗口比它小的话该条件永假，
        # 配置里的错误率阈值就被静默关掉，只剩连续失败阈值起作用。
        # history_size 仍是下界，用来保证「窗口不至于太短」这一原本意图。
        self._outcomes: Deque[bool] = deque(
            maxlen=max(1, history_size, self.min_requests)
        )

        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: Optional[float] = None
        self._half_open_probe_in_flight = False
        self._recovery_successes = 0
        self._transitions: Deque[dict[str, Any]] = deque(
            maxlen=CIRCUIT_TRANSITION_HISTORY
        )

    def _current_error_rate(self) -> float:
        requests = len(self._outcomes)
        if not requests:
            return 0.0
        return 1 - (sum(self._outcomes) / requests)

    def _record_transition(
        self,
        previous: CircuitState,
        current: CircuitState,
        reason: str,
        now: float,
    ) -> None:
        """Append one bounded, secret-free state-transition record.

        字段是固定的六个，全部是数字或枚举字符串：这份历史会经
        ``/llm/resilience/status`` 出到运维面板，不能带上游报文或凭据。
        同状态到同状态不记录——那不是迁移，记下来只会淹没真正的变化。
        """
        if previous == current:
            return
        self._transitions.append(
            {
                "from_state": previous.value,
                "to_state": current.value,
                "reason": reason,
                "at": now,
                "failure_count": self._consecutive_failures,
                "error_rate": self._current_error_rate(),
            }
        )

    def transitions(self) -> tuple[dict[str, Any], ...]:
        """Return the recent state transitions, oldest first."""
        with self._lock:
            return tuple(dict(item) for item in self._transitions)

    def _now(self, now: Optional[float]) -> float:
        return self._clock() if now is None else now

    def _refresh_state(self, now: float) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if now - self._opened_at >= self.recovery_timeout_seconds:
                # open → half-open 由时间驱动，不由任何一次调用驱动。
                # 因此它只能在这里记录：没有调用方会「知道」这一刻发生了迁移。
                self._record_transition(
                    CircuitState.OPEN,
                    CircuitState.HALF_OPEN,
                    "recovery_timeout",
                    now,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False
        return self._state

    def state(self, now: Optional[float] = None) -> CircuitState:
        with self._lock:
            return self._refresh_state(self._now(now))

    def acquire(self, now: Optional[float] = None) -> bool:
        with self._lock:
            current = self._refresh_state(self._now(now))
            if current == CircuitState.OPEN:
                return False
            if current == CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    return False
                self._half_open_probe_in_flight = True
            return True

    def _should_open(self) -> bool:
        if self._consecutive_failures >= self.failure_threshold:
            return True
        return (
            len(self._outcomes) >= self.min_requests
            and (1 - (sum(self._outcomes) / len(self._outcomes))) >= self.error_rate_threshold
        )

    def record_success(self, now: Optional[float] = None) -> None:
        with self._lock:
            timestamp = self._now(now)
            current = self._refresh_state(timestamp)
            if current == CircuitState.OPEN:
                return
            if current == CircuitState.HALF_OPEN:
                self._recovery_successes += 1
                self._half_open_probe_in_flight = False
                if self._recovery_successes < self.recovery_success_threshold:
                    return
                self._outcomes.clear()
                self._recovery_successes = 0
            self._outcomes.append(True)
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_probe_in_flight = False
            if current != CircuitState.CLOSED:
                # 探测成功攒够阈值后真正闭合。这一条是「上游恢复了」的证据，
                # 与「还在探测中」必须分开——后者不改变状态，也就没有迁移。
                self._record_transition(
                    current,
                    CircuitState.CLOSED,
                    "recovery_success",
                    timestamp,
                )
            self._state = CircuitState.CLOSED
            self._refresh_state(timestamp)

    def record_failure(self, now: Optional[float] = None) -> None:
        with self._lock:
            timestamp = self._now(now)
            previous = self._state
            self._outcomes.append(False)
            self._consecutive_failures += 1
            self._half_open_probe_in_flight = False
            self._recovery_successes = 0
            if previous == CircuitState.HALF_OPEN or self._should_open():
                if previous == CircuitState.HALF_OPEN:
                    reason = "half_open_probe_failed"
                elif self._consecutive_failures >= self.failure_threshold:
                    reason = "failure_threshold"
                else:
                    reason = "error_rate"
                self._record_transition(previous, CircuitState.OPEN, reason, timestamp)
                self._state = CircuitState.OPEN
                self._opened_at = timestamp

    def record_cancelled(self) -> None:
        """Release an acquired probe without changing health statistics."""
        with self._lock:
            self._half_open_probe_in_flight = False

    def snapshot(self, now: Optional[float] = None) -> dict[str, Any]:
        with self._lock:
            current = self._refresh_state(self._now(now))
            requests = len(self._outcomes)
            return {
                "state": current.value,
                "failure_count": self._consecutive_failures,
                "error_rate": (1 - (sum(self._outcomes) / requests)) if requests else 0.0,
                "requests": requests,
                "recovery_successes": self._recovery_successes,
                "recovery_success_threshold": self.recovery_success_threshold,
                "next_recovery_time": (
                    self._opened_at + self.recovery_timeout_seconds
                    if self._state == CircuitState.OPEN and self._opened_at is not None
                    else None
                ),
            }

    def durable_state(self) -> Optional[dict[str, Any]]:
        """Return the part of this breaker's state worth surviving a restart.

        只有「已打开」或「已在半开探测中」才值得持久化：CLOSED 是默认状态，
        存下来没有意义。这里不导出结果环形缓冲区——错误**率**描述的是最近的真实流量，
        把重启前的窗口搬回来会让一个现在健康的上游被过期样本重新熔断。
        重启后由连续失败阈值接手，这是更保守的选择。
        """
        with self._lock:
            state = self._refresh_state(self._now(None))
            if state == CircuitState.CLOSED:
                return None
            return {
                "state": state.value,
                "consecutive_failures": self._consecutive_failures,
                "recovery_successes": self._recovery_successes,
                # 距今多久之前打开的，恢复时按经过时间继续计时。
                "opened_ago_seconds": (
                    max(0.0, self._clock() - self._opened_at)
                    if self._opened_at is not None
                    else None
                ),
            }

    def restore_durable_state(
        self,
        state: Mapping[str, Any],
        *,
        elapsed_seconds: float = 0.0,
    ) -> bool:
        """Re-apply a persisted state. Returns False when nothing was applied.

        ``elapsed_seconds`` 是「保存到现在」的时长，与保存时记录的
        ``opened_ago_seconds`` 相加，得到熔断已经打开了多久。这样恢复等待时间
        在重启期间继续流逝，而不是从头再等一遍。
        """
        raw_state = str(state.get("state") or "")
        if raw_state not in {CircuitState.OPEN.value, CircuitState.HALF_OPEN.value}:
            return False
        opened_ago = state.get("opened_ago_seconds")
        total_open = 0.0
        if isinstance(opened_ago, (int, float)):
            total_open = max(0.0, float(opened_ago))
        total_open += max(0.0, float(elapsed_seconds))

        with self._lock:
            failures = state.get("consecutive_failures")
            self._consecutive_failures = (
                int(failures) if isinstance(failures, int) and failures >= 0 else 0
            )
            recoveries = state.get("recovery_successes")
            self._recovery_successes = (
                int(recoveries) if isinstance(recoveries, int) and recoveries >= 0 else 0
            )
            self._outcomes.clear()
            self._half_open_probe_in_flight = False
            self._state = CircuitState.OPEN
            self._opened_at = self._clock() - total_open
            # 若恢复等待时间在停机期间已经走完，立刻进入半开而不是继续拒绝。
            self._refresh_state(self._clock())
            return True


@dataclass
class ProviderAttempt:
    trace_id: str
    model: str
    provider: str
    attempt: int
    retry_index: int
    success: bool
    error_category: Optional[str] = None
    error_summary: Optional[str] = None
    started_at: float = 0.0
    first_byte_at: Optional[float] = None
    completed_at: Optional[float] = None
    partial_output: bool = False

    @property
    def ttft_seconds(self) -> Optional[float]:
        if self.first_byte_at is None:
            return None
        return max(0.0, self.first_byte_at - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatExecutionResult:
    response: Any
    trace_id: str
    attempts: list[ProviderAttempt]


class StreamExecutionResult(Iterator[Any]):
    """One-shot stream iterator with live trace and attempt metadata."""

    def __init__(
        self,
        iterator: Iterator[Any],
        *,
        trace_id: str,
        attempts: list[ProviderAttempt],
        on_close: Optional[Callable[[], None]] = None,
    ):
        self._iterator = iterator
        self._on_close = on_close
        self._started = False
        self._closed = False
        self.trace_id = trace_id
        self.attempts = attempts

    def __iter__(self) -> "StreamExecutionResult":
        return self

    def __next__(self) -> Any:
        if self._closed:
            raise StopIteration
        self._started = True
        try:
            return next(self._iterator)
        except BaseException:
            self._closed = True
            raise

    def close(self) -> None:
        """Stop the one-shot stream and close its logical trace exactly once."""
        if self._closed:
            return
        started = self._started
        self._closed = True
        close = getattr(self._iterator, "close", None)
        try:
            if callable(close):
                close()
        finally:
            # Closing an unstarted generator does not execute its body.
            if not started and self._on_close is not None:
                self._on_close()


class RequestCancelledError(RuntimeError):
    """Raised when a caller cancels an in-flight LLM request."""

    def __init__(
        self,
        message: str = "LLM request cancelled",
        *,
        trace_id: Optional[str] = None,
        attempts: Iterable[ProviderAttempt] = (),
    ):
        self.trace_id = trace_id
        self.attempts = list(attempts)
        super().__init__(message)


class StreamInterruptedError(RuntimeError):
    """Raised when a stream fails after at least one visible chunk was emitted."""

    def __init__(
        self,
        message: str,
        *,
        trace_id: str,
        attempts: Iterable[ProviderAttempt],
        cause: Optional[BaseException] = None,
    ):
        self.trace_id = trace_id
        self.attempts = list(attempts)
        self.cause = cause
        super().__init__(message)


class FailoverExecutionError(RuntimeError):
    def __init__(self, message: str, *, attempts: Iterable[ProviderAttempt], cause: Optional[BaseException] = None):
        self.attempts = list(attempts)
        self.cause = cause
        super().__init__(message)
