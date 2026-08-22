# 工作流操作与部署指南

适用于 `3.3.0b11` 的首次部署、模板选型、规则调试和日常维护。目标是让没有项目背景的使用者也能从空实例得到一条可验证的回复。

## 1. 部署前检查

推荐使用锁文件安装依赖，避免不同机器解析出不同版本：

```powershell
uv sync --frozen
cd webui
yarn install --frozen-lockfile
yarn build
```

Docker 部署会使用同一个 `uv.lock` 导出带哈希的运行依赖，并从仓库内的 `webui/` 构建静态页面。已有 `data/` 卷优先于镜像内默认数据；升级时不要直接删除数据卷，应先通过「系统设置 → 备份与恢复」导出备份。

## 2. 十分钟首次可用流程

1. 打开 WebUI，完成管理员密码初始化。
2. 在「LLM」新增支持文本对话的后端，并执行一次模型目录检测。
3. 在「工作流 → 模板管理」选择接近用途的模板，点击「以此为模板」创建副本。
4. 打开副本中的 LLM 节点，从模型下拉框手动选择本机已配置的模型。
5. 在「调度规则」创建或检查一条指向该副本的规则，先用「试运行」确认命中。
6. 启用聊天平台适配器，发送一条测试消息。

模型下拉框每次打开节点列表都会读取当前后端的可用模型。自动定期探测只更新后端模型目录，不会改写工作流中手动选定的主模型、备用模型或你的模板副本。

## 3. 模板怎么选

内置模板不应全部启用。先复制一个，再为自己的场景建立规则，避免多个相近规则互相遮蔽。

| 场景 | 推荐模板 | 使用提醒 |
| --- | --- | --- |
| 通用聊天 | 聊天 - 角色扮演 | 适合私聊或被明确唤起的群聊 |
| 图文对话 | 聊天 - 原生多模态对话 | 模型必须支持图片输入 |
| 带工具的回答 | 聊天 - 工具调用 (MCP) | 只连接可信的 MCP 服务 |
| 时间相关回答 | 聊天 - 时间感知 | 使用内置当前时间节点 |
| 不支持 Markdown 的平台 | 聊天 - 纯文本输出 | 在发送前去掉 Markdown |
| 需要拆分长消息 | 聊天 - 长回复分条 | 以 `<break>` 作为分段标记 |
| 公开群聊的词汇替换 | 聊天 - 敏感词替换 | 先在副本配置替换词 |
| 不使用模型的固定逻辑 | 聊天 - 自定义脚本 | 先通过工作流预检再启用 |
| 仅在群里被提及时回答 | 群聊 - 提及触发 | 规则同时应限定群聊与 @机器人 |

另外还有帮助、清空记忆、骰子、抽卡、记录聊天内容等代码注册的内置工作流。YAML 模板首次启动会释放到 `data/workflows/`；已经保存过的同 ID YAML 优先，因此升级不会覆盖用户修改。

### 内置 recipe 速查

先用 `POST /backend-api/api/dispatch/preview` 验证规则命中；工作流定义用 `GET /backend-api/api/workflow/<group_id>/<workflow_id>` 读取后提交到 `POST /backend-api/api/workflow/validate`。这两个 POST 都不执行工作流或发送消息。

| 能力 | 前置条件 | 示例触发 | 预期工作流 | 主要诊断 |
| --- | --- | --- | --- | --- |
| 帮助 | 对应规则启用 | `/help` | `system:help` | dispatch preview |
| 清空记忆 | 管理员确认当前会话范围 | `/清空记忆` | `system:clear_memory` | dispatch preview；真实触发会删除会话记忆 |
| 骰子 | 游戏规则启用 | `.roll 1d100` | `game:dice` | dispatch preview |
| 抽卡 | 游戏规则启用 | `抽卡`、`十连`、`单抽` | `game:gacha` | dispatch preview |
| 群聊 `/chat` | IM 与文本模型可用 | `/chat 你好` | `chat:normal` 或规则指定副本 | readiness + dispatch preview |
| 提及/私聊/兜底记忆 | 群聊 mention、私聊规则与 fallback 启用 | `@机器人 你好`、私聊“你好”、普通群消息 | `chat:group_mention`/`chat:normal`/`chat:memory_store` | dispatch preview + reachability |
| 多模态 | IM 能接收媒体，模型支持图片输入 | 图片 + “描述这张图” | `chat:normal_multimodal` | workflow validate + LLM trace |
| 长回复拆分 | 模型按提示产生 `<break>` | “分三段回答” | `chat:long_reply_split` | workflow validate + 日志 |
| 时间感知 | 文本模型可用 | “现在几点” | `chat:time_aware` | workflow validate + LLM trace |
| function calling | 支持函数调用的模型；自行连接工具执行分支 | “查询一个受控工具” | `chat:function_calling` | workflow validate + LLM trace |
| 自定义脚本 | 审核 `internal:code` 内容 | “统计这句话字数” | `chat:custom_script` | workflow validate + 日志 |
| 敏感词替换 | 在用户副本配置替换词 | 含测试词的受控消息 | `chat:sensitive_word_filter` | workflow validate + 日志 |
| MCP tools | 可信且已连接服务器、工具 allowlist、支持函数调用的模型 | 仅使用获批只读工具的受控问题 | `chat:mcp_tools` | `GET /backend-api/api/mcp/tools` + workflow validate |

