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
from typing import Any, Callable, Deque, Iterable, Iterator, Optional


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
        self._outcomes: Deque[bool] = deque(maxlen=max(1, history_size))
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: Optional[float] = None
        self._half_open_probe_in_flight = False
        self._recovery_successes = 0

    def _now(self, now: Optional[float]) -> float:
        return self._clock() if now is None else now

    def _refresh_state(self, now: float) -> CircuitState:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if now - self._opened_at >= self.recovery_timeout_seconds:
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
            self._state = CircuitState.CLOSED
            self._refresh_state(timestamp)

    def record_failure(self, now: Optional[float] = None) -> None:
        with self._lock:
            timestamp = self._now(now)
            self._outcomes.append(False)
            self._consecutive_failures += 1
            self._half_open_probe_in_flight = False
            self._recovery_successes = 0
            if self._state == CircuitState.HALF_OPEN or self._should_open():
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
