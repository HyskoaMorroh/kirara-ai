import json
import re
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry
from kirara_ai.workflow.implementations.rules.default_rules import (
    PRIORITY_CHAT,
    PRIORITY_COMMAND,
    PRIORITY_FALLBACK,
    PRIORITY_SOFT_COMMAND,
    build_default_rules,
    register_system_dispatch_rules,
    validate_rule_workflows,
)


class _StubWorkflowRegistry:
    """只认识给定工作流 ID 的极简工作流注册表。"""

    def __init__(self, known_workflow_ids):
        self.known_workflow_ids = set(known_workflow_ids)

    def get(self, name, container=None):
        return object() if name in self.known_workflow_ids else None


class _ContainerWithWorkflows:
    def __init__(self, workflow_registry):
        self.workflow_registry = workflow_registry

    def resolve(self, dependency):
        if dependency is WorkflowRegistry:
            return self.workflow_registry
        raise LookupError(dependency)


def test_private_chat_rule_points_at_the_generic_chat_workflow():
    """全新部署的私聊默认流程必须是通用的 chat:normal，而不是推理模型专用模板。"""
    private_rules = _private_chat_rules(build_default_rules())

    assert [rule.workflow_id for rule in private_rules] == ["chat:normal"]


def test_rule_referencing_a_deleted_workflow_falls_back_to_chat_normal():
    """用户删掉预设工作流后，指向它的规则要退回 chat:normal 而不是每次都抛异常。"""
    registry = DispatchRuleRegistry(
        _ContainerWithWorkflows(_StubWorkflowRegistry({"chat:normal"}))
    )
    register_system_dispatch_rules(registry)
    creative_rule = registry.get_rule("chat_creative")
    assert creative_rule is not None
    creative_rule.workflow_id = "chat:dsr_thinking"

    degraded = validate_rule_workflows(registry)

    assert "chat_creative" in degraded
    assert registry.get_rule("chat_creative").workflow_id == "chat:normal"
    assert registry.get_rule("chat_creative").enabled is True


def test_rule_is_disabled_when_no_fallback_workflow_exists():
    """连兜底工作流都缺失时，禁用规则并保留配置，而不是让每条消息都报错。"""
    registry = DispatchRuleRegistry(_ContainerWithWorkflows(_StubWorkflowRegistry(set())))
    register_system_dispatch_rules(registry)

    degraded = validate_rule_workflows(registry)

    assert "chat_creative" in degraded
    assert registry.get_rule("chat_creative").enabled is False
    # 配置本身没有被删除，用户仍然可以在 WebUI 里改好后重新启用
    assert registry.get_rule("chat_creative").workflow_id


def test_validation_leaves_rules_untouched_when_every_workflow_exists():
    """所有引用都有效时不能改动任何规则。"""
    known = {rule.workflow_id for rule in build_default_rules()}
    registry = DispatchRuleRegistry(_ContainerWithWorkflows(_StubWorkflowRegistry(known)))
    register_system_dispatch_rules(registry)
    before = {rule.rule_id: (rule.workflow_id, rule.enabled) for rule in registry.get_all_rules()}

    degraded = validate_rule_workflows(registry)

    assert degraded == []
    assert {
        rule.rule_id: (rule.workflow_id, rule.enabled) for rule in registry.get_all_rules()
    } == before


class _Container:
    """提供调度规则注册表初始化所需的最小容器接口。"""

    def resolve(self, dependency):
        if dependency is WorkflowRegistry:
            return object()
        raise LookupError(dependency)


def _private_chat_rules(rules):
    return [
        rule
        for rule in rules
        if any(
            simple_rule.type == "chat_type"
            and simple_rule.config.get("chat_type") == "私聊"
            for rule_group in rule.rule_groups
            for simple_rule in rule_group.rules
        )
    ]


def test_preset_private_chat_rule_matches_the_shipped_rule():
    """无 data 目录的安装应使用与随附规则相同的私聊默认值。"""
    project_root = Path(__file__).resolve().parents[1]
    yaml = YAML(typ="safe")
    with (project_root / "data" / "dispatch_rules" / "rules.yaml").open(
        encoding="utf-8"
    ) as file:
        shipped_rules = yaml.load(file)

    shipped_private_rules = [
        rule
        for rule in shipped_rules
        if any(
            simple_rule["type"] == "chat_type"
            and simple_rule["config"].get("chat_type") == "私聊"
            for rule_group in rule["rule_groups"]
            for simple_rule in rule_group["rules"]
        )
    ]

    assert [
        (rule.rule_id, rule.workflow_id, rule.priority)
        for rule in _private_chat_rules(build_default_rules())
    ] == [
        (rule["rule_id"], rule["workflow_id"], rule["priority"])
        for rule in shipped_private_rules
    ]


