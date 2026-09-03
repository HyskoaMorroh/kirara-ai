"""登记过的技能仓库必须能删掉（需求 10）。

需求 10 点名「Skills 管理」。参考界面的仓库管理页每一行右侧有两个图标按钮：
打开仓库与**删除仓库**，笔记里还写明「删除属于有影响的操作，应有确认与失败反馈」
（`docs/superpowers/plans/ccs-ui-notes.md` 的 `Image_2026-08-23_033146_102`）。

本项目只有「登记」与「启停」，**没有任何删除路径**——后端没方法、没路由，
前端也没入口。于是一个拼错的坐标（`anthropcis/skills`）会永久留在
`registry.json` 里：它可以被停用，但那条记录再也去不掉，仓库表上永远多一行
说明不了任何事的死项。想清掉只能登服务器手改 JSON。

「停用就够了」不成立：停用表达的是「这个来源暂时不用」，删除表达的是
「这个来源是错的 / 不再存在」。两者在界面上都要能做到，否则用户会为了让列表
干净而去停用一个本该删掉的条目，而下一个人看到的是一个「疑似还能启用」的坐标。

这组测试锁住的边界：

1. **删除只摘掉来源登记，不动已装资源。** 从那个仓库装过的 Skill 已经在服务器上
   独立成包（有自己的清单与摘要），删掉来源不该让它们失效——那会把「不再从这里
   拉新的」变成「把装过的都毁掉」。
2. **未登记的坐标返回 404，不是 500，也不是静默成功。** 静默成功会让一个拼错的
   删除请求看起来和真的删掉一样。
3. **创建者身份 + 显式确认。** 它写 `registry.json`，与启停同一边界；
   而删除不可逆，所以比启停多一道确认。
4. **只删指定的那一条。** 同一个 owner/name 的不同分支是两条独立记录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def _api(tmp_path: Path, *, creator: bool = True):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService(creator=creator))
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    container.register(ResourceLifecycleService, lifecycle)
    sources = ResourceSourceService(lifecycle)
    container.register(ResourceSourceService, sources)
    dependencies = SystemDependencyService(tmp_path / "data")
    container.register(SystemDependencyService, dependencies)
    container.register(
        ResourceCatalogService, ResourceCatalogService(lifecycle, sources, dependencies)
    )

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _coordinates(lifecycle: ResourceLifecycleService) -> set[tuple[str, str, str]]:
    return {
        (item["owner"], item["name"], item["branch"])
        for item in lifecycle.list_source_repositories()
    }


class TestTheService:
    def test_it_removes_one_registration(self, tmp_path: Path):
        lifecycle = ResourceLifecycleService(tmp_path / "data")
        lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

        removed = lifecycle.remove_source_repository("anthropics", "skills", "main")

        assert removed["owner"] == "anthropics"
        assert _coordinates(lifecycle) == set()

    def test_it_only_removes_the_named_branch(self, tmp_path: Path):
        """同一个 owner/name 的不同分支是两条独立记录。"""
        lifecycle = ResourceLifecycleService(tmp_path / "data")
        lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)
        lifecycle.upsert_source_repository("anthropics", "skills", "master", enabled=True)

        lifecycle.remove_source_repository("anthropics", "skills", "main")

        assert _coordinates(lifecycle) == {("anthropics", "skills", "master")}

    def test_removing_an_unknown_repository_raises(self, tmp_path: Path):
        """静默成功会让一个拼错的删除请求看起来和真的删掉一样。"""
        lifecycle = ResourceLifecycleService(tmp_path / "data")

        with pytest.raises(KeyError):
            lifecycle.remove_source_repository("nobody", "nothing", "main")

    def test_the_removal_survives_a_reload(self, tmp_path: Path):
        """删除要落盘：只改内存的话重启后那条死项又回来了。"""
        lifecycle = ResourceLifecycleService(tmp_path / "data")
        lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)
        lifecycle.remove_source_repository("anthropics", "skills", "main")

        reloaded = ResourceLifecycleService(tmp_path / "data")

        assert _coordinates(reloaded) == set()

    def test_installed_resources_are_untouched(self, tmp_path: Path):
        """删掉来源不得让从它装过的资源失效。

        那些资源已经在服务器上独立成包（有自己的清单与摘要）。
        一起删掉等于把「不再从这里拉新的」变成「把装过的都毁掉」。
        """
        lifecycle = ResourceLifecycleService(tmp_path / "data")
        catalog = ResourceCatalogService(lifecycle)
        catalog.install("prompt:office-research")
        lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

        lifecycle.remove_source_repository("anthropics", "skills", "main")

        assert lifecycle.get_resource("prompt.office-research")["type"] == "prompt"


@pytest.mark.asyncio
async def test_the_route_removes_a_registration(tmp_path: Path):
    client, lifecycle = _api(tmp_path)
    lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

    response = await client.delete(
        "/api/resources/repositories/anthropics/skills/main",
        headers=_headers(),
        json={"confirmed": True},
    )

    assert response.status_code == 200, await response.get_data(as_text=True)
    assert _coordinates(lifecycle) == set()


@pytest.mark.asyncio
async def test_the_route_requires_confirmation(tmp_path: Path):
    """删除不可逆，因此比启停多一道确认——与卸载资源同一口径。"""
    client, lifecycle = _api(tmp_path)
    lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

    response = await client.delete(
        "/api/resources/repositories/anthropics/skills/main",
        headers=_headers(),
        json={},
    )

    assert response.status_code == 400
    assert _coordinates(lifecycle) == {("anthropics", "skills", "main")}


@pytest.mark.asyncio
async def test_the_route_rejects_unsupported_fields(tmp_path: Path):
    """不接受顺带改配置：那是登记路由的职责。"""
    client, lifecycle = _api(tmp_path)
    lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

    response = await client.delete(
        "/api/resources/repositories/anthropics/skills/main",
        headers=_headers(),
        json={"confirmed": True, "enabled": False},
    )

    assert response.status_code == 400
    assert _coordinates(lifecycle) == {("anthropics", "skills", "main")}


@pytest.mark.asyncio
async def test_the_route_returns_404_for_an_unknown_repository(tmp_path: Path):
    """「没有这个仓库」是客户端问题，不是服务器故障。"""
    client, _ = _api(tmp_path)

    response = await client.delete(
        "/api/resources/repositories/nobody/nothing/main",
        headers=_headers(),
        json={"confirmed": True},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_non_creator_is_refused(tmp_path: Path):
    """它写 `registry.json`，改变「哪些外部来源可被安装」。"""
    client, lifecycle = _api(tmp_path, creator=False)
    lifecycle.upsert_source_repository("anthropics", "skills", "main", enabled=True)

    response = await client.delete(
        "/api/resources/repositories/anthropics/skills/main",
        headers=_headers(),
        json={"confirmed": True},
    )

    assert response.status_code == 403
    assert _coordinates(lifecycle) == {("anthropics", "skills", "main")}
