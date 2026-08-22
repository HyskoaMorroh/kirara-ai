# 扩展开发指南

本文讲怎么给 Kirara AI 加东西：自定义区块（Block）、插件、MCP 服务器、工作流预设、调度规则、事件监听、定时任务。

每节末尾都有一个**验证步骤**——一条能跑的命令或一个明确的界面操作。文中所有 API 路径都要加统一前缀 `/backend-api/api`。

先说清楚整体结构，后面各节都建立在这个基础上：

```
kirara_ai/
├── plugin_manager/          插件基类、加载器、插件事件总线
├── events/                  事件类型与 EventBus
├── mcp_module/              MCP 服务器管理（注意目录名不是 mcp）
├── scheduler/               定时任务（当前只有模型目录自动检测）
├── workflow/
│   ├── core/
│   │   ├── block/           Block 基类、注册表、ParamMeta、类型系统
│   │   ├── workflow/        WorkflowBuilder、注册表、结构预检
│   │   ├── dispatch/        调度器、规则注册表、各类规则
│   │   └── execution/       WorkflowExecutor
│   ├── implementations/     内置区块、工作流工厂、默认规则
│   └── presets/chat/        随包分发的 11 个工作流模板 YAML
├── plugins/                 内置插件（IM 适配器、LLM 适配器、frpc）
└── web/api/                 各模块的 REST 接口
```

运行期一切都挂在依赖注入容器 `DependencyContainer` 上（`kirara_ai/ioc/container.py`）：`register(键, 值)` 注册，`resolve(键)` 取出，键通常就是类型本身。容器支持作用域嵌套——调度器每处理一条消息就开一个 `scoped()` 子容器，把本次的 `IMAdapter`、`IMMessage`、命中的 `DispatchRule`、`Workflow`、`WorkflowExecutor` 注册进去，处理完自动失效。区块能在 `execute()` 里通过 `self.container.resolve(...)` 拿到这些上下文，就是因为这个机制。

---

## 一、编写自定义 Block

这是最常用也最值得先掌握的扩展点。一个 Block 就是画布上的一个节点。

### 1.1 基类与四个约定

基类是 `kirara_ai/workflow/core/block/base.py` 的 `Block`：

```python
class Block:
    """block 的基类"""
    id: str                              # 注册时由 BlockRegistry 写入，不用自己填
    name: str                            # 内部名称
    description: str = ""                # 展示在 WebUI 节点列表与配置面板
    color: str = ""                      # 画布上标题栏颜色，留空则按分组默认色
    inputs: Dict[str, Input] = {}         # 输入端口
    outputs: Dict[str, Output] = {}       # 输出端口
    container: DependencyContainer        # 由 WorkflowBuilder.build() 注入

    def execute(self, **kwargs) -> Dict[str, Any]: ...
```

四条硬约定，违反任何一条都会在运行期或预检时报错：

1. **`execute()` 的形参名必须与 `inputs` 的键一一对应。** 执行器用 `block.execute(**inputs)` 调用，多一个少一个都会 `TypeError`。
2. **`execute()` 的返回值必须是字典，键必须覆盖 `outputs` 的键。** 下游节点靠 `results[源节点名][源输出名]` 取值（`WorkflowExecutor._gather_inputs`），漏掉一个键就等于下游永远等不到输入。
3. **`__init__` 的参数就是节点的配置项。** `BlockRegistry.extract_block_info()` 用 `inspect.signature(block_type.__init__)` 反射出配置表，WebUI 据此渲染配置面板。所以不要在 `__init__` 里放非配置用途的参数。
4. **入口节点必须没有输入端口。** 执行器以 `[block for block in workflow.blocks if not block.inputs]` 作为起点（`executor.py`）；没有这样的节点，整张图跑不起来（预检会报 `no_entry_node`）。

`Input` / `Output` 定义在 `kirara_ai/workflow/core/block/input_output.py`：

```python
Input(name, label, data_type, description, nullable=False, default=None)
Output(name, label, data_type, description)
```

`label` 是画布上端口旁显示的文字，`data_type` 参与连线类型兼容性检查（`TypeSystem.is_compatible`，按 `issubclass` 判断，`Any` 与任何类型互通）。`nullable=True` 的输入允许不接线——预检的 `missing_required_input` 只针对 `nullable=False` 的端口。

同一文件里还有三个内置控制流子类可以当参考：`ConditionBlock`（`name = "condition"`，输出 `condition_result: bool`）、`LoopBlock`（`name = "loop"`，输出 `should_continue` 与 `iteration`）、`LoopEndBlock`（`name = "loop_end"`，输出 `loop_results: list`）。执行器对这三类有专门分支（`_execute_conditional_branch` / `_execute_loop`）。

### 1.2 让配置项在 WebUI 里正确渲染：`ParamMeta`

光有 `__init__` 参数，WebUI 只能显示参数名和类型。要显示中文标签、说明文字、下拉候选项，必须用 `Annotated[类型, ParamMeta(...)]`。

`ParamMeta` 在 `kirara_ai/workflow/core/block/param.py:9`：

```python
class ParamMeta:
    def __init__(self, label=None, description=None, options_provider=None): ...
```

| 字段 | 效果 |
| --- | --- |
| `label` | 配置面板上的字段名。不填就直接用参数名，界面上会出现 `max_iterations` 这种生硬的英文 |
| `description` | 字段下方的提示文字 |
| `options_provider` | **填了就渲染成下拉框**，不填是自由输入框 |

`options_provider` 的签名是 `Callable[[DependencyContainer, Block], List[T]]`，在 `GET /block/types` 被调用时求值：

```python
# kirara_ai/web/api/block/routes.py
for config in configs.values():
    if config.has_options:
        config.options = config.options_provider(g.container, block_type)
```

这就是「模型必须手动从下拉框里选」的机制来源——`ChatCompletion` 的 `model_name` 用的是 `model_name_options_provider`（`kirara_ai/workflow/implementations/blocks/llm/chat.py:24`），它在请求到来时才去 `LLMManager` 查一次当前可用模型：

```python
def model_name_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    llm_manager: LLMManager = container.resolve(LLMManager)
    return sorted(llm_manager.get_supported_models(ModelType.LLM, LLMAbility.TextChat))
```

因为候选项是**每次请求实时求值**的，模型目录刷新后不用重启就能在下拉框里看到新模型；也因为它只是候选项、不写入工作流，所以选择这一步必须由人来做。

最简单的固定候选写法是个 lambda，`TextStripMarkdownBlock` 就这么用：

```python
table_style: Annotated[
    str,
    ParamMeta(
        label="表格样式",
        description="box：渲染为等宽框线表格（推荐）；plain：转为空格分隔的纯文本",
        options_provider=lambda container, block: ["box", "plain"],
    ),
] = "box"
```

参数的**默认值决定这个配置是否必填**：`extract_type_info()` 把 `Optional[X]`（即 `Union[X, None]`）识别为 `required=False`，其余为 `required=True`；`default` 直接取签名里的默认值。

### 1.3 完整示例：一个问候 Block

下面这个例子在真实容器里跑通过，可以直接复制。放在 `data/plugins/demo_plugin/blocks.py`（目录布局见第二节）：

```python
from typing import Annotated, Any, Dict, Optional

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta


def greeting_style_options(container: DependencyContainer, block: Block):
    """下拉框候选项。签名固定为 (container, block)。"""
    return ["polite", "casual"]


class GreetingBlock(Block):
    """给消息发送者拼一句问候语。"""

    name = "greeting"
    description = "读取聊天消息，输出一句带发送者昵称的问候语消息。"
    color = "#4a9d7f"

    inputs = {
        "msg": Input("msg", "IM 消息", IMMessage, "触发本次执行的聊天消息"),
    }
    outputs = {
        "reply": Output("reply", "问候消息", IMMessage, "可直接接到「IM: 发送消息」"),
    }

    container: DependencyContainer

    def __init__(
        self,
        template: Annotated[
            str, ParamMeta(label="问候模板", description="可使用 {name} 占位符")
        ] = "你好，{name}！",
        style: Annotated[
            str,
            ParamMeta(
                label="语气",
                description="polite 更正式，casual 更随意",
                options_provider=greeting_style_options,
            ),
        ] = "polite",
        suffix: Annotated[
            Optional[str], ParamMeta(label="附加后缀", description="留空则不追加")
        ] = None,
    ):
        self.template = template
        self.style = style
        self.suffix = suffix

    def execute(self, msg: IMMessage) -> Dict[str, Any]:
        name = msg.sender.display_name or msg.sender.user_id
        text = self.template.format(name=name)
        if self.style == "casual":
            text = text.replace("您", "你")
        if self.suffix:
            text = f"{text}{self.suffix}"
        return {
            "reply": IMMessage(
                sender=ChatSender.get_bot_sender(),
                message_elements=[TextMessage(text)],
            )
        }
```

对照四条约定看：`execute` 的形参 `msg` 与 `inputs` 的键一致；返回字典的键 `reply` 与 `outputs` 一致；三个 `__init__` 参数会变成三个配置项（`template` 文本框、`style` 下拉框、`suffix` 可空文本框）；它有输入端口，所以不是入口节点，需要接在「IM: 获取最新消息」后面。

### 1.4 注册

Block 必须注册进 `BlockRegistry` 才会出现在节点面板里。签名在 `kirara_ai/workflow/core/block/registry.py:85`：

```python
registry.register(
    block_id,          # 组内唯一 ID，如 "greeting"
    group_id,          # 分组，如 "demo"；internal 表示框架内置
    block_class,
    localized_name=None,   # 节点面板与画布上显示的中文名
    color=None,            # 标题栏颜色
)
```

