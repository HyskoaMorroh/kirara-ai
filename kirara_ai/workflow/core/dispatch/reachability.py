"""触发规则的匹配顺序与遮蔽（可达性）语义。

「按优先级降序判断、命中第一条即停止、无条件规则会让后续规则永远不被判断」
这套语义原先在后端（试运行接口）和 WebUI（规则列表）各写了一份，两份实现必然
随时间漂移。这里把它收敛成唯一实现：调度顺序、无条件规则判定、遮蔽关系都只在
本模块定义，注册表、试运行接口与 WebUI 全部读取本模块的结果。
"""

from typing import Iterable, List, Optional, Tuple

from pydantic import BaseModel

from .models.dispatch_rules import CombinedDispatchRule, RuleGroup

#: 总是匹配的兜底条件类型，无条件规则判定的唯一依据。
FALLBACK_RULE_TYPE = "fallback"


def dispatch_order_key(rule: CombinedDispatchRule) -> Tuple[int, str]:
    """调度排序键：优先级降序，同优先级按 rule_id 升序。

    同优先级必须有确定的次序，否则在不同机器、不同规则文件读取顺序下
    （`os.listdir` 的顺序并不跨平台稳定）匹配结果会不一致。
    """
    return (-rule.priority, rule.rule_id)


def sort_rules_in_dispatch_order(
    rules: Iterable[CombinedDispatchRule],
) -> List[CombinedDispatchRule]:
    """按真实调度顺序排序规则，返回新列表，不修改入参。"""
    return sorted(rules, key=dispatch_order_key)


def is_unconditional_group(group: RuleGroup) -> bool:
    """判断一个条件组是否对任何消息都成立。

    与 :meth:`CombinedDispatchRule.match` 的组内逻辑严格对应：
    - 空组不构成约束，视为恒成立；
    - `or` 组只要含一个兜底条件就恒成立；
    - `and` 组必须**全部**是兜底条件才恒成立——只要还有别的条件，这个组就
      仍会拦下部分消息，所以整条规则不是无条件规则。
    """
    if not group.rules:
        return True
    if group.operator == "and":
        return all(rule.type == FALLBACK_RULE_TYPE for rule in group.rules)
    return any(rule.type == FALLBACK_RULE_TYPE for rule in group.rules)


def is_catch_all_rule(rule: CombinedDispatchRule) -> bool:
    """判断规则是否会拦下所有消息（无条件规则）。

    规则组之间是 AND 关系，因此必须每个组都恒成立；没有任何规则组时同样
    退化成无条件匹配。
    """
    if not rule.rule_groups:
        return True
    return all(is_unconditional_group(group) for group in rule.rule_groups)


class DispatchRuleReachability(BaseModel):
    """一条规则在真实调度顺序中的位置与遮蔽状态。"""

    rule_id: str
    name: str
    workflow_id: str
    priority: int
    enabled: bool
    #: 从 1 开始的匹配次序，与调度器实际判断顺序一致。
    order: int
    #: 该规则本身是否为无条件规则。
    catch_all: bool
    #: 该规则是否因为排在某条已启用的无条件规则之后而永远不会被判断到。
    unreachable: bool
    #: 遮蔽它的那条无条件规则的 ID；未被遮蔽时为 None。
    shadowed_by_rule_id: Optional[str] = None


def analyze_dispatch_reachability(
    rules: Iterable[CombinedDispatchRule],
) -> List[DispatchRuleReachability]:
    """按调度顺序分析每条规则的可达性。

    只做静态分析：不需要示例消息，也不会创建条件实例、取样随机概率或访问
    IM 实例，因此没有任何副作用。已禁用的规则不会遮蔽后续规则，也不会被标记
    为不可达（它本来就不参与匹配）。
    """
    ordered = sort_rules_in_dispatch_order(rules)
    shadowing_rule_id: Optional[str] = None
    results: List[DispatchRuleReachability] = []

    for index, rule in enumerate(ordered):
        catch_all = is_catch_all_rule(rule)
        unreachable = bool(rule.enabled) and shadowing_rule_id is not None
        results.append(
            DispatchRuleReachability(
                rule_id=rule.rule_id,
                name=rule.name,
                workflow_id=rule.workflow_id,
                priority=rule.priority,
                enabled=rule.enabled,
                order=index + 1,
                catch_all=catch_all,
                unreachable=unreachable,
                shadowed_by_rule_id=shadowing_rule_id if unreachable else None,
            )
        )
        # 只有已启用的无条件规则才会截断后续判断；第一条即可决定所有后继。
        if shadowing_rule_id is None and rule.enabled and catch_all:
            shadowing_rule_id = rule.rule_id

    return results
