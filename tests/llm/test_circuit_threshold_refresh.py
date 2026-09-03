"""改过的熔断参数必须立刻生效，而不是等到重启（需求 8）。

九个容错参数里有五个属于熔断器（`circuit_failure_threshold`、
`circuit_error_rate_threshold`、`circuit_min_requests`、
`circuit_recovery_timeout_seconds`、`circuit_recovery_success_threshold`）。
它们在界面上可编辑、API schema 收得下、运行时也真的读——但读的是**构造熔断器
那一刻**的值：`_initialize_resilience_state()` 用 `setdefault` 建 breaker，
已存在的那个不会被新参数重建。

于是有一条真实路径上「保存成功」与「生效」不是一回事：编辑一个当前**未加载**的
后端（`enable=False`，或进程启动后从未成功加载过）时，`PUT /llm/backends/<name>`
只在 `original_backend.enable and backend_was_loaded` 成立时才 unload——两个条件
都不成立，breaker 就留着旧阈值活到重启。这个后端也不在 `get_resilience_status()`
的行里（那里只遍历 `active_backends`），所以界面上连「重置熔断器」这个变通入口
都没有：用户把失败阈值从 8 改成 3，保存成功，而下一次故障仍按 8 次才熔断。

修法是让配置成为唯一事实来源：`_initialize_resilience_state()` 对**已存在**的
breaker 刷新阈值，而不是跳过。刷新必须保住运行时状态（当前是否熔断、在途请求数、
已积累的成败样本）——把 breaker 整个换掉等于每次编辑配置都把一个正在熔断的上游
重新当作健康，那比参数晚生效更糟。
"""

from __future__ import annotations

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
from kirara_ai.llm.resilience import CircuitBreaker, CircuitState


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


def _manager(**backend_overrides) -> tuple[LLMManager, GlobalConfig]:
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name="p1", adapter="fake", models=["model-a"], **backend_overrides)
    ]
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, config)
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(EventBus, EventBus())
    manager = LLMManager(container)
    adapter = StubAdapter("p1")
    manager.active_backends = {"model-a": [adapter]}
    manager.backends = {"p1": adapter}
    manager._initialize_resilience_state()
    return manager, config


class TestManagerRefreshesThresholds:
    def test_a_changed_failure_threshold_takes_effect_without_a_restart(self):
        """改小失败阈值后，下一次初始化就该按新值熔断。"""
        manager, config = _manager(circuit_failure_threshold=8)
        breaker = manager._resilience_breakers["p1"]
        assert breaker.failure_threshold == 8

        config.llms.api_backends[0].circuit_failure_threshold = 3
        manager._initialize_resilience_state()

        assert manager._resilience_breakers["p1"].failure_threshold == 3

    def test_every_circuit_parameter_is_refreshed(self):
        """五个参数一个都不能漏：漏一个就是一个静默失效的输入框。"""
        manager, config = _manager()
        backend = config.llms.api_backends[0]
        backend.circuit_failure_threshold = 4
        backend.circuit_error_rate_threshold = 0.9
        backend.circuit_min_requests = 25
        backend.circuit_recovery_timeout_seconds = 120.0
        backend.circuit_recovery_success_threshold = 5

        manager._initialize_resilience_state()

        breaker = manager._resilience_breakers["p1"]
        assert breaker.failure_threshold == 4
        assert breaker.error_rate_threshold == pytest.approx(0.9)
        assert breaker.min_requests == 25
        assert breaker.recovery_timeout_seconds == pytest.approx(120.0)
        assert breaker.recovery_success_threshold == 5

    def test_the_breaker_object_is_kept_not_replaced(self):
        """必须刷新同一个对象，不能换一个新的。

        换掉等于把一个正在熔断的上游重新当作健康——每次编辑任何配置都会
        清空隔离状态，比参数晚生效更糟。
        """
        manager, config = _manager()
        original = manager._resilience_breakers["p1"]

        config.llms.api_backends[0].circuit_failure_threshold = 6
        manager._initialize_resilience_state()

        assert manager._resilience_breakers["p1"] is original

    def test_an_open_breaker_stays_open_across_a_config_refresh(self):
        """刷新参数不得让隔离状态复活。"""
        manager, config = _manager(circuit_failure_threshold=1)
        breaker = manager._resilience_breakers["p1"]
        breaker.acquire()
        breaker.record_failure()
        assert breaker.snapshot()["state"] == CircuitState.OPEN.value

        config.llms.api_backends[0].circuit_error_rate_threshold = 0.8
        manager._initialize_resilience_state()

        assert manager._resilience_breakers["p1"].snapshot()["state"] == CircuitState.OPEN.value

    def test_in_flight_requests_survive_a_refresh(self):
        """在途计数不能被刷新清零，否则面板上的活跃连接会凭空少掉几条。"""
        manager, config = _manager()
        breaker = manager._resilience_breakers["p1"]
        breaker.acquire()
        breaker.acquire()
        assert breaker.in_flight == 2

        config.llms.api_backends[0].circuit_min_requests = 30
        manager._initialize_resilience_state()

        assert manager._resilience_breakers["p1"].in_flight == 2