完整类型名是 `f"{group_id}:{block_id}"`，这就是预设 YAML 里 `type` 字段的值，也是 `GET /block/types/<type_name>` 的路径参数。**重复注册同名会直接抛 `ValueError`**。

颜色的解析顺序（`registry.py:109`）：`register()` 的 `color` 参数 → 类属性 `color` → 分组默认色 `DEFAULT_GROUP_COLORS`（`internal` `#5b8dff` / `system` `#8b7cd8` / `game` `#e0954a` / `mcp` `#3fa89a`）→ 空字符串（交给前端按主题决定）。

说明文字的解析顺序（`get_description`）：类属性 `description` → 类 docstring 的首个非空段落 → 空字符串。所以哪怕忘了写 `description`，只要有 docstring 也不会是空的。

内置区块的注册集中在 `kirara_ai/workflow/implementations/blocks/system_blocks.py` 的 `register_system_blocks()`，那里能看到全部 28 个内置区块的注册写法和按功能域分配的配色（文本 `#6b7a99`、IM `#4a9d7f`、LLM `#5b8dff`、记忆 `#8b7cd8`、画图 `#c86fa8`、游戏 `#e0954a`、系统 `#7d8794`、MCP `#3fa89a`）。自定义区块建议用自己的 `group_id`，不要往 `internal` 里塞。

### 1.5 节点面板里的位置

`webui/src/components/workflow/NodeListPanel.vue` 按 `type_name.split(':')[0]` 分组，分组顺序是 `['internal', 'system', 'mcp', 'game']`，其余分组排在后面按字母序。中文组名映射写在 `getGroupDisplayName`：`internal` → 内部组件、`system` → 系统组件、`mcp` → MCP组件、`game` → 娱乐组件。**不在这张表里的自定义分组，面板上直接显示 `group_id` 原文**（比如 `demo`），这是当前实现的限制。

`internal` 组节点多于 8 个时会按中文 label 前缀再拆一层二级分组（基础 / IM / 记忆 / LLM / 画图），自定义分组不参与这个细分。

### 1.6 验证

写完一个 Block，用这三步确认它真的能用：

```bash
# 1. 确认注册成功、端口与配置项被正确反射出来
.venv-win/Scripts/python.exe -c "
from kirara_ai.workflow.core.block.registry import BlockRegistry
import sys; sys.path.insert(0, 'data/plugins')
from demo_plugin.blocks import GreetingBlock
r = BlockRegistry()
r.register('greeting', 'demo', GreetingBlock, '示例：问候', '#4a9d7f')
inputs, outputs, configs = r.extract_block_info(GreetingBlock)
print('type_name =', r.get_block_type_name(GreetingBlock))
print('label     =', r.get_localized_name('demo:greeting'))
print('color     =', r.get_color('demo:greeting', GreetingBlock))
print('inputs    =', {k: v.type for k, v in inputs.items()})
print('outputs   =', {k: v.type for k, v in outputs.items()})
for k, v in configs.items():
    print(f'config    = {k} label={v.label} required={v.required} has_options={v.has_options}')
"
```

预期输出 `type_name = demo:greeting`、`inputs = {'msg': 'IMMessage'}`、`outputs = {'reply': 'IMMessage'}`，以及三行 config 且 `style` 的 `has_options=True`。

```bash
# 2. 启动后确认接口能返回它
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/block/types/demo:greeting
```

3. 打开「工作流」编辑器，左侧节点面板应出现 `demo` 分组和「示例：问候」，拖到画布上后配置面板里应看到「问候模板」「语气」「附加后缀」三项，其中「语气」是下拉框。

---

## 二、编写插件

插件是把自定义区块、工作流、调度规则、IM/LLM 适配器、Web 路由挂进系统的载体。

### 2.1 基类与生命周期

基类是 `kirara_ai/plugin_manager/plugin.py:10` 的 `Plugin(ABC)`，三个方法**都是 `@abstractmethod`，必须全部实现**（哪怕是空实现）：

```python
class Plugin(ABC):
    ENTRY_POINT_GROUP = "chatgpt_mirai.plugins"

    event_bus: EventBus
    workflow_dispatcher: WorkflowDispatcher
    llm_registry: LLMBackendRegistry
    im_registry: IMRegistry
    im_manager: IMManager

    @abstractmethod
    def on_load(self): ...
    @abstractmethod
    def on_start(self): ...
    @abstractmethod
    def on_stop(self): ...
```

生命周期的调用时机（`kirara_ai/entry.py`）：

| 阶段 | 谁调用 | 时机 | 适合做什么 |
| --- | --- | --- | --- |
| 实例化 | `PluginLoader.instantiate_plugin` | 发现插件时 | `__init__` 里只做纯内存初始化，此时依赖注入还没完成 |
| `on_load()` | `load_plugins()` | Web 服务启动**之前** | 注册区块、工作流、规则、IM/LLM 适配器；注册事件监听 |
| `on_start()` | `start_plugins()` | Web 服务启动**之后**、IM 适配器启动之前 | 挂 Web 蓝图、启动后台任务、连接外部服务 |
| `on_stop()` | `stop_plugins()` | 关停流程末尾 | 停后台任务、反注册、清理资源 |

每个阶段都会往 `EventBus` 发事件：`PluginLoaded` / `PluginStarted` / `PluginStopped`（`kirara_ai/events/plugin.py`）。任一阶段抛异常都只会被记进日志、不会中断其他插件的加载。

**类属性即依赖注入**。`instantiate_plugin()` 用 `Inject(scoped_container).create(plugin_class)()` 创建实例，`Inject.inject_class` 遍历类的 `__annotations__`（含父类），把每个能在容器里 `resolve` 到的类型变成 property。所以想用什么，就在类上加一行类型标注：

```python
class MyPlugin(Plugin):
    block_registry: BlockRegistry               # 注册区块
    workflow_registry: WorkflowRegistry         # 注册工作流
    dispatch_registry: DispatchRuleRegistry     # 注册调度规则
    web_server: WebServer                       # 挂路由/静态资源
    global_config: GlobalConfig                 # 读配置
    loop: asyncio.AbstractEventLoop             # 起后台任务
```

`event_bus` 是个特例：`instantiate_plugin` 在作用域容器里用 `PluginEventBus` **替换**了全局 `EventBus`，所以插件拿到的是包装过的版本，它会记录你注册的所有监听器，`stop_plugins()`/`disable_plugin()` 时自动 `unregister_all()`。这意味着**不用手写反注册逻辑**。

### 2.2 目录布局：本地内部插件

想在本机快速试，最省事的方式是放进内部插件目录。`PluginLoader.discover_internal_plugins()` 把插件目录加入 `sys.path`，然后把其中**每个子目录**当作一个可导入的包 `importlib.import_module(目录名)`，从模块的顶层命名空间里找出第一个 `Plugin` 子类（`_load_internal_plugin`）。

因此布局必须是：

```
data/plugins/                    # PLUGIN_PATH，见 kirara_ai/config/__init__.py
└── demo_plugin/
    ├── __init__.py              # 必须在这里 import 出 Plugin 子类
    └── blocks.py                # 你的 Block
```

`__init__.py`：

```python
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.workflow.core.block.registry import BlockRegistry

from .blocks import GreetingBlock

logger = get_logger("DemoPlugin")


class DemoPlugin(Plugin):
    """示例插件：注册一个问候 Block"""

    block_registry: BlockRegistry

    def __init__(self):
        pass

    def on_load(self):
        self.block_registry.register(
            "greeting", "demo", GreetingBlock, "示例：问候", "#4a9d7f"
        )
        logger.info("registered demo:greeting")

    def on_start(self):
        pass

    def on_stop(self):
        pass
```

**注意 `data/plugins/` 与 `kirara_ai/plugins/` 的区别**：`entry.py` 构造 `PluginLoader` 时传的是 `os.path.join(os.path.dirname(__file__), "plugins")`，也就是**包内的** `kirara_ai/plugins/`（里面放的是随包分发的内置插件：`im_telegram_adapter`、`im_qqbot_adapter`、`im_wecom_adapter`、`im_http_legacy_adapter`、`llm_preset_adapters`、`bundled_frpc`）。`data/plugins/`（`PLUGIN_PATH`）是规范约定给插件存放自己数据文件的位置，**当前版本并不会自动扫描它**。

所以本地测试有两条路：

- **路径 A（改动最小，不碰仓库代码）**：写个一次性脚本，自己构造容器并显式扫描你的目录——这正是下面验证步骤用的办法。
- **路径 B（模拟真实分发）**：按外部插件打包，`pip install -e .` 装进当前环境，靠 entry point 被发现。

### 2.3 目录布局：外部插件（真正的分发方式）

外部插件靠 Python entry point 发现。`PluginLoader.discover_external_plugins()` 遍历所有已安装分发包，找 group 等于 `chatgpt_mirai.plugins` 的 entry point。**这个 group 名是历史遗留，必须逐字照抄，写成 `kirara_ai.plugins` 不会被发现。**

`setup.py` 写法（照抄 `kirara_ai/plugins/im_telegram_adapter/setup.py` 的结构）：

```python
from setuptools import find_packages, setup

setup(
    name="kirara_ai-demo-plugin",
    version="1.0.0",
    description="Demo plugin for kirara_ai",
    author="You",
    packages=find_packages(),
    install_requires=[],
    entry_points={
        "chatgpt_mirai.plugins": [
            "demo = demo_plugin:DemoPlugin",
        ]
    },
)
```

