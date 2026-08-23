import threading
import time
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.resilience import FailoverExecutionError, RequestCancelledError


class AdapterConfig(BaseModel):
    pass


class HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"upstream status {status_code}")
        self.status_code = status_code


class FakeAdapter(LLMBackendAdapter, LLMChatProtocol):
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def chat(self, req):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class BlockingAdapter(FakeAdapter):
    def __init__(self):
        super().__init__(response())
        self.started = threading.Event()

    def chat(self, req):
        self.calls += 1
        self.started.set()
        time.sleep(5)
        return self.outcome


class CancellableBlockingAdapter(BlockingAdapter):
    def __init__(self):
        super().__init__()
        self.cancelled = threading.Event()

    def cancel_pending_request(self, req):
        self.cancelled.set()

    def chat(self, req):
        self.calls += 1
        self.started.set()
        self.cancelled.wait(5)
        return self.outcome


def make_manager(backends):
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(
            name=name,
            adapter="fake",
            priority=priority,
            models=["model-a"],
        )
        for name, priority, _ in backends
    ]
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, config)
    registry = LLMBackendRegistry()
    container.register(LLMBackendRegistry, registry)
    event_bus = EventBus()
    container.register(EventBus, event_bus)
    manager = LLMManager(container)
    manager.active_backends = {"model-a": [adapter for _, _, adapter in backends]}
    manager.backends = {name: adapter for name, _, adapter in backends}
    for name, _, adapter in backends:
        adapter.backend_name = name
    manager._initialize_resilience_state()
    return manager


def response():
    return LLMChatResponse(
        model="model-a",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="ok")],
        ),
    )


def request():
    return LLMChatRequest(
        model="model-a",
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ],
    )


def test_execute_chat_uses_stable_priority_and_switches_on_transient_failure():
    primary = FakeAdapter(HttpError(503))
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("secondary", 20, secondary),
        ("primary", 10, primary),
    ])

    result = manager.execute_chat(request())

    assert result.response == response()
    assert [attempt.provider for attempt in result.attempts] == ["primary", "secondary"]
    assert result.attempts[0].error_category == "upstream"
    assert result.attempts[1].success is True
    assert result.trace_id


def test_execute_chat_does_not_fill_queue_after_authentication_failure():
    primary = FakeAdapter(HttpError(401))
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 10, primary),
        ("secondary", 20, secondary),
    ])

    with pytest.raises(FailoverExecutionError) as error:
        manager.execute_chat(request())

    assert secondary.calls == 0
    assert error.value.attempts[0].error_category == "authentication"


def test_resilience_status_contains_only_sanitized_provider_state():
    primary = FakeAdapter(HttpError(503))
    manager = make_manager([("primary", 1, primary)])
    with pytest.raises(FailoverExecutionError):
        manager.execute_chat(request())

    status = manager.get_resilience_status()
    assert status[0]["provider"] == "primary"
    assert status[0]["priority"] == 1
    assert "status_code" not in str(status)
    assert "Authorization" not in str(status)


def test_execute_chat_stops_waiting_for_a_blocking_adapter_at_deadline():
    blocking = BlockingAdapter()
    manager = make_manager([("blocking", 1, blocking)])

    started = time.monotonic()
    with pytest.raises(FailoverExecutionError) as error:
        manager.execute_chat(request(), deadline_seconds=0.05)

    assert blocking.started.wait(0.2)
    assert time.monotonic() - started < 0.5
    assert error.value.attempts[0].error_category == "timeout"


def test_all_attempts_share_logical_trace_and_non_stream_has_no_fake_first_byte():
    primary = FakeAdapter(HttpError(503))
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])

    result = manager.execute_chat(request())

    assert len({attempt.trace_id for attempt in result.attempts}) == 1
    assert result.attempts[-1].first_byte_at is None


def test_execute_chat_rejects_a_request_cancelled_before_provider_selection():
    primary = FakeAdapter(response())
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    cancellation_event = threading.Event()
    cancellation_event.set()

    with pytest.raises(RequestCancelledError):
        manager.execute_chat(request(), cancellation_event=cancellation_event)

    assert primary.calls == 0
    assert secondary.calls == 0


def test_execute_chat_cancels_a_blocking_attempt_without_failover_or_breaker_failure():
    primary = CancellableBlockingAdapter()
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    cancellation_event = threading.Event()

    def cancel_after_start():
        assert primary.started.wait(0.2)
        cancellation_event.set()

    canceller = threading.Thread(target=cancel_after_start)
    canceller.start()
    started = time.monotonic()
    with pytest.raises(RequestCancelledError):
        manager.execute_chat(request(), cancellation_event=cancellation_event, deadline_seconds=2)
    canceller.join()

    assert time.monotonic() - started < 0.5
    assert primary.cancelled.is_set()
    assert secondary.calls == 0
    assert manager.get_resilience_status()[0]["failure_count"] == 0
    assert manager.get_resilience_status()[0]["recent_attempts"][-1]["error_category"] == "cancelled"


def test_execute_chat_cancels_during_retry_backoff_without_next_attempt():
    primary = FakeAdapter(HttpError(503))
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    cancellation_event = threading.Event()

    def cancel_during_backoff():
        time.sleep(0.03)
        cancellation_event.set()

    canceller = threading.Thread(target=cancel_during_backoff)
    canceller.start()
    with pytest.raises(RequestCancelledError):
        manager.execute_chat(
            request(),
            max_retries=3,
            retry_delay=1,
            cancellation_event=cancellation_event,
        )
    canceller.join()

    assert primary.calls == 1
    assert secondary.calls == 0
    assert manager.get_resilience_status()[0]["failure_count"] == 1
