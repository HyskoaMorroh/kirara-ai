from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from kirara_ai.workflow.core.workflow.base import WorkflowConfig


class Wire(BaseModel):
    """工作流连线"""

    source_block: str  # block ID
    source_output: str
    target_block: str  # block ID
    target_input: str


class BlockInstance(BaseModel):
    """工作流中的Block实例"""

    type_name: str
    name: str
    config: Dict[str, Any]
    # None represents a node that has never been laid out. {"x": 0, "y": 0}
    # remains a valid, user-chosen position.
    position: Optional[Dict[str, int]] = None  # x, y 坐标


class WorkflowDefinition(BaseModel):
    """工作流定义"""

    group_id: str
    workflow_id: str
    name: str
    description: str
    blocks: List[BlockInstance]
    wires: List[Wire]
    config: WorkflowConfig = WorkflowConfig()
    metadata: Optional[Dict[str, Any]] = None


class WorkflowInfo(BaseModel):
    """工作流基本信息"""

    group_id: str
    workflow_id: str
    name: str
    description: str
    block_count: int
    metadata: Optional[Dict[str, Any]] = None


class WorkflowList(BaseModel):
    """工作流列表响应"""

    workflows: List[WorkflowInfo]


class WorkflowResponse(BaseModel):
    """单个工作流响应"""

    workflow: WorkflowDefinition


class WorkflowValidationIssue(BaseModel):
    """工作流草稿的一项静态诊断。"""

    severity: Literal["error", "warning"]
    code: str
    message: str
    node_name: Optional[str] = None
    port_name: Optional[str] = None


class WorkflowValidationResponse(BaseModel):
    """预检结果；不会改变工作流、文件或注册表。"""

    errors: List[WorkflowValidationIssue]
    warnings: List[WorkflowValidationIssue]