entry point 的**名字**（这里是 `demo`）就是插件在系统里的标识：`config.plugins.enable` 列表里写的是它，`GET /plugin/plugins/<plugin_name>`、启用/禁用接口用的也是它。

装好之后：

```bash
pip install -e .
```

外部插件默认**不启用**（`discover_external_plugins` 里 `is_enabled=False`），只有名字出现在 `data/config.yaml` 的 `plugins.enable` 里才会被 `_load_external_plugin` 加载：

```yaml
plugins:
  enable:
    - demo
```

也可以启动后在「插件」页点启用，走 `POST /plugin/plugins/<plugin_name>/enable`（它会调 `PluginLoader.enable_plugin`，依次执行 `on_load()` 和 `on_start()`，并把名字写进配置落盘）。启用失败时 `plugin_info.requires_restart` 会被置为 `True`，界面提示需要重启。

`PluginLoader` 还提供 `install_plugin` / `uninstall_plugin` / `update_plugin`（均为 `async`），它们通过 `sys.executable -m pip` 子进程操作，索引地址取自 `config.update.pypi_registry`。对应「插件 → 插件市场」页面。

### 2.4 在插件里注册区块、工作流、调度规则

这三件事都在 `on_load()` 里做。下面是一个同时做完三件事的完整插件（已实测跑通）：

```python
import os

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.dispatch import (CombinedDispatchRule, DispatchRuleRegistry,
                                              RuleGroup, SimpleDispatchRule)
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder

from .blocks import GreetingBlock

HERE = os.path.dirname(os.path.abspath(__file__))


class DemoPlugin(Plugin):
    """示例插件：一个区块 + 一个工作流 + 一条调度规则"""

    container: DependencyContainer
    block_registry: BlockRegistry
    workflow_registry: WorkflowRegistry
    dispatch_registry: DispatchRuleRegistry

    def __init__(self):
        pass

    def on_load(self):
        # 1. 区块：必须先注册，否则下面加载 YAML 时会因找不到 demo:greeting 而失败
        self.block_registry.register(
            "greeting", "demo", GreetingBlock, "示例：问候", "#4a9d7f"
        )

        # 2. 工作流：从随插件分发的 YAML 载入，用 preset 语义注册
        builder = WorkflowBuilder.load_from_yaml(
            os.path.join(HERE, "presets", "greeting.yaml"), self.container
        )
        self.workflow_registry.register_preset_workflow("demo", "greeting", builder)

        # 3. 调度规则：同样用 preset 语义，用户改过的同 ID 规则优先保留
        self.dispatch_registry.register_preset_rule(
            CombinedDispatchRule(
                rule_id="demo_greeting",
                name="问候指令",
                description="以 /hi 开头即回复一句问候。",
                workflow_id="demo:greeting",
                priority=60,
                enabled=True,
                rule_groups=[
                    RuleGroup(
                        operator="or",
                        rules=[SimpleDispatchRule(type="prefix", config={"prefix": "/hi"})],
                    )
                ],
                metadata={"category": "demo", "permission": "user"},
            )
        )

    def on_start(self):
        pass

    def on_stop(self):
        self.workflow_registry.unregister("demo", "greeting")
        self.dispatch_registry.delete_rule("demo_greeting")
```

几个关键选择的理由：

- **用 `register_preset_workflow` 而不是 `register`**：前者在同 ID 已存在时跳过，并且尊重 `.preset_tombstones.json` 里的删除记录；后者会覆盖用户的修改并清除删除标记。同理规则用 `register_preset_rule` 而不是 `register`。
- **`on_stop` 里用 `unregister` 而不是 `delete`**：`WorkflowRegistry.delete()` 会写 tombstone，等于替用户做了「永久删除」的决定；禁用插件不该有这个副作用。
- **注册顺序**：区块必须在工作流之前。`WorkflowBuilder.load_from_yaml` 会立刻调 `registry.get(type_name)` 解析每个节点类型，找不到就抛 `ValueError: Block type demo:greeting not found in registry`。
- **`BlockRegistry` 没有 `unregister`**：这是当前实现的现实。禁用插件后它注册的区块类型仍留在注册表里，直到进程重启。引用了这些类型的工作流因此不会立刻变成「未知区块类型」，但也意味着热禁用不彻底。

### 2.5 插件还能挂什么

从内置插件里能看到另外三类用法：

| 用途 | 参考 | 做法 |
| --- | --- | --- |
| 注册 IM 适配器 | `kirara_ai/plugins/im_telegram_adapter/__init__.py` | `on_load` 里 `self.im_registry.register("telegram", TelegramAdapter, TelegramConfig, 显示名, 简介, 详细说明)`；`on_stop` 里 `self.im_registry.unregister("telegram")` |
| 注册 LLM 适配器 | `kirara_ai/plugins/llm_preset_adapters/__init__.py` | `on_load` 里 `self.llm_registry.register("OpenAI", OpenAIAdapter, OpenAIConfig)` |
| 挂自己的 Web API | `kirara_ai/plugins/bundled_frpc/__init__.py` | `on_start` 里 `self.web_server.web_api_app.register_blueprint(frpc_bp, url_prefix="/api/frpc")`；请求上下文注入用 `@bp.before_request` |
| 提供静态资源（图标等） | `im_telegram_adapter` | `self.web_server.add_static_assets("/assets/icons/im/telegram.png", 本地路径)` |

注意蓝图要挂在 `web_server.web_api_app` 上（那是 Quart 应用，前缀 `/backend-api`），挂到 `web_server.app`（外层 FastAPI）上是另一种玩法，`im_http_legacy_adapter` 用的就是后者，因为它要提供不带 `/backend-api` 前缀的 `/v1/chat`。

### 2.6 验证

把下面这段存成 `verify_plugin.py` 放在项目根目录，它构造一个最小容器并显式扫描你的插件目录，不需要启动完整应用：

```python
import os
import sys

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.dispatch import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from kirara_ai.workflow.implementations.blocks import register_system_blocks

container = DependencyContainer()
container.register(DependencyContainer, container)
container.register(EventBus, EventBus())
container.register(GlobalConfig, GlobalConfig())
registry = BlockRegistry()
container.register(BlockRegistry, registry)
register_system_blocks(registry)                       # 先注册内置区块
container.register(WorkflowRegistry, WorkflowRegistry(container))
container.register(DispatchRuleRegistry, DispatchRuleRegistry(container))

loader = PluginLoader(container, os.path.abspath("data/plugins"))
loader.discover_internal_plugins()
loader.load_plugins()
loader.start_plugins()

print("block   :", registry.get("demo:greeting"))
print("workflow:", container.resolve(WorkflowRegistry).get("demo:greeting"))
print("rule    :", container.resolve(DispatchRuleRegistry).get_rule("demo_greeting"))

loader.stop_plugins()
```

```bash
.venv-win/Scripts/python.exe verify_plugin.py
```

预期：日志里出现 `Found plugin directory: demo_plugin`、`Internal plugin demo_plugin loaded successfully`、`Plugin DemoPlugin initialized`、`Plugin DemoPlugin started`，三个 `print` 都打出非 `None` 的对象，最后 `Plugin DemoPlugin stopped`。任何一行是 `None`，说明对应的注册没生效。

外部插件的验证方式：`pip install -e .` 之后启动应用，「插件」页应该能看到它（此时是禁用态），点启用不报错即为成功；或者直接查接口：

```bash
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/plugin/plugins
```

---

## 三、接入 MCP 服务器并让模型调用它的工具

Agent、Skill、permissioned lifecycle Hook 和 MCP 的组合方式及安全边界另见 [Agents、Skills、Hooks 与 MCP 实用指南](AGENTS_SKILLS_HOOKS_MCP_GUIDE.md)。Agent/Skill 不引入第二套执行器；Hook 不是 Python sandbox；MCP 当前没有通用人工审批中心。

MCP 相关代码在 `kirara_ai/mcp_module/`（**目录名是 `mcp_module`，不是 `mcp`**——`mcp` 是上游 SDK 的包名，重名会冲突）。

### 3.1 一条工具从服务器走到模型的完整路径

```
MCPServerConfig (data/config.yaml)
   └─ MCPServerManager.load_server()        建 MCPServer 实例
        └─ connect_server()                  stdio 或 sse 连上去
             └─ _update_tools_cache()         把 server.get_tools() 写进 tools_cache
                  ↓
「MCP: 提供工具」节点 (MCPToolProvider)
   └─ execute() 从 tools_cache 里挑出 enabled_tools 勾选的那些
        └─ 包装成 List[Tool]，invokeFunc=CallableWrapper(self._call_tool)
             ↓ 连线 tools → tools
「LLM: 执行对话并调用工具」节点 (ChatCompletionWithTools)
   └─ 把 tools 塞进 LLMChatRequest.tools 发给模型
        └─ 模型回 tool_calls → 逐个 await tool.invokeFunc(tool_call)
             └─ MCPToolProvider._call_tool() → server.call_tool(原始名, 参数)
                  └─ 结果转成 LLMToolResultContent，role="tool" 追加进对话
                       └─ 再发一轮，直到模型不再要工具或达到 max_iterations
```

几个实现细节值得知道：