def test_deleting_a_preset_rule_remains_deleted_after_restart(tmp_path):
    """用户删除内置规则后，下一次启动不能重新注册它。"""
    first_registry = DispatchRuleRegistry(_Container())
    register_system_dispatch_rules(first_registry)
    first_registry.delete_rule("system_help")
    first_registry.save_rules(str(tmp_path))

    restarted_registry = DispatchRuleRegistry(_Container())
    restarted_registry.load_rules(str(tmp_path))
    register_system_dispatch_rules(restarted_registry)

    assert restarted_registry.get_rule("system_help") is None


def test_existing_rules_file_does_not_restore_a_historically_deleted_preset(tmp_path):
    """升级前已删除的内置规则，即使尚无 tombstone 也不能在升级后复活。"""
    first_registry = DispatchRuleRegistry(_Container())
    register_system_dispatch_rules(first_registry)
    first_registry.delete_rule("system_help")
    first_registry.save_rules(str(tmp_path))
    (tmp_path / ".preset_tombstones.json").unlink()

    restarted_registry = DispatchRuleRegistry(_Container())
    restarted_registry.load_rules(str(tmp_path))
    register_system_dispatch_rules(restarted_registry)

    assert restarted_registry.get_rule("system_help") is None


def test_gacha_rule_only_matches_a_complete_command():
    """抽卡规则不能把普通聊天中的关键词误当作游戏指令。"""
    gacha_rule = next(
        rule for rule in build_default_rules() if rule.rule_id == "game_gacha"
    )
    simple_rule = gacha_rule.rule_groups[0].rules[0]

    project_root = Path(__file__).resolve().parents[1]
    yaml = YAML(typ="safe")
    with (project_root / "data" / "dispatch_rules" / "rules.yaml").open(
        encoding="utf-8"
    ) as file:
        shipped_rules = yaml.load(file)
    shipped_gacha_rule = next(
        rule for rule in shipped_rules if rule["rule_id"] == "game_gacha"
    )
    shipped_simple_rule = shipped_gacha_rule["rule_groups"][0]["rules"][0]

    assert simple_rule.type == "regex"
    pattern = simple_rule.config["pattern"]
    assert (simple_rule.type, simple_rule.config) == (
        shipped_simple_rule["type"],
        shipped_simple_rule["config"],
    )
    assert re.fullmatch(pattern, "抽卡")
    assert re.fullmatch(pattern, "/十连")
    assert re.fullmatch(pattern, "。单抽")
    assert not re.fullmatch(pattern, "抽卡概率是多少？")
    assert not re.fullmatch(pattern, "我想了解十连机制")


def test_gacha_mention_rule_restores_loose_keyword_matching():
    """句子里夹带的「抽卡」必须仍能触发抽卡模拟器：这是宽松规则存在的唯一理由。"""
    mention_rule = next(
        rule for rule in build_default_rules() if rule.rule_id == "game_gacha_mention"
    )
    simple_rule = mention_rule.rule_groups[0].rules[0]

    assert simple_rule.type == "keyword"
    keywords = simple_rule.config["keywords"]
    assert keywords == ["抽卡", "十连", "单抽"]
    # KeywordMatchRule.match 是子串匹配，等价于下面的判断
    assert any(keyword in "今天抽卡吗" for keyword in keywords)
    assert any(keyword in "我想了解十连机制" for keyword in keywords)
    assert not any(keyword in "今天天气不错" for keyword in keywords)


def test_gacha_mention_rule_sits_between_chat_and_fallback():
    """宽松规则必须低于对话、高于兜底，否则会劫持私聊对话或永远被兜底遮蔽。"""
    rules = {rule.rule_id: rule for rule in build_default_rules()}

    assert PRIORITY_FALLBACK < PRIORITY_SOFT_COMMAND < PRIORITY_CHAT
    assert rules["game_gacha_mention"].priority == PRIORITY_SOFT_COMMAND
    assert rules["chat_normal"].priority == PRIORITY_CHAT
    assert rules["chat_creative"].priority == PRIORITY_CHAT
    assert rules["fallback"].priority == PRIORITY_FALLBACK
    # 精确指令仍然排在对话之上，整条消息只有指令时优先走精确规则
    assert rules["game_gacha"].priority == PRIORITY_COMMAND