表中的 `chat:*` ID 以预设 catalog 为准；用户副本会有自己的 group/ID。function calling、自定义脚本、清空记忆和 MCP 真实调用都可能有副作用，不应作为无人值守 smoke。

## 4. 默认触发规则

全新实例在不存在规则文件时会得到以下优先级：

| 优先级 | 用途 | 典型触发 |
| --- | --- | --- |
| 100 | 系统指令 | `/help`、`/清空记忆` |
| 60 | 游戏指令 | `.roll XdY`、`抽卡`、`十连`、`单抽` |
| 30 | 聊天 | 群聊 `/chat` 或 @机器人；私聊直接发送 |
| 15 | 宽松提及 | `game_gacha_mention`：群聊未用 `/chat` 也未 @机器人时，句中提到「抽卡」即触发 |
| 0 | 兜底 | 只记录聊天内容，不回复 |

规则按「优先级从高到低、规则 ID 从小到大」匹配。已有 `data/dispatch_rules/rules.yaml` 时，系统不会补写或复活默认规则。删除、导入和批量维护前，先在规则页运行试运行与可达性分析。

## 5. 画布使用

画布支持节点搜索、网格吸附、缩放、小地图、导入导出、撤销重做、结构预检和自动排布。出现节点框重叠时，先点击「自动排布」；它使用节点实际尺寸并保留既有连接关系。节点列表会在窄屏自动缩小，避免遮挡画布。

编辑完成后先点「检查」。预检会报告未知节点或端口、类型不兼容、缺失必需输入、重复输入连线、无入口、不可达节点与不受控环。预检不保存、不执行、不发送消息。

## 6. 排错顺序

1. 规则页试运行：确认测试消息是否命中预期规则，是否被更高优先级规则遮蔽。
2. 工作流预检：修复红色问题角标后再保存。
3. LLM 页面：确认后端已启用、模型目录中有目标模型，节点下拉框已手动选择。
4. 聊天平台页面：确认适配器启用且连接正常。
5. 追踪与日志：需要内容级 LLM 追踪时，先在系统设置开启相应选项；密钥、令牌和备份包只能留在受信任位置。

当前版本没有跨服务分布式追踪、工作流执行历史或告警系统。遇到需要长期审计的场景，应先保留脱敏日志和规则/工作流导出，再实施扩展。

## 7. 扩展边界与建议

现有稳定扩展点是 Block、插件、MCP、工作流预设、调度规则、事件总线和模型自动探测；具体做法见 [扩展开发指南](EXTENDING.md)。

Agent/Skill 不是新的运行时：它们分别是工作流加策略元数据、catalog 支持的模板元数据。Hook 已有受 manifest capability 约束的 lifecycle allowlist，但不是 sandbox 或任意中间件。完整边界与示例见 [Agents、Skills、Hooks 与 MCP 实用指南](AGENTS_SKILLS_HOOKS_MCP_GUIDE.md)。不要把不受信任的 MCP 命令、环境变量或文件访问直接接到公开聊天规则上。

### 7.1 先交付一个有边界的插件

适合第一批上线的能力是“知识库问答”“日报整理”“工单分流”这类输入、输出和权限都清楚的任务。每个插件只做一件事，并包含：

