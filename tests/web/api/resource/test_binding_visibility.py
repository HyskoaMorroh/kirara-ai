"""需求 22.3：启用了但没绑定任何 Agent 的 Skill 对会话零影响。

装好、启用之后，界面显示「已启用」。但一个 Skill 只有在被**绑定到某个 Agent**
之后才会进入 LLM 的 system 消息（`executor._build_messages` 遍历
`agent.skill_bindings`）。没有绑定的 Skill 状态是「已启用」，实际效果是零——
用户看到「已启用」，得到的却是「什么都没变」，然后去怀疑模型或提示词。

这不是「功能没做」，是**状态显示与实际效果不一致**：最难自查的一类，
因为界面上没有任何地方在说「它还差一步」。

这些用例要求资源响应带上「有没有被任何 Agent 绑定」这个事实。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.agent_runtime import AgentDefinition, AgentRegistry, ResourceBinding
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


HASH_SKILL = "a" * 64


def _agent(*bindings: ResourceBinding) -> AgentDefinition:
    return AgentDefinition(
        agent_id="agent",
        model_priority=("model",),
        skill_bindings=tuple(bindings),
    )


def _binding(resource_id: str, *, enabled: bool = True) -> ResourceBinding:
    return ResourceBinding(
        resource_id=resource_id,
        resource_type="skill",
        version="1.0.0",
        content_sha256=HASH_SKILL,
        enabled=enabled,
    )


def _base_container(tmp_path: Path) -> tuple[DependencyContainer, ResourceLifecycleService]:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(creator=True))
    lifecycle = ResourceLifecycleService(tmp_path / "runtime")
    container.register(ResourceLifecycleService, lifecycle)
    source_service = ResourceSourceService(lifecycle)
    container.register(ResourceSourceService, source_service)
    dependency_service = SystemDependencyService(tmp_path / "runtime")
    container.register(SystemDependencyService, dependency_service)
    container.register(
        ResourceCatalogService,
        ResourceCatalogService(lifecycle, source_service, dependency_service),
    )
    return container, lifecycle


def _make_api(tmp_path: Path, *, agents: list[AgentDefinition]):
    container, lifecycle = _base_container(tmp_path)
    registry = AgentRegistry(tmp_path / "agents")
    for agent in agents:
        registry.register(agent)
    container.register(AgentRegistry, registry)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _register_skill(lifecycle: ResourceLifecycleService, resource_id: str) -> None:
    """Put one enabled skill into the registry without unpacking an archive."""
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


def _disable_resource(lifecycle: ResourceLifecycleService, resource_id: str) -> None:
    with lifecycle._lock:  # noqa: SLF001
        registry = lifecycle._registry
        registry["resources"][resource_id]["enabled"] = False
        lifecycle._write_registry(registry)


@pytest.mark.asyncio
async def test_an_enabled_but_unbound_skill_says_it_is_not_in_effect(tmp_path: Path):
    client, lifecycle = _make_api(tmp_path, agents=[_agent()])
    _register_skill(lifecycle, "skill.orphan")

    response = await client.get("/api/resources", headers=_headers())

    assert response.status_code == 200
    rows = {item["resource_id"]: item for item in await response.get_json()}
    row = rows["skill.orphan"]

    assert row["enabled"] is True
    # 「已启用」不等于「生效」：没有 Agent 绑定它，system 消息里就没有它。
    assert row["in_effect"] is False
    assert row["bound_agent_ids"] == []


@pytest.mark.asyncio
async def test_a_bound_skill_is_reported_as_in_effect(tmp_path: Path):
    client, lifecycle = _make_api(
        tmp_path, agents=[_agent(_binding("skill.bound"))]
    )
    _register_skill(lifecycle, "skill.bound")

    response = await client.get("/api/resources", headers=_headers())
    rows = {item["resource_id"]: item for item in await response.get_json()}

    assert rows["skill.bound"]["in_effect"] is True
    assert rows["skill.bound"]["bound_agent_ids"] == ["agent"]


@pytest.mark.asyncio
async def test_a_disabled_binding_does_not_count_as_in_effect(tmp_path: Path):
    """绑定存在但被停用时同样不生效——`_build_messages` 会跳过它。"""
    client, lifecycle = _make_api(
        tmp_path, agents=[_agent(_binding("skill.off", enabled=False))]
    )
    _register_skill(lifecycle, "skill.off")

    response = await client.get("/api/resources", headers=_headers())
    rows = {item["resource_id"]: item for item in await response.get_json()}

    assert rows["skill.off"]["in_effect"] is False
    # 绑定关系本身仍然要显示：它解释了「为什么改这个 Agent 会影响这个资源」。
    assert rows["skill.off"]["bound_agent_ids"] == ["agent"]


@pytest.mark.asyncio
async def test_a_resource_disabled_at_the_registry_is_never_in_effect(tmp_path: Path):
    client, lifecycle = _make_api(
        tmp_path, agents=[_agent(_binding("skill.disabled"))]
    )
    _register_skill(lifecycle, "skill.disabled")
    _disable_resource(lifecycle, "skill.disabled")

    response = await client.get("/api/resources", headers=_headers())
    rows = {item["resource_id"]: item for item in await response.get_json()}

    assert rows["skill.disabled"]["in_effect"] is False


@pytest.mark.asyncio
async def test_without_an_agent_registry_the_field_is_absent_rather_than_false(
    tmp_path: Path,
):
    """拿不到 Agent 注册表时不能断言「未生效」。

    `in_effect: false` 是一个论断；在读不到绑定关系的部署里给出它，
    等于告诉用户「你的 Skill 没生效」，而实际情况是「我们不知道」。
    """
    container, lifecycle = _base_container(tmp_path)
    app = create_web_api_app(container)
    app.config["TESTING"] = True
    client = app.test_client()
    _register_skill(lifecycle, "skill.unknown")

    response = await client.get("/api/resources", headers=_headers())
    rows = {item["resource_id"]: item for item in await response.get_json()}

    assert "in_effect" not in rows["skill.unknown"]
    assert "bound_agent_ids" not in rows["skill.unknown"]
