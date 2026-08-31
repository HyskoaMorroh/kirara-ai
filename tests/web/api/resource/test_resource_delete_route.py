"""需求 22.3：资源要能被卸载，而不只是被停用。

`ResourceLifecycleService.remove` 早就实现完整（备份当前版本、写注册表、留审计、
要求显式确认），但仓库里没有任何路由调用它。实际后果：一个装错的 Skill、
一个不再用的 MCP 条目、一份写坏的 Prompt，只能永久留在资源列表里被「停用」，
而停用不释放磁盘、不清注册表，也不让那个 ID 重新可用——想重装同名资源会撞
「重复 ID」。运维唯一的办法是登服务器手改 `registry.json`。

卸载是不可逆的外部动作，因此边界与依赖安装一致：
创建者身份 + 显式确认 + 留审计。停用不需要这些（停用只是让它不生效），
两者不能共用一条口径。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.agent_runtime import AgentRegistry
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService

HASH_SKILL = "b" * 64


def _make_api(tmp_path: Path, *, creator: bool = True):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(creator=creator))
    lifecycle = ResourceLifecycleService(tmp_path / "runtime")
    container.register(ResourceLifecycleService, lifecycle)
    sources = ResourceSourceService(lifecycle)
    container.register(ResourceSourceService, sources)
    dependencies = SystemDependencyService(tmp_path / "runtime")
    container.register(SystemDependencyService, dependencies)
    container.register(
        ResourceCatalogService,
        ResourceCatalogService(lifecycle, sources, dependencies),
    )
    container.register(AgentRegistry, AgentRegistry(tmp_path / "agents"))
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _register_skill(lifecycle: ResourceLifecycleService, resource_id: str) -> Path:
    """把一个已启用的技能塞进注册表，并在磁盘上放出它的版本目录。"""
    version_path = lifecycle.installed_path / resource_id / "1.0.0"
    version_path.mkdir(parents=True, exist_ok=True)
    (version_path / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    with lifecycle._lock:  # noqa: SLF001 - 测试直接构造注册表状态
        registry = lifecycle._registry
        registry["resources"][resource_id] = {
            "resource_id": resource_id,
            "type": "skill",
            "current_version": "1.0.0",
            "enabled": True,
            "workflow_id": None,
            "versions": [
                {
                    "version": "1.0.0",
                    "entry": "SKILL.md",
                    "content_sha256": HASH_SKILL,
                    "source": "local",
                    "permissions": [],
                }
            ],
        }
        lifecycle._write_registry(registry)
    return version_path


def _audit_operations(lifecycle: ResourceLifecycleService) -> list[str]:
    if not lifecycle.audit_path.exists():
        return []
    operations: list[str] = []
    for line in lifecycle.audit_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            operations.append(str(json.loads(line).get("operation")))
    return operations


@pytest.mark.asyncio
async def test_delete_route_removes_the_resource(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    _register_skill(lifecycle, "skill.obsolete")

    response = await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={"confirmed": True}
    )

    # 回归点：路由不存在时这里是 405（同路径的 GET 存在，DELETE 不存在）。
    assert response.status_code == 200
    listing = await (await client.get("/api/resources", headers=_headers())).get_json()
    assert all(item["resource_id"] != "skill.obsolete" for item in listing)


@pytest.mark.asyncio
async def test_delete_requires_explicit_confirmation(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    _register_skill(lifecycle, "skill.obsolete")

    response = await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={}
    )

    # 卸载不可逆。没有确认就执行，等于把一次误点变成一次删除。
    # 409 而不是 400：请求本身合法，缺的是一个前置条件——与依赖安装的
    # `DependencyInstallConfirmationRequired` 同一口径。
    assert response.status_code == 409
    assert "skill.obsolete" in lifecycle._registry["resources"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_delete_backs_up_the_current_version_first(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    _register_skill(lifecycle, "skill.obsolete")

    await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={"confirmed": True}
    )

    backups = await (await client.get("/api/resources/backups", headers=_headers())).get_json()
    # 删之前先留一份备份：卸载错了还能恢复，否则「删」和「丢」是同一件事。
    assert any(entry.get("resource_id") == "skill.obsolete" for entry in backups)


@pytest.mark.asyncio
async def test_delete_removes_the_installed_directory(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    version_path = _register_skill(lifecycle, "skill.obsolete")

    await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={"confirmed": True}
    )

    # 只从注册表里摘掉却留着目录，等于卸载不释放磁盘，而且重装同名资源会撞已存在的路径。
    assert not version_path.exists()


@pytest.mark.asyncio
async def test_delete_is_audited(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    _register_skill(lifecycle, "skill.obsolete")

    await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={"confirmed": True}
    )

    assert "remove" in _audit_operations(lifecycle)


@pytest.mark.asyncio
async def test_delete_of_an_unknown_resource_is_404(tmp_path: Path):
    client, _ = _make_api(tmp_path)

    response = await client.delete(
        "/api/resources/skill.nope", headers=_headers(), json={"confirmed": True}
    )

    # 「没有这个资源」是客户端问题，不是服务器故障。
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_rejects_unsupported_fields(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path)
    _register_skill(lifecycle, "skill.obsolete")

    response = await client.delete(
        "/api/resources/skill.obsolete",
        headers=_headers(),
        json={"confirmed": True, "keep_backup": False},
    )

    # 不接受顺带改行为：一个被静默忽略的 `keep_backup: false` 会让调用方以为
    # 备份没留，或者反过来以为留了。
    assert response.status_code == 400
    assert "skill.obsolete" in lifecycle._registry["resources"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_non_creator_cannot_delete(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path, creator=False)
    _register_skill(lifecycle, "skill.obsolete")

    response = await client.delete(
        "/api/resources/skill.obsolete", headers=_headers(), json={"confirmed": True}
    )

    # 与依赖安装同一边界：删除会写磁盘并改变服务器能提供什么，要创建者身份。
    # 403 而不是 401——token 有效，缺的是身份。
    assert response.status_code == 403
    assert "skill.obsolete" in lifecycle._registry["resources"]  # noqa: SLF001
