# Kirara AI Unified Agent Runtime Design

> 日期：2026-08-24  
> 状态：实施规格  
> 适用版本：Kirara AI 3.3.0b11+

## 1. 目标与边界

本规格定义 Kirara AI 的统一 Agent Runtime。WeCom、QQBot、OneBot、Telegram、HTTP
以及后续适配器都必须把入站消息交给同一个运行时，不得为某一渠道复制一套模型、工具或
记忆逻辑。

标准链路是：

```text
原始事件 -> IMMessage + ChannelContext -> Agent 解析
-> Prompt/Skill/Memory/MCP 版本快照 -> 模型主备链
-> 同轮工具循环与执行前授权检查 -> 渠道回复
-> Session/Memory/LLM trace/工具审计持久化
```

本运行时不承担外部 QQ 登录器、二维码生成器或第三方客户端配置接管职责。项目内置的
`kirara_ai/plugins/im_onebot_adapter` 是运行时可直接使用的适配器，不进入资源安装市场。

## 2. 核心关系

### 2.1 Agent

Agent 是一组可审计的运行策略，至少包含：

- `agent_id`、显示名称、启用状态和默认 Workflow；
- 主模型与备用模型的有序优先级；
- Provider 约束、所需模型能力、上下文预算和是否允许工具调用；
- 成本、追踪、超时、取消和最大工具迭代次数；
- Prompt、Skill、MCP 绑定及每项资源的启用状态；
- 高影响操作确认策略和会话保留策略。

Agent 不保存 Token、Cookie、请求头密钥或原始账号凭据。模型调用交由现有
`LLMManager` 的 Provider queue 执行，Agent 只提供经过校验的模型候选和能力约束。

### 2.2 渠道身份

每条消息都规范化为不可变 `ChannelContext`：

`channel_type + adapter_instance + account_scope + conversation_scope + sender_scope`

其中：

- `channel_type` 是 `wecom`、`qqbot`、`onebot`、`telegram`、`http` 等受控标识；
- `adapter_instance` 是本地适配器实例标识；
- `account_scope` 标识适配器下的机器人账号作用域；
- `conversation_scope` 标识私聊或群聊会话；
- `sender_scope` 标识会话中的发送者。

所有字段在进入日志、API 和审计前均须脱敏或哈希化。原始事件只保留适配器自己的
必要关联信息，不把 Token、手机号、地址、Cookie、二维码内容写入 Session。

### 2.3 Agent 选择与覆盖

选择顺序为：

1. 会话覆盖；
2. 账号覆盖；
3. 渠道覆盖；
4. 全局默认 Agent。

覆盖值必须引用已启用的 Agent，未知引用、禁用 Agent 或不满足模型能力约束时拒绝运行，
不能静默换成意料之外的模型或工具。

对一个 Agent 的配置合并顺序是：

`Agent 默认值 -> 渠道覆盖 -> 账号覆盖 -> 会话覆盖 -> Workflow 覆盖`

更窄范围只能收紧权限、缩短预算、减少模型或工具集合，不能扩大上游 Provider、模型能力、
Prompt 权限或 MCP allowlist。所有覆盖结果都写入本轮运行快照。

### 2.4 Workflow

Workflow 仍是可视化编排和兼容入口。若某条规则命中 Workflow，Dispatcher 在创建
`WorkflowExecutor` 前解析 Agent Runtime 上下文；现有工作流节点可以读取上下文快照。
旧工作流没有 Agent 绑定时继续使用原有节点模型和规则语义，但新建或显式绑定的 Agent
必须使用统一的模型、工具和审计策略。

## 3. Prompt、Skill、MCP 与 Memory

### 3.1 版本快照

每次 Session 运行开始时，为 Prompt、Skill、MCP 配置生成不可变快照：

- 资源 ID、类型、版本、来源摘要和内容哈希；
- 绑定范围和启用状态；
- 权限声明、有效权限和生成时间。

模型请求只使用本轮快照。资源随后更新不能改变已经完成或恢复中的消息上下文。

### 3.2 Prompt

Prompt 绑定按资源顺序合并，系统 Prompt、Agent Prompt、渠道/账号/会话覆盖和 Workflow
补充 Prompt 分层保存。停用最后一个 Prompt 时实际上下文清空；启用、停用和删除均须
与资源注册状态同步。已被启用的 Prompt 不允许直接删除，须先停用并在审计中记录。

### 3.3 Skill

Skill 的主要入口是仓库发现和 `skills.sh` 搜索；ZIP 仅用于离线导入、迁移、备份和恢复。
安装器必须保存来源、版本、内容哈希、入口、文件清单和权限，处理重复 ID、嵌套包装目录、
缺少 `SKILL.md`、版本降级和部分导入失败。安装后默认禁用，显式启用并通过权限确认后才
能进入 Agent 执行链路。Skill 不因出现在资源页面而自动生效。

### 3.4 MCP

MCP 配置启用只表示连接和发现能力，不表示 Agent 可以执行工具。有效工具集合为：

`Agent allowlist ∩ Session allowlist ∩ Workflow allowlist ∩ 当前已连接工具`

