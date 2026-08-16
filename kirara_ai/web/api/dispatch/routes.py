from quart import Blueprint, g, jsonify, request

from kirara_ai.im.message import IMMessage, MentionElement, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.workflow.core.dispatch import (
    CombinedDispatchRule,
    DispatchRule,
    DispatchRuleRegistry,
    analyze_dispatch_reachability,
    sort_rules_in_dispatch_order,
)
from kirara_ai.workflow.core.workflow import WorkflowRegistry

from ...auth.middleware import require_auth
from .models import (
    DispatchPreviewRequest,
    DispatchPreviewResponse,
    DispatchPreviewRuleResult,
    DispatchReachabilityRequest,
    DispatchReachabilityResponse,
    DispatchRuleList,
    DispatchRuleResponse,
)

dispatch_bp = Blueprint("dispatch", __name__)


def _rules_with_draft(rules, draft_rule):
    """把编辑中的草稿并入规则集合参与排序；同 rule_id 的现有规则会被替换。"""
    if draft_rule is None:
        return list(rules)
    merged = [rule for rule in rules if rule.rule_id != draft_rule.rule_id]
    merged.append(draft_rule)
    return merged


def _has_configured_conditions(rule: CombinedDispatchRule) -> bool:
    """判断规则是否至少有一个真实的匹配条件。"""
    return any(group.rules for group in rule.rule_groups)


def _empty_conditions_response(rule: CombinedDispatchRule):
    """拒绝会退化成无条件匹配的空规则。"""
    if _has_configured_conditions(rule):
        return None
    return jsonify(
        {
            "error": "Rule must contain at least one condition; use an explicit fallback condition for a catch-all rule"
        }
    ), 400


def _build_preview_message(preview: DispatchPreviewRequest) -> IMMessage:
    """构造仅用于规则判断的内存消息，不连接任何 IM 适配器。"""
    if preview.chat_type == "群聊":
        sender = ChatSender.from_group_chat(
            preview.sender_id,
            preview.group_id or "preview-group",
            "试运行用户",
        )
    else:
        sender = ChatSender.from_c2c_chat(preview.sender_id, "试运行用户")

    elements = [TextMessage(preview.content)]
    if preview.mentioned:
        elements.insert(0, MentionElement(ChatSender.get_bot_sender()))
    return IMMessage(sender=sender, message_elements=elements)


@dispatch_bp.route("/reachability", methods=["POST"])
@require_auth
async def analyze_reachability():
    """静态分析规则的匹配顺序与遮蔽关系，不需要示例消息，也无任何副作用。

    WebUI 在编辑草稿时用它做即时反馈：遮蔽语义只在
    `workflow.core.dispatch.reachability` 中定义一次，界面不再自己推导。
    """
    payload = await request.get_json(silent=True) or {}
    reachability_request = DispatchReachabilityRequest(**payload)
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)

    rules = _rules_with_draft(registry.get_all_rules(), reachability_request.draft_rule)
    return DispatchReachabilityResponse(
        reachability=analyze_dispatch_reachability(rules)
    ).model_dump()


