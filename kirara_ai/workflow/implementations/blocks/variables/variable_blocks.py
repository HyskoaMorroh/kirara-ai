from typing import Any, Dict, Optional, Type, TypeVar

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import Block
from kirara_ai.workflow.core.block.input_output import Input, Output
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor

T = TypeVar("T")


class SetVariableBlock(Block):
    """把值写入工作流变量

    注意：本块尚未注册到 BlockRegistry，构造函数需要注入 DependencyContainer，
    无法表达为 WebUI 的配置项，因此不会出现在节点列表中。
    """

    description = "把输入值写入工作流变量，供后续节点通过 {变量名} 占位符引用。"

    def __init__(self, container: DependencyContainer):
        inputs: Dict[str, Input] = {
            "name": Input("name", "变量名", str, "变量名"),
            "value": Input("value", "变量值", Any, "变量值"), # type: ignore
        }
        outputs: Dict[str, Output] = {}  # 这个 block 不需要输出
        super().__init__("set_variable", inputs, outputs)
        self.container = container

    def execute(self, name: str, value: Any) -> Dict[str, Any]:
        executor = self.container.resolve(WorkflowExecutor)
        executor.set_variable(name, value)
        return {}


class GetVariableBlock(Block):
    """读取工作流变量

    注意：本块尚未注册到 BlockRegistry，构造函数需要注入 DependencyContainer
    与目标类型，无法表达为 WebUI 的配置项，因此不会出现在节点列表中。
    """

    description = "按变量名读取工作流变量，变量不存在时使用默认值。"

    def __init__(self, container: DependencyContainer, var_type: Type[T]):
        inputs = {
            "name": Input("name", "变量名", str, "变量名"),
            "default": Input("default", "默认值", var_type, "默认值", nullable=True),
        }
        outputs = {"value": Output("value", "变量值", var_type, "变量值")}
        super().__init__("get_variable", inputs, outputs)
        self.container = container
        self.var_type = var_type

    def execute(self, name: str, default: Optional[T] = None) -> Dict[str, T]:
        executor = self.container.resolve(WorkflowExecutor)
        value = executor.get_variable(name, default)

        # 类型检查
        if value is not None and not isinstance(value, self.var_type):
            raise TypeError(
                f"Variable '{name}' must be of type {self.var_type}, got {type(value)}"
            )

        return {"value": value} # type: ignore