- **工具名冲突处理**：`_update_tools_cache` 发现某个工具名已被别的服务器占用时，会把它改名为 `服务器ID.工具名` 并打一条 warning。所以下拉框里出现带点的名字是正常的。
- **`ToolCacheEntry`** 记录 `(server_id, original_name, tool_info)`，调用时用 `original_name` 发给服务器，模型看到的是显示名。
- **`max_iterations`** 默认 4。最后一轮会强制 `tool_choice = "none"`，防止模型无限要工具（`chat.py:623`）。
- **工具是异步执行的**：`ChatCompletionWithTools.execute()` 是同步方法（跑在线程池里），通过 `asyncio.run_coroutine_threadsafe(actual_tool.invokeFunc(tool_call), loop)` 把协程扔回主事件循环，再 `.result()` 等结果。
- **工具返回的图片会进媒体库**：`_create_tool_result` 对 `types.ImageContent` 调 `MediaManager.register_from_data`，转成 `MediaContent`。

### 3.2 添加一台 MCP 服务器

配置模型是 `kirara_ai/config/global_config.py:67` 的 `MCPServerConfig`：

| 字段 | 说明 |
| --- | --- |
| `id` | 服务器标识，全局唯一 |
| `connection_type` | `stdio` 或 `sse`（`kirara_ai/mcp_module/server.py:133`），其他值会抛 `不支持的服务器连接类型` |
| `command` / `args` / `env` | `stdio` 用：可执行命令、参数列表、环境变量。缺 `command` 会抛 `stdio 连接类型需要提供命令` |
| `url` / `headers` | `sse` 用：SSE 端点与请求头。缺 `url` 会抛 `sse 连接类型需要提供 url` |
| `enable` | 是否启用 |
| `description` | 描述 |

**界面方式**：「MCP」页 → 新建服务器，选连接类型后表单会切换成对应字段（`webui/src/views/mcp/MCPList.vue`）。保存走 `POST /mcp/servers`，注意这个接口把 `args` 当**空格分隔的字符串**收，服务端做 `args.split(" ")`。

**配置文件方式**：直接写 `data/config.yaml`：

```yaml
mcp:
  servers:
    - id: filesystem
      description: 本地文件读写
      connection_type: stdio
      command: npx
      args:
        - "-y"
        - "@modelcontextprotocol/server-filesystem"
        - "/path/to/allowed/dir"
      env: {}
      enable: true
    - id: remote-search
      description: 远端搜索服务
      connection_type: sse
      url: http://localhost:8000/sse
      headers:
        Authorization: Bearer xxx
      enable: true
```

改完重启，`entry.py` 会先 `mcp_manager.load_servers()` 建实例，再在 `run_application` 里 `connect_all_servers(loop)` 并发连接。

运行期操作用这几个接口：`POST /mcp/servers/<id>/start` 连接、`POST /mcp/servers/<id>/stop` 断开、`GET /mcp/servers/<id>/tools` 看这台服务器的工具、`GET /mcp/tools` 看全局工具表、`POST /mcp/servers/<id>/tools/call` 直接试调一个工具（不经过模型）。

> 更新一台正在运行的服务器会返回 409「无法更新正在运行的服务器，请先停止服务器」，这是 `PUT /mcp/servers/<id>` 的刻意设计。

### 3.3 工作范例：`mcp_tools.yaml`

`kirara_ai/workflow/presets/chat/mcp_tools.yaml`（「聊天 - 工具调用 (MCP)」）就是这条路径的最小完整实现，11 个节点。核心是这两段：

```yaml
  # 在此勾选允许模型调用的 MCP 工具；留空表示暂不开放任何工具。
  - type: mcp:mcp_tool_provider
    name: mcp_tools
    params:
      enabled_tools: []
    position:
      x: 880
      y: 520
    connected_to:
      - target: llm_chat_with_tools
        mapping:
          from: tools
          to: tools
  # model_name 故意留空：请在编辑器的下拉框里选择一个支持函数调用的模型。
  - type: internal:chat_completion_with_tools
    name: llm_chat_with_tools
    params:
      model_name: ''
      max_iterations: 4
    position:
      x: 1260
      y: 120
    connected_to:
      - target: chat_response_converter
        mapping:
          from: resp
          to: resp
      - target: chat_memory_store
        mapping:
          from: resp
          to: llm_resp
      - target: chat_memory_store
        mapping:
          from: iteration_msgs
          to: middle_steps
```

三处刻意留空/接线值得注意：

1. **`enabled_tools: []`**。`MCPToolProvider.__init__` 的这个参数带 `options_provider=get_enabled_mcp_tools`，候选项是 `mcp_manager.get_tools().keys()`——也就是**当前已连接**服务器提供的工具。服务器没连上时下拉框是空的，所以顺序必须是「先连服务器，再来勾工具」。留空表示不开放任何工具，模型会退化成普通对话。
2. **`model_name: ''`**。必须手动选一个**支持函数调用**的模型。`ChatCompletionWithTools.execute()` 开头就检查 `if not self.model_name:` 并抛出中文报错，其中带上节点名与要点开的下拉框名（英文原文 `need a model name which support function calling` 保留在括号里便于检索日志）。下拉框的候选来自 `model_name_options_provider`（筛 `TextChat` 能力），它**不会**帮你筛掉不支持函数调用的模型——这一步得你自己判断。
3. **`iteration_msgs → middle_steps`**。工具调用过程中产生的 assistant/tool 消息通过这条线存进记忆。不接的话，下一轮对话看不到「上次调了什么工具、结果是什么」。

用它的正确顺序：

1. 「MCP」页添加并启动服务器，确认状态是 `connected`
2. 工作流模板页把「聊天 - 工具调用 (MCP)」复制一份到自己的分组
3. 编辑副本：点「MCP: 提供工具」节点，在「启用工具列表」里勾选工具
4. 点「LLM: 执行对话并调用工具」节点，在「模型 ID, 需要支持函数调用」下拉框里选模型
5. 保存，然后把某条调度规则的 `workflow_id` 指向这个副本

### 3.4 MCP 的其他能力与现状

`MCPServerManager` 除工具外还缓存 prompts 与 resources（`_update_prompts_cache` / `_update_resources_cache`），并提供 `get_prompt_list` / `get_prompt` / `get_resource_list` / `get_resource`，对应接口 `GET /mcp/servers/<id>/prompts`、`GET /mcp/servers/<id>/resources`、`GET /mcp/servers/<id>/resources/<resource_id>`、`POST /mcp/servers/<id>/prompts/sample`。服务器不支持某类能力时（`Method not found`）会记 warning 并缓存空列表，不影响其他功能。

服务器主动推送 `ToolListChangedNotification` / `PromptListChangedNotification` / `ResourceListChangedNotification` 时，`_handle_server_message` 会自动刷新对应缓存——工具变化不需要重连。

**目前只有工具能进工作流。** prompts 与 resources 只有 WebUI 上的查看/采样入口，没有对应的 Block。想在工作流里用 MCP 的 prompt 或 resource，得自己写一个 Block：`self.container.resolve(MCPServerManager)` 拿到管理器，调 `get_prompt(server_id, prompt_name, args)` 或 `get_resource(server_id, uri)`（都是 `async`，同步的 `execute()` 里用 `asyncio.run_coroutine_threadsafe(..., self.container.resolve(asyncio.AbstractEventLoop)).result()`，写法照抄 `ChatCompletionWithTools`）。

### 3.5 验证

```bash
# 1. 服务器是否连上
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/mcp/servers
# 看每一项的 connection_state，应为 connected

# 2. 工具是否进了全局缓存（这就是节点下拉框的候选来源）
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/mcp/tools

# 3. 有副作用：不经过模型直接执行真实工具，只能在人工核对后运行
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"toolName":"read_file","params":{"path":"/tmp/a.txt"}}' \
  http://127.0.0.1:8080/backend-api/api/mcp/servers/filesystem/tools/call
```

第 2 步返回空数组说明服务器虽然「在配置里」但没连上，回去看 `MCP` tag 的日志。第 3 步不是健康检查：它可能读写数据、执行命令、发送消息或产生费用，Kirara AI 也没有通用逐次审批中心。只有操作人员确认服务器、工具、参数和数据范围后才能执行。随后如需验证模型路径，在受控规则里发一条无敏感信息的消息，并在「LLM 追踪」页看请求详情里有没有 `tool_calls`（开启完整内容会把聊天正文写入数据库）。

---

## 四、手写工作流预设 YAML

绝大多数时候在 WebUI 里拖比手写快。需要手写的场合是：把工作流随插件/wheel 分发、批量生成、或者做版本控制下的 review。

### 4.1 精确的 schema

权威定义是 `WorkflowBuilder.save_to_yaml()` 与 `load_from_yaml()`（`kirara_ai/workflow/core/workflow/builder.py:507` 与 `:589`）。

顶层键：

| 键 | 必需 | 说明 |
| --- | --- | --- |
| `name` | 是 | 工作流显示名，`load_from_yaml` 直接 `workflow_data["name"]`，缺了会 KeyError |
| `blocks` | 是 | 节点列表 |
| `description` | 否 | 说明文字，默认 `""` |
| `config` | 否 | `WorkflowConfig`，目前只有 `max_execution_time`（默认 3600 秒，≤0 表示不限） |
| `metadata` | 否 | 任意字典，透传给 `GET /workflow` 列表 |

`blocks[]` 每一项：

| 键 | 必需 | 说明 |
| --- | --- | --- |
| `type` | 是 | 完整区块类型名 `group_id:block_id`，如 `internal:get_message` |
| `name` | 是 | 节点在本图内的唯一名字，连线靠它引用 |
| `params` | 否 | 传给 `__init__` 的配置，键名必须与参数名一致；没有配置就写 `{}` |
| `position` | 否 | `{x: int, y: int}` 画布坐标 |
| `parallel` | 否 | `true` 时走 `builder.parallel()` 分支 |
| `connected_to` | 否 | 出边列表 |

`connected_to[]` 每一项：

