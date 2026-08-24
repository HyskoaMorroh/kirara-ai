import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol, LLMChatStreamProtocol
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage, UsageSource
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.pricing import PriceCatalog, PriceVersion
from kirara_ai.llm.resilience import (
    FailoverExecutionError,
    RequestCancelledError,
    StreamInterruptedError,
)
from kirara_ai.tracing import LLMTracer


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


class StreamingAdapter(LLMBackendAdapter, LLMChatStreamProtocol):
    def __init__(self, events):
        self.events = events
        self.calls = 0
        self.cancelled = threading.Event()

    def stream_chat(self, req):
        self.calls += 1
        for event in self.events:
            if isinstance(event, tuple):
                delay, event = event
                self.cancelled.wait(delay)
            if isinstance(event, BaseException):
                raise event
            yield event

    def cancel_pending_request(self, req):
        self.cancelled.set()


class RecordingTracer:
    def __init__(self):
        self.started = []
        self.completed = []
        self.failed = []

    def start_request_tracking(self, backend_name, request):
        self.started.append((backend_name, request))
        return "logical-trace"

    def complete_request_tracking(self, trace_id, request, response, **kwargs):
        self.completed.append((trace_id, request, response, kwargs))

    def fail_request_tracking(self, trace_id, request, error, **kwargs):
        self.failed.append((trace_id, request, error, kwargs))


class TypeErrorCompleteTracer(RecordingTracer):
    def __init__(self):
        super().__init__()
        self.complete_calls = 0

    def complete_request_tracking(self, trace_id, request, response, **kwargs):
        self.complete_calls += 1
        raise TypeError("trace storage failed internally")


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


def response(text="ok", usage=None):
    return LLMChatResponse(
        model="model-a",
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text=text)],
        ),
        usage=usage,
    )


def request():
    return LLMChatRequest(
        model="model-a",
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ],
    )


def test_optional_observability_dependencies_allow_lightweight_manager_instances():
    manager = object.__new__(LLMManager)

    assert manager._get_llm_tracer() is None
    assert manager._get_price_catalog() is None


def test_optional_observability_dependencies_allow_unregistered_services():
    manager = object.__new__(LLMManager)
    manager.container = DependencyContainer()

    assert manager._get_llm_tracer() is None
    assert manager._get_price_catalog() is None


def test_optional_observability_dependencies_return_registered_services():
    manager = object.__new__(LLMManager)
    manager.container = DependencyContainer()
    tracer = RecordingTracer()
    catalog = PriceCatalog([])
    manager.container.register(LLMTracer, tracer)
    manager.container.register(PriceCatalog, catalog)

    assert manager._get_llm_tracer() is tracer
    assert manager._get_price_catalog() is catalog


@pytest.mark.parametrize("resolver", ["_get_llm_tracer", "_get_price_catalog"])
def test_optional_observability_dependencies_do_not_hide_resolution_failures(resolver):
    manager = object.__new__(LLMManager)
    manager.container = MagicMock()
    manager.container.resolve.side_effect = RuntimeError("container is unavailable")

    with pytest.raises(RuntimeError, match="container is unavailable"):
        getattr(manager, resolver)()


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


def test_execute_chat_persists_one_logical_trace_for_all_provider_attempts():
    primary = FakeAdapter(HttpError(503))
    secondary = FakeAdapter(response())
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])

    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)

    result = manager.execute_chat(request())

    assert result.trace_id == "logical-trace"
    assert len(tracer.started) == 1
    assert len(tracer.completed) == 1
    assert tracer.completed[0][0] == "logical-trace"
    assert [item.provider for item in tracer.completed[0][3]["attempts"]] == [
        "primary",
        "secondary",
    ]
    assert tracer.failed == []


