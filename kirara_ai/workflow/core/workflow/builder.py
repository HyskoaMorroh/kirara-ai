import importlib
import random
import string
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from ruamel.yaml import YAML

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import Block, ConditionBlock, LoopBlock, LoopEndBlock
from kirara_ai.workflow.core.block.registry import BlockRegistry

from .base import Wire, Workflow


def get_block_class(type_name: str, registry: BlockRegistry) -> Type[Block]:
    if type_name.startswith("!!"):
        warnings.warn(
            f"Loading block using class path: {type_name[2:]}. This is not recommended.",
            UserWarning,
        )
        module_path, class_name = type_name[2:].rsplit(".", 1)
        module = importlib.import_module(module_path)
        return getattr(module, class_name)

    block_class = registry.get(type_name)
    if block_class is None:
        raise ValueError(f"Block type {type_name} not found in registry")
    return block_class

@dataclass
class BlockSpec:
    """Block 规格的数据类，用于统一处理 block 的创建参数"""

    block_class: Type[Block]
    name: Optional[str] = None
    kwargs: Dict[str, Any] = field(default_factory=dict)
    wire_from: Optional[Union[str, List[str]]] = None

    def __post_init__(self):
        if isinstance(self.wire_from, str):
            self.wire_from = [self.wire_from]


@dataclass
class Node:
    spec: BlockSpec
    name: str
    next_nodes: List["Node"] = field(default_factory=list)
    merge_point: Optional["Node"] = None
    parallel_nodes: List["Node"] = field(default_factory=list)
    is_parallel: bool = False
    condition: Optional[Callable] = None
    is_conditional: bool = False
    is_loop: bool = False
    parent: Optional["Node"] = None
    position: Optional[Dict[str, int]] = None
    def __init__(
        self,
        spec: BlockSpec,
        name: Optional[str] = None,
        next_nodes: Optional[List["Node"]] = None,
        merge_point: Optional["Node"] = None,
        parallel_nodes: Optional[List["Node"]] = None,
        is_parallel: bool = False,
        condition: Optional[Callable] = None,
        is_conditional: bool = False,
        is_loop: bool = False,
        parent: Optional["Node"] = None,
        position: Optional[Dict[str, int]] = None,
    ):
        self.spec = spec
        self.name = name or spec.name or f"{spec.block_class.__name__}_{id(self)}"
        self.next_nodes = next_nodes or []
        self.merge_point = merge_point
        self.parallel_nodes = parallel_nodes or []
        self.is_parallel = is_parallel
        self.condition = condition
        self.is_conditional = is_conditional
        self.is_loop = is_loop
        self.parent = parent
        self.position = position

    def ancestors(self) -> List["Node"]:
        """获取所有祖先节点"""
        result: List["Node"] = []
        current = self.parent
        while current:
            result.append(current)
            current = current.parent
        return result


