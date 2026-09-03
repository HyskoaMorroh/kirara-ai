"""运行时覆盖必须真的作用到启动的进程上，而不只是存在注册表里。

`set_runtime_overrides` 只负责记录。真正决定「跑起来是什么样」的是
`MCPServerManager._configured_servers()`——它读归档里的 `server.json` 建
`MCPServerConfig`。覆盖不在那里合并的话，界面上写着 `/srv/data`，
进程里仍然没有任何可访问目录：一个「已配置」的假象。

这组测试锁住合并语义：

1. `extra_args` **追加**在归档 args 之后，顺序保持——
   `@modelcontextprotocol/server-filesystem` 之后跟着目录，正是它要求的形态。
2. `command` / `type` / `url` / `id` 仍然来自归档，覆盖动不了它们。
3. `env` / `headers` 按键合并，归档里的默认值保留。
4. `cwd` / `roots` / `startup_timeout_ms` 直接生效。
5. 没有覆盖时，配置与合并前逐字段相同——这条特性不能改变既有部署的行为。
6. 坏覆盖（比如注册表被手改成非法结构）**跳过覆盖但仍然启动服务器**：
   一条坏覆盖不该让一个本来能跑的 MCP 服务器彻底消失。
7. `config.mcp.servers` 里的传统条目不受影响——它们有自己的编辑路由。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig, MCPServerConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def _manager(tmp_path: Path) -> tuple[MCPServerManager, ResourceLifecycleService]:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    container.register(GlobalConfig, config)
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    container.register(ResourceLifecycleService, lifecycle)
    ResourceCatalogService(lifecycle).install("mcp:filesystem")
    lifecycle.enable("mcp.filesystem", confirmed=True)

    manager = MCPServerManager(container)
    return manager, lifecycle


def _filesystem(manager: MCPServerManager) -> MCPServerConfig:
    return next(
        item for item in manager._configured_servers() if item.id == "filesystem"
    )


def test_extra_args_land_after_the_packaged_args(tmp_path: Path):
    """这是整条特性的判据：目录必须真的出现在启动参数里。"""
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides("mcp.filesystem", extra_args=["/srv/data/docs"])

    server = _filesystem(manager)

    assert server.server.args == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/srv/data/docs",
    ]


def test_multiple_directories_keep_their_order(tmp_path: Path):
    """多个目录的顺序是用户写的顺序，不排序、不去重成集合。"""
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides(
        "mcp.filesystem", extra_args=["/srv/b", "/srv/a"]
    )

    assert _filesystem(manager).server.args[-2:] == ["/srv/b", "/srv/a"]


def test_identity_still_comes_from_the_archive(tmp_path: Path):
    """摘要保护的身份不受覆盖影响。"""
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides("mcp.filesystem", extra_args=["/srv/data"])

    server = _filesystem(manager)

    assert server.id == "filesystem"
    assert server.server.command == "npx"
    assert server.server.type == "stdio"
    assert server.server.url is None


def test_env_merges_with_the_packaged_env(tmp_path: Path):
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides("mcp.filesystem", env={"LOG_LEVEL": "debug"})

    assert _filesystem(manager).server.env["LOG_LEVEL"] == "debug"


def test_cwd_roots_and_timeout_take_effect(tmp_path: Path):
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides(
        "mcp.filesystem",
        cwd="/srv",
        roots=["/srv/data"],
        startup_timeout_ms=30_000,
    )

    server = _filesystem(manager)

    assert server.server.cwd == "/srv"
    assert server.server.roots == ["/srv/data"]
    assert server.server.startup_timeout_ms == 30_000


def test_without_overrides_nothing_changes(tmp_path: Path):
    """这条特性不能改变既有部署的行为。"""
    manager, _ = _manager(tmp_path)

    server = _filesystem(manager)

    assert server.server.args == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert server.server.cwd is None
    assert server.server.roots == []
    assert server.server.startup_timeout_ms == 120_000


def test_a_broken_override_does_not_hide_the_server(tmp_path: Path):
    """一条坏覆盖不该让一个本来能跑的 MCP 服务器彻底消失。

    那时用户既看不到服务器，也无从知道原因是覆盖坏了——
    `_configured_servers` 的既有失败处理是 `logger.warning` + 跳过整条资源，
    对「覆盖读不动」而言跳过整条太重了。
    """
    manager, lifecycle = _manager(tmp_path)
    # 直接改注册表，复现一份被手改坏的覆盖。
    lifecycle._registry["resources"]["mcp.filesystem"]["runtime_overrides"] = "not-a-dict"

    server = _filesystem(manager)

    assert server.server.args == ["-y", "@modelcontextprotocol/server-filesystem"]


def test_a_partially_broken_override_keeps_the_usable_keys(tmp_path: Path):
    manager, lifecycle = _manager(tmp_path)
    lifecycle._registry["resources"]["mcp.filesystem"]["runtime_overrides"] = {
        "extra_args": "/srv/data",  # 该是列表
        "cwd": "/srv",
    }

    server = _filesystem(manager)

    assert server.server.cwd == "/srv", "一个坏键不该丢掉别的键"
    assert server.server.args == ["-y", "@modelcontextprotocol/server-filesystem"]


def test_a_legacy_config_entry_is_untouched(tmp_path: Path):
    """`config.mcp.servers` 里的条目有自己的编辑路由，不走这套覆盖。"""
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.mcp.servers.append(
        MCPServerConfig.model_validate(
            {
                "id": "legacy",
                "name": "Legacy",
                "server": {"type": "stdio", "command": "echo", "args": ["hi"]},
            }
        )
    )
    container.register(GlobalConfig, config)
    container.register(ResourceLifecycleService, ResourceLifecycleService(tmp_path / "data"))
    manager = MCPServerManager(container)

    legacy = next(item for item in manager._configured_servers() if item.id == "legacy")

    assert legacy.server.args == ["hi"]


def test_the_resource_id_is_still_recorded_in_metadata(tmp_path: Path):
    """合并不能弄丢 `metadata.resource_id`——界面靠它把服务器与资源对起来。"""
    manager, lifecycle = _manager(tmp_path)
    lifecycle.set_runtime_overrides("mcp.filesystem", extra_args=["/srv/data"])

    assert _filesystem(manager).metadata["resource_id"] == "mcp.filesystem"
