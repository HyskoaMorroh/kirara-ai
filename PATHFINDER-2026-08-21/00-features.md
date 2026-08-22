# Kirara AI + OneBot 功能边界

## 审计范围

| 代号 | 项目 | 角色 |
| --- | --- | --- |
| A | `E:/output/kirara-ai/kirara-ai3.3.0b8` | 待发布主仓库 |
| B | `C:/Users/devin/OneDrive/Desktop/kirara-ai-main` | Kirara 基线对照 |
| C | `C:/Users/devin/OneDrive/Desktop/chatgpt-mirai-qq-bot-onebot-adapter-main` | 旧外置 OneBot 适配器 |
| D | `C:/Users/devin/OneDrive/Desktop/llonebot.nix-main` | LLOneBot/PMHQ Nix 打包与运行封装 |
| E | `C:/Users/devin/OneDrive/Desktop/LuckyLilliaBot-main` | QQ 客户端与 OneBot/Milky/Satori 服务端参考实现 |

本审计比较当前源代码工作树，排除 `.git`、`node_modules`、构建产物、缓存、运行数据和 `graphify-out`。A 当前有用户既有未提交改动，因此结论描述当前工作树，不等同于干净上游版本。

## 原始红线

- 保持 Workflow、Block、DispatchRule、Plugin、EventBus、MCP、配置和 YAML 契约。
- 不改变工作流 ID、调度语义、模型选择、fallback 和用户数据。
- 不引入破坏性迁移、静默降级、无限重试或可能产生重复消息的盲目发送重试。
- QQ 登录、设备指纹和 session 属于 LLOneBot/PMHQ 边界，不伪装成 Kirara 能直接保证的能力。
- 不修改、移动、删除、暂存或打包 `docs/LOGO.jpg`。

## 功能边界

### F1. Kirara 平台基线覆盖

目的：确认 A 保留 B 的通用平台能力和契约，而不是只比较 OneBot 文件。

入口与核心证据：

- 应用启动与依赖装配：A/B `kirara_ai/__main__.py:10-24`、`kirara_ai/entry.py:139-362`
- 工作流调度：A/B `kirara_ai/workflow/core/dispatch/dispatcher.py:16-32`
- 工作流执行：A/B `kirara_ai/workflow/core/execution/executor.py:147-210`
- LLM 管理：A/B `kirara_ai/llm/llm_manager.py:17-131`
- IM 管理：A/B `kirara_ai/im/manager.py:19-196`
- 记忆：A/B `kirara_ai/memory/memory_manager.py:19-160`
- Web 管理 API：A/B `kirara_ai/web/app.py:79-331`
- 媒体：A/B `kirara_ai/media/manager.py:25-619`
- MCP：A/B `kirara_ai/mcp_module/manager.py:28-363`
- 插件生命周期：A `kirara_ai/plugin_manager/plugin_loader.py:25-540`；B 同文件 `:20-214` 起
- LLM 调用追踪：A/B `kirara_ai/tracing/llm_tracer.py:37-219`

边界：Kirara 的工作流、LLM、记忆、MCP 和插件系统不属于 QQ 协议实现，不应因合入 OneBot 而改写其公共契约。

### F2. OneBot 插件发现与兼容替换

目的：把旧外置插件 C 的有效入口迁入 A，并保证旧配置名可继续加载且外部旧包不能覆盖内置实现。

入口与核心证据：

- A 内置注册：`kirara_ai/plugins/im_onebot_adapter/__init__.py:14-39`
- A 旧名映射与覆盖保护：`kirara_ai/plugin_manager/plugin_loader.py:20-22,94-104,145-154,537-540`
- C 原 entry point：`setup.py:5-16`、`im_onebot_adapters/__init__.py:13-37`
- A 发行依赖与资源：`pyproject.toml:44,90`、`MANIFEST.in:5`

边界：保留旧插件名兼容，不同时运行两套 OneBot adapter。

### F3. OneBot 入站消息与反向 WebSocket

目的：接收 OneBot V11 生命周期、心跳和消息事件，转换成 Kirara IM 消息并交给现有 dispatcher。

入口与核心证据：

- A：`kirara_ai/plugins/im_onebot_adapter/adapter.py:35-140`
- A 消息转换：`kirara_ai/plugins/im_onebot_adapter/utils/message.py:8`
- A URL 配置：`kirara_ai/plugins/im_onebot_adapter/config.py:26-68`
- C 对照：`im_onebot_adapters/adapter.py:26-113,196-323`
- E OneBot 服务端：`src/onebot11/connect/ws.ts:116-441`

边界：A/C 是 OneBot 消费端与 Kirara 桥；E 是 QQ 客户端和 OneBot 服务端。双方通过协议衔接，不能直接合并源码层生命周期。

### F4. OneBot 出站消息、渲染与分页

目的：把 Kirara 消息转换成 QQ 可读的 OneBot 消息段，等待真实 action 结果，并在长文本、代码、表格和 LaTeX 场景保持可读与完整。

