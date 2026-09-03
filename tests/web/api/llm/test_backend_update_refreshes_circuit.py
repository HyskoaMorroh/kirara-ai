"""保存供应商配置后，熔断阈值必须立刻按新值生效（需求 8）。

这是 `tests/llm/test_circuit_threshold_refresh.py` 的路由一侧：那份守的是
`_initialize_resilience_state()` 会刷新已存在的 breaker，这份守的是
**`PUT /llm/backends/<name>` 真的会让它跑一次**。

两者缺一不可。管理器会刷新但没人触发它，与能触发但不刷新，症状完全一样：
用户把失败阈值从 8 改成 3、保存成功，而下一次故障仍按 8 次才熔断。

最容易漏的是「后端当前未加载」这条路径：PUT 只在
`original_backend.enable and backend_was_loaded` 成立时 unload、
只在 `updated_backend.enable` 成立时 load，而这两处是唯一会间接调用
`_initialize_resilience_state()` 的地方。编辑一个 `enable=False` 的后端时
两个条件都不成立——旧阈值就活到重启。这个后端也不在
`get_resilience_status()` 的行里（那里只遍历 `active_backends`），
所以界面上连「重置熔断」这个变通入口都没有。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService

BACKEND = "p1"


def _api(tmp_path: Path, *, enable: bool):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    config = GlobalConfig()
    config.web = WebConfig(secret_key="k", password_file=str(tmp_path / "pw.hash"))
    config.llms.api_backends = [
        LLMBackendConfig(
            name=BACKEND,
            adapter="stub",
            enable=enable,
            models=["model-a"],
            circuit_failure_threshold=8,
            circuit_min_requests=20,
        )
    ]
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService(creator=True))
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(
        ResourceLifecycleService, ResourceLifecycleService(tmp_path / "runtime")
    )
    manager = LLMManager(container)
    container.register(LLMManager, manager)
    # 只建熔断状态，不真的加载适配器：注册表里没有 `stub`，而这份测试要断言的
    # 是阈值刷新，与适配器能否实例化无关。
    manager._initialize_resilience_state()

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), manager, config


def _payload(config: GlobalConfig, **overrides) -> dict:
    backend = config.llms.api_backends[0]
    payload = {
        "name": backend.name,
        "adapter": backend.adapter,
        "config": dict(backend.config),
        "enable": backend.enable,
        "models": [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in backend.models
        ],
    }
    payload.update(overrides)
    return payload


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_a_disabled_backend_still_gets_the_new_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`enable=False` 的后端也要立刻生效——这是最容易漏的那条路径。

    PUT 的 unload / load 两个分支在这里都不成立，于是此前没有任何东西会让
    熔断器重读配置。
    """
    client, manager, config = _api(tmp_path, enable=False)
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)
    assert manager._resilience_breakers[BACKEND].failure_threshold == 8

    response = await client.put(
        f"/api/llm/backends/{BACKEND}",
        headers=_headers(),
        json=_payload(config, circuit_failure_threshold=3),
    )

    assert response.status_code == 200
    assert manager._resilience_breakers[BACKEND].failure_threshold == 3


@pytest.mark.asyncio
async def test_the_other_circuit_parameters_are_refreshed_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """五个参数一个都不能漏：漏一个就是一个静默失效的输入框。"""
    client, manager, config = _api(tmp_path, enable=False)
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        f"/api/llm/backends/{BACKEND}",
        headers=_headers(),
        json=_payload(
            config,
            circuit_error_rate_threshold=0.85,
            circuit_min_requests=30,
            circuit_recovery_timeout_seconds=90.0,
            circuit_recovery_success_threshold=4,
        ),
    )

    assert response.status_code == 200
    breaker = manager._resilience_breakers[BACKEND]
    assert breaker.error_rate_threshold == pytest.approx(0.85)
    assert breaker.min_requests == 30
    assert breaker.recovery_timeout_seconds == pytest.approx(90.0)
    assert breaker.recovery_success_threshold == 4


@pytest.mark.asyncio
async def test_an_unrelated_edit_does_not_cancel_an_active_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """改一个无关字段不得把正在熔断的上游放回队列。

    这是刷新而不是重建 breaker 的理由：重建等于每次编辑配置都取消一次
    正在生效的隔离，而那比参数晚生效更糟——用户改的是超时数字，
    被改掉的是「这家已经被隔离」。
    """
    client, manager, config = _api(tmp_path, enable=False)
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)
    breaker = manager._resilience_breakers[BACKEND]
    for _ in range(8):
        breaker.acquire()
        breaker.record_failure()
    assert breaker.snapshot()["state"] == "open"

    response = await client.put(
        f"/api/llm/backends/{BACKEND}",
        headers=_headers(),
        json=_payload(config, non_stream_timeout_seconds=123.0),
    )

    assert response.status_code == 200
    assert manager._resilience_breakers[BACKEND].snapshot()["state"] == "open"
    assert manager._resilience_breakers[BACKEND] is breaker


def test_the_real_manager_exposes_the_refresh_hook():
    """路由用 `getattr` 容忍替身管理器，因此真实管理器必须有这个方法。

    没有这条断言时，一次改名会让刷新在**生产**里静默变成空操作，
    而上面那几条测试仍然通过——它们用的是同一条 `getattr` 路径。
    """
    from kirara_ai.web.api.llm import routes

    assert callable(getattr(LLMManager, "refresh_resilience_settings", None))
    source = Path(routes.__file__).read_text(encoding="utf-8")
    assert "refresh_resilience_settings" in source, "PUT 路由没有触发容错参数刷新"
