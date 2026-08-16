"""无副作用的工作流结构预检。

这里刻意不构建或执行 Block，也不改写 ``WorkflowBuilder``：编辑器可以在用户
分阶段搭图时正常保存草稿，而管理界面与 API 能在运行前展示真实会出错的结构。
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional

from kirara_ai.workflow.core.block import LoopBlock, LoopEndBlock
from kirara_ai.workflow.core.block.registry import BlockRegistry

# 环检测的递归深度上限。超大工作流（或恶意导入的深链）会让递归版 DFS
# 撞上 Python 的栈上限而抛 RecursionError，预检本身反而成了故障点。
# 这里改成显式栈的迭代实现，该常量只作为最终的安全阀。
MAX_CYCLE_SCAN_DEPTH = 10000


def _dynamic_port_names(block: Any, kind: Literal["inputs", "outputs"]) -> set[str]:
    """读取节点自身声明的动态端口。

    「基础：代码」这类节点的端口由用户在 params 里填写（inputs / outputs 两个
    列表），类上的 inputs/outputs 永远是空字典。若只看类定义，这些节点的每条
    连线都会被误报成「端口不存在」。
    """
    config = getattr(block, "config", None)
    if config is None:
        config = getattr(block, "params", None)
    if not isinstance(config, dict):
        return set()
    declared = config.get(kind)
    if not isinstance(declared, list):
        return set()
    names: set[str] = set()
    for port in declared:
        if isinstance(port, dict) and isinstance(port.get("name"), str):
            names.add(port["name"])
    return names



@dataclass(frozen=True)
class WorkflowValidationIssue:
    """一个可定位、可展示，但不会改变工作流的诊断结果。"""

    severity: Literal["error", "warning"]
    code: str
    message: str
    node_name: Optional[str] = None
    port_name: Optional[str] = None


def _issue(
    issues: list[WorkflowValidationIssue],
    severity: Literal["error", "warning"],
    code: str,
    message: str,
    node_name: Optional[str] = None,
    port_name: Optional[str] = None,
) -> None:
    issues.append(
        WorkflowValidationIssue(
            severity=severity,
            code=code,
            message=message,
            node_name=node_name,
            port_name=port_name,
        )
    )


def validate_workflow_definition(
    blocks: Iterable[Any], wires: Iterable[Any], block_registry: BlockRegistry
) -> list[WorkflowValidationIssue]:
    """校验可序列化的工作流草稿，不实例化 Block，不写文件。

    ``blocks`` 与 ``wires`` 只需要提供 Web API 模型同名字段。这让核心诊断器不依赖
    Web 层的 Pydantic 模型，也可被导入器、CLI 或将来的运行前检查复用。
    """
    issues: list[WorkflowValidationIssue] = []
    block_items = list(blocks)
    wire_items = list(wires)
    name_counts = Counter(block.name for block in block_items)
    blocks_by_name: dict[str, Any] = {}
    block_classes: dict[str, Any] = {}

    for block in block_items:
        if name_counts[block.name] > 1:
            _issue(
                issues,
                "error",
                "duplicate_node_name",
                f"节点名称「{block.name}」重复；每个节点必须唯一",
                block.name,
            )
        blocks_by_name.setdefault(block.name, block)
        block_class = block_registry.get(block.type_name)
        if block_class is None:
            _issue(
                issues,
                "error",
                "unknown_block_type",
                f"节点「{block.name}」使用了未安装或未知的区块类型「{block.type_name}」",
                block.name,
            )
        else:
            block_classes.setdefault(block.name, block_class)

    incoming_valid_ports: set[tuple[str, str]] = set()
    incoming_nodes: dict[str, int] = defaultdict(int)
    adjacency: dict[str, set[str]] = defaultdict(set)
    connected_inputs: set[tuple[str, str]] = set()

    for wire in wire_items:
        source_name = wire.source_block
        target_name = wire.target_block
        source_block = blocks_by_name.get(source_name)
        target_block = blocks_by_name.get(target_name)
        source_class = block_classes.get(source_name)
        target_class = block_classes.get(target_name)

        if source_block is None:
            _issue(
                issues,
                "error",
                "unknown_source_node",
                f"连线来源节点「{source_name}」不存在",
                source_name,
                wire.source_output,
            )
        if target_block is None:
            _issue(
                issues,
                "error",
                "unknown_target_node",
                f"连线目标节点「{target_name}」不存在",
                target_name,
                wire.target_input,
            )
        if source_block is None or target_block is None or source_class is None or target_class is None:
            continue

        source_outputs = getattr(source_class, "outputs", {})
        target_inputs = getattr(target_class, "inputs", {})
        # 代码节点等动态端口节点的端口写在 params 里，需要一并认作合法端口
        source_dynamic_outputs = _dynamic_port_names(source_block, "outputs")
        target_dynamic_inputs = _dynamic_port_names(target_block, "inputs")
        if wire.source_output not in source_outputs and wire.source_output not in source_dynamic_outputs:
            _issue(
                issues,
                "error",
                "unknown_output_port",
                f"节点「{source_name}」没有输出端口「{wire.source_output}」",
                source_name,
                wire.source_output,
            )
        if wire.target_input not in target_inputs and wire.target_input not in target_dynamic_inputs:
            _issue(
                issues,
                "error",
                "unknown_input_port",
                f"节点「{target_name}」没有输入端口「{wire.target_input}」",
                target_name,
                wire.target_input,
            )
        source_port_known = (
            wire.source_output in source_outputs or wire.source_output in source_dynamic_outputs
        )
        target_port_known = (
            wire.target_input in target_inputs or wire.target_input in target_dynamic_inputs
        )
        if not source_port_known or not target_port_known:
            continue

        input_key = (target_name, wire.target_input)
        if input_key in connected_inputs:
            _issue(
                issues,
                "error",
                "multiple_wires_for_input",
                f"输入端口「{target_name}.{wire.target_input}」有多条连线；运行时只会保留其中一条",
                target_name,
                wire.target_input,
            )
        connected_inputs.add(input_key)

        # 动态端口在运行期才确定类型（一律为 Any），跳过类型比较，只记录连通性
        if wire.source_output in source_outputs and wire.target_input in target_inputs:
            source_type = block_registry.get_type_name(
                source_outputs[wire.source_output].data_type
            )
            target_type = block_registry.get_type_name(
                target_inputs[wire.target_input].data_type
            )
            if not block_registry.is_type_compatible(source_type, target_type):
                _issue(
                    issues,
                    "error",
                    "incompatible_wire_type",
                    f"连线「{source_name}.{wire.source_output} → {target_name}.{wire.target_input}」类型不兼容（{source_type} → {target_type}）",
                    target_name,
                    wire.target_input,
                )
                continue

        incoming_valid_ports.add(input_key)
        incoming_nodes[target_name] += 1
        adjacency[source_name].add(target_name)

    for node_name, block_class in block_classes.items():
        for input_name, input_info in getattr(block_class, "inputs", {}).items():
            if not input_info.nullable and (node_name, input_name) not in incoming_valid_ports:
                _issue(
                    issues,
                    "error",
                    "missing_required_input",
                    f"节点「{node_name}」的必需输入「{input_name}」未连接",
                    node_name,
                    input_name,
                )
        # 代码节点等动态端口节点：params 里声明的输入同样必须接线，否则运行期
        # 会因缺少关键字参数直接失败
        dynamic_block = blocks_by_name.get(node_name)
        if dynamic_block is not None:
            for input_name in sorted(_dynamic_port_names(dynamic_block, "inputs")):
                if (node_name, input_name) not in incoming_valid_ports:
                    _issue(
                        issues,
                        "error",
                        "missing_required_input",
                        f"节点「{node_name}」的必需输入「{input_name}」未连接",
                        node_name,
                        input_name,
                    )

    # 入口与执行器一致：入口 Block 没有声明输入，而且没有前置连线。
    entry_names = [
        name
        for name, block_class in block_classes.items()
        if not getattr(block_class, "inputs", {})
        and not _dynamic_port_names(blocks_by_name.get(name), "inputs")
        and incoming_nodes[name] == 0
    ]
    if block_classes and not entry_names:
        _issue(
            issues,
            "error",
            "no_entry_node",
            "没有可执行的入口节点：入口必须没有输入端口，也不能有前置连线",
        )

    reachable: set[str] = set(entry_names)
    pending = list(entry_names)
    while pending:
        source_name = pending.pop()
        for target_name in adjacency[source_name]:
            if target_name not in reachable:
                reachable.add(target_name)
                pending.append(target_name)
    for node_name in block_classes:
        if entry_names and node_name not in reachable:
            _issue(
                issues,
                "warning",
                "unreachable_node",
                f"节点「{node_name}」不在任何入口节点的可达路径上，运行时不会执行",
                node_name,
            )

    visited: set[str] = set()
    active_path: list[str] = []
    active_names: set[str] = set()
    reported_cycles: set[tuple[str, ...]] = set()

    def _report_cycle(cycle: list[str], target_name: str) -> None:
        """记录一个环，重复出现的同一个环只报一次。"""
        cycle_key = tuple(sorted(cycle))
        if cycle_key in reported_cycles:
            return
        reported_cycles.add(cycle_key)
        controlled = any(
            issubclass(block_classes[name], (LoopBlock, LoopEndBlock)) for name in cycle
        )
        _issue(
            issues,
            "warning" if controlled else "error",
            "controlled_cycle" if controlled else "unsafe_cycle",
            (
                f"检测到包含循环控制块的环：{' → '.join(cycle)}"
                if controlled
                else f"检测到未受循环控制的环：{' → '.join(cycle)}"
            ),
            target_name,
        )

    def visit(node_name: str) -> None:
        """迭代式 DFS 找环。

        原实现是递归的：节点数一多（导入的工作流可以有几千个节点）就会
        RecursionError，让预检自己先崩掉。这里用显式栈保存「待访问的后继
        迭代器」，深度不再受 Python 递归上限约束，同时保留 MAX_CYCLE_SCAN_DEPTH
        作为极端情况下的安全阀。
        """
        stack: list[tuple[str, Any]] = [(node_name, iter(sorted(adjacency[node_name])))]
        visited.add(node_name)
        active_path.append(node_name)
        active_names.add(node_name)
        while stack:
            current_name, successors = stack[-1]
            next_name = next(successors, None)
            if next_name is None:
                stack.pop()
                active_names.discard(current_name)
                if active_path:
                    active_path.pop()
                continue
            if next_name in active_names:
                _report_cycle(active_path[active_path.index(next_name):], next_name)
                continue
            if next_name in visited:
                continue
            if len(stack) >= MAX_CYCLE_SCAN_DEPTH:
                _issue(
                    issues,
                    "warning",
                    "cycle_scan_depth_exceeded",
                    f"工作流层级超过 {MAX_CYCLE_SCAN_DEPTH} 层，环检测已提前停止",
                    next_name,
                )
                continue
            visited.add(next_name)
            active_path.append(next_name)
            active_names.add(next_name)
            stack.append((next_name, iter(sorted(adjacency[next_name]))))

    for node_name in block_classes:
        if node_name not in visited:
            visit(node_name)

    return issues