入口与核心证据：

- A 工作流发送等待：`kirara_ai/workflow/implementations/blocks/im/messages.py:38-90`
- A OneBot 出站：`kirara_ai/plugins/im_onebot_adapter/adapter.py:142-323`
- A 渲染与分页：`kirara_ai/plugins/im_onebot_adapter/render.py:1`
- C 对照：`im_onebot_adapters/adapter.py:153-178,385-442`
- E 服务端 action：`src/onebot11/action/message/SendMsg.ts:16-17`

边界：服务端可以保证围栏、分页、表格文本和发送完成语义，不能保证所有 QQ 客户端出现原生“复制代码”按钮。

### F5. OneBot 状态、超时与诊断

目的：区分“Kirara adapter 任务已启动”和“OneBot WebSocket/QQ 实际在线”，并让读取与发送失败有界、可诊断且不泄露 Token。

入口与核心证据：

- A 心跳状态：`kirara_ai/plugins/im_onebot_adapter/adapter.py:47-105`
- A IM 管理运行状态：`kirara_ai/im/manager.py:152-196`
- A adapter API：`kirara_ai/web/api/im/routes.py:38-199`
- A readiness：`kirara_ai/web/api/system/readiness.py:202-214`
- A WebUI 模型：`webui/src/api/im.ts:15-71`
- A WebUI 详情：`webui/src/views/im/IMAdapterDetail.vue:80-119,266-304`

当前真实缺口：单次 OneBot action 和 profile 查询需要有界超时；API/readiness 需要暴露实际连接状态；发送不做自动重试，以免响应丢失时重复消息。

### F6. QQ 管理 action 的能力边界

目的：核对 C 的撤回、禁言、解禁、踢人和资料查询是否应该进入 Kirara 通用能力。

入口与核心证据：

- C 管理调用：`im_onebot_adapters/adapter.py:374-383,444-587`
- A 通用 IM 契约：`kirara_ai/im/adapter.py:23-81`
- E action 注册：`src/onebot11/action/index.ts:1-25,125-144`
- E 代表性实现：`src/onebot11/action/message/DeleteMsg.ts:9-10`、`src/onebot11/action/group/SetGroupBan.ts:10-11`

边界：这些是有权限与平台语义的平台管理动作，当前无通用工作流消费者。没有明确权限模型和跨平台契约前，不把 C 的私有调用直接复制进通用 `IMAdapter`。

### F7. LLOneBot/PMHQ 部署、登录态与恢复

目的：明确 Kirara、OneBot 服务端和 QQ 客户端之间的部署关系，避免更新 Kirara 时无谓中断 QQ 或删除登录数据。

入口与核心证据：

- D 包与 Docker 输出：`flake.nix:2-53`
- D 上游获取：`package/sources.nix:13-15`、`package/llonebot-js.nix:4-23`
- D PMHQ：`package/pmhq.nix:8-108`
- D 服务装配：`package/llonebot-service.nix:33-109`
- E 主程序协议选择：`src/main/main.ts:66-167`
- E 登录：`src/main/qqProtocol/direct-lib/login.ts:129-283`
- E 在线与心跳：`src/main/qqProtocol/direct-lib/online.ts:171-204`

边界：卷持久化可保存配置、设备信息和 session，但不能绕过 QQ 风控或保证过期 session 永远恢复。只更新 Kirara 时应只重建 `kirara-agent`，不应执行会停止全部 QQ 相关容器的全栈 `down`。

## 初步覆盖结论

| 能力 | 分类 | 结论 |
| --- | --- | --- |
| B 的 Kirara 通用平台能力 | 已覆盖，待全量回归证明 | A 与 B 共享主体架构，A 的改动集中在 OneBot、插件兼容和发送完成语义 |
| C 的插件注册、入站、出站、反向 WS | 已覆盖，待逐链路验证 | A 已内置并替代 C |
| C 的拟人化发送等待 | 不应吸收 | 它造成 QQ 相比 Telegram/WeCom 的明显额外延迟 |
| C 的 notice handler | 无有效功能可吸收 | C 自身为 `pass` |
| C 的编辑状态 | 无有效功能可吸收 | C 仅写日志，没有实际 OneBot action |
| C 的撤回/禁言/踢人 | 平台专属，暂不进入通用契约 | 需要先设计权限、审计和跨平台能力模型 |
| D/E 的 QQ 登录和底层协议 | 上游依赖，不复制 | 通过 OneBot V11 协议衔接 |
| E 的 Milky/Satori/WebUI | 上游平台专属，不复制 | 不属于 Kirara OneBot 消费端边界 |
| OneBot 单 action 超时、真实在线状态、诊断 | 真正缺失 | 进入本次补强范围 |
| QQ/PMHQ 卷和局部升级说明 | 真正缺失 | 进入本次部署文档范围 |

