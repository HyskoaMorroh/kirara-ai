"""内置调度规则。

调度规则原先只存在于 `data/dispatch_rules/rules.yaml`，而该文件既不在
`MANIFEST.in` 也不在 `pyproject.toml` 的 package-data 里，只有 Docker 镜像的
`start.sh` 会把它拷进数据目录。结果是 `pip install kirara-ai` 之后直接运行，
调度规则表为空——机器人对任何消息都不会响应，用户必须自己去 WebUI 里
逐条配置才能得到第一个回复。

这里把默认规则以代码形式内置，语义与 `register_preset_workflow` 一致：
只在同 rule_id 不存在时注册，用户在 WebUI 里改过或删掉的规则不会被覆盖。
"""

from typing import List

from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import (CombinedDispatchRule, RuleGroup,
                                                                    SimpleDispatchRule)
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry

# 优先级分层约定，避免不同类别的规则互相遮蔽：
#   100 系统命令  —— /help、/清空记忆 等，必须优先于一切对话规则
#    60 精确指令  —— 有明确前缀或正则的功能（骰子、抽卡）
#    30 对话      —— 群聊 @/前缀触发、私聊直接触发
#     0 兜底      —— 前面都没命中时记录聊天内容
PRIORITY_SYSTEM = 100
PRIORITY_COMMAND = 60
PRIORITY_CHAT = 30
PRIORITY_FALLBACK = 0


def _rule(
    rule_id: str,
    name: str,
    description: str,
    workflow_id: str,
    priority: int,
    rule_groups: List[RuleGroup],
    metadata: dict | None = None,
) -> CombinedDispatchRule:
    """构造一条组合规则，减少下面的重复样板"""
    return CombinedDispatchRule(
        rule_id=rule_id,
        name=name,
        description=description,
        workflow_id=workflow_id,
        priority=priority,
        enabled=True,
        rule_groups=rule_groups,
        metadata=metadata or {},
    )


def _group(*rules: SimpleDispatchRule, operator: str = "or") -> RuleGroup:
    return RuleGroup(operator=operator, rules=list(rules))  # type: ignore[arg-type]


def build_default_rules() -> List[CombinedDispatchRule]:
    """构造默认调度规则集合

    这些规则只引用代码内置的预设工作流（system:help、system:clear_memory、
    game:dice、game:gacha、chat:normal、chat:memory_store），确保在没有
    `data/workflows` 的纯 pip 安装环境下也能全部正常执行。
    """
    return [
        # ---- 系统命令：优先级最高，任何聊天场景下都应先被识别 ----
        _rule(
            rule_id="system_help",
            name="帮助命令",
            description="发送 /help 查看当前可用的全部功能与触发方式。",
            workflow_id="system:help",
            priority=PRIORITY_SYSTEM,
            rule_groups=[_group(SimpleDispatchRule(type="prefix", config={"prefix": "/help"}))],
            metadata={"category": "system", "permission": "user"},
        ),
        _rule(
            rule_id="system_clear_memory",
            name="清空记忆",
            description="发送 /清空记忆 清除当前会话的聊天记忆，让机器人重新开始。",
            workflow_id="system:clear_memory",
            priority=PRIORITY_SYSTEM,
            rule_groups=[
                _group(SimpleDispatchRule(type="prefix", config={"prefix": "/清空记忆"}))
            ],
            metadata={"category": "system", "permission": "user"},
        ),
        # ---- 精确指令：优先级高于对话，否则会被私聊对话规则吞掉 ----
        _rule(
            rule_id="game_dice",
            name="骰子",
            description="发送 .roll 1d100 之类的指令掷骰，支持 XdY 格式。",
            workflow_id="game:dice",
            priority=PRIORITY_COMMAND,
            rule_groups=[
                _group(
                    SimpleDispatchRule(
                        type="regex", config={"pattern": r"^[.。]roll\s*(\d+)?d(\d+)"}
                    )
                )
            ],
            metadata={"category": "game", "permission": "user"},
        ),
        _rule(
            rule_id="game_gacha",
            name="抽卡",
            description="单独发送「抽卡」「十连」或「单抽」体验抽卡模拟器。注意必须整条消息只有该指令才会触发，「今天抽卡吗」这类夹在句子里的写法不算。",
            workflow_id="game:gacha",
            priority=PRIORITY_COMMAND,
            rule_groups=[
                _group(
                    SimpleDispatchRule(
                        type="regex",
                        config={
                            "pattern": r"^\s*(?:[/.。])?(?:抽卡|十连|单抽)\s*$"
                        },
                    )
                )
            ],
            metadata={"category": "game", "permission": "user"},
        ),
        # ---- 对话：群聊需要显式召唤，私聊直接响应 ----
        _rule(
            rule_id="chat_normal",
            name="群聊 AI 对话",
            description="群聊中以 /chat 开头或直接 @机器人 即可对话。",
            workflow_id="chat:normal",
            priority=PRIORITY_CHAT,
            rule_groups=[
                _group(
                    SimpleDispatchRule(type="prefix", config={"prefix": "/chat"}),
                    SimpleDispatchRule(type="bot_mention", config={}),
                ),
                _group(SimpleDispatchRule(type="chat_type", config={"chat_type": "群聊"})),
            ],
            metadata={"category": "chat", "permission": "user"},
        ),
        _rule(
            rule_id="chat_creative",
            name="私聊 AI 对话",
            description="私聊时直接发送内容即可对话，无需任何前缀。",
            # 指向通用的 chat:normal。原先指向 chat:dsr_thinking，而那是针对
            # DeepSeek 推理模型（会输出 <think> 标签）的专用模板：全新部署换成
            # 普通模型时，正则会把整段回复当作 think 之后的内容处理，用户看到的
            # 输出很容易莫名其妙。dsr_thinking 保留为可选模板，需要时在 WebUI
            # 里把本规则改指向它即可。
            workflow_id="chat:normal",
            priority=PRIORITY_CHAT,
            rule_groups=[
                _group(SimpleDispatchRule(type="chat_type", config={"chat_type": "私聊"}))
            ],
            metadata={"category": "chat", "permission": "user"},
        ),
        # ---- 兜底：没有命中任何规则时，静默记录聊天内容供后续查询记忆使用 ----
        _rule(
            rule_id="fallback",
            name="默认规则",
            description="以上规则都没有匹配时执行，默默记录聊天内容，不会回复消息。",
            workflow_id="chat:memory_store",
            priority=PRIORITY_FALLBACK,
            rule_groups=[_group(SimpleDispatchRule(type="fallback", config={}))],
            metadata={"category": "system", "permission": "user"},
        ),
    ]


