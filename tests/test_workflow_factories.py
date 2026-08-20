from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.implementations.factories.default_factory import DefaultWorkflowFactory
from kirara_ai.workflow.implementations.factories.game_factory import GameWorkflowFactory
from kirara_ai.workflow.implementations.factories.persona import (DEFAULT_PERSONA_SYSTEM_PROMPT,
                                                                  DEFAULT_USER_PROMPT_FORMAT)
from kirara_ai.workflow.implementations.factories.system_factory import SystemWorkflowFactory

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 会把人设正文发给模型的角色扮演预设。这些文件里的人设必须与 persona.py 一致，
# 否则「同一个角色」在不同预设下表现不一样；历史上 normal_multimodal.yaml 曾被
# 静默剥掉人设，只剩 # Information / # Memories 两段，模型因此不再扮演角色。
# dsr_thinking.yaml 不在此列：它针对思维链模型专门重写为 # Rules 指令集，是有意的差异。
PERSONA_PRESETS = (
    "normal.yaml",
    "normal_multimodal.yaml",
    "talk_break.yaml",
)


@pytest.fixture
def container():
    return MagicMock(spec=DependencyContainer)


def test_game_dice_workflow(container):
    """测试骰子游戏工作流创建"""
    workflow = GameWorkflowFactory.create_dice_workflow().build(container)

    # 验证工作流结构
    assert workflow.name == "骰子游戏"
    assert len(workflow.blocks) == 3  # GetIMMessage -> DiceRoll -> SendIMMessage

    # 验证连接
    assert len(workflow.wires) == 2  # 两个连接


def test_game_gacha_workflow(container):
    """测试抽卡游戏工作流创建"""
    workflow = GameWorkflowFactory.create_gacha_workflow().build(container)

    # 验证工作流结构
    assert workflow.name == "抽卡游戏"
    assert len(workflow.blocks) == 3  # GetIMMessage -> GachaSimulator -> SendIMMessage

    # 验证连接
    assert len(workflow.wires) == 2  # 两个连接


def test_system_help_workflow(container):
    """测试帮助信息工作流创建"""
    workflow = SystemWorkflowFactory.create_help_workflow().build(container)

    # 验证工作流结构
    assert workflow.name == "帮助信息"
    assert len(workflow.blocks) == 2  # GenerateHelp -> SendIMMessage

    # 验证连接
    assert len(workflow.wires) == 1  # 一个连接


# ---- 人设提示词：代码侧与数据侧不允许漂移 ----


def test_default_workflow_uses_the_shared_persona(container):
    """默认工作流的提示词必须来自 persona.py，不能再各存一份副本。"""
    workflow = DefaultWorkflowFactory.create_default_workflow().build(container)

    texts = [
        block.text for block in workflow.blocks
        if getattr(block, "name", "") in {"system_prompt", "user_prompt"}
    ]

    assert DEFAULT_PERSONA_SYSTEM_PROMPT in texts
    assert DEFAULT_USER_PROMPT_FORMAT in texts


@pytest.mark.parametrize("preset_name", PERSONA_PRESETS)
def test_roleplay_presets_still_carry_the_persona(preset_name: str):
    """角色扮演预设不能只剩 # Information / # Memories 骨架。

    只断言人设的关键段落，而不是整段文本相等：YAML 是用户可编辑的数据文件，
    允许在人设之外追加内容（例如 talk_break.yaml 就多了实时时间拼接）。
    """
    for root in (
        PROJECT_ROOT / "kirara_ai" / "workflow" / "presets" / "chat",
        PROJECT_ROOT / "data" / "workflows" / "chat",
    ):
        path = root / preset_name
        if not path.exists():
            # normal.yaml 只存在于 data/workflows，不随包分发。
            continue

        text = path.read_text(encoding="utf-8")
        assert "# Role: 角色扮演" in text, f"{path} 缺少人设主体"
        assert "刘思思" in text, f"{path} 缺少人设主体"
        assert "## 互动规则" in text, f"{path} 缺少互动规则"
        assert "{memory_content}" in text, f"{path} 缺少记忆占位符"

        # 时间可以由提示词里的 {current_date_time} 占位符提供，也可以由
        # current_time_block 在运行时拼接（talk_break.yaml 走后者，因此提示词里
        # 没有占位符）。两者都不满足才说明时间信息真的丢了。
        assert (
            "{current_date_time}" in text or "internal:current_time_block" in text
        ), f"{path} 既没有时间占位符，也没有实时时间节点"
