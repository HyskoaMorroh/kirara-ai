"""需求 21.1：供应商配置的每一次写操作都要留痕，并且要能从备份恢复。

审计此前只覆盖了 `POST /backends/import` 一条路径。而单条后端的
创建 / 编辑 / 删除恰恰是更敏感的动作：改错一个后端会让整个渠道停摆，
删掉一个后端会让所有指向它的 Agent 立刻失去上游。这三条路径不留痕，
「谁在什么时候把生产上游改成了别的 Key」就无法回答。

恢复同理：定价目录一直有 `/pricing/restore`，供应商配置只有一份
`config.yaml.bak`，且没有任何接口能把它取回来——出错之后只能手工登服务器。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


class _StubManager:
    """只记录调用，不真的建立上游连接。"""

    def __init__(self) -> None:
        self.backends: dict[str, object] = {}

    def load_backend(self, name: str) -> None:
        self.backends[name] = object()

    async def unload_backend(self, name: str) -> None:
        self.backends.pop(name, None)


def _backend(name: str = "provider-a") -> LLMBackendConfig:
    return LLMBackendConfig(
        name=name,
        adapter="stub-adapter",
        config={"api_key": "secret-key", "model": "m"},
        enable=False,
        models=["m"],
    )


def _make_api(tmp_path: Path, *, config_file: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.llms.api_backends = [_backend()]
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService(subject="backend-operator"))
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    lifecycle = ResourceLifecycleService(tmp_path / "runtime")
    container.register(ResourceLifecycleService, lifecycle)

    from kirara_ai.llm.llm_manager import LLMManager

    container.register(LLMManager, _StubManager())

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), config, lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _audit_operations(lifecycle: ResourceLifecycleService) -> list[str]:
    if not lifecycle.audit_path.exists():
        return []
    operations: list[str] = []
    for line in lifecycle.audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("component") == "llm_backend":
            operations.append(str(record.get("operation")))
    return operations


@pytest.mark.asyncio
async def test_creating_a_backend_is_audited(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    client, _, lifecycle = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    response = await client.post(
        "/api/llm/backends",
        headers=_headers(),
        json=_backend("provider-b").model_dump(),
    )

    assert response.status_code == 200
    assert "create" in _audit_operations(lifecycle)


@pytest.mark.asyncio
async def test_updating_a_backend_is_audited(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    client, _, lifecycle = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    payload = _backend().model_dump()
    payload["models"] = ["m", "m2"]
    response = await client.put(
        "/api/llm/backends/provider-a", headers=_headers(), json=payload
    )

    assert response.status_code == 200
    assert "update" in _audit_operations(lifecycle)


@pytest.mark.asyncio
async def test_deleting_a_backend_is_audited(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    client, _, lifecycle = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    response = await client.delete(
        "/api/llm/backends/provider-a", headers=_headers()
    )

    assert response.status_code == 200
    assert "delete" in _audit_operations(lifecycle)


@pytest.mark.asyncio
async def test_audit_never_stores_the_credential(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    client, _, lifecycle = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    await client.post(
        "/api/llm/backends",
        headers=_headers(),
        json=_backend("provider-c").model_dump(),
    )

    text = lifecycle.audit_path.read_text(encoding="utf-8")
    # 留痕记录「谁改了哪个后端」，绝不记录 Key 本身，也不记录主体明文。
    assert "secret-key" not in text
    assert "backend-operator" not in text
    assert "provider-c" in text


@pytest.mark.asyncio
async def test_restore_requires_confirmation_and_reports_a_missing_backup(
    tmp_path: Path, monkeypatch
):
    config_file = tmp_path / "config.yaml"
    client, _, _ = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    unconfirmed = await client.post(
        "/api/llm/backends/restore", headers=_headers(), json={}
    )
    missing = await client.post(
        "/api/llm/backends/restore", headers=_headers(), json={"confirmed": True}
    )

    assert unconfirmed.status_code == 400
    # 备份不存在是「没有可恢复的东西」，不是服务器故障。
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_restore_brings_back_the_previous_provider_set(
    tmp_path: Path, monkeypatch
):
    """恢复只回滚供应商清单，不回滚 `config.yaml` 里其他无关配置。

    `config.yaml` 是整个项目的配置文件。把 `.bak` 整份写回去，会把用户在
    同一份文件里改过的 Web 端口、IM 适配器、工作流一起退回到上一个状态——
    那不是「恢复供应商配置」，那是「回滚全部设置」。所以这里只取
    `llms.api_backends` 一段。
    """
    config_file = tmp_path / "config.yaml"
    client, config, lifecycle = _make_api(tmp_path, config_file=config_file)
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.CONFIG_FILE", str(config_file)
    )

    from kirara_ai.config.config_loader import ConfigLoader

    # 先落一份「好」配置，再做一次会写备份的删除动作。
    ConfigLoader.save_config(str(config_file), config)
    deleted = await client.delete(
        "/api/llm/backends/provider-a", headers=_headers()
    )
    assert deleted.status_code == 200
    assert [item.name for item in config.llms.api_backends] == []

    # 恢复期间用户改过的无关设置不应被 `.bak` 覆盖。
    config.web.port = 18080

    restored = await client.post(
        "/api/llm/backends/restore", headers=_headers(), json={"confirmed": True}
    )

    assert restored.status_code == 200
    payload = await restored.get_json()
    assert payload["data"]["restored_count"] == 1
    assert [item.name for item in config.llms.api_backends] == ["provider-a"]
    assert config.web.port == 18080
    assert "restore" in _audit_operations(lifecycle)

    # 响应里不得回显凭据。
    assert "secret-key" not in str(payload)
