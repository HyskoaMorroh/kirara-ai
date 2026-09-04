"""内置目录必须覆盖截图里出现过的每一类插件（需求 10）。

现场报障：Agent 编辑器里 Prompt / Skill / Memory / MCP / Hook 五个「添加」按钮全灰。
链路本身没断（装 → 启用 → 绑定 → 生效每一段都在），断的是**首屏无货**：
`_BUILTINS` 只有 4 条（prompt / mcp / memory / hook 各 1），**skill 与 agent 各 0**。
type 选 skill 又不输关键词就是空列表，看起来像功能没做。

参考界面的「新增 MCP」页给出六个快捷类型：`custom` / `fetch` / `time` / `memory` /
`sequential-thinking` / `context7`，点一下预填一套可编辑模板；已装列表里还出现过
`chrome-devtools`、`filesystem`、`playwright`。这些都要能开箱看到。

一条硬纪律写在这里：**预置一个模板 ≠ 那个 MCP 能跑起来**。
`fetch` / `time` 靠 `uvx`，其余靠 `npx`，而这两个都不是本项目的依赖。因此每个
stdio 预设必须声明它需要的运行时（`runtime_dependency`），界面据此显示
「模板已填好，但这台机器缺 uvx」——而不是让用户点了启用之后看到一个连不上的服务器。
"""

from __future__ import annotations

from collections import Counter

import pytest

from kirara_ai.plugin_manager.resource_catalog import _BUILTINS


def _by_type() -> Counter[str]:
    return Counter(str(item["type"]) for item in _BUILTINS)


def _ids() -> set[str]:
    return {str(item["catalog_id"]) for item in _BUILTINS}


def test_every_bindable_type_has_at_least_one_builtin():
    """五个绑定区各自至少有一个可选项，否则那个「添加」按钮必然是灰的。

    按钮灰的判据是 `enabled && !confirmation_required` 过滤后为空
    （`AgentView.vue`）。类型下一条内置都没有时，用户唯一的出路是先学会
    在线搜索——而他看到的只是一个灰按钮。
    """
    counts = _by_type()
    missing = [
        kind
        for kind in ("prompt", "skill", "memory", "mcp", "hook")
        if counts.get(kind, 0) == 0
    ]
    assert not missing, f"这些类型没有任何内置条目，绑定区必然为空：{missing}"


def test_the_six_mcp_presets_from_the_reference_ui_are_present():
    """参考界面「新增 MCP」页的六个快捷类型都要有。

    `custom` 是「自定义」那一档，它不是一个可安装条目而是一张空白模板，
    因此这里只要求另外五个真实服务器在目录里。
    """
    expected = {
        "mcp:fetch",
        "mcp:time",
        "mcp:memory",
        "mcp:sequential-thinking",
        "mcp:context7",
    }
    assert expected <= _ids(), f"缺少 MCP 预设：{sorted(expected - _ids())}"


def test_the_mcp_servers_seen_in_the_installed_list_are_present():
    """参考界面已装列表里出现过的服务器同样要能一键装上。"""
    expected = {"mcp:filesystem", "mcp:chrome-devtools", "mcp:playwright"}
    assert expected <= _ids(), f"缺少 MCP 条目：{sorted(expected - _ids())}"


@pytest.mark.parametrize(
    "catalog_id",
    [
        "mcp:fetch",
        "mcp:time",
        "mcp:memory",
        "mcp:sequential-thinking",
        "mcp:context7",
        "mcp:filesystem",
        "mcp:chrome-devtools",
        "mcp:playwright",
    ],
)
def test_every_stdio_preset_declares_the_runtime_it_needs(catalog_id: str):
    """每个 stdio 预设必须说清它靠什么拉起。

    **这一条比「有没有这个条目」更要紧。** `uvx` 与 `npx` 都不是本项目的依赖：
    容器里没有 Node，`fetch`/`time` 还需要 Python 的 uv。缺声明时用户点「启用」
    会得到一个连不上的服务器，而界面上没有任何线索指向真正的原因
    （现场那句「连接失败 / 已连接 0 / 工具数 0」正是这个形态）。
    """
    item = next(entry for entry in _BUILTINS if entry["catalog_id"] == catalog_id)
    server = item["content"]["server"]
    assert server["type"] == "stdio"
    command = server["command"]
    assert command in {"npx", "uvx"}, f"未知的拉起方式：{command}"
    # 声明必须与实际命令一致——写了 npx 却声明需要 uvx 比不声明更糟。
    assert item.get("runtime_dependency") == command, (
        f"{catalog_id} 没有声明它需要 {command}"
    )


def test_no_builtin_ships_a_credential_or_a_local_path():
    """内置模板里不得出现真实凭据或本机路径。

    模板会被写进 `data/resources/` 并可能随备份导出；一个顺手填进去的 token
    会跟着走。需要密钥的服务器留空 env 并在描述里说明要填什么。
    """
    offenders = []
    for item in _BUILTINS:
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        env = content.get("server", {}).get("env", {})
        for key, value in env.items():
            if value:
                offenders.append(f"{item['catalog_id']}: env.{key} 有预填值")
        args = content.get("server", {}).get("args", [])
        for argument in args:
            text = str(argument)
            if text.startswith("/home/") or text.startswith("C:\\"):
                offenders.append(f"{item['catalog_id']}: args 含本机路径 {text}")
    assert not offenders, offenders


def test_catalog_ids_are_unique():
    """重复 ID 会让 `_find()` 取到第一个，而用户以为装的是另一个。"""
    ids = [str(item["catalog_id"]) for item in _BUILTINS]
    duplicates = [name for name, count in Counter(ids).items() if count > 1]
    assert not duplicates, f"重复的 catalog_id：{duplicates}"


def test_every_builtin_declares_the_fields_the_installer_reads():
    """`_install_builtin` 会读这些字段，缺一个就是安装时才炸。"""
    required = {"catalog_id", "type", "name", "version", "entry", "source"}
    for item in _BUILTINS:
        missing = required - set(item)
        assert not missing, f"{item.get('catalog_id')} 缺字段：{sorted(missing)}"


def test_skill_builtins_carry_a_resolvable_source_key():
    """从 GitHub 安装的 skill，`source_key` 必须是 `owner/repo:directory`。

    `install()` 对这类 skill 直接 `split(":", 1)` 再 `split("/", 1)`，格式不对会抛
    ValueError——而那发生在用户点了安装之后。

    **随包技能（`bundled_dir`）不在此列**：它们的正文在 wheel 里，安装不出网，
    因此没有 GitHub 坐标也不需要有。把它们一起要求会让这条断言从「格式校验」
    变成「禁止随包」——那是两件不同的事。
    """
    skills = [
        item
        for item in _BUILTINS
        if item["type"] == "skill" and not item.get("bundled_dir")
    ]
    assert skills, "没有任何走 GitHub 安装的内置 skill"
    for item in skills:
        source_key = item.get("source_key")
        assert isinstance(source_key, str) and ":" in source_key, (
            f"{item['catalog_id']} 的 source_key 不可解析：{source_key!r}"
        )
        owner_repo, _, directory = source_key.partition(":")
        assert "/" in owner_repo, f"{source_key} 缺少 owner/repo"
        assert directory, f"{source_key} 缺少目录段"