1. 一个或一组 Block，输入和输出端口使用稳定、可检查的类型。
2. 一份工作流模板，模板内不写死模型 ID、密钥、机器路径或外部服务地址。
3. 一条默认关闭的调度规则；先在规则页用“试运行”确认命中顺序，再由管理员启用。
4. 单元测试、模板预检和失败时的用户可理解提示。

上线时先只给受控群聊或管理员会话使用。运行一周后再评估命中率、超时、工具失败和误触发情况；没有这些证据，不要把插件直接挂到私聊默认规则。

### 7.2 MCP 的可靠接入方法

项目已经能够管理 MCP 服务、浏览工具/资源/提示词，并通过“聊天 - 工具调用 (MCP)”模板把工具交给模型。部署顺序如下：

1. 在 MCP 页面添加一个可信服务，先保持停用，核对启动命令、工作目录、网络地址、环境变量和可访问文件范围。
2. 连接后只查看工具、资源和提示词，不执行有写入、副作用或付费风险的工具。
3. 复制“聊天 - 工具调用 (MCP)”模板；在 LLM 节点手动选择支持工具调用的模型，再在画布中点击“检查”。
4. 用受控测试消息验证只读工具。确认工具参数、超时提示和失败文案后，再创建一条限定会话范围的规则并试运行。
5. 对可能写文件、发消息、调用外部 API 或产生费用的工具，必须保留人工确认节点或管理员专用规则，不能交给公开触发条件。

MCP 规范把工具、资源和提示词分别定义为可发现的能力；本项目的界面也应继续按这三类展示，而不是把所有内容混成一个不透明的“智能体”按钮。官方规范可作为实现和兼容性参考：[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)、[Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)、[Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)。

### 7.3 Agents、Skills、Hooks 的真实组合方式

使用现有 Block、模板、规则和 MCP 组合出单一职责流程，沿用当前画布预检、导入导出、版本兼容和测试体系：

| 概念 | 最小可交付物 | 必须的保护 |
| --- | --- | --- |
| Agent | 一个具名工作流加受限工具清单，而不是能任意执行的循环 | 最大步骤数、超时、会话作用域、工具白名单、人工确认 |
| Skill | 插件 manifest 中声明的 Block 组、模板和示例输入 | 版本、能力说明、依赖检查、迁移说明、卸载前影响分析 |
| Hook | manifest 声明的 `workflow_before`、`workflow_after`、`workflow_error` 等允许列表 lifecycle | 只申请 `lifecycle_hooks` 等必需 capability；异常审计；插件代码必须可信 |

manifest 还支持 `startup_completed`、`shutdown_requested`、`dispatch_preview`、`model_catalog_refreshed`、`mcp_operation`。插件仍在主进程内执行，权限只约束注入的 host facade；不要用不可信 Python 回调，也不要把 Hook 当成安全边界或事务回滚。

### 7.4 可观测性与画布体验的下一批提升

当前可以用 LLM 追踪和系统日志定位问题，但还没有跨服务的完整执行历史。推荐按以下顺序增强：

1. 为一次工作流运行生成 `run_id`，在日志、LLM 追踪、规则命中和节点错误中一致传递。
2. 在画布右侧加入只读执行时间线：节点开始/结束、耗时、输入输出摘要和错误，不保存密钥或完整隐私消息。
3. 先记录本地 JSON 日志和指标，再可选导出到 OpenTelemetry；一条 trace 由多段 span 组成，适合表达“规则命中 -> 工作流 -> LLM/MCP 工具”的因果链。
4. 为自动排版添加“仅预览”“应用后可撤销”和“重叠节点数”提示，继续保留现有手动拖拽和保存坐标，不替用户静默改图。

OpenTelemetry 的 traces/spans 模型可作为这部分的通用语义参考：[Observability primer](https://opentelemetry.io/docs/concepts/observability-primer/)。

### 7.5 每次扩展上线前的检查表

1. 新模板能通过画布“检查”，且默认关闭或仅限受控规则。
2. 没有写死模型 ID、令牌、密码、主机路径或个人资料。
3. 失败、超时、权限不足和 MCP 断连都有可理解的提示与不破坏原流程的降级。
4. 新增或变更的 Block、模板、规则和 API 都有回归测试；前端运行类型检查、单元测试和生产构建。
5. 更新 README、扩展指南、操作指南和 CHANGELOG，并说明兼容性和迁移方式。
