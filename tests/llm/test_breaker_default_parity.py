"""熔断器的构造默认值必须与配置字段的默认值一致（需求 8）。

`LLMBackendConfig.circuit_recovery_success_threshold` 默认 **2**，而
`CircuitBreaker.__init__` 的同名参数默认 **1**。两者在正常路径上不冲突——
`_initialize_resilience_state()` 会按配置显式构造。但
`get_resilience_status()` 里有一处兜底::

    breaker = self._resilience_breakers.setdefault(provider_name, CircuitBreaker())

这个无参构造拿到的是 1。于是「配置里写 2、面板上按 1 恢复」——同一个参数在同一个
进程里有两个值，而读面板的人无从判断哪个在生效。

一个参数只能有一个默认值。真正的默认写在配置模型上（那是用户看得见、改得到的
地方），构造函数应当与它对齐而不是各自持有一份。
"""

from __future__ import annotations

import inspect

from kirara_ai.config.global_config import LLMBackendConfig
from kirara_ai.llm.resilience import CircuitBreaker


def _breaker_defaults() -> dict[str, object]:
    signature = inspect.signature(CircuitBreaker.__init__)
    return {
        name: parameter.default
        for name, parameter in signature.parameters.items()
        if parameter.default is not inspect.Parameter.empty
    }


def _config_defaults() -> dict[str, object]:
    fields = LLMBackendConfig.model_fields
    return {name: field.default for name, field in fields.items()}


#: 构造参数名 → 配置字段名。命名不必相同（配置带 `circuit_` 前缀表明它属于哪一组），
#: 但**默认值必须相同**。
_PAIRS = {
    "failure_threshold": "circuit_failure_threshold",
    "error_rate_threshold": "circuit_error_rate_threshold",
    "min_requests": "circuit_min_requests",
    "recovery_timeout_seconds": "circuit_recovery_timeout_seconds",
    "recovery_success_threshold": "circuit_recovery_success_threshold",
}


def test_every_breaker_default_matches_its_config_field():
    breaker = _breaker_defaults()
    config = _config_defaults()
    mismatches = []
    for parameter, field in _PAIRS.items():
        assert parameter in breaker, f"CircuitBreaker 缺少参数 {parameter}"
        assert field in config, f"LLMBackendConfig 缺少字段 {field}"
        if breaker[parameter] != config[field]:
            mismatches.append(
                f"{parameter}={breaker[parameter]!r} 但 {field}={config[field]!r}"
            )
    assert not mismatches, (
        "熔断器构造默认值与配置默认值不一致，无参兜底构造会得到另一套行为："
        f"{mismatches}"
    )


def test_a_bare_breaker_recovers_on_the_configured_threshold():
    """无参构造出的熔断器，恢复阈值必须等于配置的默认值。

    `get_resilience_status()` 用 `setdefault(name, CircuitBreaker())` 兜底，
    因此这条路径上的行为直接由构造默认值决定。
    """
    breaker = CircuitBreaker()
    expected = LLMBackendConfig.model_fields[
        "circuit_recovery_success_threshold"
    ].default
    assert breaker.recovery_success_threshold == expected