class TestBreakerReconfigure:
    def test_it_preserves_the_recorded_outcomes(self):
        """已积累的成败样本要留着：错误率是按窗口内样本算的。

        清掉样本等于把「刚刚连错五次」变成「没有任何记录」，
        而错误率阈值那一支要攒够 `min_requests` 条才生效。
        """
        breaker = CircuitBreaker(failure_threshold=99, min_requests=4)
        for _ in range(3):
            breaker.acquire()
            breaker.record_failure()

        breaker.reconfigure(failure_threshold=99, min_requests=4)

        assert breaker.snapshot()["error_rate"] == pytest.approx(1.0)

    def test_a_larger_min_requests_grows_the_sample_window(self):
        """窗口至少要能装下 `min_requests` 条，否则错误率阈值被静默关掉。

        `_should_open` 的错误率分支判断 `len(outcomes) >= min_requests`；
        窗口比它小时该条件永假，用户配的错误率阈值一次都不会命中。
        """
        breaker = CircuitBreaker(failure_threshold=99, min_requests=4, history_size=4)

        breaker.reconfigure(min_requests=40)

        assert breaker._outcomes.maxlen is not None
        assert breaker._outcomes.maxlen >= 40

    def test_a_smaller_window_keeps_the_most_recent_samples(self):
        """收窄窗口时保留最近的样本，丢最早的——健康判断看的是当下。"""
        breaker = CircuitBreaker(failure_threshold=99, min_requests=10, history_size=10)
        for succeeded in (True, True, False, False):
            breaker.acquire()
            breaker.record_success() if succeeded else breaker.record_failure()

        breaker.reconfigure(min_requests=2, history_size=2)

        assert list(breaker._outcomes) == [False, False]

    def test_values_are_clamped_the_same_way_the_constructor_clamps_them(self):
        """刷新与构造必须同一套边界，否则改一次配置就能绕过校验。"""
        breaker = CircuitBreaker()

        breaker.reconfigure(
            failure_threshold=0,
            error_rate_threshold=5.0,
            min_requests=0,
            recovery_timeout_seconds=-10.0,
            recovery_success_threshold=0,
        )

        assert breaker.failure_threshold == 1
        assert breaker.error_rate_threshold == pytest.approx(1.0)
        assert breaker.min_requests == 1
        assert breaker.recovery_timeout_seconds == pytest.approx(0.0)
        assert breaker.recovery_success_threshold == 1

    def test_omitted_parameters_are_left_alone(self):
        """只传要改的那一个：其余保持原值，不被默认值悄悄覆盖。"""
        breaker = CircuitBreaker(
            failure_threshold=7,
            error_rate_threshold=0.75,
            min_requests=13,
            recovery_timeout_seconds=45.0,
            recovery_success_threshold=4,
        )

        breaker.reconfigure(failure_threshold=2)

        assert breaker.failure_threshold == 2
        assert breaker.error_rate_threshold == pytest.approx(0.75)
        assert breaker.min_requests == 13
        assert breaker.recovery_timeout_seconds == pytest.approx(45.0)
        assert breaker.recovery_success_threshold == 4

    def test_the_transition_history_is_preserved(self):
        """迁移历史是复盘一次隔离的依据，刷新参数不该把它清掉。"""
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.acquire()
        breaker.record_failure()
        assert breaker.transitions()

        breaker.reconfigure(failure_threshold=5)

        assert breaker.transitions()
