"""按名字识别技能依赖的那张表必须覆盖每一条 CLI 登记项（需求 10）。

需求 10 点名了五个工具：tk（`rtk`）、context mode、graphify、memsearch、caveman。
它们都在 `system_dependencies.py` 里有登记项，探测与安装都能跑。断的是**中间那一层**：
`dependency_ids_for_resource()` 只认 `agent-browser` 与 `graphify` 两个名字，
其余一律返回空列表——而空列表的含义是「这个技能不需要任何服务器依赖」。

后果有两处，都不报错：

1. **技能广告里不会出现「服务器上没有这个命令」。**
   `skill_readiness_note()` 按这张表拿依赖 id，拿到空列表就什么都不说。
   于是一个装了 memsearch 技能、而 VPS 上没有 `memsearch` 的部署里，
   模型照着技能说明自信地调用一个不存在的命令。
2. **安装界面不显示这个技能缺什么。**
   `project_dependencies()` 用同一张表投影就绪状态。

这条测试按**登记项**驱动，而不是逐个写死名字：新增一条 CLI 登记项时，
如果忘了往映射表里加对应的技能名，这里立刻红。写死名字的版本只能守住今天这几个。

不覆盖 `claude-plugin` 类型的两条（context-mode / caveman）：它们装在操作者
自己的 Claude 配置里，不是服务器组件（`install_supported` 为假）。
一个技能声明「我需要操作者本机装了某个 Claude 插件」是无从校验的，
因此那两条只出现在依赖面板上，不参与技能就绪判定——这一点也要锁住，
否则下一个人会顺手把它们加进映射表，让技能永远显示缺依赖。
"""

from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.system_dependencies import (
    _definitions,
    dependency_ids_for_resource,
    known_dependency_ids,
)


def _cli_definitions():
    """服务器侧可安装的 CLI 登记项——只有这些才该参与技能就绪判定。"""
    return [item for item in _definitions() if item.kind == "cli"]


def test_the_probe_finds_cli_definitions():
    """自检：确实有 CLI 登记项，不是在空集合上断言。"""
    assert len(_cli_definitions()) >= 4


@pytest.mark.parametrize("definition", _cli_definitions(), ids=lambda item: item.dependency_id)
def test_every_cli_dependency_is_reachable_by_a_skill_name(definition):
    """每条 CLI 登记项都要有一个能命中它的技能名。

    命中方式与产品一致：按技能的 `name` / `directory` 识别。取登记项 id 去掉
    `-cli` 后缀作为候选名——这正是现有两条（`graphify-cli` ← `graphify`、
    `agent-browser-cli` ← `agent-browser`）的构成方式。
    """
    candidate = definition.dependency_id.removesuffix("-cli")
    resolved = dependency_ids_for_resource(
        {"type": "skill", "name": candidate, "directory": f"skills/{candidate}"}
    )

    assert definition.dependency_id in resolved, (
        f"技能名 {candidate!r} 识别不出依赖 {definition.dependency_id}："
        "技能广告里不会提示这台机器缺这个命令，模型会照着一份它执行不了的说明作答"
    )


def test_resolved_ids_are_all_registered():
    """判定出的 id 必须能在登记表里查到，否则探测与安装都无从进行。"""
    known = known_dependency_ids()
    for definition in _cli_definitions():
        candidate = definition.dependency_id.removesuffix("-cli")
        for resolved in dependency_ids_for_resource(
            {"type": "skill", "name": candidate, "directory": f"skills/{candidate}"}
        ):
            assert resolved in known, f"{resolved} 不在登记表里，界面会显示一个查不到状态的依赖"


def test_claude_plugins_are_not_treated_as_skill_dependencies():
    """操作者本机的 Claude 插件不参与技能就绪判定。

    它们的 `install_supported` 为假、探测的是宿主 `claude --version`。
    把它们加进映射表会让技能在**任何**服务器上都显示缺依赖——
    而那个「缺」是无从修复的，因为服务器侧压根不该装它。
    """
    for name in ("context-mode", "caveman"):
        resolved = dependency_ids_for_resource(
            {"type": "skill", "name": name, "directory": f"skills/{name}"}
        )
        assert "context-mode-plugin" not in resolved
        assert "caveman-plugin" not in resolved


def test_a_skill_with_no_known_tool_needs_nothing():
    """对照组：不认识的技能名返回空列表，而不是随便挂一条依赖。"""
    assert dependency_ids_for_resource(
        {"type": "skill", "name": "some-unrelated-skill", "directory": "skills/x"}
    ) == []


def test_an_installed_resource_shape_resolves_the_same_way():
    """已安装资源与目录项两种形状必须得到同一批依赖。

    同一个技能在这两处的字段名不同（`resource_id` + `source_metadata` vs
    `catalog_id` + `directory`）。只认一种形状的后果是安装界面说缺依赖、
    运行时说就绪（或者反过来），而这种不一致没有任何症状。
    """
    for definition in _cli_definitions():
        candidate = definition.dependency_id.removesuffix("-cli")
        installed = dependency_ids_for_resource(
            {
                "type": "skill",
                "resource_id": f"skill.{candidate}",
                "source_metadata": {"name": candidate, "directory": f"skills/{candidate}"},
            }
        )
        assert definition.dependency_id in installed, (
            f"已安装形状下 {candidate!r} 识别不出 {definition.dependency_id}"
        )