def test_execute_chat_persists_final_provider_and_frozen_cost_snapshot():
    usage = Usage(
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        cached_tokens=200_000,
        cache_write_tokens=100_000,
    )
    primary = FakeAdapter(HttpError(503))
    secondary = FakeAdapter(response(usage=usage))
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)
    manager.container.register(
        PriceCatalog,
        PriceCatalog([
            PriceVersion(
                version_id="secondary-model-a-v1",
                provider="secondary",
                model="model-a",
                effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
                currency="USD",
                input_per_million=Decimal("4"),
                output_per_million=Decimal("12"),
                cache_read_per_million=Decimal("1"),
                cache_write_per_million=Decimal("3"),
            )
        ]),
    )

    result = manager.execute_chat(request())

    assert result.response.usage is not None
    assert result.response.usage.source is UsageSource.PROVIDER
    completed = tracer.completed[0]
    assert completed[3]["backend_name"] == "secondary"
    assert completed[3]["cost_snapshot"].price_version_id == "secondary-model-a-v1"
    assert completed[3]["cost_snapshot"].total_cost == Decimal("9.3")


def test_execute_chat_failure_closes_the_logical_trace_with_attempts():
    manager = make_manager([("primary", 1, FakeAdapter(HttpError(401)))])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)

    with pytest.raises(FailoverExecutionError):
        manager.execute_chat(request())

    assert len(tracer.started) == 1
    assert tracer.completed == []
    assert len(tracer.failed) == 1
    assert tracer.failed[0][0] == "logical-trace"
    assert tracer.failed[0][3]["backend_name"] == "primary"
    assert tracer.failed[0][3]["attempts"][0].error_category == "authentication"


def test_execute_chat_does_not_retry_a_tracer_internal_type_error():
    manager = make_manager([("primary", 1, FakeAdapter(response()))])
    tracer = TypeErrorCompleteTracer()
    manager.container.register(LLMTracer, tracer)

    result = manager.execute_chat(request())

    assert result.response == response()
    assert tracer.complete_calls == 1


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


def test_execute_stream_switches_provider_before_first_visible_chunk():
    primary = StreamingAdapter([HttpError(503)])
    secondary = StreamingAdapter([response("backup")])
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])

    execution = manager.execute_stream(request(), deadline_seconds=1)

    assert list(execution) == [response("backup")]
    assert [attempt.provider for attempt in execution.attempts] == ["primary", "secondary"]
    assert execution.attempts[0].first_byte_at is None
    assert execution.attempts[0].partial_output is False
    assert execution.attempts[1].first_byte_at is not None
    assert execution.attempts[1].partial_output is False


def test_execute_stream_persists_one_logical_trace_with_final_provider_and_usage():
    primary = StreamingAdapter([HttpError(503)])
    secondary = StreamingAdapter([
        response(
            "backup",
            usage=Usage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )
    ])
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)

    execution = manager.execute_stream(request(), deadline_seconds=1)

    assert execution.trace_id == "logical-trace"
    assert list(execution) == [
        response(
            "backup",
            usage=Usage(
                prompt_tokens=4,
                completion_tokens=2,
                total_tokens=6,
                source=UsageSource.PROVIDER,
            ),
        )
    ]
    assert len(tracer.started) == 1
    assert len(tracer.completed) == 1
    assert tracer.failed == []
    completed = tracer.completed[0]
    assert completed[0] == "logical-trace"
    assert completed[2].usage.source is UsageSource.PROVIDER
    assert completed[3]["backend_name"] == "secondary"
    assert [item.provider for item in completed[3]["attempts"]] == [
        "primary",
        "secondary",
    ]


def test_execute_stream_close_before_iteration_closes_logical_trace_once():
    manager = make_manager([("primary", 1, StreamingAdapter([response("unused")]))])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)

    execution = manager.execute_stream(request(), deadline_seconds=1)
    execution.close()
    execution.close()

    assert tracer.completed == []
    assert len(tracer.failed) == 1
    assert tracer.failed[0][0] == "logical-trace"
    assert tracer.failed[0][3]["attempts"] == []