```yaml
connected_to:
  - target: 目标节点的 name
    mapping:
      from: 本节点的输出端口名
      to: 目标节点的输入端口名
```

**连线的方向是「从源节点声明出边」**，一个源可以对同一个目标声明多条（端口不同），`mcp_tools.yaml` 里 `llm_chat_with_tools` 就同时把 `resp` 和 `iteration_msgs` 连给 `chat_memory_store`。

加载分两遍（`load_from_yaml`）：第一遍按顺序创建所有节点（第一个用 `builder.use()`，其余 `builder.chain()`）并写入 `position`；第二遍**先清空 `builder.wire_specs = []`**，再逐条 `force_connect()`。这个清空很关键——`use`/`chain` 会按类型自动配对端口生成连线，如果不清掉，YAML 里写的连线会和自动推断的混在一起。这也意味着**你在 YAML 里必须把每一条连线都写全**，不能指望自动配对。

一个能跑的最小三节点例子（已实测 `load_from_yaml` + 预检零问题）：

```yaml
name: 示例 - 关键词问候
description: 收到消息后回复一句问候语，用于演示自定义 Block 与预设 YAML 的写法。
blocks:
  - type: internal:get_message
    name: get_message
    params: {}
    position:
      x: 120
      y: 120
    connected_to:
      - target: greeting
        mapping:
          from: msg
          to: msg
  - type: demo:greeting
    name: greeting
    params:
      template: 你好，{name}！
      style: casual
      suffix: ' 有什么可以帮你的吗？'
    position:
      x: 520
      y: 120
    connected_to:
      - target: send_message
        mapping:
          from: reply
          to: msg
  - type: internal:send_message
    name: send_message
    params: {}
    position:
      x: 920
      y: 120
```

### 4.2 坐标约定

节点框的最大宽度是 360px（`webui/src/components/workflow/useLayout.ts` 的 `NODE_MAX_WIDTH = 360`，最小 `NODE_MIN_WIDTH = 220`；`internal:code` 节点单独用 200~300px）。这个常量同时被 `CustomNode.vue` 以内联样式绑定，所以它就是真实渲染宽度的上限。

**自动排版只对「没有保存过位置」的节点生效**，YAML 里写死的坐标会原样进入画布。坐标写重了，用户打开编辑器看到的就是一堆叠在一起的方框。

随包的 11 个预设都排在一个规则网格上，行列间距足够大，任意两个节点在 x 与 y 上不会同时靠得太近。具体间距各文件略有差异（大部分是 400 列距 / 420 行距，`mcp_tools.yaml`、`plain_text.yaml`、`time_aware.yaml` 用的是 380 / 400），所以**不要照抄某一个数字，要照抄「不重叠」这个结果**。

这条约束由 `tests/test_workflow_presets.py::test_preset_nodes_do_not_overlap` 强制执行：它对每个预设做两两比较，只要存在一对节点同时满足 `|Δx| < 360` 且 `|Δy| < 360` 就判定重叠并失败。同一个文件里还会校验每个节点都有 `position`。

自己排坐标时照这个思路最省事：

- 按数据流方向从左到右分列，列距 ≥ 380
- 同一列里多个节点上下排，行距 ≥ 400
- 起点放在 `(120, 120)`，与预设保持一致

### 4.3 用 `relayout-presets.mjs` 重算坐标

节点一多，手工排坐标很痛苦。`webui/scripts/relayout-presets.mjs` 是个**只读**脚本：它复用编辑器里同一套 `computeWorkflowLayout()`（`webui/src/components/workflow/useLayout.ts` 导出的 dagre + 去重叠算法），算出无重叠坐标后**只打印到 stdout**，永远不修改任何文件。写回 YAML 由你决定。

```bash
cd webui
node scripts/relayout-presets.mjs ../kirara_ai/workflow/presets/chat/mcp_tools.yaml
```

输出形如：

```text
# ../kirara_ai/workflow/presets/chat/mcp_tools.yaml
# 工作流：聊天 - 工具调用 (MCP)  节点数：11  方向：LR
# 提示：未提供 --block-types，节点尺寸按 YAML 推断，建议补上以获得与编辑器一致的结果
  - name: get_message
    position:
      x: 0
      y: 417   # 原为 x: 120 y: 120
```

参数：

| 参数 | 作用 |
| --- | --- |
| `--json` | 输出 `{ 文件: { 节点名: {x, y} } }`，便于脚本消费 |
| `--block-types=F` | 传入 `GET /block/types` 响应落盘后的 JSON，按真实端口/配置项数量估算节点尺寸，结果与编辑器的「自动排布」完全一致 |
| `--direction=LR\|TB` | 布局方向，默认 `LR` |

不带 `--block-types` 时脚本只能从 `connected_to` 的 mapping 反推端口，节点会被估得略小（输出里那行提示就是这个意思），但仍保证互不重叠。想要精确结果就先导一份类型表：

```bash
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/block/types > /tmp/types.json
cd webui
node scripts/relayout-presets.mjs ../kirara_ai/workflow/presets/chat/mcp_tools.yaml \
  --block-types=/tmp/types.json --json
```

两个前置条件：脚本用 Node 的类型擦除能力直接 `import` 那个 `.ts` 文件，**需要 Node ≥ 22.18 或 23+**；解析 YAML 用 `js-yaml`，缺了会提示 `请在 webui 目录下执行：yarn add -D js-yaml`。

### 4.4 怎么分发

**随插件分发**：YAML 放在插件包里（例如 `demo_plugin/presets/greeting.yaml`），`on_load()` 里 `WorkflowBuilder.load_from_yaml(路径, self.container)` 再 `register_preset_workflow()`。打包时记得让 `setup.py` 带上数据文件（`package_data` 或 `MANIFEST.in`），否则 wheel 里没有它。

**随主包分发**（改本仓库时）：放进 `kirara_ai/workflow/presets/<group>/`，两处打包声明都已经覆盖到：

```
# MANIFEST.in
recursive-include kirara_ai/workflow/presets *
```

```toml
# pyproject.toml
[tool.setuptools.package-data]
"kirara_ai.workflow.presets" = ["*/*.yaml"]
```

首次启动时 `WorkflowRegistry.load_workflows()` 会调 `_extract_bundled_presets()`，把 `PRESETS_DIR`（`kirara_ai/workflow/presets/__init__.py` 里的 `os.path.dirname(os.path.abspath(__file__))`）下每个分组目录里的 `*.yaml` 复制到 `data/workflows/<group>/`。三条规则：

1. **目标文件已存在就跳过**，所以用户在 WebUI 里的修改不会被升级覆盖。
2. **用户明确删除过的预设不会被再次释放**。删除记录存在 `data/workflows/.preset_tombstones.json`（原子写 + fsync），`mark_preset_deleted` 写入、`restore_preset` 回滚（文件删除失败时用）。
3. **用同 ID 新建工作流会清除删除标记**（`register()` 里 `deleted_preset_workflow_ids.discard`）。

释放之后，`load_workflows()` 才遍历 `data/workflows/` 的分组目录逐个 `load_from_yaml` + `register`。所以**新增预设文件名就等于工作流 ID**：`presets/chat/mcp_tools.yaml` → `chat:mcp_tools`。ID 只允许 `[a-zA-Z0-9_-]`（`get_workflow_path` 会校验并拒绝路径穿越）。

### 4.5 验证

```bash
# 1. 结构预检：不实例化任何 Block，直接查类型名、端口、连线、入口、环
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d @- http://127.0.0.1:8080/backend-api/api/workflow/validate <<'JSON'
{"group_id":"demo","workflow_id":"greeting","name":"示例 - 关键词问候","description":"",
 "blocks":[{"type_name":"internal:get_message","name":"get_message","config":{}},
           {"type_name":"demo:greeting","name":"greeting","config":{"template":"你好，{name}！"}},
           {"type_name":"internal:send_message","name":"send_message","config":{}}],
 "wires":[{"source_block":"get_message","source_output":"msg","target_block":"greeting","target_input":"msg"},
          {"source_block":"greeting","source_output":"reply","target_block":"send_message","target_input":"msg"}]}
JSON
```

期望 `{"errors": [], "warnings": []}`。各 issue code 的含义见 `docs/OBSERVABILITY.md`。

```bash
# 2. 确认 YAML 真的能被 WorkflowBuilder 载入（比预检更严格：会解析 type 与 params）
.venv-win/Scripts/python.exe -c "
from kirara_ai.events.event_bus import EventBus
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.implementations.blocks import register_system_blocks
c = DependencyContainer(); c.register(DependencyContainer, c)
c.register(EventBus, EventBus()); c.register(GlobalConfig, GlobalConfig())
r = BlockRegistry(); c.register(BlockRegistry, r); register_system_blocks(r)
b = WorkflowBuilder.load_from_yaml('data/workflows/chat/mcp_tools.yaml', c)
print(b.name, '|', len(b.nodes_by_name), 'nodes |', len(b.wire_specs), 'wires')
"
```

```bash
# 3. 新增随包预设后跑这套契约测试（会校验名称/说明/类型/端口/坐标不重叠/与 data 目录同步）
.venv-win/Scripts/python.exe -m pytest tests/test_workflow_presets.py -q
```

4. 界面上打开编辑器，点工具栏「检查」按钮，应提示「检查通过，未发现问题」，且没有节点带角标。

---

## 五、编写调度规则

调度规则决定「哪条消息交给哪个工作流」。代码在 `kirara_ai/workflow/core/dispatch/`。

### 5.1 数据模型

一条规则是 `CombinedDispatchRule`（`models/dispatch_rules.py:23`）：

