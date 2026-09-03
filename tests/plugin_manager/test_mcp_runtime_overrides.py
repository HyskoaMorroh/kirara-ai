"""受管 MCP 资源必须能在本机配置，否则目录里那几条自己的说明无法照做。

发现过程：`mcp:filesystem` 的描述写着「启用前必须在 args 末尾追加允许访问的目录」。
装完之后 `args` 是 `['-y', '@modelcontextprotocol/server-filesystem']`——没有目录，
也没有 `roots`。而唯一的编辑入口 `PUT /mcp/servers/<id>` 只在
`config.mcp.servers` 里查找：

    config.mcp.servers ids: []
    PUT /mcp/servers/filesystem finds it? -> False

受管 MCP 资源住在资源注册表里，不在 `config.mcp.servers`。于是那条路由对**任何**
受管服务器都返回 404，而 `MCPList.vue` 给每一行都渲染了「编辑」按钮。

所以缺口不是「filesystem 这条预设该不该带目录」，而是**受管 MCP 资源完全无法配置**；
filesystem 只是把这一点摆得最明显的一条，因为它的描述亲口要求用户去做一件做不到的事。

为什么不改归档里的 `server.json`：那份声明有 `content_sha256` 护着，它是「目录发布了
什么」。而一个本机目录白名单是「这台机器允许什么」。两者是不同的东西，混在一起会让
每次配一个目录都变成一次版本递增 + 一次备份，并且升级时用本机路径去覆盖上游的新声明。

因此这里的形态是**运行时覆盖**：存在可变的注册表记录里（与 `set_display_metadata`
同一类），由 `_configured_servers()` 在构造 `MCPServerConfig` 时合并。

这组测试锁住的边界：

1. 只有 `mcp` 类型接受覆盖——其余类型没有「传输配置」这回事。
2. 可覆盖的键按构造只有那几个「这台机器怎么跑它」的：
   `extra_args` / `env` / `cwd` / `roots` / `headers` / `startup_timeout_ms`。
   `command` / `type` / `url` / `id` **不可覆盖**：那是摘要保护的身份，
   改它们等于把这条资源指向另一个程序或另一台服务器。
3. `extra_args` 是**追加**而不是替换：描述里说的就是「在 args 末尾追加」，
   而且追加能让上游后续给 base args 加的新参数继续生效，
   也让包名留在摘要保护的那一段里。
4. `env` / `headers` 按键合并：上游新增的默认值仍然生效。
5. 覆盖**不动版本、不动摘要、不触发备份**。
6. 覆盖在升级与回滚后**存活**：它描述的是这台机器，与装的是哪一版无关。
7. 删除资源时覆盖跟着消失，不留给下一个同名资源。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)


@pytest.fixture()
def lifecycle(tmp_path: Path) -> ResourceLifecycleService:
    return ResourceLifecycleService(tmp_path / "data")


@pytest.fixture()
def filesystem(lifecycle: ResourceLifecycleService) -> str:
    ResourceCatalogService(lifecycle).install("mcp:filesystem")
    return "mcp.filesystem"


def test_the_shipped_preset_has_no_directory_to_work_with(lifecycle, filesystem):
    """先钉住这条特性存在的前提：预设确实没有可操作范围。

    这不是在测别人的 bug，而是在锁住「描述要求用户追加目录」这个事实——
    如果哪天预设自己带了目录，这条会红，提醒去重新评估覆盖机制还需不需要。
    """
    resource = lifecycle.get_resource(filesystem)
    payload = json.loads(lifecycle.read_entry(filesystem, resource["current_version"]))

    assert payload["server"]["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert not payload["server"].get("roots"), "预设不该预填 roots——那会替用户决定可读范围"
    assert "追加允许访问的目录" in resource["description"], "描述要求用户去做这件事"


def test_extra_args_are_appended_to_the_packaged_args(lifecycle, filesystem):
    """追加而不是替换：包名留在摘要保护的那一段里。"""
    updated = lifecycle.set_runtime_overrides(
        filesystem, extra_args=["/srv/data/docs"]
    )

    assert updated["runtime_overrides"]["extra_args"] == ["/srv/data/docs"]
    # 归档里的 args 一个字没动。
    payload = json.loads(lifecycle.read_entry(filesystem, updated["current_version"]))
    assert payload["server"]["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]


def test_identity_fields_cannot_be_overridden(lifecycle, filesystem):
    """`command` / `type` / `url` / `id` 是摘要保护的身份，不能从这个入口改。

    放开它们等于让「配一个目录」这个操作可以把 `npx` 换成任意程序、
    把一个本地 stdio 服务器换成一个远端地址——而那是安装路径的权限。
    """
    for field in ("command", "type", "url", "id", "args"):
        with pytest.raises(TypeError):
            lifecycle.set_runtime_overrides(filesystem, **{field: "x"})


def test_only_mcp_resources_accept_overrides(lifecycle):
    """其余类型没有「传输配置」这回事。"""
    lifecycle.author_document(
        resource_id="prompt.mine", resource_type="prompt", content="正文\n"
    )

    with pytest.raises(ResourceValidationError):
        lifecycle.set_runtime_overrides("prompt.mine", cwd="/tmp")


def test_env_and_headers_merge_by_key(lifecycle, filesystem):
    """按键合并，这样上游后续新增的默认值仍然生效。"""
    lifecycle.set_runtime_overrides(filesystem, env={"A": "1", "B": "2"})
    updated = lifecycle.set_runtime_overrides(filesystem, env={"B": "3"})

    assert updated["runtime_overrides"]["env"] == {"A": "1", "B": "3"}


def test_an_empty_value_clears_one_key(lifecycle, filesystem):
    """「没提供」与「明确清空」是两件事。"""
    lifecycle.set_runtime_overrides(filesystem, extra_args=["/srv/a"], cwd="/srv")

    cleared = lifecycle.set_runtime_overrides(filesystem, extra_args=[])

    assert "extra_args" not in cleared["runtime_overrides"]
    assert cleared["runtime_overrides"]["cwd"] == "/srv", "只传一个键不该动别的"


def test_overrides_change_neither_version_nor_digest(lifecycle, filesystem):
    before = lifecycle.get_resource(filesystem)

    after = lifecycle.set_runtime_overrides(filesystem, extra_args=["/srv/data"])

    assert after["current_version"] == before["current_version"]
    assert after["content_sha256"] == before["content_sha256"]
    assert len(after["versions"]) == len(before["versions"])
    assert lifecycle.list_backups(filesystem) == [], "配一个目录不该触发备份"


def test_overrides_survive_an_upgrade(lifecycle, filesystem):
    """覆盖描述的是这台机器，与装的是哪一版无关。

    升级时丢掉它的后果是静默的：服务器照样启动，但突然没有任何可访问目录，
    而界面上那条覆盖看起来还在。
    """
    catalog = ResourceCatalogService(lifecycle)
    lifecycle.set_runtime_overrides(filesystem, extra_args=["/srv/data"])

    item = dict(
        next(
            entry
            for entry in __import__(
                "kirara_ai.plugin_manager.resource_catalog", fromlist=["_BUILTINS"]
            )._BUILTINS
            if entry["catalog_id"] == "mcp:filesystem"
        )
    )
    item["version"] = "1.1.0"
    catalog._install_builtin(item, update=True)

    upgraded = lifecycle.get_resource(filesystem)
    assert upgraded["current_version"] == "1.1.0"
    assert upgraded["runtime_overrides"]["extra_args"] == ["/srv/data"]


def test_overrides_survive_a_rollback(lifecycle, filesystem):
    catalog = ResourceCatalogService(lifecycle)
    item = dict(
        next(
            entry
            for entry in __import__(
                "kirara_ai.plugin_manager.resource_catalog", fromlist=["_BUILTINS"]
            )._BUILTINS
            if entry["catalog_id"] == "mcp:filesystem"
        )
    )
    original = lifecycle.get_resource(filesystem)["current_version"]
    item["version"] = "1.1.0"
    catalog._install_builtin(item, update=True)
    lifecycle.set_runtime_overrides(filesystem, extra_args=["/srv/data"])

    restored = lifecycle.restore_version(filesystem, original, confirmed=True)

    assert restored["current_version"] == original
    assert restored["runtime_overrides"]["extra_args"] == ["/srv/data"]


def test_removing_the_resource_drops_its_overrides(lifecycle, filesystem):
    """不留给下一个同名资源：那会让一个新装的服务器带上前一个的目录白名单。"""
    lifecycle.set_runtime_overrides(filesystem, extra_args=["/srv/data"])
    lifecycle.remove(filesystem, confirmed=True)

    ResourceCatalogService(lifecycle).install("mcp:filesystem")

    assert lifecycle.get_resource(filesystem).get("runtime_overrides") in (None, {})


def test_a_missing_resource_fails_loudly(lifecycle):
    with pytest.raises(ResourceStateError):
        lifecycle.set_runtime_overrides("mcp.absent", cwd="/tmp")


def test_roots_and_timeout_are_accepted(lifecycle, filesystem):
    """`roots` 是协议层的可访问根，`startup_timeout_ms` 是本机快慢，都属于部署。"""
    updated = lifecycle.set_runtime_overrides(
        filesystem, roots=["/srv/data"], startup_timeout_ms=30_000
    )

    assert updated["runtime_overrides"]["roots"] == ["/srv/data"]
    assert updated["runtime_overrides"]["startup_timeout_ms"] == 30_000


def test_a_bad_timeout_is_refused(lifecycle, filesystem):
    """越界的超时会在 `MCPTransportConfig` 那层炸掉，届时整条资源无法启动。

    在写入这一刻拒绝，用户当场知道；放过去则要等到下次启动服务器才显形，
    而那时的报错指向 pydantic 校验，与「我改了个超时」看不出关系。

    `True` 单列一项：`bool` 是 `int` 的子类，不显式排除的话它会被当成 1
    通过下界检查，然后写进注册表。
    """
    for value in (0, 999, 600_001, "abc", 1.5, True):
        with pytest.raises(ResourceValidationError):
            lifecycle.set_runtime_overrides(filesystem, startup_timeout_ms=value)


def test_none_means_leave_the_timeout_alone(lifecycle, filesystem):
    """`None` 是「没提供」的哨兵，与其余键一致，不是一个非法值。"""
    lifecycle.set_runtime_overrides(filesystem, startup_timeout_ms=30_000)

    updated = lifecycle.set_runtime_overrides(filesystem, cwd="/srv")

    assert updated["runtime_overrides"]["startup_timeout_ms"] == 30_000
