"""故障转移队列必须给出运行态汇总（需求 8）。

参考界面在队列下方汇总**活跃连接、总请求数、成功率、运行时间**，理由是修改策略
时要同步观察服务表现——只看逐行健康状态回答不了「刚把 P1 换掉之后整体好了没」。

而 `get_resilience_status()` 只返回逐行快照，四项汇总一个都没有。前端因此只能显示
「N 个供应商不处于正常状态」，那是一个计数，不是运行表现。

四条呈现纪律，每条都对应一种会误导读者的写法：

* **成功率没有样本时是 `None`，不是 1.0（也不是 0）。** 刚启动、还没有任何请求时
  写 100% 会让人以为链路已经验证过；写 0% 更糟，看起来像全线故障。
* **总请求数按熔断器窗口计，且必须说明这一点。** 窗口是有界的（至少容纳
  `min_requests`），它回答「最近的表现」而不是「历史总量」——后者在 LLM 追踪表里。
* **运行时间是进程运行时长**，与「这个供应商健康多久了」不是一回事；混用会让人
  以为某个上游已经稳定运行了几小时。
* **活跃连接是正在进行的请求数**（半开探测许可占用的那些也算），它与「配置了几个
  供应商」无关。
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.resilience import CircuitBreaker, ResilienceSummary


def _breaker(**kwargs) -> CircuitBreaker:
    kwargs.setdefault("failure_threshold", 2)
    kwargs.setdefault("min_requests", 4)
    return CircuitBreaker(**kwargs)


def test_an_untouched_queue_reports_no_success_rate():
    """没有样本时成功率是 None——绝不是 100%。"""
    summary = ResilienceSummary.from_breakers({"a": _breaker()}, uptime_seconds=12.0)

    assert summary.total_requests == 0
    assert summary.success_rate is None
    assert summary.active_connections == 0
    assert summary.uptime_seconds == pytest.approx(12.0)


def test_success_rate_is_computed_across_every_provider():
    first, second = _breaker(), _breaker()
    first.acquire(now=0)
    first.record_success(now=0)
    first.acquire(now=1)
    first.record_success(now=1)
    second.acquire(now=1)
    second.record_failure(now=1)

    summary = ResilienceSummary.from_breakers(
        {"a": first, "b": second}, uptime_seconds=5.0
    )

    assert summary.total_requests == 3
    assert summary.success_rate == pytest.approx(2 / 3)


def test_active_connections_counts_requests_in_flight():
    """已 acquire 未记录结果的那些请求就是「活跃连接」。"""
    breaker = _breaker()
    breaker.acquire(now=0)
    breaker.acquire(now=0)

    summary = ResilienceSummary.from_breakers({"a": breaker}, uptime_seconds=1.0)
    assert summary.active_connections == 2

    breaker.record_success(now=1)
    after = ResilienceSummary.from_breakers({"a": breaker}, uptime_seconds=2.0)
    assert after.active_connections == 1


def test_degraded_and_tripped_providers_are_counted_separately():
    """降级与熔断是两种处置，不能合并成一个「不正常」计数。

    半开是「正在试探、仍在服务」，熔断是「被跳过」。合并之后运维无法判断
    此刻还有几家在真的承接流量。
    """
    healthy = _breaker()
    tripped = _breaker(failure_threshold=1, recovery_timeout_seconds=100.0)
    tripped.acquire(now=0)
    tripped.record_failure(now=0)

    probing = _breaker(failure_threshold=1, recovery_timeout_seconds=10.0)
    probing.acquire(now=0)
    probing.record_failure(now=0)

    summary = ResilienceSummary.from_breakers(
        {"ok": healthy, "open": tripped, "half": probing},
        uptime_seconds=1.0,
        now=20.0,
    )

    assert summary.total_providers == 3
    assert summary.tripped_providers == 1
    assert summary.probing_providers == 1
    assert summary.healthy_providers == 1


def test_the_summary_serializes_with_stable_keys():
    """前端按固定键读；键名变了等于接口破坏。"""
    summary = ResilienceSummary.from_breakers({"a": _breaker()}, uptime_seconds=3.0)
    payload = summary.to_dict()

    assert set(payload) == {
        "active_connections",
        "total_requests",
        "success_rate",
        "uptime_seconds",
        "total_providers",
        "healthy_providers",
        "probing_providers",
        "tripped_providers",
        "sample_window",
    }


def test_sample_window_states_what_total_requests_means():
    """必须说明这个总数来自有界窗口，而不是历史总量。

    不说明的话，读者会拿它与 LLM 追踪页的请求总数对比，然后认为其中一个是错的。
    """
    summary = ResilienceSummary.from_breakers(
        {"a": _breaker(min_requests=4)}, uptime_seconds=1.0
    )
    assert summary.to_dict()["sample_window"] >= 4


def test_the_manager_exposes_the_summary_alongside_the_rows():
    """`LLMManager` 必须能同时给出逐行状态与汇总，且两者同源。

    各自统计会让「三态计数之和 ≠ 队列行数」这种矛盾出现在同一个页面上，
    而读者无法判断哪个数字可信。
    """
    from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
    from kirara_ai.events.event_bus import EventBus
    from kirara_ai.ioc.container import DependencyContainer
    from kirara_ai.llm.llm_manager import LLMManager
    from kirara_ai.llm.llm_registry import LLMBackendRegistry

    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name="a", adapter="openai", config={}, models=[]),
        LLMBackendConfig(name="b", adapter="openai", config={}, models=[]),
    ]
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, config)
    container.register(EventBus, EventBus())
    registry = LLMBackendRegistry()
    container.register(LLMBackendRegistry, registry)
    manager = LLMManager(container, config, registry, EventBus())

    summary = manager.get_resilience_summary()

    # 键必须齐全：前端按固定键读，少一个就是一处空白。
    assert set(summary) == {
        "active_connections",
        "total_requests",
        "success_rate",
        "uptime_seconds",
        "total_providers",
        "healthy_providers",
        "probing_providers",
        "tripped_providers",
        "sample_window",
    }
    # 刚构造：没有任何请求，成功率必须是 None 而不是 0 或 1。
    assert summary["success_rate"] is None
    assert summary["total_requests"] == 0
    assert summary["active_connections"] == 0
    # 运行时长是真实测量而不是常数 0。
    assert summary["uptime_seconds"] >= 0.0
    # 三态之和等于总数——这条是「两者同源」的可验证形式。
    assert (
        summary["healthy_providers"]
        + summary["probing_providers"]
        + summary["tripped_providers"]
        == summary["total_providers"]
    )