```python
class CombinedDispatchRule(BaseModel):
    rule_id: str                    # 唯一 ID
    name: str
    description: str = ""
    workflow_id: str                # 目标工作流，格式 group:id
    priority: int = 5
    enabled: bool = True
    rule_groups: List[RuleGroup]    # 组之间是 AND
    metadata: Dict[str, Any] = {}
```

`RuleGroup` 有 `operator`（`"and"` 或 `"or"`，默认 `or`）和 `rules: List[SimpleDispatchRule]`；`SimpleDispatchRule` 只有 `type` 和 `config` 两个字段。

匹配语义（`CombinedDispatchRule.match`）：

- **规则组之间是 AND**：所有组都得过。
- **组内按 `operator`**：`or` 至少一条命中，`and` 全部命中。
- **空组（`rules` 为空）`continue`，不构成约束**。注释里特意说明了为什么不能 `return True`——那会让整条规则短路成兜底。
- **组内所有条件都无法评估时返回 `False`**（不匹配），单个条件构造或匹配抛异常时记 error 日志并跳过该条件。

### 5.2 全部规则类型

注册在 `registry.py` 末尾的 `DispatchRule.register_rule_type(...)`，共 10 种：

| `type` | 类 | `config` 字段 | 匹配依据 |
| --- | --- | --- | --- |
| `regex` | `RegexMatchRule` | `pattern` | `re.search(pattern, message.content)` |
| `prefix` | `PrefixMatchRule` | `prefix` | 第一个 `TextMessage` 元素的 `text.startswith(prefix)` |
| `keyword` | `KeywordMatchRule` | `keywords: List[str]` | 任一关键词是 `message.content` 的子串 |
| `bot_mention` | `BotMentionMatchRule` | 无 | 消息元素里有指向机器人的 `MentionElement` |
| `random` | `RandomChanceMatchRule` | `chance: int`（0–100） | `random.random() * 100 < chance` |
| `sender` | `ChatSenderMatchRule` | `sender_id`、`sender_group` | 填了的字段必须相等，都为空则恒真 |
| `sender_mismatch` | `ChatSenderMismatchRule` | 同上 | 填了的字段必须**不**相等 |
| `chat_type` | `ChatTypeMatchRule` | `chat_type`：`私聊` 或 `群聊` | `message.sender.chat_type` 相等 |
| `im_instance` | `IMInstanceMatchRule` | `im_instance` | 消息来自指定的 IM 实例 |
| `fallback` | `FallbackMatchRule` | 无 | 恒真 |

`prefix` 与 `keyword`/`regex` 的区别值得注意：`prefix` 只看**第一个文本元素的开头**，而 `keyword`/`regex` 作用在 `message.content` 上（`IMMessage.content` 会把所有元素的 `to_plain()` 拼起来，文本元素后面追加换行）。所以 `@机器人 /chat 你好` 这种消息，`prefix: /chat` 可能匹配不上——`group_mention.yaml` 那条路线正是为此存在。

每种类型的 config schema 可以直接查：`GET /dispatch/types` 列出全部类型名，`GET /dispatch/types/<rule_type>/config-schema` 返回该类型 `config_class` 的 JSON Schema（WebUI 的规则编辑器就靠它动态渲染表单）。

### 5.3 优先级分层

常量在 `kirara_ai/workflow/implementations/rules/default_rules.py:25`：

| 常量 | 值 | 用途 | 内置规则 |
| --- | --- | --- | --- |
| `PRIORITY_SYSTEM` | 100 | 系统命令，必须优先于一切对话规则 | `system_help`、`system_clear_memory` |
| `PRIORITY_COMMAND` | 60 | 有明确前缀/正则的功能 | `game_dice`、`game_gacha` |
| `PRIORITY_CHAT` | 30 | 对话 | `chat_normal`、`chat_creative` |
| `PRIORITY_FALLBACK` | 0 | 兜底 | `fallback` |

`get_active_rules()` 按 `(-priority, rule_id)` 排序——**同优先级按 `rule_id` 升序**，这是刻意的：`os.listdir` 的顺序不跨平台稳定，如果不加第二排序键，同一份配置在不同机器上可能命中不同规则。

`WorkflowDispatcher.dispatch()` 遍历这个有序列表，**第一条匹配的就执行并返回**，后面的不再考虑。所以给自定义规则选优先级时，问自己「它该在哪一档之前被识别」：新增一个前缀指令就用 60，否则会被 30 档的私聊对话规则吞掉。

分层设计还有个约束：`CombinedDispatchRule` 里 `priority` 只是个 `int`，没有枚举校验，写 55 或 1000 都合法。上面四个常量是约定而非强制。

### 5.4 `metadata.temperature`：本轮才真正生效

`metadata` 是自由字典，内置规则里用了三个键：

| 键 | 消费者 |
| --- | --- |
| `category` | `GenerateHelp` 区块按它给帮助文本分类（缺失时退回 `workflow_id` 的前缀） |
| `visible_in_help` | `GenerateHelp` 默认跳过 `fallback` 类规则，置 `true` 可强制显示 |
| `temperature` | `resolve_temperature()`，见下 |

`resolve_temperature()` 在 `kirara_ai/workflow/implementations/blocks/llm/chat.py:36`。**在此之前 `metadata.temperature` 只是个注释性字段，从未进入请求。** 现在的优先级是：

```
节点配置的「采样温度」  >  命中规则的 metadata.temperature  >  不携带该字段（用模型/后端自身默认值）
```

实现要点：

- 合法区间 `TEMPERATURE_MIN = 0.0` 到 `TEMPERATURE_MAX = 2.0`。取上限 2.0 是各家 API 的并集（OpenAI 2.0、Claude 1.0），**超出范围或非数字会被忽略并打一条 warning，然后落回下一级**，不会把一个必然被服务端拒绝的请求发出去。
- 规则那一级是通过容器读的：`container.has(DispatchRule)` → `container.resolve(DispatchRule)` → `rule.metadata.get("temperature")`。命中的规则由 `WorkflowDispatcher.dispatch()` 注册进作用域容器（`scoped_container.register(DispatchRule, rule)`），所以**只有经由调度器触发的执行**才有这一级；直接调 API 跑工作流时这级为空。
- 消费者是两个区块：`ChatCompletion` 与 `ChatCompletionWithTools`，两者都把「采样温度」暴露为可空的配置项（`Optional[float]`，留空即 `None`）。

`data/dispatch_rules/rules.yaml` 里的实际写法：

```yaml
- rule_id: chat_normal
  name: 群聊 AI 对话
  description: 群聊中以 /chat 开头或直接 @机器人 即可对话。
  workflow_id: chat:normal
  priority: 30
  enabled: true
  rule_groups:
  - operator: or
    rules:
    - type: prefix
      config:
        prefix: /chat
    - type: bot_mention
      config: {}
  - operator: or
    rules:
    - type: chat_type
      config:
        chat_type: 群聊
  metadata:
    category: chat
    permission: user
    temperature: 0.7
```

这条规则读起来是：（以 `/chat` 开头 **或** @了机器人）**并且** 是群聊。两个组之间的 AND 就是「群聊里必须显式召唤」的实现方式。

### 5.5 三种创建方式

**方式一：WebUI**。「工作流 → 调度规则」页新建。走 `POST /dispatch/rules`，服务端会校验两件事：`workflow_id` 必须存在，且**至少要有一个真实条件**——所有组都为空会返回 400 `Rule must contain at least one condition; use an explicit fallback condition for a catch-all rule`。想要兜底规则就显式加一个 `fallback` 条件。

**方式二：直接写 `data/dispatch_rules/rules.yaml`**。文件是一个规则列表，格式就是上面那段。启动时 `load_rules()` 遍历目录下所有 `*.yaml`；带 `rule_groups` 键的按新格式解析，不带的走 `_convert_old_rule()` 兼容旧的单条件格式。它是已有实例的持久化来源；全新实例没有任何规则文件时，代码中的 `build_default_rules()` 才会注册默认规则。WebUI 保存规则会写回该 YAML，所以不要把它当成永远会被代码覆盖的只读样例。

有个关键副作用要知道：`load_rules()` 只要成功读到任何一个合法的规则列表（**包括空列表**）就把 `has_persisted_rules` 置为 `True`，而 `register_system_dispatch_rules()` 一看到这个标志就直接 `return`。也就是说**一旦你有了规则文件，内置默认规则就完全不再注入**。这是刻意的：已有文件代表用户的完整配置，也保护了在 tombstone 机制之前做的删除。

**方式三：插件里 `register_preset_rule()`**。语义与预设工作流一致：同 `rule_id` 已存在则跳过，尊重 `data/dispatch_rules/.preset_tombstones.json` 里的删除记录。代码见本文第 2.4 节。

无论哪种方式，规则的持久化都走 `save_rules()`：原子写（临时文件 + fsync + `os.replace`）到 `rules.yaml`，同时保存 tombstone。Web 层调的是 `save_rules_async()`，把这些同步磁盘 I/O 丢进线程池——直接在请求协程里 fsync 会卡住整个事件循环，连带所有 IM 适配器的消息收发。

### 5.6 引用了不存在的工作流会怎样

启动时 `validate_rule_workflows()`（`default_rules.py:196`）会检查每条规则的 `workflow_id`：

- 能退回 `chat:normal` 的，自动改指过去并打 warning
- 连 `chat:normal` 都没有的，把规则 `enabled = False`（保留配置，随时可以改好再启用）

这个检查存在的原因很实际：用户在 WebUI 里删掉一个预设工作流后，指向它的规则还在，`dispatch()` 匹配到它时 `rule.get_workflow()` 返回 `None`，于是**每一条消息都抛 `WorkflowNotFoundException`**——用户看到的是「机器人坏了」而不是「模板被删了」。

