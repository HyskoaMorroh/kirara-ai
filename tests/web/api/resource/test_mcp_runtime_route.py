"""受管 MCP 资源的运行时配置必须有一条 HTTP 入口，且必须限创建者。

背景：`PUT /mcp/servers/<id>` 只在 `config.mcp.servers` 里查找，因此对任何受管
MCP 资源都返回 404（`tests/plugin_manager/test_mcp_runtime_overrides.py` 记录了
这个发现过程）。`mcp:filesystem` 的描述要求「启用前必须在 args 末尾追加允许访问
的目录」——在修好之前，这件事在产品里做不到。

这组测试锁住接口侧的边界：

1. `PUT /resources/<id>/runtime` 写入覆盖，响应带回合并前的注册表视图。
2. **限创建者**：覆盖决定 MCP 进程能读写哪些目录、带什么环境变量。
   需求 10 明确「只有该项目的创建者才能通过插件修改服务器内容或执行文件操作」,
   而一个目录白名单正是文件操作的范围本身。
3. 只接受白名单字段，未知字段 400——不是静默忽略。静默忽略会让一个拼错的
   `extra_arg`（少个 s）看起来保存成功，而目录从未生效。
4. 非 mcp 资源 400，不存在的资源 404。
5. 写入后 `refresh_managed_servers` 被调用：不刷新的话进程还在用旧参数跑，
   而界面已经显示新配置了。
6. 目录字符串原样存储、原样返回——不做路径规范化。
   规范化会让 `~/data` 变成一个具体用户的家目录，而 MCP 进程的用户可能不是它。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.plugin_manager.system_dependencies import SystemDependencyService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


def _api(tmp_path: Path, *, creator: bool = True, with_manager: bool = True):
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
    ResourceCatalogService(lifecycle).install("mcp:filesystem")

    manager = None
    if with_manager:
        manager = MCPServerManager(container)
        manager.refresh_managed_servers = AsyncMock()
        container.register(MCPServerManager, manager)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle, manager


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


async def _put(client, body: dict, resource_id: str = "mcp.filesystem"):
    return await client.put(
        f"/api/resources/{resource_id}/runtime", json=body, headers=_headers()
    )


@pytest.mark.asyncio
async def test_it_records_a_directory_allowlist(tmp_path: Path):
    """这条接口存在的理由：目录白名单必须能从界面配上去。"""
    client, lifecycle, _ = _api(tmp_path)

    response = await _put(client, {"extra_args": ["/srv/data/docs"]})

    assert response.status_code == 200, await response.get_data(as_text=True)
    payload = await response.get_json()
    assert payload["runtime_overrides"]["extra_args"] == ["/srv/data/docs"]
    stored = lifecycle.get_resource("mcp.filesystem")["runtime_overrides"]
    assert stored["extra_args"] == ["/srv/data/docs"]


@pytest.mark.asyncio
async def test_a_non_creator_is_refused(tmp_path: Path):
    """覆盖决定 MCP 进程能读写哪些目录——那正是需求 10 说的文件操作范围。

    默认 token 带 `["*"]`，所以只查 scope 一定放行；这里要的是身份。
    """
    client, lifecycle, _ = _api(tmp_path, creator=False)

    response = await _put(client, {"extra_args": ["/etc"]})

    assert response.status_code == 403
    assert lifecycle.get_resource("mcp.filesystem").get("runtime_overrides") in (None, {})


@pytest.mark.asyncio
async def test_unknown_fields_are_refused(tmp_path: Path):
    """静默忽略会让一个拼错的字段名看起来保存成功，而目录从未生效。"""
    client, _, _ = _api(tmp_path)

    response = await _put(client, {"extra_arg": ["/srv/data"]})

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_identity_fields_are_refused(tmp_path: Path):
    """`command` 等是摘要保护的身份，不能从这个入口改。"""
    client, _, _ = _api(tmp_path)

    for field, value in (
        ("command", "bash"),
        ("args", ["-c", "curl evil"]),
        ("type", "sse"),
        ("url", "https://example.com"),
        ("id", "other"),
    ):
        response = await _put(client, {field: value})
        assert response.status_code == 400, f"{field} 不该被接受"


@pytest.mark.asyncio
async def test_a_non_mcp_resource_is_refused(tmp_path: Path):
    client, lifecycle, _ = _api(tmp_path)
    lifecycle.author_document(
        resource_id="prompt.mine", resource_type="prompt", content="正文\n"
    )

    response = await _put(client, {"cwd": "/srv"}, resource_id="prompt.mine")

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_missing_resource_is_a_404(tmp_path: Path):
    client, _, _ = _api(tmp_path)

    response = await _put(client, {"cwd": "/srv"}, resource_id="mcp.absent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_managed_servers_are_refreshed(tmp_path: Path):
    """不刷新的话进程还在用旧参数跑，而界面已经显示新配置了。"""
    client, _, manager = _api(tmp_path)

    await _put(client, {"extra_args": ["/srv/data"]})

    manager.refresh_managed_servers.assert_awaited_once()
    assert manager.refresh_managed_servers.await_args.kwargs == {"connect": False}


@pytest.mark.asyncio
async def test_it_works_without_an_mcp_manager(tmp_path: Path):
    """没有注册 MCP 管理器的部署仍然能写入覆盖。

    与既有的启用/停用/删除三条路径一致（它们都用 `container.has` 守卫）。
    """
    client, lifecycle, _ = _api(tmp_path, with_manager=False)

    response = await _put(client, {"cwd": "/srv"})

    assert response.status_code == 200
    assert lifecycle.get_resource("mcp.filesystem")["runtime_overrides"]["cwd"] == "/srv"


@pytest.mark.asyncio
async def test_paths_are_stored_verbatim(tmp_path: Path):
    """不做路径规范化。

    把 `~/data` 展开成一个具体用户的家目录是替 MCP 进程做决定，
    而那个进程的用户可能不是当前用户；Windows 与 POSIX 的分隔符也不该被改写。
    """
    client, _, _ = _api(tmp_path)

    response = await _put(client, {"extra_args": ["~/data", "C:\\srv\\docs"]})

    assert (await response.get_json())["runtime_overrides"]["extra_args"] == [
        "~/data",
        "C:\\srv\\docs",
    ]


@pytest.mark.asyncio
async def test_an_empty_body_is_a_no_op_not_an_error(tmp_path: Path):
    """空请求体不改任何东西，也不报错——PUT 幂等。"""
    client, _, _ = _api(tmp_path)

    response = await _put(client, {})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_response_does_not_leak_secret_values(tmp_path: Path):
    """`env` 会装凭据。响应里只回键名，值一律打掩码。

    与 `GET /mcp/servers` 的 `_redact_transport` 同一条规则：
    这两个接口回的是同一份东西，一个遮一个不遮等于遮了也没用。
    """
    client, _, _ = _api(tmp_path)

    response = await _put(client, {"env": {"API_TOKEN": "s3cr3t"}})

    body = await response.get_data(as_text=True)
    assert "s3cr3t" not in body
    assert "API_TOKEN" in body, "键名要保留，否则界面看不出配过什么"
