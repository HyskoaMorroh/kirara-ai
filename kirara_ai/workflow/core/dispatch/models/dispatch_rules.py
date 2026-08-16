from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from kirara_ai.im.message import IMMessage
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.workflow import Workflow
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

logger = get_logger("DispatchRule")

class SimpleDispatchRule(BaseModel):
    """简单规则，包含规则类型和配置"""
    type: str
    config: Dict[str, Any]

class RuleGroup(BaseModel):
    """规则组，包含多个简单规则和组合操作符"""
    operator: Literal["and", "or"] = "or"
    rules: List[SimpleDispatchRule]

class CombinedDispatchRule(BaseModel):
    """组合调度规则，支持复杂的规则组合"""
    rule_id: str
    name: str
    description: str = ""
    workflow_id: str
    priority: int = 5
    enabled: bool = True
    rule_groups: List[RuleGroup]  # 规则组之间是 AND 关系
    metadata: Dict[str, Any] = {}

    def match(self, message: IMMessage, workflow_registry: WorkflowRegistry, container: DependencyContainer) -> bool:
        """
        判断消息是否匹配该规则。
        规则组之间是 AND 关系，规则组内部根据 operator 决定是 AND 还是 OR 关系。
        """
        # 如果规则被禁用，直接返回 False
        if not self.enabled:
            return False

        # 所有规则组都必须匹配（AND 关系）
        for group in self.rule_groups:

            # 如果组内没有规则，视为该组不构成约束，继续检查后续规则组。
            # 注意：这里必须 continue 而不是 return True——直接返回会让整条
            # 规则短路成“匹配”，跳过后面所有 AND 组，等同于兜底规则。
            if len(group.rules) == 0:
                continue

            # 获取组内所有规则的匹配结果
            rule_results = []
            for rule in group.rules:
                try:
                    from ..rules.base import DispatchRule

                    # 创建具体的规则实例
                    rule_class = DispatchRule.get_rule_type(rule.type)
                    rule_instance = rule_class.from_config(
                        rule_class.config_class(**rule.config),
                        workflow_registry,
                        self.workflow_id,
                    )
                    rule_results.append(rule_instance.match(message, container))
                except Exception as e:
                    # 如果规则创建或匹配过程出错，视为不匹配
                    logger.error(f"Rule {rule.type} from config {rule.config} creation or matching failed: {e}")
                    continue

            # 根据操作符确定组的匹配结果
            if not rule_results:  # 如果组内没有有效规则，视为不匹配
                return False

            if group.operator == "and":
                if not all(rule_results):  # AND 关系：所有规则都必须匹配
                    return False
            else:  # operator == "or"
                if not any(rule_results):  # OR 关系：至少一个规则匹配
                    return False

        # 所有规则组都匹配成功
        return True

    def explain_match(
        self,
        message: IMMessage,
        workflow_registry: WorkflowRegistry,
        container: DependencyContainer,
    ) -> Dict[str, Any]:
        """以不产生副作用的方式解释一条规则的匹配结果。

        调度器仍然使用 :meth:`match` 执行真实规则。本方法只用于管理界面的
        “试运行”：随机概率和 IM 实例条件不会取样或启动/查询外部实例，因此会
        返回 ``None``（不确定），而不是伪造一个确定的匹配结果。
        """
        if not self.enabled:
            return {
                "matched": False,
                "reason": "规则已禁用",
                "groups": [],
            }

        group_explanations: List[Dict[str, Any]] = []
        group_results: List[Optional[bool]] = []

        for group in self.rule_groups:
            if not group.rules:
                group_explanations.append(
                    {
                        "operator": group.operator,
                        "matched": True,
                        "rules": [],
                        "reason": "空条件组不构成额外限制",
                    }
                )
                group_results.append(True)
                continue

            condition_explanations: List[Dict[str, Any]] = []
            valid_results: List[Optional[bool]] = []
            for rule in group.rules:
                condition: Dict[str, Any] = {
                    "type": rule.type,
                    "config": rule.config,
                    "matched": False,
                }
                if rule.type == "random":
                    condition.update(
                        matched=None,
                        reason="随机概率规则在试运行中不取样",
                    )
                    valid_results.append(None)
                elif rule.type == "im_instance":
                    condition.update(
                        matched=None,
                        reason="IM 实例条件需要真实运行中的消息来源，当前试运行无法确定",
                    )
                    valid_results.append(None)
                else:
                    try:
                        from ..rules.base import DispatchRule

                        rule_class = DispatchRule.get_rule_type(rule.type)
                        rule_instance = rule_class.from_config(
                            rule_class.config_class(**rule.config),
                            workflow_registry,
                            self.workflow_id,
                        )
                        matched = rule_instance.match(message, container)
                        condition["matched"] = matched
                        valid_results.append(matched)
                    except Exception as exc:
                        # 与 match() 一致：无效条件不会成为可用匹配结果；这里只额外
                        # 把原因返回给管理员，便于修复配置。
                        logger.error(
                            f"Rule {rule.type} from config {rule.config} creation or matching failed: {exc}"
                        )
                        condition["reason"] = f"条件无法评估：{exc}"
                condition_explanations.append(condition)

            if not valid_results:
                group_matched: Optional[bool] = False
                group_reason = "条件组中没有可评估的条件"
            elif group.operator == "and":
                group_matched = (
                    False
                    if False in valid_results
                    else True
                    if all(result is True for result in valid_results)
                    else None
                )
                group_reason = "所有条件均需满足"
            else:
                group_matched = (
                    True
                    if True in valid_results
                    else False
                    if all(result is False for result in valid_results)
                    else None
                )
                group_reason = "任一条件满足即可"

            group_explanations.append(
                {
                    "operator": group.operator,
                    "matched": group_matched,
                    "rules": condition_explanations,
                    "reason": group_reason,
                }
            )
            group_results.append(group_matched)

        matched = (
            False
            if False in group_results
            else True
            if all(result is True for result in group_results)
            else None
        )
        return {
            "matched": matched,
            "reason": None if matched is not None else "至少一个条件的结果无法确定",
            "groups": group_explanations,
        }

    def get_workflow(self, container: DependencyContainer) -> Optional[Workflow]:
        """获取该规则对应的工作流实例。"""
        workflow = container.resolve(WorkflowRegistry).get_workflow(self.workflow_id, container)
        return workflow
