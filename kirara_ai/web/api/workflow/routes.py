import asyncio
import os
from typing import List

from quart import Blueprint, g, jsonify, request

from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.core.workflow.validation import validate_workflow_definition
from kirara_ai.workflow.presets.catalog import catalog_metadata

from ...auth.middleware import require_auth
from .models import (
    BlockInstance,
    Wire,
    WorkflowDefinition,
    WorkflowInfo,
    WorkflowList,
    WorkflowResponse,
    WorkflowValidationIssue,
    WorkflowValidationResponse,
)

workflow_bp = Blueprint("workflow", __name__)


@workflow_bp.route("/validate", methods=["POST"])
@require_auth
async def validate_workflow():
    """只诊断工作流草稿的结构，不保存或执行任何内容。"""
    workflow_def = WorkflowDefinition(**(await request.get_json()))
    block_registry: BlockRegistry = g.container.resolve(BlockRegistry)
    issues = validate_workflow_definition(
        workflow_def.blocks, workflow_def.wires, block_registry
    )
    response_issues = [WorkflowValidationIssue(**issue.__dict__) for issue in issues]
    return WorkflowValidationResponse(
        errors=[issue for issue in response_issues if issue.severity == "error"],
        warnings=[issue for issue in response_issues if issue.severity == "warning"],
    ).model_dump()


@workflow_bp.route("", methods=["GET"])
@require_auth
async def list_workflows():
    """获取所有工作流列表"""
    registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    workflows = []
    for workflow_id, builder in registry.snapshot_builders():
        # 从 workflow_id 解析 group_id
        group_id, wf_id = workflow_id.split(":", 1)

        metadata = dict(getattr(builder, "metadata", None) or {})
        preset_metadata = catalog_metadata(workflow_id)
        if preset_metadata is not None:
            metadata["catalog"] = preset_metadata
        workflows.append(
            WorkflowInfo(
                group_id=group_id,
                workflow_id=wf_id,
                name=builder.name,
                description=builder.description,
                block_count=len(builder.nodes_by_name),
                metadata=metadata or None,
            )
        )
    workflows.sort(key=lambda x: f"{x.group_id}:{x.workflow_id}")
    return WorkflowList(workflows=workflows).model_dump()


@workflow_bp.route("/<group_id>/<workflow_id>", methods=["GET"])
@require_auth
async def get_workflow(group_id: str, workflow_id: str):
    """获取特定工作流的详细信息"""
    registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)
    block_registry: BlockRegistry = g.container.resolve(BlockRegistry)
    full_id = f"{group_id}:{workflow_id}"
    builder = registry.get(full_id)
    if not builder:
        return jsonify({"error": "Workflow not found"}), 404

    assert isinstance(builder, WorkflowBuilder)

    # 构建工作流定义
    blocks: List[BlockInstance] = []
    for node in builder.nodes:
        blocks.append(
            BlockInstance(
                type_name=block_registry.get_block_type_name(node.spec.block_class),
                name=node.name,
                config=node.spec.kwargs,
                position=node.position,
            )
        )

    wires: List[Wire] = []
    for source_name, source_output, target_name, target_input in builder.wire_specs:
        wires.append(
            Wire(
                source_block=source_name,
                source_output=source_output,
                target_block=target_name,
                target_input=target_input,
            )
        )

    workflow_def = WorkflowDefinition(
        group_id=group_id,
        workflow_id=workflow_id,
        name=builder.name,
        description=builder.description,
        blocks=blocks,
        wires=wires,
        metadata=getattr(builder, "metadata", None),
        config=builder.config,
    )

    return WorkflowResponse(workflow=workflow_def).model_dump()