def register_system_dispatch_rules(registry: DispatchRuleRegistry):
    """注册系统自带的调度规则

    必须在 `load_rules()` 之后调用：已从 YAML 载入的同 id 规则会被保留，
    因此用户的自定义配置始终优先于这里的默认值。
    """
    # Existing YAML is the user's complete ruleset. Only inject defaults on first start.
    # This keeps deletions made before tombstones were introduced from being restored.
    if registry.has_persisted_rules:
        return

    for rule in build_default_rules():
        registry.register_preset_rule(rule)


def _workflow_exists(registry: DispatchRuleRegistry, workflow_id: str) -> bool:
    """判断某个工作流当前是否可用

    工作流注册表不可用（例如单元测试里的精简容器）时返回 True，
    这样校验只会拦掉"确认不存在"的引用，不会误伤正常注册流程。
    """
    workflow_registry = getattr(registry, "workflow_registry", None)
    getter = getattr(workflow_registry, "get", None)
    if not callable(getter):
        return True
    try:
        return getter(workflow_id) is not None
    except Exception:
        return True


def validate_rule_workflows(registry: DispatchRuleRegistry, logger=None) -> List[str]:
    """检查已注册规则引用的工作流是否真的存在，并对失效引用降级处理。

    背景：用户在 WebUI 里删除（tombstone）一个预设工作流后，指向它的调度规则
    仍然留在规则表里。`WorkflowDispatcher.dispatch` 匹配到该规则时
    `rule.get_workflow()` 返回 None，于是每一条私聊消息都抛
    `WorkflowNotFoundException`，用户看到的是"机器人坏了"而不是"模板被删了"。

    这里在启动阶段就发现这种情况：能退回 chat:normal 的就自动改指过去，
    否则把规则禁用（保留配置，用户随时可以在 WebUI 里改好再启用），
    两种情况都会打一条警告日志说明原因。

    :return: 被降级处理过的 rule_id 列表
    """
    log = logger or get_logger("DispatchRuleRegistry")
    # 兜底工作流：代码内置的 chat:normal 总是随包分发，最不容易缺失
    fallback_workflow_id = "chat:normal"
    fallback_available = _workflow_exists(registry, fallback_workflow_id)
    degraded: List[str] = []

    for rule in registry.get_all_rules():
        if _workflow_exists(registry, rule.workflow_id):
            continue

        missing_workflow_id = rule.workflow_id
        if fallback_available and missing_workflow_id != fallback_workflow_id:
            rule.workflow_id = fallback_workflow_id
            log.warning(
                f"Dispatch rule {rule.rule_id} references missing workflow "
                f"{missing_workflow_id}, falling back to {fallback_workflow_id}"
            )
        else:
            rule.enabled = False
            log.warning(
                f"Dispatch rule {rule.rule_id} references missing workflow "
                f"{missing_workflow_id} and no fallback workflow is available, "
                f"rule has been disabled"
            )
        degraded.append(rule.rule_id)

    return degraded

