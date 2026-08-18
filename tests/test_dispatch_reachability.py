"""触发规则遮蔽（可达性）语义的唯一真源测试。

WebUI 曾经在浏览器里复刻过一份「无条件规则会遮蔽后续规则」的判断，两份实现必然
漂移。现在界面只渲染这里定义的结论，因此本测试就是该语义的契约。
"""

from typing import Literal

import pytest

from kirara_ai.workflow.core.dispatch import (
    CombinedDispatchRule,
    RuleGroup,
    SimpleDispatchRule,
    analyze_dispatch_reachability,
    is_catch_all_rule,
    sort_rules_in_dispatch_order,
)


def make_rule(rule_id: str, priority: int, rule_groups=None, enabled: bool = True) -> CombinedDispatchRule:
    return CombinedDispatchRule(
        rule_id=rule_id,
        name=rule_id,
        description="",
        workflow_id="chat:normal",
        priority=priority,
        enabled=enabled,
        rule_groups=rule_groups
        if rule_groups is not None
        else [
            RuleGroup(
                operator="or",
                rules=[SimpleDispatchRule(type="prefix", config={"prefix": f"/{rule_id}"})],
            )
        ],
        metadata={},
    )


def fallback_groups(
    operator: Literal["and", "or"] = "or", extra_types: tuple = ()
):
    rules = [SimpleDispatchRule(type="fallback", config={})]
    rules.extend(SimpleDispatchRule(type=extra, config={}) for extra in extra_types)
    return [RuleGroup(operator=operator, rules=rules)]


def test_dispatch_order_is_priority_desc_then_rule_id_asc():
    rules = [make_rule("zeta", 30), make_rule("alpha", 30), make_rule("system", 100)]

    assert [rule.rule_id for rule in sort_rules_in_dispatch_order(rules)] == [
        "system",
        "alpha",
        "zeta",
    ]


@pytest.mark.parametrize(
    "rule_groups,expected",
    [
        # 没有任何规则组 → 退化成无条件匹配
        ([], True),
        # 空条件组不构成约束
        ([RuleGroup(operator="or", rules=[])], True),
        # or 组含兜底条件 → 恒成立
        (fallback_groups("or"), True),
        # or 组里兜底条件与其他条件并列，任一满足即可，兜底永远满足
        (fallback_groups("or", ("prefix",)), True),
        # and 组里除兜底外还有别的条件 → 仍会拦下部分消息
        (fallback_groups("and", ("prefix",)), False),
        # and 组全是兜底条件 → 恒成立
        (fallback_groups("and"), True),
        # 普通条件 → 不是无条件规则
        (None, False),
    ],
)
def test_catch_all_detection_matches_match_semantics(rule_groups, expected):
    assert is_catch_all_rule(make_rule("target", 10, rule_groups=rule_groups)) is expected


def test_an_and_group_mixing_fallback_with_other_conditions_does_not_shadow_later_rules():
    """回归：前端旧实现只看「组里是否含 fallback」，会把这种规则误判成兜底。"""
    mixed = make_rule("mixed", 50, rule_groups=fallback_groups("and", ("prefix",)))
    later = make_rule("later", 10)

    results = {item.rule_id: item for item in analyze_dispatch_reachability([mixed, later])}

    assert results["mixed"].catch_all is False
    assert results["later"].unreachable is False


def test_rules_after_an_enabled_catch_all_rule_are_unreachable():
    catch_all = make_rule("catch_all", 50, rule_groups=fallback_groups())
    rules = [make_rule("high", 100), catch_all, make_rule("low", 10), make_rule("lowest", 0)]

    results = analyze_dispatch_reachability(rules)
    by_id = {item.rule_id: item for item in results}

    assert [item.rule_id for item in results] == ["high", "catch_all", "low", "lowest"]
    assert [item.order for item in results] == [1, 2, 3, 4]
    assert by_id["high"].unreachable is False
    assert by_id["catch_all"].unreachable is False
    assert by_id["catch_all"].catch_all is True
    assert by_id["low"].unreachable is True
    assert by_id["low"].shadowed_by_rule_id == "catch_all"
    assert by_id["lowest"].unreachable is True
    # 遮蔽者始终是第一条无条件规则，而不是最近的一条
    assert by_id["lowest"].shadowed_by_rule_id == "catch_all"


def test_a_disabled_catch_all_rule_shadows_nothing():
    rules = [
        make_rule("catch_all", 50, rule_groups=fallback_groups(), enabled=False),
        make_rule("low", 10),
    ]

    by_id = {item.rule_id: item for item in analyze_dispatch_reachability(rules)}

    assert by_id["catch_all"].catch_all is True
    assert by_id["low"].unreachable is False
    assert by_id["low"].shadowed_by_rule_id is None


def test_a_disabled_rule_after_a_catch_all_is_not_reported_as_unreachable():
    """已禁用的规则本来就不参与匹配，标成“永远不会触发”只会制造噪音。"""
    rules = [
        make_rule("catch_all", 50, rule_groups=fallback_groups()),
        make_rule("low", 10, enabled=False),
    ]

    by_id = {item.rule_id: item for item in analyze_dispatch_reachability(rules)}

    assert by_id["low"].unreachable is False
    assert by_id["low"].shadowed_by_rule_id is None


def test_order_counts_only_enabled_rules():
    """调度器的 get_active_rules() 先过滤 enabled 再排序，序号不能把禁用规则算进去。"""
    rules = [
        make_rule("aaa", 100),
        make_rule("bbb", 90, enabled=False),
        make_rule("ccc", 80),
        make_rule("ddd", 70, enabled=False),
        make_rule("eee", 60),
    ]

    results = analyze_dispatch_reachability(rules)
    by_id = {item.rule_id: item for item in results}

    # 禁用规则仍然返回一行，界面上必须可见，只是没有匹配次序
    assert [item.rule_id for item in results] == ["aaa", "bbb", "ccc", "ddd", "eee"]
    assert by_id["bbb"].order is None
    assert by_id["ddd"].order is None
    # 序号只在已启用的规则之间递增，界面上的「匹配顺序第 N 位」不再虚高
    assert by_id["aaa"].order == 1
    assert by_id["ccc"].order == 2
    assert by_id["eee"].order == 3


def test_same_priority_catch_all_shadows_only_the_rules_ordered_after_it():
    """同优先级按 rule_id 升序：排在兜底之前的规则仍然可达。"""
    rules = [
        make_rule("aaa", 30),
        make_rule("mmm", 30, rule_groups=fallback_groups()),
        make_rule("zzz", 30),
    ]

    by_id = {item.rule_id: item for item in analyze_dispatch_reachability(rules)}

    assert by_id["aaa"].unreachable is False
    assert by_id["mmm"].unreachable is False
    assert by_id["zzz"].unreachable is True
    assert by_id["zzz"].shadowed_by_rule_id == "mmm"
