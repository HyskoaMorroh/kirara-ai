from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from kirara_ai.workflow.core.dispatch import CombinedDispatchRule, DispatchRuleReachability


class DispatchRuleList(BaseModel):
    """调度规则列表"""

    rules: List[CombinedDispatchRule]
    #: 每条规则的匹配次序与遮蔽状态。WebUI 不再自行推导这套语义，直接读取本字段，
    #: 保证界面与调度器对“哪条规则永远不会被触发”的判断永远一致。
    reachability: List[DispatchRuleReachability] = []


class DispatchRuleResponse(BaseModel):
    """调度规则响应"""

    rule: CombinedDispatchRule


class DispatchReachabilityRequest(BaseModel):
    """只做静态可达性分析的请求：不需要示例消息，也不会保存任何规则。"""

    #: 正在编辑的草稿规则。若其 rule_id 已存在则替换同 id 的现有规则，
    #: 否则作为新规则参与排序，用于在保存前预判遮蔽关系。
    draft_rule: Optional[CombinedDispatchRule] = None


class DispatchReachabilityResponse(BaseModel):
    """按真实调度顺序给出的可达性分析结果。"""

    reachability: List[DispatchRuleReachability]


class DispatchPreviewRequest(BaseModel):
    """不执行工作流的触发规则试运行请求。"""

    content: str = Field(default="", max_length=2000)
    chat_type: Literal["私聊", "群聊"] = "私聊"
    sender_id: str = Field(default="preview-user", min_length=1, max_length=200)
    group_id: Optional[str] = Field(default=None, max_length=200)
    mentioned: bool = False
    draft_rule: Optional[CombinedDispatchRule] = None


class DispatchPreviewRuleResult(BaseModel):
    """一条已排序规则的试运行结果。"""

    rule_id: str
    name: str
    workflow_id: str
    priority: int
    enabled: bool
    matched: Optional[bool]
    decision: Literal["selected", "shadowed", "not_matched", "indeterminate", "disabled"]
    explanation: Dict[str, Any]
    #: 从 1 开始的匹配次序，与调度器判断顺序一致。
    #: 只统计已启用的规则；已禁用规则不参与匹配，因此为 None。
    order: Optional[int] = None
    #: 该规则本身是否为无条件规则（会拦下所有消息）。
    catch_all: bool = False
    #: 与 decision 无关的静态结论：该规则排在某条已启用的无条件规则之后，
    #: 对**任何**消息都不会被判断到。decision=="shadowed" 只针对当前示例消息。
    unreachable: bool = False
    #: 造成静态不可达的那条无条件规则 ID。
    shadowed_by_rule_id: Optional[str] = None


class DispatchPreviewResponse(BaseModel):
    """完整的规则试运行结果，不包含任何执行副作用。"""

    selected_rule_id: Optional[str] = None
    selected_workflow_id: Optional[str] = None
    rules: List[DispatchPreviewRuleResult]