### 5.7 验证

先用 `POST /dispatch/preview` 干跑，确认无误再保存。它按真实调度顺序解释每条规则，但不执行工作流、不发消息、不改规则：

```bash
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"content":"/hi 大家好","chat_type":"群聊","sender_id":"u1","group_id":"g1","mentioned":false}' \
  http://127.0.0.1:8080/backend-api/api/dispatch/preview
```

看响应里的 `selected_rule_id` 是不是你期望的那条，以及每条规则的 `decision`（`selected` / `shadowed` / `not_matched` / `indeterminate` / `disabled`）。`explanation.groups` 会逐组逐条给出判定和原因，是排查「为什么我的规则不生效」最直接的工具。

想在**保存之前**验证草稿，把完整规则塞进 `draft_rule`：它会替换掉同 `rule_id` 的已存规则参与排序。界面上对应「试运行当前草稿」按钮。完整字段说明与两个「不确定」情形（`random` 不取样、`im_instance` 无法确定）见 `docs/OBSERVABILITY.md`。

不启动服务也能验证匹配逻辑：

```bash
.venv-win/Scripts/python.exe -c "
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import (
    CombinedDispatchRule, RuleGroup, SimpleDispatchRule)
import kirara_ai.workflow.core.dispatch.registry  # 触发规则类型注册

rule = CombinedDispatchRule(
    rule_id='demo_greeting', name='问候指令', description='',
    workflow_id='demo:greeting', priority=60, enabled=True,
    rule_groups=[
        RuleGroup(operator='or', rules=[SimpleDispatchRule(type='prefix', config={'prefix': '/hi'})]),
        RuleGroup(operator='or', rules=[SimpleDispatchRule(type='chat_type', config={'chat_type': '群聊'})]),
    ],
    metadata={'category': 'demo', 'temperature': 0.6},
)
c = DependencyContainer(); c.register(DependencyContainer, c)
group = IMMessage(sender=ChatSender.from_group_chat('u1','g1','小明'), message_elements=[TextMessage('/hi 大家好')])
c2c   = IMMessage(sender=ChatSender.from_c2c_chat('u1','小明'),        message_elements=[TextMessage('/hi')])
print('群聊 ->', rule.match(group, None, c))   # True
print('私聊 ->', rule.match(c2c, None, c))     # False（第二个组要求群聊）
"
```

注意 `import kirara_ai.workflow.core.dispatch.registry` 那一行不能省——规则类型是在该模块的模块级代码里注册的，不导入就会 `Unknown rule type: prefix`。

验证 `temperature` 真的生效：给规则加 `metadata.temperature: 0.9`，把工作流里 LLM 节点的「采样温度」**留空**，发一条命中该规则的消息，然后在「LLM 追踪」页看请求详情里 `temperature` 是不是 `0.9`（需要先打开「LLM请求记录时包含完整内容」）。故意填个 `3.0` 再试，日志里会出现 `Ignoring out-of-range temperature from dispatch rule metadata: 3.0 (expected 0.0~2.0)`，且请求里不带该字段。

---

## 六、事件与钩子：真实存在的只有一个事件总线

先把话说清楚：**这个项目没有钩子（hook）系统。** 没有前置/后置拦截器，没有中间件链，没有「在消息处理前改写消息」或「在工具调用前审批」这类插入点。唯一的扩展机制是一个**类型键、同步调用**的事件总线。

### 6.1 `EventBus` 与 `PluginEventBus`

`kirara_ai/events/event_bus.py`：

```python
class EventBus:
    def register(self, event_type: Type, listener: Callable): ...
    def unregister(self, event_type: Type, listener: Callable): ...
    def post(self, event): ...
```

`post(event)` 取 `type(event)` 查表，**同步**依次调用监听器；某个监听器抛异常会被捕获、记 error 日志，不影响其他监听器（`event_bus.py:26`）。

三个直接后果：

- **监听器改不了事件流向**。`post()` 不看返回值，没有「取消」或「替换」的概念。想拦截消息只能自己写 IM 适配器或自己写 Block。
- **监听器不能是耗时操作**。它跑在 `post()` 的调用栈上，而 `post()` 的调用方可能是消息处理路径（`WorkflowExecutor.run()` 里就有两处）。要做耗时的事，在监听器里 `loop.create_task(...)` 丢出去。
- **监听器不能是 `async def`**。`post()` 直接 `listener(event)`，传协程函数只会得到一个没人 await 的协程对象和一句没有实际效果的调用。

插件拿到的 `event_bus` 是 `PluginEventBus`（`kirara_ai/plugin_manager/plugin_event_bus.py:6`）。它包一层 `EventBus`，额外维护一份 `_registered_listeners`，提供 `unregister_all()`。`stop_plugins()` 和 `disable_plugin()` 都会自动调它，所以**插件里注册的监听器不需要手动反注册**。

### 6.2 全部可监听的事件

`kirara_ai/events/__init__.py` 导出的就是全集：

| 事件类 | 定义位置 | 发出位置 | 属性 |
| --- | --- | --- | --- |
| `ApplicationStarted` | `application.py` | `entry.py:302`，Web/插件/适配器/MCP/调度器全部启动之后 | 无 |
| `ApplicationStopping` | `application.py` | `entry.py:305`，关停流程最开头 | 无 |
| `PluginLoaded` | `plugin.py` | `plugin_loader.py:166`，每个插件 `on_load()` 之后 | `plugin` |
| `PluginStarted` | `plugin.py` | `plugin_loader.py:180` | `plugin` |
| `PluginStopped` | `plugin.py` | `plugin_loader.py:195` | `plugin` |
| `IMAdapterStarted` | `im.py` | `im/manager.py:157` | `im` |
| `IMAdapterStopped` | `im.py` | `im/manager.py:164` | `im` |
| `LLMAdapterLoaded` | `llm.py` | `llm/llm_manager.py:98` | `adapter`、`backend_name` |
| `LLMAdapterUnloaded` | `llm.py` | `llm/llm_manager.py:129` | `adapter`、`backend_name` |
| `WorkflowExecutionBegin` | `workflow.py` | `execution/executor.py:79` | `workflow`、`executor` |
| `WorkflowExecutionEnd` | `workflow.py` | `execution/executor.py:96`、`:100` | `workflow`、`executor`、`results` |

另外还有一组追踪事件（`kirara_ai/events/tracing/llm.py` 的 `LLMRequestStartEvent` / `LLMRequestCompleteEvent` / `LLMRequestFailEvent`），它们不在 `kirara_ai.events` 的 `__all__` 里，但走的是同一个总线，`LLMTracer` 就是靠监听它们落库的。想自己统计 token 消耗或做用量告警，监听这三个是最省事的路子。

**`WorkflowExecutionEnd` 是最有价值的一个**：它的 `results` 就是执行器的 `self.results`，即 `{节点名: {输出端口名: 值}}`。这是目前唯一能拿到工作流全部中间产物的地方——框架自己没有任何监听器消费它（见 `docs/OBSERVABILITY.md`）。

`WorkflowExecutionEnd` 会在两条路径上发出：正常结束（`:100`）和超时（`:96`，随后抛 `WorkflowExecutionTimeoutException`）。所以收到它不代表执行成功，要自己判断。

### 6.3 两种注册写法

**直接注册**（推荐，插件里用这个）：

```python
class MyPlugin(Plugin):
    def on_load(self):
        self.event_bus.register(ApplicationStarted, self._on_started)
        self.event_bus.register(WorkflowExecutionEnd, self._on_workflow_end)

    def _on_started(self, event: ApplicationStarted):
        logger.info("应用已启动")

    def _on_workflow_end(self, event: WorkflowExecutionEnd):
        logger.info(f"工作流 {event.workflow.name} 执行完毕，产出 {len(event.results)} 个节点结果")

    def on_start(self):
        pass

    def on_stop(self):
        pass   # PluginEventBus 会自动 unregister_all()
```

**用 `@listen` 装饰器**（`kirara_ai/events/listen.py`）：

```python
from kirara_ai.events import listen

@listen(event_bus)
def on_started(event: ApplicationStarted):
    ...
```

它从函数签名的**第一个参数的类型标注**推断事件类型，所以那个标注是必需的——没有参数会抛 `Listener function must have at least one parameter`，没有标注会抛 `Listener function must have an annotated first parameter`。装饰器需要在装饰时就拿到 `event_bus` 实例，在插件类里不太顺手（`self.event_bus` 要等实例化后才有），所以插件内一般用直接注册。

### 6.4 事件监听与 permissioned lifecycle 的边界

`3.3.0b10` 另提供 extension manifest lifecycle：`startup_completed`、`shutdown_requested`、`workflow_before`、`workflow_after`、`workflow_error`、`dispatch_preview`、`model_catalog_refreshed`、`mcp_operation`。插件必须声明 `lifecycle_hooks` capability 和具体 hook；未知或未声明注册会被拒绝并审计。它只约束框架注入的 host facade，不能把进程内 Python 变成 sandbox。

如果你需要的是这些能力：

