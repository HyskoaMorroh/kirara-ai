"""`runtime_dependency` 声明必须真的被消费（需求 10）。

8 个 stdio MCP 预设各自声明了自己需要的运行时（`mcp:fetch` / `mcp:time` 是
`uvx`，其余 6 个是 `npx`）。但 `dependency_ids_for_resource` 不读这个字段——
它硬编码只认 `mcp:context7`、`agent-browser`、`graphify` 三个名字。

于是其余 7 个 stdio 预设的依赖判定返回空列表，含义是"这个资源不需要任何系统
依赖"。而它们全都需要：没有 `uvx` 的机器上 `mcp:fetch` 一次都起不来。

失败形态是完全静默的三层叠加：
1. `uvx` 连登记项都没有（登记表里只有 `uv --version` 的 `python-tooling`），
   所以即使去查也查不到它；
2. 依赖判定不读 `runtime_dependency`，所以 7 个预设一律"无依赖"；
3. 前端从不渲染后端已投影的 `dependency_status` 四个字段。

用户在「发现并安装」里点装 `mcp:fetch`，安装成功、启用成功、绑定成功，
界面上唯一的线索是 MCP 面板显示「连接失败 / 工具数 0」——没有任何一处说
"这台机器缺 uvx"。而 `resource_catalog.py` 里那段注释恰好描述了这个失败模式，
只是它针对的 `context7` 是唯一被真正接线的那一个。

这里锁住：声明被读取、`uvx` 有登记项、每个 stdio 预设都能报出自己缺什么。
"""

from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.resource_catalog import _BUILTINS
from kirara_ai.plugin_manager.system_dependencies import (
    dependency_ids_for_resource,
    known_dependency_ids,
)


def _builtin(catalog_id: str) -> dict:
    for item in _BUILTINS:
        if item["catalog_id"] == catalog_id:
            return dict(item)
    raise AssertionError(f"内置目录里没有 {catalog_id}")


def _stdio_presets() -> list[dict]:
    return [dict(item) for item in _BUILTINS if item.get("runtime_dependency")]


def test_the_probe_finds_every_stdio_preset():
    """自检：确实有一批声明了运行时依赖的预设，不是拿空列表在跑。"""
    presets = _stdio_presets()

    assert len(presets) >= 8, f"只找到 {len(presets)} 个声明 runtime_dependency 的预设"
    declared = {item["runtime_dependency"] for item in presets}
    assert declared == {"npx", "uvx"}, f"声明的运行时集合是 {declared}"


@pytest.mark.parametrize(
    "catalog_id",
    [item["catalog_id"] for item in _BUILTINS if item.get("runtime_dependency")],
)
def test_every_stdio_preset_reports_a_dependency(catalog_id: str):
    """声明了运行时的预设，依赖判定不能返回空——空的含义是「不需要任何依赖」。"""
    item = _builtin(catalog_id)

    dependency_ids = dependency_ids_for_resource(item)

    assert dependency_ids, (
        f"{catalog_id} 声明了 runtime_dependency="
        f"{item['runtime_dependency']!r}，但依赖判定返回空列表——"
        "界面会显示「无需依赖」，而它其实起不来"
    )


@pytest.mark.parametrize(
    "catalog_id",
    [item["catalog_id"] for item in _BUILTINS if item.get("runtime_dependency")],
)
def test_reported_dependencies_are_all_registered(catalog_id: str):
    """判定出的依赖 id 必须在登记表里，否则界面查不到它的探测状态。"""
    item = _builtin(catalog_id)
    known = known_dependency_ids()

    for dependency_id in dependency_ids_for_resource(item):
        assert dependency_id in known, (
            f"{catalog_id} 需要 {dependency_id}，但登记表里没有这一项——"
            "探测、安装与状态展示全都无从进行"
        )


def test_uvx_has_its_own_registration():
    """`uvx` 与 `uv` 是两个命令。

    登记表原先只有 `uv --version`（`python-tooling`）。`uv` 装了不等于 `uvx`
    可用，而 `mcp:fetch` / `mcp:time` 启动时执行的正是 `uvx`。
    """
    known = known_dependency_ids()

    uvx_entries = [name for name in known if "uvx" in name]
    assert uvx_entries, "登记表里没有任何 uvx 相关项，缺它时无从探测"


def test_a_uvx_preset_maps_to_the_uvx_dependency():
    """`mcp:fetch` 靠 uvx 启动，它报出的依赖里必须含 uvx 那一项。"""
    fetch = _builtin("mcp:fetch")

    dependency_ids = dependency_ids_for_resource(fetch)

    assert any("uvx" in name for name in dependency_ids), (
        f"mcp:fetch 报出的依赖是 {dependency_ids}，其中没有 uvx——"
        "而它的 command 就是 uvx"
    )


def test_an_npx_preset_maps_to_the_npx_dependency():
    """`mcp:memory` 靠 npx 启动，报出的依赖要指向 npx 那条链。"""
    memory = _builtin("mcp:memory")

    dependency_ids = dependency_ids_for_resource(memory)

    assert dependency_ids, "mcp:memory 报不出任何依赖"
    known = known_dependency_ids()
    assert all(name in known for name in dependency_ids)


def test_context7_keeps_its_existing_mapping():
    """既有映射不能因为新增通用规则而改变——它是唯一本来就接线的那个。"""
    context7 = _builtin("mcp:context7")

    assert dependency_ids_for_resource(context7) == ["context7-runtime"]


def test_skill_mappings_are_unchanged():
    """两个 skill 的既有映射保持原样，新规则只补 MCP 那一侧。"""
    agent_browser = _builtin("skill:agent-browser")

    assert dependency_ids_for_resource(agent_browser) == [
        "agent-browser-cli",
        "agent-browser-browser",
    ]


def test_an_item_without_a_declaration_reports_nothing():
    """没声明运行时的条目仍然返回空——不能因为新规则就凭空造出依赖。"""
    prompt = _builtin("prompt:office-research")

    assert dependency_ids_for_resource(prompt) == []


def test_an_installed_resource_shape_also_resolves():
    """已安装资源的字段名与目录项不同，同一个依赖必须都能认出来。

    `project_dependencies` 用目录项形状，运行时用已安装资源形状。两边判断不一致
    时没有任何症状：界面说已就绪、运行时说缺失，而模型只会照着一份它执行不了的
    说明自信作答。
    """
    fetch = _builtin("mcp:fetch")
    installed_shape = {
        "resource_id": "mcp.fetch",
        "type": "mcp",
        "source_metadata": {
            "catalog_id": "mcp:fetch",
            "runtime_dependency": fetch["runtime_dependency"],
        },
    }

    assert dependency_ids_for_resource(installed_shape) == dependency_ids_for_resource(
        fetch
    )