空交集意味着本轮不提供工具。搜索和展示只使用工具名、描述、服务器 ID 等非敏感字段，
禁止索引环境变量、请求头和密钥内容。每次实际调用前再次检查 allowlist、服务器连接状态、
参数 schema、超时、取消和确认状态，并写入工具调用审计。现有 `MCPToolProvider` 必须
通过这一边界调用管理器，不能直接调用 `server.call_tool()`。

### 3.5 Memory

Memory 的作用域必须包含渠道身份摘要、账号、会话以及既有私聊/群聊范围。不同渠道中同名
用户不得共享记忆，除非 Agent 明确配置了经过审计的跨渠道共享作用域。查询只能读取计算出的
目标作用域，不得遍历所有缓存键。写入和查询都关联 Session 与资源快照。

## 4. 模型主备与工具循环

Agent 给 `LLMManager` 提供按优先级去重的模型链、Provider 约束、能力要求和总 deadline。
保留现有失败分类、重试、熔断和流式边界：首个可见片段前可以切换，首片段后不得拼接另一
Provider 的输出；取消、总超时和静默超时必须停止当前请求并记录原因。

同轮工具循环执行以下步骤：

1. 发送完整上下文和有效工具定义；
2. 收到工具调用后校验名称、参数和调用 ID；
3. 对每个调用执行 Agent/Session/Workflow 交集检查；
4. 高影响工具进入确认等待状态，未确认前不得调用外部系统；
5. 普通工具在单次超时和总 deadline 内执行，结果作为 `tool` 消息回传同一轮；
6. 达到最大迭代次数时强制 `tool_choice=none`，返回模型最终答复；
7. 工具失败、拒绝、过期或取消都以结构化结果回传模型，不静默丢弃。

并行工具调用只有在每个工具声明可并行、互不依赖且均未触发高影响确认时允许；默认串行，
同一 Session 的外部副作用仍保持接收者顺序。

## 5. 高影响操作确认状态机

高影响操作包括发送、修改、删除、发布、付款及 Agent 策略声明的其他外部副作用。状态为：

`proposed -> awaiting_confirmation -> confirmed -> executing -> succeeded|failed`

也允许：

`awaiting_confirmation -> rejected|expired|cancelled`

确认记录包含 Session、消息、工具、参数摘要、资源快照、发起时间、过期时间和审计 ID，
不保存未经脱敏的凭据。确认必须匹配一次性确认 ID 和当前 Session；拒绝或过期后工具不得
执行，结构化结果需回传模型。应用重启后 `awaiting_confirmation` 恢复为可恢复的等待状态，
超过期限则转为 `expired`。

## 6. Session、恢复点与审计

Kirara 原生 Session 不等同于 CC Switch 的外部客户端会话浏览器。每个 Session 至少保存：

- 规范化渠道身份摘要、Agent、Workflow 和所有权；
- 用户、助手、工具调用、工具结果和确认事件消息；
- Prompt/Skill/MCP/模型策略版本快照；
- 当前状态、错误原因、取消信息、恢复点和保留期限；
- LLM trace ID、Provider attempts、usage 来源、价格版本和成本快照。

恢复必须从最后一个完整恢复点继续，不能重复执行已确认的外部副作用。删除是单独的高影响
动作，浏览、导出和恢复不应隐式删除数据。API 和控制面只展示脱敏身份、摘要和统计。

## 7. API 与控制面

服务端提供 Agent 的创建、更新、启停、渠道/账号/会话覆盖、资源绑定、运行快照和审计查询。
资源页面将仓库/`skills.sh` 搜索作为 Skill 主入口，将 ZIP 标记为离线入口；不展示内置
`im_onebot_adapter` 的重复安装按钮。MCP 页面区分连接状态、配置启用和 Agent 执行授权。
Session 页面提供搜索、详情、恢复点和带确认的删除。

所有写操作返回状态和审计 ID；需要确认的操作先返回 `awaiting_confirmation`，不得在同一
请求内执行外部副作用。分页、筛选和全文搜索只允许使用非敏感索引字段。

## 8. 跨渠道验收

- 同一 Agent 在 WeCom、QQBot、OneBot、Telegram、HTTP 消息上产生相同的资源快照结构；
- 渠道、实例、账号、群组、发送者不同，Session 与 Memory 不串线；
- 每个渠道都能走主模型失败后的受控备用链，并保留统一 trace 与回复状态；
- Prompt、已启用 Skill 和允许的 MCP 工具实际出现在模型请求上下文中；
- MCP 工具在 Agent、Session 或 Workflow 任一层撤销后立即不可执行；
- 普通工具在同一轮得到模型消费的工具结果，高影响工具在确认前无外部调用；
- 重启后 Session、待确认事件、OneBot outbox 和恢复点可继续处理且不重复副作用；
- 内置 OneBot 适配器无需安装；Skill 仓库搜索、`skills.sh` 搜索、安装、更新、回滚和
  ZIP 离线恢复均有部分失败报告；
- 后端、WebUI、迁移、构建、差异检查和敏感信息扫描通过，未验证的真实外部账号链路单独
  标注，不能用静态配置冒充真实发送成功。