def test_execute_stream_close_after_first_chunk_closes_logical_trace_once():
    manager = make_manager([
        ("primary", 1, StreamingAdapter([response("first"), response("unused")]))
    ])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)
    execution = manager.execute_stream(request(), deadline_seconds=1)

    assert next(execution) == response("first")
    execution.close()
    execution.close()

    assert tracer.completed == []
    assert len(tracer.failed) == 1
    assert tracer.failed[0][0] == "logical-trace"


def test_execute_stream_switches_provider_after_first_byte_timeout():
    primary = StreamingAdapter([(0.2, response("late"))])
    secondary = StreamingAdapter([response("backup")])
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    manager.config.llms.api_backends[0].stream_first_byte_timeout_seconds = 0.03

    execution = manager.execute_stream(request(), deadline_seconds=1)

    assert list(execution) == [response("backup")]
    assert execution.attempts[0].error_category == "timeout"
    assert execution.attempts[0].partial_output is False
    assert secondary.calls == 1


def test_execute_stream_stops_without_failover_after_idle_timeout_with_partial_output():
    primary = StreamingAdapter([
        response("first"),
        (0.2, response("late")),
    ])
    secondary = StreamingAdapter([response("must-not-run")])
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    manager.config.llms.api_backends[0].stream_idle_timeout_seconds = 0.03
    execution = manager.execute_stream(request(), deadline_seconds=1)

    received = []
    with pytest.raises(StreamInterruptedError) as error:
        for chunk in execution:
            received.append(chunk)

    assert received == [response("first")]
    assert secondary.calls == 0
    assert error.value.trace_id == execution.trace_id
    assert error.value.attempts[-1].error_category == "timeout"
    assert error.value.attempts[-1].partial_output is True


def test_execute_stream_partial_failure_closes_the_logical_trace_once():
    primary = StreamingAdapter([
        response("first"),
        HttpError(503),
    ])
    manager = make_manager([("primary", 1, primary)])
    tracer = RecordingTracer()
    manager.container.register(LLMTracer, tracer)

    execution = manager.execute_stream(request(), deadline_seconds=1)
    with pytest.raises(StreamInterruptedError):
        list(execution)

    assert tracer.completed == []
    assert len(tracer.failed) == 1
    failed = tracer.failed[0]
    assert failed[0] == "logical-trace"
    assert failed[3]["backend_name"] == "primary"
    assert failed[3]["attempts"][-1].partial_output is True


def test_execute_stream_total_deadline_applies_after_first_chunk():
    primary = StreamingAdapter([
        response("first"),
        (0.2, response("late")),
    ])
    manager = make_manager([("primary", 1, primary)])
    manager.config.llms.api_backends[0].stream_idle_timeout_seconds = 1
    execution = manager.execute_stream(request(), deadline_seconds=0.05)

    with pytest.raises(StreamInterruptedError) as error:
        list(execution)

    assert error.value.attempts[-1].partial_output is True
    assert error.value.attempts[-1].error_category == "timeout"


def test_execute_stream_propagates_cancellation_without_failover():
    primary = StreamingAdapter([
        response("first"),
        (5, response("late")),
    ])
    secondary = StreamingAdapter([response("must-not-run")])
    manager = make_manager([
        ("primary", 1, primary),
        ("secondary", 2, secondary),
    ])
    cancellation_event = threading.Event()
    execution = manager.execute_stream(
        request(), cancellation_event=cancellation_event, deadline_seconds=2
    )

    iterator = iter(execution)
    assert next(iterator) == response("first")
    cancellation_event.set()
    with pytest.raises(RequestCancelledError) as error:
        next(iterator)

    assert primary.cancelled.wait(0.2)
    assert secondary.calls == 0
    assert error.value.attempts[-1].partial_output is True


def test_execute_stream_keeps_legacy_synchronous_adapter_compatible():
    legacy = FakeAdapter(response("legacy"))
    manager = make_manager([("legacy", 1, legacy)])

    execution = manager.execute_stream(request(), deadline_seconds=1)

    assert list(execution) == [response("legacy")]
    assert legacy.calls == 1
    assert execution.attempts[-1].success is True
    assert execution.attempts[-1].first_byte_at is not None