def test_both_gacha_rules_point_at_the_same_workflow():
    """精确与宽松两条规则必须指向同一个抽卡工作流。"""
    rules = {rule.rule_id: rule for rule in build_default_rules()}

    assert rules["game_gacha"].workflow_id == "game:gacha"
    assert rules["game_gacha_mention"].workflow_id == "game:gacha"


def test_code_built_rules_match_the_shipped_yaml():
    """代码内置规则集必须与随包的 rules.yaml 逐字段一致，两份是手工同步的。"""
    project_root = Path(__file__).resolve().parents[1]
    yaml = YAML(typ="safe")
    with (project_root / "data" / "dispatch_rules" / "rules.yaml").open(
        encoding="utf-8"
    ) as file:
        shipped_rules = yaml.load(file)

    def normalize(rule_id, name, description, workflow_id, priority, enabled, rule_groups):
        return (
            rule_id,
            name,
            description,
            workflow_id,
            priority,
            enabled,
            [
                (
                    group["operator"] if isinstance(group, dict) else group.operator,
                    [
                        (
                            simple["type"] if isinstance(simple, dict) else simple.type,
                            dict(simple["config"] if isinstance(simple, dict) else simple.config),
                        )
                        for simple in (
                            group["rules"] if isinstance(group, dict) else group.rules
                        )
                    ],
                )
                for group in rule_groups
            ],
        )

    built = [
        normalize(
            rule.rule_id,
            rule.name,
            rule.description,
            rule.workflow_id,
            rule.priority,
            rule.enabled,
            rule.rule_groups,
        )
        for rule in build_default_rules()
    ]
    shipped = [
        normalize(
            rule["rule_id"],
            rule["name"],
            rule["description"],
            rule["workflow_id"],
            rule["priority"],
            rule["enabled"],
            rule["rule_groups"],
        )
        for rule in shipped_rules
    ]

    assert built == shipped


def test_save_rules_keeps_the_previous_yaml_when_atomic_replace_fails(tmp_path, monkeypatch):
    """写入失败不能截断用户已有的 rules.yaml。"""
    registry = DispatchRuleRegistry(_Container())
    register_system_dispatch_rules(registry)
    registry.save_rules(str(tmp_path))
    rules_path = tmp_path / "rules.yaml"
    previous_content = rules_path.read_text(encoding="utf-8")

    original_replace = __import__(
        "kirara_ai.workflow.core.dispatch.registry", fromlist=["os"]
    ).os.replace

    def fail_rules_replace(source, destination):
        if Path(destination).name == "rules.yaml":
            raise OSError("simulated rules replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "kirara_ai.workflow.core.dispatch.registry.os.replace", fail_rules_replace
    )

    registry.disable_rule("system_help")
    with pytest.raises(OSError, match="simulated rules replace failure"):
        registry.save_rules(str(tmp_path))

    assert rules_path.read_text(encoding="utf-8") == previous_content
    assert not list(tmp_path.glob(".rules.yaml.*.tmp"))


def test_save_rules_keeps_the_previous_tombstones_when_atomic_replace_fails(
    tmp_path, monkeypatch
):
    """删除标记写入失败时也必须保留上一份可恢复的 JSON。"""
    registry = DispatchRuleRegistry(_Container())
    register_system_dispatch_rules(registry)
    registry.save_rules(str(tmp_path))
    tombstones_path = tmp_path / ".preset_tombstones.json"
    previous_content = tombstones_path.read_text(encoding="utf-8")

    original_replace = __import__(
        "kirara_ai.workflow.core.dispatch.registry", fromlist=["os"]
    ).os.replace

    def fail_tombstones_replace(source, destination):
        if Path(destination).name == ".preset_tombstones.json":
            raise OSError("simulated tombstones replace failure")
        return original_replace(source, destination)

    monkeypatch.setattr(
        "kirara_ai.workflow.core.dispatch.registry.os.replace",
        fail_tombstones_replace,
    )

    registry.delete_rule("system_help")
    with pytest.raises(OSError, match="simulated tombstones replace failure"):
        registry.save_rules(str(tmp_path))

    assert json.loads(tombstones_path.read_text(encoding="utf-8")) == json.loads(
        previous_content
    )
    assert not list(tmp_path.glob("..preset_tombstones.json.*.tmp"))