class WorkflowBuilder:
    """工作流构建器，提供流畅的 DSL 语法来构建工作流。

    基本语法:
    1. 初始化:
        builder = WorkflowBuilder("workflow_name", container)

    2. 添加节点的方法:
        .use(BlockClass)                    # 添加初始节点
        .chain(BlockClass)                  # 链式添加节点
        .parallel([BlockClass1, BlockClass2]) # 并行添加多个节点

    3. 节点配置格式:
        - BlockClass                                    # 最简单形式
        - (BlockClass, name)                           # 指定名称
        - (BlockClass, wire_from)                     # 指定连接来源
        - (BlockClass, kwargs)                         # 指定参数
        - (BlockClass, name, kwargs)                   # 指定名称和参数
        - (BlockClass, name, wire_from)                # 指定名称和连接来源
        - (BlockClass, name, kwargs, wire_from)        # 指定名称、参数和连接来源

    4. 控制流:
        .if_then(condition)                 # 条件分支开始
        .else_then()                        # else 分支
        .end_if()                          # 条件分支结束
        .loop(condition)                    # 循环开始
        .end_loop()                         # 循环结束

    完整示例:
    ```python
    workflow = (WorkflowBuilder("example", container)
        # 基本用法
        .use(InputBlock)                    # 最简单形式
        .chain(ProcessBlock, name="process") # 指定名称
        .chain(TransformBlock,              # 指定参数
               kwargs={"param": "value"})

        # 并行处理
        .parallel([
            ProcessA,                       # 简单形式
            (ProcessB, "proc_b"),           # 指定名称
            (ProcessC, {"param": "val"}),   # 指定参数
            (ProcessD, "proc_d",            # 完整形式
             {"param": "val"},
             ["process"])                   # 指定连接来源
        ])

        # 条件分支
        .if_then(lambda ctx: ctx["value"] > 0)
            .chain(PositiveBlock)
        .else_then()
            .chain(NegativeBlock)
        .end_if()

        # 循环处理
        .loop(lambda ctx: ctx["count"] < 5)
            .chain(LoopBlock)
        .end_loop()

        # 多输入连接
        .chain(MergeBlock,
               wire_from=["proc_b", "proc_d"])

        .build())
    ```

    特性说明:
    1. 自动连接: 默认情况下，节点会自动与前一个节点连接
    2. 命名节点: 通过指定 name 可以后续引用该节点
    3. 参数传递: 可以通过 kwargs 字典传递构造参数
    4. 自定义连接: 通过 wire_from 指定输入来源
    5. 并行处理: parallel 方法支持多个节点并行执行
    6. 条件和循环: 支持基本的控制流结构

    注意事项:
    1. wire_from 引用的节点名称必须已经存在
    2. 条件和循环语句必须正确配对
    3. 并行节点可以各自指定不同的连接来源
    4. 节点名称在工作流中必须唯一
    """

    def __init__(self, name: str):
        self.id: Optional[str] = None
        self.name: str = name
        self.description: str = ""
        self.head: Optional[Node] = None
        self.current: Optional[Node] = None
        self.nodes: List[Node] = []  # 存储所有节点
        self.nodes_by_name: Dict[str, Node] = {}
        self.wire_specs: List[Tuple[str, str, str, str]] = []  # (source_name, source_output, target_name, target_input)

    def _generate_unique_name(self, base_name: str) -> str:
        """生成唯一的块名称"""
        while True:
            # 生成6位随机字符串（数字和字母的组合）
            suffix = "".join(
                random.choices(string.ascii_lowercase + string.digits, k=6)
            )
            name = f"{base_name}_{suffix}"
            if name not in self.nodes_by_name:
                return name

    def _parse_block_spec(self, block_spec: Union[Type[Block], tuple]) -> BlockSpec:
        """解析 block 规格，统一处理各种输入格式"""
        if isinstance(block_spec, type):
            return BlockSpec(block_spec)

        if not isinstance(block_spec, tuple):
            raise ValueError(f"Invalid block specification: {block_spec}")

        if len(block_spec) == 4:  # (BlockClass, name, kwargs, wire_from)
            return BlockSpec(*block_spec)
        elif len(block_spec) == 3:  # (BlockClass, name/kwargs, kwargs/wire_from)
            block_class, second, third = block_spec
            if isinstance(second, dict):
                return BlockSpec(block_class, kwargs=second, wire_from=third)
            return BlockSpec(block_class, name=second, kwargs=third)
        elif len(block_spec) == 2:  # (BlockClass, name/kwargs)
            block_class, second = block_spec
            if isinstance(second, dict):
                return BlockSpec(block_class, kwargs=second)
            return BlockSpec(block_class, name=second)

        raise ValueError(f"Invalid block specification format: {block_spec}")

    def _get_available_inputs(self, node: Node) -> List[str]:
        """获取节点未被连接的输入端口"""
        connected_inputs = {wire[3] for wire in self.wire_specs if wire[2] == node.name}
        return [input_name for input_name in node.spec.block_class.inputs.keys() 
                if input_name not in connected_inputs]

    def _find_matching_ports(
        self, 
        source_node: Node, 
        target_node: Node,
        available_inputs: List[str]
    ) -> List[Tuple[str, str]]:
        """查找匹配的输出和输入端口
        
        Returns:
            List of (output_name, input_name) pairs
        """
        matches: List[Tuple[str, str]] = []
        source_outputs = source_node.spec.block_class.outputs
        target_inputs = {name: target_node.spec.block_class.inputs[name] 
                        for name in available_inputs}

        for out_name, output in source_outputs.items():
            for in_name, input in target_inputs.items():
                if output.data_type == input.data_type:
                    matches.append((out_name, in_name))
                    # 一旦找到匹配就从可用输入中移除
                    target_inputs.pop(in_name)
                    break

        return matches

    def _store_wire_spec(
        self,
        source_name: str,
        target_name: str,
        source_node: Optional[Node] = None,
        target_node: Optional[Node] = None,
    ):
        """存储连接规格，自动匹配输入输出端口"""
        if source_node is None:
            source_node = self.nodes_by_name[source_name]
        if target_node is None:
            target_node = self.nodes_by_name[target_name]

        # 获取目标节点的可用输入端口
        available_inputs = self._get_available_inputs(target_node)
        if not available_inputs:
            return  # 如果没有可用的输入端口，直接返回

        # 查找匹配的端口
        matches = self._find_matching_ports(source_node, target_node, available_inputs)
        # 存储匹配的连接
        for source_output, target_input in matches:
            self.wire_specs.append((source_name, source_output, target_name, target_input))

    def _create_node(self, spec: BlockSpec, is_parallel: bool = False) -> Node:
        """创建一个新的节点，但不实例化 Block"""
        # 设置 block 名称
        if not spec.name:
            spec.name = self._generate_unique_name(spec.block_class.__name__)

        node = Node(spec=spec, is_parallel=is_parallel)
        self.nodes.append(node)
        self.nodes_by_name[node.name] = node

        # 处理连接
        if spec.wire_from:
            for source_name in spec.wire_from:
                source_node = self.nodes_by_name.get(source_name)
                if source_node:
                    self._store_wire_spec(source_node.name, node.name, source_node, node)
        elif self.current:
            self._store_wire_spec(self.current.name, node.name, self.current, node)

        return node

    def use(
        self, block_class: Type[Block], name: Optional[str] = None, **kwargs: Any
    ) -> "WorkflowBuilder":
        spec = BlockSpec(block_class, name=name, kwargs=kwargs)
        node = self._create_node(spec)
        self.head = node
        self.current = node
        return self

    def chain(
        self,
        block_class: Type[Block],
        name: Optional[str] = None,
        wire_from: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> "WorkflowBuilder":
        spec = BlockSpec(block_class, name=name, kwargs=kwargs, wire_from=wire_from)
        node = self._create_node(spec)
        if self.current:
            self.current.next_nodes.append(node)
            node.parent = self.current
        self.current = node
        return self

    def parallel(
        self, block_specs: List[Union[Type[Block], tuple]]
    ) -> "WorkflowBuilder":
        parallel_nodes: List[Node] = []

        for block_spec in block_specs:
            spec = self._parse_block_spec(block_spec)
            node = self._create_node(spec, is_parallel=True)
            node.parent = self.current
            parallel_nodes.append(node)

        if self.current:
            self.current.next_nodes.extend(parallel_nodes)
        self.current = parallel_nodes[0]
        self.current.parallel_nodes = parallel_nodes
        return self

    def condition(self, condition_func: Callable) -> "WorkflowBuilder":
        """添加条件判断"""
        assert self.current is not None
        self.current.condition = condition_func
        return self

    def if_then(
        self, condition: Callable[[Dict[str, Any]], bool], name: Optional[str] = None
    ) -> "WorkflowBuilder":
        """添加条件判断"""
        if not name:
            name = self._generate_unique_name("condition")
        
        spec = BlockSpec(
            block_class=ConditionBlock,
            name=name,
            kwargs={"condition": condition, "outputs": {}}  # outputs will be set during build
        )
        node = Node(spec=spec, is_conditional=True)
        self.nodes.append(node)
        self.nodes_by_name[node.name] = node

        if self.current:
            self._store_wire_spec(self.current.name, "output", self.current, node)
            self.current.next_nodes.append(node)
            node.parent = self.current
        self.current = node
        return self

    def else_then(self) -> "WorkflowBuilder":
        """添加else分支"""
        if not self.current or not self.current.is_conditional:
            raise ValueError("else_then must follow if_then")
        self.current = self.current.parent
        return self

    def end_if(self) -> "WorkflowBuilder":
        """结束条件分支"""
        if not self.current or not self.current.is_conditional:
            raise ValueError("end_if must close an if block")
        self.current = self.current.merge_point or self.current
        return self

    def loop(
        self,
        condition: Callable[[Dict[str, Any]], bool],
        name: Optional[str] = None,
        iteration_var: str = "index",
    ) -> "WorkflowBuilder":
        """开始一个循环"""
        if not name:
            name = self._generate_unique_name("loop")
        
        spec = BlockSpec(
            block_class=LoopBlock,
            name=name,
            kwargs={
                "condition": condition,
                "outputs": {},  # outputs will be set during build
                "iteration_var": iteration_var
            }
        )
        node = Node(spec=spec, is_loop=True)
        self.nodes.append(node)
        self.nodes_by_name[node.name] = node

        if self.current:
            self._store_wire_spec(self.current.name, "output", self.current, node)
            self.current.next_nodes.append(node)
            node.parent = self.current
        self.current = node
        return self

    def end_loop(self) -> "WorkflowBuilder":
        """结束循环"""
        if self.current is None:
            raise ValueError("end_loop must close a loop block")
        
        if not any(n.is_loop for n in self.current.ancestors()):
            raise ValueError("end_loop must close a loop block")

        spec = BlockSpec(
            block_class=LoopEndBlock,
            name=self._generate_unique_name("loop_end"),
            kwargs={"outputs": {}}  # outputs will be set during build
        )
        node = Node(spec=spec)
        self.nodes.append(node)
        self.nodes_by_name[node.name] = node

        if self.current:
            self._store_wire_spec(self.current.name, "output", self.current, node)
            loop_start = next(n for n in self.current.ancestors() if n.is_loop)
            self._store_wire_spec(node.name, "output", loop_start, node)
            node.parent = self.current
        self.current = node
        return self

    def build(self, container: DependencyContainer) -> Workflow:
        """构建工作流，在此阶段实例化所有 Block 并创建 Wire"""
        blocks: List[Block] = []
        wires: List[Wire] = []
        name_to_block: Dict[str, Block] = {}
        name_to_node: Dict[str, Node] = {}

        # 首先实例化所有 Block
        for node in self.nodes:
            try:
                # 如果是条件或循环块，需要从前一个块获取输出信息
                if node.is_conditional or node.is_loop:
                    prev_node = node.parent
                    if prev_node and prev_node.spec.block_class:
                        node.spec.kwargs["outputs"] = prev_node.spec.block_class.outputs.copy()
                
                block = node.spec.block_class(**node.spec.kwargs)
                if node.name:
                    block.name = node.name
                block.container = container
                blocks.append(block)
                name_to_block[node.name] = block
                name_to_node[node.name] = node
            except Exception as e:
                raise ValueError(f"Failed to create block {node.spec.block_class.__name__}: {e}")

        # 然后创建所有 Wire
        for source_name, source_output, target_name, target_input in self.wire_specs:
            source_block = name_to_block.get(source_name)
            target_block = name_to_block.get(target_name)
            if source_block and target_block:
                wires.append(Wire(source_block, source_output, target_block, target_input))

        return Workflow(name=self.name, blocks=blocks, wires=wires, id=self.id)

    def force_connect(
        self,
        source_name: str,
        target_name: str,
        source_output: str,
        target_input: str,
    ):
        """强制存储特定的连接规格"""
        self.wire_specs.append((source_name, source_output, target_name, target_input))

    def _find_parallel_nodes(self, start_node: Node) -> List[Node]:
        """查找所有并行节点"""
        parallel_nodes: List[Node] = []
        current = start_node
        while current:
            if current.is_parallel:
                parallel_nodes.extend(current.parallel_nodes)
            if current.next_nodes:
                current = current.next_nodes[0]
            else:
                break
        return parallel_nodes

    def update_position(self, name: str, position: Dict[str, int]):
        """更新节点的位置"""
        node = self.nodes_by_name[name]
        node.position = position

    def save_to_yaml(self, file_path: str, container: DependencyContainer):
        """将工作流保存为 YAML 格式"""
        registry: BlockRegistry = container.resolve(BlockRegistry)
        yaml = YAML()
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.width = 4096
        workflow_data: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "blocks": [],
        }

        def serialize_node(node: Node) -> dict:
            block_data: Dict[str, Any] = {
                "type": registry.get_block_type_name(node.spec.block_class),
                "name": node.name,
                "params": node.spec.kwargs,
                "position": node.position,
            }

            if node.is_parallel:
                block_data["parallel"] = True

            # 添加连接信息
            connected_to: List[Dict[str, Any]] = []
            for wire in self.wire_specs:
                if wire[0] == node.name:
                    # 使用 block.name 查找目标节点
                    target_node = next(
                        (
                            n
                            for n in self.nodes_by_name.values()
                            if n.name == wire[2]
                        ),
                        None,
                    )
                    if target_node:  # 只在找到目标节点时添加连接
                        connected_to.append(
                            {
                                "target": target_node.name,
                                "mapping": {
                                    "from": wire[1],
                                    "to": wire[3],
                                },
                            }
                        )
            if connected_to:
                block_data["connected_to"] = connected_to
            return block_data

        # 序列化所有节点
        for node in self.nodes_by_name.values():
            workflow_data["blocks"].append(serialize_node(node))

        # 保存到文件
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(workflow_data, f)

        return self

    @classmethod
    def load_from_yaml(
        cls, file_path: str, container: DependencyContainer
    ) -> "WorkflowBuilder":
        """从 YAML 文件加载工作流

        Args:
            file_path: YAML 文件路径
            container: 依赖注入容器

        Returns:
            WorkflowBuilder 实例
        """
        yaml = YAML(typ="safe")
        with open(file_path, "r", encoding="utf-8") as f:
            workflow_data: Dict[str, Any] = yaml.load(f)

        builder: WorkflowBuilder = cls(workflow_data["name"])
        builder.description = workflow_data.get("description", "")
        registry: BlockRegistry = container.resolve(BlockRegistry)

        # 第一遍：创建所有块
        for block_data in workflow_data["blocks"]:
            block_class = get_block_class(block_data["type"], registry)
            params = block_data.get("params", {})

            if block_data.get("parallel"):
                # 处理并行节点
                parallel_blocks = [(block_class, block_data["name"], params)]
                builder.parallel(parallel_blocks) # type: ignore
            else:
                # 处理普通节点
                if builder.head is None:
                    builder.use(block_class, name=block_data["name"], **params)
                else:
                    builder.chain(block_class, name=block_data["name"], **params)
            if block_data.get("position"):
                builder.update_position(block_data["name"], block_data["position"])

        # 第二遍：建立连接
        builder.wire_specs = []
        for block_data in workflow_data["blocks"]:
            if "connected_to" in block_data:
                source_node = builder.nodes_by_name[block_data["name"]]
                for connection in block_data["connected_to"]:
                    target_node = builder.nodes_by_name[connection["target"]]
                    builder.force_connect(
                        source_node.name,
                        target_node.name,
                        connection["mapping"]["from"],
                        connection["mapping"]["to"],
                    )

        return builder
