"""Bounded timeout and priority contracts for provider selection.

These cover three defects that were reachable from the shipped configuration
surface:

1. A stream had no configurable total deadline of its own and silently fell
   back to the legacy ``request_timeout_seconds``, so a backend that only
   configured the newer timeout keys still ran streams on the old default.
2. ``get_llm`` picked a provider at random, bypassing the deterministic
   priority queue that ``get_provider_candidates`` already establishes.
3. Nothing rejected a stream activity budget larger than the stream's own
   total deadline, so first-byte plus idle could exceed the deadline that is
   supposed to bound them.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry


class AdapterConfig(BaseModel):
    pass


class StubAdapter(LLMBackendAdapter, LLMChatProtocol):
    def __init__(self, name: str):
        self.backend_name = name

    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        return LLMChatResponse(
            model=req.model or "model-a",
            message=Message(role="assistant", content=[LLMChatTextContent(text="ok")]),
        )


def _manager(backends: list[tuple[str, int]]) -> LLMManager:
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name=name, adapter="fake", priority=priority, models=["model-a"])
        for name, priority in backends
    ]
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, config)
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(EventBus, EventBus())
    manager = LLMManager(container)
    adapters = [StubAdapter(name) for name, _ in backends]
    manager.active_backends = {"model-a": adapters}
    manager.backends = {adapter.backend_name: adapter for adapter in adapters}
    manager._initialize_resilience_state()
    return manager


def test_get_llm_follows_the_priority_queue_instead_of_choosing_at_random():
    manager = _manager([("low-priority", 900), ("high-priority", 1)])

    for _ in range(20):
        selected = manager.get_llm("model-a")
        assert selected is not None
        assert selected.backend_name == "high-priority"


def test_get_llm_skips_a_provider_excluded_from_the_failover_queue():
    manager = _manager([("excluded", 1), ("eligible", 2)])
    config = manager.container.resolve(GlobalConfig)
    config.llms.api_backends[0].participate_in_failover = False

    selected = manager.get_llm("model-a")

    assert selected is not None
    assert selected.backend_name == "eligible"


def test_get_llm_still_answers_when_every_provider_left_the_failover_queue():
    """Excluding a provider from failover must not remove it from single-provider use."""
    manager = _manager([("solo", 1)])
    config = manager.container.resolve(GlobalConfig)
    config.llms.api_backends[0].participate_in_failover = False

    selected = manager.get_llm("model-a")

    assert selected is not None
    assert selected.backend_name == "solo"


def test_get_llm_returns_none_for_an_unknown_model():
    manager = _manager([("solo", 1)])

    assert manager.get_llm("model-missing") is None


def test_stream_deadline_prefers_the_stream_total_timeout_over_the_legacy_key():
    backend = LLMBackendConfig(
        name="explicit",
        adapter="fake",
        stream_total_timeout_seconds=222.0,
        request_timeout_seconds=11.0,
    )

    assert LLMManager._stream_timeout(backend) == pytest.approx(222.0)


def test_stream_deadline_falls_back_to_the_legacy_key_when_unset():
    backend = LLMBackendConfig(name="legacy", adapter="fake", request_timeout_seconds=11.0)

    assert LLMManager._stream_timeout(backend) == pytest.approx(11.0)


def test_stream_deadline_has_a_default_for_a_missing_backend():
    assert LLMManager._stream_timeout(None) == pytest.approx(60.0)


def test_stream_activity_budget_cannot_exceed_the_stream_total_deadline():
    with pytest.raises(ValidationError) as error:
        LLMBackendConfig(
            name="over-budget",
            adapter="fake",
            stream_total_timeout_seconds=20.0,
            stream_first_byte_timeout_seconds=15.0,
            stream_idle_timeout_seconds=30.0,
        )

    assert "stream_total_timeout_seconds" in str(error.value)


def test_non_stream_activity_budget_is_validated_against_its_own_total():
    with pytest.raises(ValidationError):
        LLMBackendConfig(
            name="over-budget-sync",
            adapter="fake",
            non_stream_timeout_seconds=5.0,
            max_retries=3,
            retry_backoff_seconds=10.0,
            retry_backoff_max_seconds=10.0,
        )


def test_retry_backoff_max_cannot_be_smaller_than_the_initial_backoff():
    with pytest.raises(ValidationError):
        LLMBackendConfig(
            name="inverted-backoff",
            adapter="fake",
            retry_backoff_seconds=9.0,
            retry_backoff_max_seconds=1.0,
        )


def test_cc_switch_recommended_resilience_values_are_accepted():
    backend = LLMBackendConfig(
        name="cc-switch-parity",
        adapter="fake",
        max_retries=6,
        circuit_failure_threshold=8,
        stream_first_byte_timeout_seconds=90.0,
        stream_idle_timeout_seconds=180.0,
        stream_total_timeout_seconds=900.0,
        non_stream_timeout_seconds=600.0,
        circuit_recovery_success_threshold=3,
        circuit_recovery_timeout_seconds=90.0,
        circuit_error_rate_threshold=0.7,
        circuit_min_requests=15,
    )

    assert backend.stream_total_timeout_seconds == pytest.approx(900.0)
    assert backend.circuit_min_requests == 15