@dispatch_bp.route("/preview", methods=["POST"])
@require_auth
async def preview_rules():
    """按真实调度顺序解释规则匹配，但绝不执行、保存或修改规则。"""
    preview = DispatchPreviewRequest(**(await request.get_json()))
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)
    workflow_registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    rules = sort_rules_in_dispatch_order(
        _rules_with_draft(registry.get_all_rules(), preview.draft_rule)
    )
    # 静态可达性与「本条消息的判定结果」是两回事：前者说明规则永远不会被判断到，
    # 后者只针对当前示例消息。两者都由后端给出，界面无需再算一遍。
    reachability_by_rule_id = {
        item.rule_id: item for item in analyze_dispatch_reachability(rules)
    }

    selected_rule_id = None
    selected_workflow_id = None
    has_indeterminate_predecessor = False
    results = []
    message = _build_preview_message(preview)
    with g.container.scoped() as scoped_container:
        for rule in rules:
            explanation = rule.explain_match(message, workflow_registry, scoped_container)
            matched = explanation["matched"]

            if not rule.enabled:
                decision = "disabled"
            elif matched is None:
                decision = "indeterminate"
                has_indeterminate_predecessor = True
            elif matched:
                if selected_rule_id is None and not has_indeterminate_predecessor:
                    decision = "selected"
                    selected_rule_id = rule.rule_id
                    selected_workflow_id = rule.workflow_id
                else:
                    decision = "shadowed"
            else:
                decision = "not_matched"

            reachability = reachability_by_rule_id[rule.rule_id]
            results.append(
                DispatchPreviewRuleResult(
                    rule_id=rule.rule_id,
                    name=rule.name,
                    workflow_id=rule.workflow_id,
                    priority=rule.priority,
                    enabled=rule.enabled,
                    matched=matched,
                    decision=decision,
                    explanation=explanation,
                    order=reachability.order,
                    catch_all=reachability.catch_all,
                    unreachable=reachability.unreachable,
                    shadowed_by_rule_id=reachability.shadowed_by_rule_id,
                )
            )

    return DispatchPreviewResponse(
        selected_rule_id=selected_rule_id,
        selected_workflow_id=selected_workflow_id,
        rules=results,
    ).model_dump()


@dispatch_bp.route("/rules", methods=["GET"])
@require_auth
async def list_rules():
    """获取所有调度规则"""
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)
    all_rules = sort_rules_in_dispatch_order(registry.get_all_rules())
    reachability = analyze_dispatch_reachability(all_rules)
    rules = [rule.model_dump() for rule in all_rules]
    return DispatchRuleList(rules=rules, reachability=reachability).model_dump()


@dispatch_bp.route("/rules/<rule_id>", methods=["GET"])
@require_auth
async def get_rule(rule_id: str):
    """获取特定调度规则的信息"""
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)

    rule = registry.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404

    return DispatchRuleResponse(rule=rule).model_dump()


@dispatch_bp.route("/rules", methods=["POST"])
@require_auth
async def create_rule():
    """创建新的调度规则"""
    data = await request.get_json()
    rule_data = CombinedDispatchRule(**data)

    empty_conditions_response = _empty_conditions_response(rule_data)
    if empty_conditions_response:
        return empty_conditions_response

    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)
    workflow_registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    # 检查规则ID是否已存在
    if registry.get_rule(rule_data.rule_id):
        return jsonify({"error": "Rule ID already exists"}), 400

    # 检查工作流是否存在
    if not workflow_registry.get(rule_data.workflow_id):
        return jsonify({"error": "Workflow not found"}), 400

    # 记录该 rule_id 之前是否已存在，错误回滚时才知道该不该删
    rule_existed_before = registry.get_rule(rule_data.rule_id) is not None
    try:
        # 创建规则
        rule = registry.create_rule(rule_data)

        # 保存规则（放到线程池，避免 fsync 阻塞事件循环）
        await registry.save_rules_async()

        return DispatchRuleResponse(rule=rule).model_dump()
    except Exception as e:
        # Keep the in-memory dispatcher aligned with the durable rules file.
        # A disk-full/permission failure must not leave a rule active until the
        # next restart even though the API reported that creation failed.
        # 仅当这个 id 本来不存在时才删除：否则会连带把用户原有的同 id 规则
        # 一起从内存里抹掉（虽然上面的重复检查通常会先返回 400）。
        if not rule_existed_before:
            registry.rules.pop(rule_data.rule_id, None)
        return jsonify({"error": str(e)}), 400