| 想做的事 | 现状 | 可行替代 |
| --- | --- | --- |
| 消息进入调度前改写/拦截 | 无插入点。`WorkflowDispatcher.dispatch()` 里没有任何回调 | 写一个自定义 IM 适配器包装真适配器；或在工作流最前面加一个自己的 Block 做过滤 |
| 工具调用前审批 | 无。`ChatCompletionWithTools` 直接 `await tool.invokeFunc(...)` | 写一个自己的 tool provider Block，在 `_call_tool` 等价物里加审批逻辑 |
| 每个 Block 执行前后插逻辑 | 无。`WorkflowExecutor._execute_normal_block` 里没有回调 | 只能改执行器源码 |
| LLM 请求前改写 prompt | 无中间件。只有 `@trace_llm_chat` 这个装饰器，且是内置的 | 在工作流里用「基础：替换文本」等节点处理，或自己写 Block |
| 异步监听器 | `post()` 是同步的 | 监听器里 `loop.create_task(...)` |

诚实的结论：可在不改核心源码的前提下增加 Block、插件、既有 EventBus 监听器和允许列表 lifecycle；消息改写、逐 Block 中间件、通用工具审批仍不是现成 primitive。

### 6.5 验证

```bash
.venv-win/Scripts/python.exe -c "
from kirara_ai.events import ApplicationStarted, ApplicationStopping
from kirara_ai.events.event_bus import EventBus

bus = EventBus()
seen = []
bus.register(ApplicationStarted, lambda e: seen.append(('started', repr(e))))
bus.register(ApplicationStopping, lambda e: seen.append(('stopping', repr(e))))
bus.post(ApplicationStarted())
bus.post(ApplicationStopping())
print(seen)
"
```

预期 `[('started', 'ApplicationStarted()'), ('stopping', 'ApplicationStopping()')]`。

在真实插件里验证：`on_load()` 注册一个监听 `ApplicationStarted` 的方法，里面 `logger.info(...)`，启动应用后在「控制台」页或 `logs/` 里搜那条日志。收不到就检查两点——监听器是不是 `async def`（不支持），事件类型是不是写成了父类（`register` 用 `type(event)` 精确匹配，注册 `IMEvent` 收不到 `IMAdapterStarted`）。

---

## 七、定时任务

### 7.1 现有的调度器只做一件事

`kirara_ai/scheduler/scheduler.py` 的 `TaskScheduler` **只负责按周期刷新 LLM 模型目录**，它不是通用的任务注册中心——没有 `add_job()` 之类的接口，也没有 cron 表达式。想加自己的周期任务，得照它的模式自己起一个 `asyncio` 任务。

它的运作方式（这套模式很值得照抄）：

```python
async def _loop(self) -> None:
    startup_delay = STARTUP_DELAY_SECONDS + random.uniform(0, STARTUP_JITTER_SECONDS)
    try:
        await asyncio.wait_for(self._stop_event.wait(), timeout=startup_delay)
        return                      # 等待期间被要求停止 → 直接退出
    except asyncio.TimeoutError:
        pass                        # 正常超时 → 该干活了

    while not self._stop_event.is_set():
        try:
            await self.run_once()
        except Exception as e:
            self.logger.opt(exception=e).error(f"...")
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            return
        except asyncio.TimeoutError:
            continue
```

两个关键手法：

- **用 `asyncio.wait_for(stop_event.wait(), timeout=X)` 代替 `asyncio.sleep(X)`**。这样收到停止信号能立刻退出，而不是傻等一整个周期。`TimeoutError` 才是「正常的下一轮」。
- **业务异常必须在循环里吞掉**。一次失败不能让整个循环死掉。

相关常量与配置：

| 项 | 值/位置 |
| --- | --- |
| `CHECK_INTERVAL_SECONDS` | `86400`（24 小时检查一次谁到期） |
| `STARTUP_DELAY_SECONDS` | `60` |
| `STARTUP_JITTER_SECONDS` | `300`（随机抖动 0–300 秒，避免全新安装时所有后端同时探测） |
| 每后端间隔 | `LLMBackendConfig.auto_detect_interval_days`（`global_config.py:36`，默认 5，0 表示禁用；`scheduler.py:161` 与 `:240` 读它） |
| 上次执行记录 | `data/auto_detect_state.json`（`STATE_FILE`） |

启停由 `entry.py` 驱动：`init_application()` 里 `container.register(TaskScheduler, TaskScheduler(container))`，`run_application()` 里 `task_scheduler.start(loop)`，关停时 `task_scheduler.stop()`。

`run_once(force=False)` 遍历所有 `enable` 且 `auto_detect_interval_days > 0` 的后端，对到期的（或 `force=True` 时全部）执行 `_detect_backend()`：调适配器的 `auto_detect_models()`、经 `normalize_detected_models()` 规范化、与现有目录比较（`model_catalogs_equal`）、有变化才写配置并 `reload_backend()`。整个过程在 `CONFIG_WRITE_LOCK`（模块级 `threading.RLock`）保护下改 `config.llms.api_backends`，因为 Web 路由此刻可能正在读同一份对象；配置落盘走 `asyncio.to_thread`，避免 fsync 卡事件循环。

### 7.2 在插件里加自己的周期任务

照上面的模式，用 `ApplicationStarted` 触发启动、`on_stop()` 收尾：

```python
import asyncio

from kirara_ai.events import ApplicationStarted
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.plugin import Plugin

logger = get_logger("HeartbeatPlugin")

INTERVAL_SECONDS = 300


class HeartbeatPlugin(Plugin):
    """每 5 分钟打一条心跳日志"""

    loop: asyncio.AbstractEventLoop      # 类型标注即依赖注入

    def __init__(self):
        self._task = None
        self._stop = asyncio.Event()

    def on_load(self):
        # 不能在 on_load 里直接 create_task：此时事件循环还没跑起来
        self.event_bus.register(ApplicationStarted, self._on_started)

    def _on_started(self, event: ApplicationStarted):
        self._task = self.loop.create_task(self._run())

    async def _run(self):
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=INTERVAL_SECONDS)
                return                       # 收到停止信号
            except asyncio.TimeoutError:
                pass
            try:
                logger.info("heartbeat")     # 换成你的业务
            except Exception as e:
                logger.opt(exception=e).error("heartbeat failed")

    def on_start(self):
        pass

    def on_stop(self):
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
```

三个容易踩的坑：

1. **别在 `on_load()` 里 `create_task()`**。`load_plugins()` 在 `entry.py` 的 `init_application()` 阶段执行，那时 `loop` 还没 `run_until_complete`。用 `ApplicationStarted` 事件是最稳的时机——它在 `entry.py:302` 发出，此时 Web、插件、适配器都已就绪。
2. **`on_stop()` 里必须停任务**。不停的话禁用插件后任务还在跑，重新启用会跑出两份。
3. **`loop` 从容器取，不要 `asyncio.get_event_loop()`**。容器里注册的是 `entry.py` 显式创建的那个循环（`container.register(asyncio.AbstractEventLoop, asyncio.new_event_loop())`），插件代码不一定跑在它上面。

如果任务需要「按天/按间隔」而不是「固定周期」，参考 `TaskScheduler._is_due()`：把上次执行时间存成 ISO 字符串写文件，每轮醒来自己算 `elapsed_days`。另一种模式见 `MediaManager.setup_cleanup_task()`（`kirara_ai/media/manager.py:630`），它把「下次执行时间」算出来后 `await asyncio.sleep(max(0, next - now))`，适合「每 N 天一次且要记住上次时间」的场景。

### 7.3 验证

```bash
# 立刻强制跑一轮模型目录检测（不等间隔），返回每个后端的成功/失败
curl -X POST -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule/run

# 查看调度器状态与各后端上次执行时间
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule
```

第二条返回的 `running` 字段就是 `self._task is not None and not self._task.done()`，`false` 说明后台循环已经退出或还没启动。

自己的周期任务：把间隔临时改小（比如 5 秒），启动应用，在「控制台」页看日志是不是按预期节奏出现；然后在「插件」页禁用该插件，确认日志**停止**输出——这一步验证的是 `on_stop()` 真的把任务停掉了，比验证任务能跑更重要。

---

## 八、全量校验

改完任何东西，跑这三套：

```bash
# 后端测试（注意用虚拟环境的 python，系统 python 没装 pytest）
.venv-win/Scripts/python.exe -m pytest ./tests -q

# 前端单元测试
cd webui && npx vitest run --config vitest.config.ts

# 前端类型检查
cd webui && npx vue-tsc --noEmit
```

与本文各节直接相关的测试文件：

| 文件 | 覆盖什么 |
| --- | --- |
| `tests/test_workflow_presets.py` | 每个随包预设的名称/说明/节点、类型与端口能否解析、坐标不重叠、与 `data/workflows` 是否同步 |
| `tests/test_default_dispatch_rules.py` | 内置默认规则的注册语义与降级逻辑 |
| `tests/test_workflow_builder.py` | `WorkflowBuilder` 的 DSL 与 YAML 往返 |
| `tests/test_system_blocks.py`、`tests/system_blocks/` | 内置区块行为 |
| `tests/workflow_executor/` | 执行器、输入输出、类型检查 |
| `tests/test_release_workflow_contract.py`、`tests/test_webui_build_contract.py` | 分发完整性（wheel 里有没有预设、Docker 默认数据） |
| `tests/test_model_catalog.py` | `normalize_detected_models` / `model_catalogs_equal` |

新增随包预设或改动区块端口时，`tests/test_workflow_presets.py` 是最容易被打破的一个——它会把每个预设都过一遍 `validate_workflow_definition()`，端口改名会直接让它红。

---

## 相关文档

- 首次部署、配置 LLM 后端、选模型、外观设置：`docs/QUICKSTART.md`
- 日志、LLM 追踪、预检 issue code、规则试运行、画布角标：`docs/OBSERVABILITY.md`
- 部署到首条回复、模板选型、默认规则与画布排错：`docs/WORKFLOW_OPERATIONS_GUIDE.md`


