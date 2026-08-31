"""手动重置熔断器必须真的把它清成 closed，而不是当场从磁盘读回来。

`reset_provider_circuit` 原实现只做两件事：从内存字典里 `pop` 掉那个
Provider，然后调用 `_initialize_resilience_state()` 重建。但重建逻辑把
「字典里没有这个名字」当成**新建**，于是紧接着走 `_restore_circuit_state()`，
从 `data/llm/circuit-state.json` 把停机前的 open / half-open 状态原地读回来。

结果是重置不需要等到重启就已经失效：调用返回成功，界面显示已重置，
下一个请求仍然跳过这个 Provider。这比「重启后复活」更难查——运维会认为
重置按钮坏了，而日志里既没有错误也没有重置痕迹。

修复要求两件事同时成立：
1. 重置后的内存状态是 closed；
2. 持久化文件里该 Provider 的记录被删掉，否则下次进程启动仍会恢复它。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.llm.circuit_store import CircuitBreakerStore
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.resilience import CircuitBreaker, CircuitState


class _StubContainer:
    """最小容器：LLMManager 只用它取 logger 与注册表，不参与本用例的断言。"""

    def __init__(self, config: GlobalConfig) -> None:
        self._values = {"global_config": config}

    def resolve(self, key):  # noqa: ANN001 - 容器协议由被测代码定义
        return self._values.get(getattr(key, "__name__", key))

    def register(self, *_args, **_kwargs) -> None:
        return None


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LLMManager:
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name="primary", adapter="fake", enable=True),
        LLMBackendConfig(name="backup", adapter="fake", enable=True),
    ]
    instance = LLMManager.__new__(LLMManager)
    instance.config = config
    instance.logger = _SilentLogger()
    instance._resilience_breakers = {}
    instance._resilience_attempts = {}
    instance._resilience_initialized = False
    instance._resilience_store = CircuitBreakerStore(tmp_path / "circuit-state.json")
    return instance


class _SilentLogger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    warning = debug = error = info


def _trip(breaker: CircuitBreaker) -> None:
    """把熔断器打到 open。用真实失败计数，不直接改私有字段。"""
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()


def test_reset_leaves_the_breaker_closed_instead_of_restoring_from_disk(manager: LLMManager):
    manager._initialize_resilience_state()
    _trip(manager._resilience_breakers["primary"])
    manager._persist_circuit_state()
    assert manager._resilience_breakers["primary"].state() is CircuitState.OPEN

    manager.reset_provider_circuit("primary")

    # 回归点：原实现在这里仍是 OPEN——pop 之后的重建把它当成新建，
    # 立刻从刚写下的文件里恢复了同一个 open 状态。
    assert manager._resilience_breakers["primary"].state() is CircuitState.CLOSED


def test_reset_removes_the_provider_from_the_durable_file(manager: LLMManager):
    manager._initialize_resilience_state()
    _trip(manager._resilience_breakers["primary"])
    _trip(manager._resilience_breakers["backup"])
    manager._persist_circuit_state()

    manager.reset_provider_circuit("primary")

    stored = manager._resilience_store.load()
    # 只删被重置的那一个，另一个的隔离必须留着——顺手清空等于把
    # 「重置一家」变成「把所有隔离都取消」，而那是一次没人要求的放行。
    assert "primary" not in stored
    assert "backup" in stored


def test_reset_survives_a_fresh_process_reading_the_same_file(manager: LLMManager, tmp_path: Path):
    manager._initialize_resilience_state()
    _trip(manager._resilience_breakers["primary"])
    manager._persist_circuit_state()
    manager.reset_provider_circuit("primary")

    # 模拟重启：换一个全新的 manager 读同一个文件。
    revived = LLMManager.__new__(LLMManager)
    revived.config = manager.config
    revived.logger = _SilentLogger()
    revived._resilience_breakers = {}
    revived._resilience_attempts = {}
    revived._resilience_initialized = False
    revived._resilience_store = CircuitBreakerStore(tmp_path / "circuit-state.json")
    revived._initialize_resilience_state()

    assert revived._resilience_breakers["primary"].state() is CircuitState.CLOSED


def test_reset_of_an_unknown_provider_is_not_an_error(manager: LLMManager):
    manager._initialize_resilience_state()

    # 界面可能对一个刚被删掉的 Provider 发出重置；那是「没有可重置的东西」，
    # 不是服务器故障。
    manager.reset_provider_circuit("never-configured")

    assert "never-configured" not in manager._resilience_breakers


def test_forget_on_the_store_keeps_the_file_valid_for_the_rest(tmp_path: Path):
    store = CircuitBreakerStore(tmp_path / "circuit-state.json")
    first = CircuitBreaker(failure_threshold=2)
    second = CircuitBreaker(failure_threshold=2)
    _trip(first)
    _trip(second)
    store.save({"primary": first, "backup": second})

    store.forget("primary")

    raw = json.loads((tmp_path / "circuit-state.json").read_text(encoding="utf-8"))
    # 删一个条目之后文件仍必须是可读的完整结构，否则 `load()` 会整体丢弃，
    # 把「重置一家」变成「所有隔离都没了」。
    assert raw["breakers"].keys() == {"backup"}
    assert store.load().keys() == {"backup"}


def test_forget_when_no_file_exists_is_a_no_op(tmp_path: Path):
    store = CircuitBreakerStore(tmp_path / "missing.json")

    store.forget("primary")

    assert not (tmp_path / "missing.json").exists()