@workflow_bp.route("/<group_id>/<workflow_id>", methods=["POST"])
@require_auth
async def create_workflow(group_id: str, workflow_id: str):
    """创建新的工作流"""
    data = await request.get_json()
    workflow_def = WorkflowDefinition(**data)

    if workflow_def.group_id != group_id or workflow_def.workflow_id != workflow_id:
        return jsonify({"error": "Workflow ID in the URL must match the request body"}), 400

    registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)
    block_registry: BlockRegistry = g.container.resolve(BlockRegistry)

    # 检查工作流是否已存在
    full_id = f"{group_id}:{workflow_id}"
    if registry.get(full_id):
        return jsonify({"error": "Workflow already exists"}), 400
    file_path = registry.resolve_workflow_path(group_id, workflow_id)
    if os.path.exists(file_path):
        return jsonify({"error": "Workflow file already exists"}), 409

    # 创建工作流构建器
    try:
        # 创建工作流构建器
        builder = WorkflowBuilder(workflow_def.name)
        builder.description = workflow_def.description
        builder.metadata = workflow_def.metadata

        # 根据定义添加块和连接
        for block_def in workflow_def.blocks:
            block_class = block_registry.get(block_def.type_name)
            if not block_class:
                raise ValueError(f"Block type {block_def.type_name} not found")

            if not builder.head:
                builder.use(block_class, name=block_def.name, **block_def.config)
            else:
                builder.chain(block_class, name=block_def.name, **block_def.config)

            if block_def.position is not None:
                builder.update_position(block_def.name, block_def.position)
        
        # 不要用自动连线，用我们的
        builder.wire_specs = []
        # 添加连接
        for wire in workflow_def.wires:
            builder.force_connect(
                wire.source_block,
                wire.target_block,
                wire.source_output,
                wire.target_input
            )

        # 保存工作流
        builder.set_config(workflow_def.config)
        await asyncio.to_thread(
            registry.persist_builder,
            group_id,
            workflow_id,
            builder,
            create_only=True,
        )

        return workflow_def.model_dump()
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@workflow_bp.route("/<group_id>/<workflow_id>", methods=["PUT"])
@require_auth
async def update_workflow(group_id: str, workflow_id: str):
    """更新现有工作流"""
    data = await request.get_json()
    workflow_def = WorkflowDefinition(**data)

    registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)
    block_registry: BlockRegistry = g.container.resolve(BlockRegistry)

    # 检查工作流是否存在
    full_id = f"{group_id}:{workflow_id}"
    if not registry.get(full_id):
        return jsonify({"error": "Workflow not found"}), 404

    new_full_id = f"{workflow_def.group_id}:{workflow_def.workflow_id}"
    if new_full_id != full_id and registry.get(new_full_id):
        return jsonify({"error": "Workflow already exists"}), 409

    # The registry contains workflows successfully loaded at startup, but a
    # manually restored or invalid YAML can still occupy the target path
    # without an in-memory entry.  Never let a rename replace that file: it
    # may be the user's recoverable workflow source.
    new_file_path = registry.resolve_workflow_path(
        workflow_def.group_id, workflow_def.workflow_id
    )
    if new_full_id != full_id and os.path.exists(new_file_path):
        return jsonify({"error": "Workflow file already exists"}), 409

    # 更新工作流
    try:
        # 创建新的工作流构建器
        builder = WorkflowBuilder(workflow_def.name)
        builder.description = workflow_def.description
        builder.metadata = workflow_def.metadata

        # 根据定义添加块和连接
        for block_def in workflow_def.blocks:
            block_class = block_registry.get(block_def.type_name)
            if not block_class:
                raise ValueError(f"Block type {block_def.type_name} not found")

            if not builder.head:
                builder.use(block_class, name=block_def.name, **block_def.config)
            else:
                builder.chain(block_class, name=block_def.name, **block_def.config)

            if block_def.position is not None:
                builder.update_position(block_def.name, block_def.position)
        
        # 不要用自动连线，用我们的
        builder.wire_specs = []

        # 添加连接
        for wire in workflow_def.wires:
            builder.force_connect(
                wire.source_block,
                wire.target_block,
                wire.source_output,
                wire.target_input
            )

        # 保存工作流
        builder.set_config(workflow_def.config)
        await asyncio.to_thread(
            registry.persist_builder,
            workflow_def.group_id,
            workflow_def.workflow_id,
            builder,
            previous_group_id=group_id,
            previous_workflow_id=workflow_id,
        )

        return workflow_def.model_dump()
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@workflow_bp.route("/<group_id>/<workflow_id>", methods=["DELETE"])
@require_auth
async def delete_workflow(group_id: str, workflow_id: str):
    """删除工作流"""
    registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    # 检查工作流是否存在
    full_id = f"{group_id}:{workflow_id}"
    if not registry.get(full_id):
        return jsonify({"error": "Workflow not found"}), 404

    try:
        await asyncio.to_thread(
            registry.delete_persisted, group_id, workflow_id
        )

        return jsonify({"message": "Workflow deleted successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