@dispatch_bp.route("/rules/<rule_id>", methods=["PUT"])
@require_auth
async def update_rule(rule_id: str):
    """更新调度规则"""
    data = await request.get_json()
    rule_data = CombinedDispatchRule(**data)

    empty_conditions_response = _empty_conditions_response(rule_data)
    if empty_conditions_response:
        return empty_conditions_response

    if rule_id != rule_data.rule_id:
        return jsonify({"error": "Rule ID mismatch"}), 400

    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)
    workflow_registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    # 检查规则是否存在
    if not registry.get_rule(rule_id):
        return jsonify({"error": "Rule not found"}), 404

    # 检查工作流是否存在
    if not workflow_registry.get(rule_data.workflow_id):
        return jsonify({"error": "Workflow not found"}), 400

    previous_rule = registry.get_rule(rule_id)
    assert previous_rule is not None
    try:
        # 更新规则
        rule = registry.update_rule(rule_id, rule_data)

        # 保存规则（放到线程池，避免 fsync 阻塞事件循环）
        await registry.save_rules_async()
        return DispatchRuleResponse(rule=rule).model_dump()
    except Exception as e:
        registry.rules[rule_id] = previous_rule
        return jsonify({"error": str(e)}), 400


@dispatch_bp.route("/rules/<rule_id>", methods=["DELETE"])
@require_auth
async def delete_rule(rule_id: str):
    """删除调度规则"""
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)

    # 检查规则是否存在
    if not registry.get_rule(rule_id):
        return jsonify({"error": "Rule not found"}), 404

    deleted_rule = registry.get_rule(rule_id)
    assert deleted_rule is not None
    had_tombstone = rule_id in registry.deleted_preset_rule_ids
    try:
        registry.delete_rule(rule_id)
        await registry.save_rules_async()
        return jsonify({"message": "Rule deleted successfully"})
    except Exception as e:
        registry.rules[rule_id] = deleted_rule
        if had_tombstone:
            registry.deleted_preset_rule_ids.add(rule_id)
        else:
            registry.deleted_preset_rule_ids.discard(rule_id)
        return jsonify({"error": str(e)}), 400


@dispatch_bp.route("/rules/<rule_id>/enable", methods=["POST"])
@require_auth
async def enable_rule(rule_id: str):
    """启用调度规则"""
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)

    rule = registry.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404

    if rule.enabled:
        return jsonify({"error": "Rule is already enabled"}), 400

    try:
        registry.enable_rule(rule_id)
        await registry.save_rules_async()
        return jsonify({"message": "Rule enabled successfully"})
    except Exception as e:
        rule.enabled = False
        return jsonify({"error": str(e)}), 400


@dispatch_bp.route("/rules/<rule_id>/disable", methods=["POST"])
@require_auth
async def disable_rule(rule_id: str):
    """禁用调度规则"""
    registry: DispatchRuleRegistry = g.container.resolve(DispatchRuleRegistry)

    rule = registry.get_rule(rule_id)
    if not rule:
        return jsonify({"error": "Rule not found"}), 404

    if not rule.enabled:
        return jsonify({"error": "Rule is already disabled"}), 400

    try:
        registry.disable_rule(rule_id)
        await registry.save_rules_async()
        return jsonify({"message": "Rule disabled successfully"})
    except Exception as e:
        rule.enabled = True
        return jsonify({"error": str(e)}), 400


@dispatch_bp.route("/types", methods=["GET"])
@require_auth
async def get_rule_types():
    """获取所有可用的规则类型"""
    return jsonify({"types": list(DispatchRule.rule_types.keys())})


@dispatch_bp.route("/types/<rule_type>/config-schema", methods=["GET"])
@require_auth
async def get_rule_config_schema(rule_type: str):
    """获取指定规则类型的配置字段模式"""
    try:
        if rule_type not in DispatchRule.rule_types:
            return jsonify({"error": "Invalid rule type"}), 404

        rule_class = DispatchRule.rule_types[rule_type]
        config_class = rule_class.config_class
        schema = config_class.model_json_schema()
        return jsonify({"configSchema": schema})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
