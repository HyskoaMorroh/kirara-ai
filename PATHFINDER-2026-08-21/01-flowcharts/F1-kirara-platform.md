# F1. Kirara 平台主链路

## 范围

本流程覆盖当前工作树中的应用启动、插件生命周期、IM 消息标准化、调度规则匹配、工作流执行和 LLM 选择。OneBot 协议细节另见 F2-F5。

## Happy path

1. CLI 初始化配置和 IoC 容器，加载插件、工作流、调度规则及 LLM 后端。
2. 运行阶段启动插件和已启用的 IM adapter。
3. 平台消息转为统一 `IMMessage`，由有序规则选择第一个匹配工作流。
4. `WorkflowExecutor` 校验执行图并传播 Block 输出。
5. LLM Block 按显式模型及备用模型顺序调用后端；成功响应继续流向发送 Block。

## Side effects

- 读取或创建 `data/config.yaml`、数据库、工作流和调度规则文件。
- 动态导入内置及 entry-point 插件，注册 Block、IM 和 LLM 能力。
- 启动 Web、IM、MCP、调度和插件任务，并发布生命周期事件。
- 对外发起 LLM 与 IM 网络请求。

## Error / fallback

- 单个插件生命周期、IM adapter 或 LLM 后端加载失败会记录错误并隔离，不影响其他实例继续启动。
- 无规则命中时返回 `None`，不静默选择另一工作流。
- 工作流连线类型错误或执行超时会显式抛错。
- LLM Block 仅按既有重试及备用模型契约回退；没有可用模型时给出明确配置错误。

## Flowchart

```mermaid
flowchart TD
    A["CLI main<br/>kirara_ai/__main__.py:10"] --> B["init_application<br/>kirara_ai/entry.py:139"]
    B --> C["注册 IoC、Registry、Manager<br/>kirara_ai/entry.py:181"]
    C --> D["发现内置及外部插件<br/>kirara_ai/plugin_manager/plugin_loader.py:70"]
    D --> E["导入并实例化插件<br/>kirara_ai/plugin_manager/plugin_loader.py:90"]
    E --> F["执行插件 on_load<br/>kirara_ai/plugin_manager/plugin_loader.py:213"]
    F --> G["加载工作流与调度规则<br/>kirara_ai/entry.py:247"]
    G --> H["加载启用的 LLM 后端<br/>kirara_ai/llm/llm_manager.py:46"]
    H --> I["run_application<br/>kirara_ai/entry.py:275"]
    I --> J["执行插件 on_start<br/>kirara_ai/plugin_manager/plugin_loader.py:226"]
    J --> K["创建并启动 IM adapter<br/>kirara_ai/im/manager.py:90"]
    K --> L["平台事件转为 IMMessage<br/>kirara_ai/plugins/im_onebot_adapter/adapter.py:109"]
    L --> M["遍历有序启用规则<br/>kirara_ai/workflow/core/dispatch/dispatcher.py:40"]
    M --> N{"首条规则命中？<br/>kirara_ai/workflow/core/dispatch/dispatcher.py:43"}
    N -- "否" --> O["返回 None<br/>kirara_ai/workflow/core/dispatch/dispatcher.py:63"]
    N -- "是" --> P["解析工作流并创建执行器<br/>kirara_ai/workflow/core/dispatch/dispatcher.py:45"]
    P --> Q["构建执行图并校验连线<br/>kirara_ai/workflow/core/execution/executor.py:45"]
    Q --> R["从入口 Block 并行执行<br/>kirara_ai/workflow/core/execution/executor.py:99"]
    R --> S["构造模型优先级<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:264"]
    S --> T{"取得模型 adapter？<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:315"}
    T -- "否" --> U["尝试下一备用模型<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:316"]
    U --> T
    T -- "是" --> V["构造请求并调用 chat<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:320"]
    V --> W["POST chat/completions<br/>kirara_ai/plugins/llm_preset_adapters/openai_adapter.py:209"]
    W --> X{"调用成功？<br/>kirara_ai/plugins/llm_preset_adapters/openai_adapter.py:212"}
    X -- "否" --> Y["当前模型重试后再回退<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:339"]
    Y --> T
    X -- "是" --> Z["返回统一 LLMChatResponse<br/>kirara_ai/workflow/implementations/blocks/llm/chat.py:327"]
    Z --> AA["传播输出并完成工作流<br/>kirara_ai/workflow/core/execution/executor.py:134"]
    R -. "超过执行时限" .-> AB["抛工作流超时异常<br/>kirara_ai/workflow/core/execution/executor.py:108"]
    I -. "关闭信号" .-> AC["停止 IM、MCP 与插件<br/>kirara_ai/entry.py:355"]
```

## Sources consulted

- `kirara_ai/__main__.py:10-35`
- `kirara_ai/entry.py:139-366`
- `kirara_ai/plugin_manager/plugin_loader.py:25-254`
- `kirara_ai/im/manager.py:89-166`
- `kirara_ai/workflow/core/dispatch/dispatcher.py:32-64`
- `kirara_ai/workflow/core/dispatch/registry.py:83-92,157-170,224-310`
- `kirara_ai/workflow/core/workflow/registry.py:295-362`
- `kirara_ai/workflow/core/execution/executor.py:22-242`
- `kirara_ai/llm/llm_manager.py:46-99,169-180,209-223`
- `kirara_ai/workflow/implementations/blocks/llm/chat.py:190-366`
- `kirara_ai/plugins/llm_preset_adapters/openai_adapter.py:140-221`
