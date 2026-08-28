# 可观测性：怎么看清系统在干什么

本文只写**当前版本真实存在**的观测手段，并明确标出哪些东西现在看不到。所有 API 路径都使用统一前缀 `/backend-api/api`（`kirara_ai/web/app.py:285`），除 `POST /backend-api/api/auth/login` 外都需要 `Authorization: Bearer <token>`（`kirara_ai/web/auth/middleware.py` 的 `require_auth`）。

---

## 1. 日志

日志配置集中在 `kirara_ai/logger.py`，进程一启动就生效：

| 去向 | 级别 | 细节 |
| --- | --- | --- |
| 控制台 | `DEBUG` | 带颜色，格式为 `时间 \| 级别 \| tag \| 消息` |
| 文件 `logs/log_{YYYY-MM-DD}.log` | `DEBUG` | 每天午夜轮转，保留 7 天，旧文件压缩为 zip |
| 内存环形缓冲 | `INFO` | `_recent_logs`，最多 500 条，供 WebUI 新连接时回灌历史 |
| WebSocket 广播 | `INFO` | 通过 `LogBroadcaster` 推给所有订阅者 |

每条日志都带一个 `tag`，来自 `get_logger("tag")`。想在日志里定位某个子系统，直接搜 tag 即可：

| tag | 来源 |
| --- | --- |
| `Entrypoint` | 启动/停止流程（`kirara_ai/entry.py`） |
| `PluginLoader` | 插件发现、加载、启用/禁用 |
| `WorkflowRegistry` / `DispatchRuleRegistry` | 工作流与规则的注册、预设释放、tombstone |
| `WorkflowDispatcher` / `WorkflowExecutor` | 规则匹配与工作流执行 |
| `Block.Code` / `ChatCompletionBlock` / `Block.ChatCompletionWithTools` / `MCPCallTool` | 具体区块的运行细节 |
| `MCP` | MCP 服务器连接与工具缓存 |
| `TaskScheduler` | 模型目录定期自动检测 |
| `Tracing-API` / `WebServer` | Web 层 |

### WebUI 控制台

「控制台」页（`webui/src/views/console/Console.vue`）通过 `WS /system/logs` 实时接收日志，支持关键词过滤和清空。连接时握手要先发一条 token 消息，无效则以 code 1008 关闭（`kirara_ai/web/api/system/routes.py:73`）。

注意：WebSocket 与内存缓冲的门槛都是 `INFO`，**`DEBUG` 日志只在控制台和文件里能看到**。排查区块级细节（例如 `_can_execute` 判断某个输入未满足）必须去翻 `logs/` 下的文件。

### 启动 readiness

`GET /backend-api/api/system/readiness` 是鉴权后的本地、有界、密钥安全诊断。响应包含 `ready`、`timestamp` 和按固定顺序排列的 `checks`：`data_directories_writable`、`configuration_parseable`、`workflows_valid`、`dispatch_targets_exist`、`im_available`、`llm_available`、`mcp_health`。每项给出 `status`、摘要、修复建议和不含敏感值的 evidence。

它不主动调用远端 LLM 来证明模型可回答，也不保证 MCP 远端持续可用。未配置 MCP 为 `skip`，部分 MCP 不可用通常为 `warn`。它需要 Bearer 鉴权，因此不能直接替代容器的匿名 TCP healthcheck。

---

## 2. LLM 请求追踪

这是目前唯一的结构化执行记录，落在 SQLite（`data/db/kirara.db`，表 `llm_request_traces`，模型定义在 `kirara_ai/tracing/models.py`）。

### 记录是怎么产生的

`kirara_ai/tracing/decorator.py` 的 `@trace_llm_chat` 装饰适配器的 `chat()` 方法，请求前 `start_request_tracking`、成功后 `complete_request_tracking`、异常时 `fail_request_tracking`。这三步都是往 `EventBus` 发事件（`LLMRequestStartEvent` / `LLMRequestCompleteEvent` / `LLMRequestFailEvent`，定义在 `kirara_ai/events/tracing/llm.py`），由 `LLMTracer`（`kirara_ai/tracing/llm_tracer.py`）落库。

启动时 `LLMTracer.initialize()` 会把上次进程留下的 `pending` 记录标记为 `failed`（原因写成 `Incomplete request`），并清掉超过 30 天的记录。所以看到一批 `Incomplete request` 通常意味着上次是非正常退出。

### 每条记录包含什么

`trace_id`、`correlation_id`、`model_id`、`backend_name`、`provider`、
`request_time` / `response_time` / `duration`、
`prompt_tokens` / `completion_tokens` / `total_tokens` / `cached_tokens` / `cache_write_tokens`、
`usage_source`（`provider` / `estimated` / `unknown`）、`ttft_ms`（首字节毫秒数）、
`attempt_count` 与 `attempts_json`（每次 Provider 尝试的顺序与失败原因）、
`cost_snapshot_json`（请求当时的价格快照）、`status`（`pending` / `success` / `failed`）、
`error`、`error_category`，以及可选的 `request_json` / `response_json`。

关于其中三个容易误读的字段：

- `usage_source` 区分「供应商返回的用量」与「未知」。**当前没有本地估算器**，
  因此不会出现 `estimated`；供应商没返回 usage 时记为 `unknown`，不会拿字符数冒充真实消耗。
- `ttft_ms` 只有在真的观测到首字节时才有值。非流式请求没有首字节概念，
  该字段为空而**不是 0**——把「没测到」写成 0 会被读成「极快」。
- `cost_snapshot_json` 是请求完成时冻结的价格快照。后来改价**不会**改写历史账单。
  没有匹配价格版本时该字段为空，并在统计里计入「未定价请求」，而不是按 0 元计。

**请求与响应正文默认不记录。** `LLMTracer` 三个事件处理器都会先检查 `config.tracing.llm_tracing_content`（默认 `False`，见 `kirara_ai/config/global_config.py:152`）。要看完整 prompt 和回复，去「设置 → 系统设置 → LLM请求记录时包含完整内容」打开（`TracingCard.vue`，写入接口 `POST /backend-api/api/system/config/tracing`）。这是隐私与磁盘占用的权衡，打开后聊天正文会进数据库。

### 界面

前端在 `webui/src/views/tracing/`：

| 文件 | 作用 |
| --- | --- |
| `TracingView.vue` | 追踪模块的容器路由 |
| `tracing.vm.ts` | 通用追踪视图模型（分页、筛选、WebSocket 实时推送） |
| `llm/LLMTraceList.vue` | 请求列表 + 顶部统计卡片 + 多维筛选 + CSV 导出 |
| `llm/LLMTraceDetail.vue` | 单条请求详情：时间、耗时、错误信息、token、用量来源、首字节、尝试次数、成本快照 |
| `llm/llm-tracing.vm.ts` | LLM 专属的表格列、统计卡片与详情字段定义 |

路由为 `/tracing`（索引）、`/tracing/llm`（列表）、`/tracing/llm/detail/:traceId`（详情），见 `webui/src/router/index.ts`。

统计卡片按数据可得性展示：总请求数、请求中、成功、失败、总 Token，
以及总成本（附计价货币）、未定价请求数、平均首字节。
**筛选条件与浏览器时区会一并送到统计接口**，因此列表与卡片始终描述同一批数据；
不传时区会让后端按服务器时区分桶，跨时区用户看到的「今天」是错的。

筛选维度：回合 ID、模型、供应商、后端、请求状态、失败类型、用量来源、
请求时间范围（带时区的 ISO-8601）与关键词。「导出 CSV」使用同一份筛选条件，
超过单次上限时会明确提示已截断并要求收窄条件。

### 对应接口

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/tracing/types` | 列出已注册的追踪器类型（当前只有 `llm`） |
| `POST /backend-api/api/tracing/llm/traces` | 分页查询，body 支持 `page`、`page_size`、`model_id`、`backend_name`、`provider`、`status`、`error_category`、`usage_source`、`correlation_id`、`start_time`、`end_time`、`query` |
| `GET /backend-api/api/tracing/llm/detail/<trace_id>` | 单条详情 |
| `GET /backend-api/api/tracing/llm/statistics` | 总览（含成本）+ 首字节/尝试次数摘要 + 每日与每小时分桶 + 按模型/后端/供应商/用量来源/失败类型分组，支持同一套筛选参数与 `timezone` |
| `POST /backend-api/api/tracing/llm/export` | 导出筛选结果，`format` 为 `json` 或 `csv`，`limit` 1–10000 |
| `GET /backend-api/api/llm/resilience/status` | 各 Provider 的熔断状态与最近尝试快照 |
| `WS /tracing/ws` | 实时推送新的追踪事件 |

```bash
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"page":1,"page_size":20,"status":"failed"}' \
  http://127.0.0.1:8080/backend-api/api/tracing/llm/traces
```

### 投递时间线：回答「为什么这条回复慢」

LLM 追踪只覆盖模型调用那一段。一条回复从收到消息到发出去还有排队、排版与发送，
这些阶段记在 `IMMessage` 的投递时间线上（`kirara_ai/im/message.py`）：

| 阶段 | 含义 |
| --- | --- |
| `received_event` | 适配器收到 IM 事件 |
| `workflow_started` | 工作流 / Agent 开始执行 |
| `llm_first_byte` | 模型首字节（非流式请求没有这一项，不会伪造） |
| `llm_completed` | 模型输出完成 |
| `formatting_started` / `formatting_completed` | 排版与分页，附分段数量 |
| `send_started` | 开始调用平台接口 |
| `send_succeeded` / `send_failed` | 发送结果，附重试次数与错误类型 |

`delivery_durations()` 据此换算出 `queue_seconds`、`llm_first_byte_seconds`、
`llm_generation_seconds`、`formatting_seconds`、`send_seconds`、`total_seconds`。
**缺少证据的阶段不会输出**，不会用 0 顶替。四个渠道使用同一套阶段命名，
可以横向比较同一问题在 QQ、Telegram、WeCom 上的耗时分布。

时间线会随 `IMMessage.to_dict()` 一起序列化，可进日志或接口响应；
`WorkflowDispatcher` 在 DEBUG 级别打印一行各阶段耗时汇总。
QQ 侧的具体排查顺序见 [`QQ_ONEBOT_OPERATIONS.md`](QQ_ONEBOT_OPERATIONS.md) 第七节。

### 追踪覆盖不到的部分

只有 LLM 请求被追踪。**工作流的逐节点执行过程没有持久化记录**：`WorkflowExecutor` 只在 `EventBus` 上发 `WorkflowExecutionBegin` / `WorkflowExecutionEnd` 两个事件（`kirara_ai/workflow/core/execution/executor.py`），框架自身没有任何监听器把它们落库。要观察节点级流转，当前只有两条路：读 `WorkflowExecutor` 打的 `INFO`/`DEBUG` 日志（`Executing Block: xxx`、`Block [xxx] executed successfully`），或者自己写插件监听这两个事件（做法见 `docs/EXTENDING.md` 的事件小节，`WorkflowExecutionEnd` 的 `results` 里就是每个节点的输出字典）。

---

## 3. 工作流结构预检：`POST /backend-api/api/workflow/validate`

`kirara_ai/workflow/core/workflow/validation.py` 的 `validate_workflow_definition()` 是一次**完全无副作用**的静态检查：不实例化任何 Block、不写文件、不改注册表。路由在 `kirara_ai/web/api/workflow/routes.py:26`。

请求体就是完整的工作流定义（`WorkflowDefinition`，`kirara_ai/web/api/workflow/models.py`），最小可用形态：

```json
{
  "group_id": "user",
  "workflow_id": "draft",
  "name": "草稿",
  "description": "",
  "blocks": [
    { "type_name": "internal:get_message", "name": "get_message", "config": {}, "position": {"x": 120, "y": 120} },
    { "type_name": "internal:send_message", "name": "send_message", "config": {} }
  ],
  "wires": [
    { "source_block": "get_message", "source_output": "msg", "target_block": "send_message", "target_input": "msg" }
  ]
}
```

响应把问题按严重程度分成两组：

```json
{
  "errors":   [{ "severity": "error",   "code": "...", "message": "...", "node_name": "...", "port_name": "..." }],
  "warnings": [{ "severity": "warning", "code": "...", "message": "...", "node_name": null,  "port_name": null }]
}
```

### 完整的 issue code 列表

| code | 严重程度 | 含义 |
| --- | --- | --- |
| `unknown_block_type` | error | 节点用了未安装或不存在的区块类型 |
| `unknown_source_node` | error | 连线的来源节点不存在 |
| `unknown_target_node` | error | 连线的目标节点不存在 |
| `unknown_output_port` | error | 来源节点没有这个输出端口 |
| `unknown_input_port` | error | 目标节点没有这个输入端口 |
| `missing_required_input` | error | 必需（非 nullable）输入没有接线 |
| `multiple_wires_for_input` | error | 同一个输入端口接了多条线，运行时只会保留其中一条 |
| `incompatible_wire_type` | error | 连线两端类型不兼容 |
| `duplicate_node_name` | error | 节点名称重复 |
| `no_entry_node` | error | 没有可执行的入口节点（入口必须没有输入端口，也不能有前置连线） |
| `unreachable_node` | warning | 节点不在任何入口的可达路径上，运行时不会执行 |
| `controlled_cycle` | warning | 环里包含 `LoopBlock` / `LoopEndBlock`，属于受控循环 |
| `unsafe_cycle` | error | 检测到未受循环控制的环 |
| `cycle_scan_depth_exceeded` | warning | 层级超过 `MAX_CYCLE_SCAN_DEPTH`（10000），环检测提前停止 |

两个容易踩的细节：

- **「基础：代码」这类动态端口节点**的端口写在 `params.inputs` / `params.outputs` 里，类上的 `inputs`/`outputs` 永远是空字典。预检通过 `_dynamic_port_names()` 把它们一并认作合法端口，并且 `params.inputs` 里声明的输入同样要求必须接线（否则运行期会因缺关键字参数直接失败）。
- 动态端口的类型一律是 `Any`，所以**跳过类型比较**，只记录连通性。

### 画布上的问题角标

`webui/src/components/workflow/WorkflowCanvas.vue` 把三个来源的问题合并成同一份列表：

1. **服务端预检结果**（`serverValidationIssues`，点「检查」按钮时调 `POST /backend-api/api/workflow/validate`）
2. **本地即时检查**（`localValidationIssues`）：`missing_required_input`、`isolated_node`、`no_entry_node` 三类，编辑时实时算，不用等网络
3. **重叠检查**（`overlapValidationIssues`）：按真实渲染尺寸做两两相交，code 为 `node_overlap`，warning 级，提示「建议点击自动排布」

合并时以服务端结果优先（按 `nodeId:code` 去重），因此同一个问题不会重复出现。合并后的列表通过 `provide('workflowNodeIssues', ...)` 下发给 `CustomNode.vue`，在每个节点右上角渲染一个角标：数字是该节点的问题条数，颜色按最严重的一条取 error（红）或 warning（黄），悬停显示全部消息。

工具栏还有一个问题清单弹窗，点任意一条会把视图居中到对应节点并选中它（`focusNode`）。服务端预检失败（网络问题）时会提示「未能完成服务端预检，已显示本地检查结果」，不阻断编辑或保存。

**预检不阻断保存**：存在问题时保存仍会成功，只是提示「已保存，但仍有 N 处问题待处理」。这是刻意的——允许分阶段搭图。

### 预检看不到的东西

预检只看结构，不看运行时值。这些都查不出来：模型 ID 填错或已下线、提示词占位符写错、MCP 工具没勾选、`internal:code` 节点里的 Python 代码有 bug、外部 API 密钥无效。这些只能靠实际跑一次 + 看日志和 LLM 追踪。

---

## 4. 调度规则试运行：`POST /backend-api/api/dispatch/preview`

路由在 `kirara_ai/web/api/dispatch/routes.py:88`，界面入口是「工作流 → 调度规则」页的「试运行消息」按钮（草稿态另有「试运行当前草稿」）。

它按**真实调度顺序**（优先级降序，同优先级按 `rule_id` 升序，与 `get_active_rules()` 一致）解释每条规则会不会命中，但绝不执行工作流、不发消息、不修改任何规则。消息是内存里临时构造的（`_build_preview_message`），不连接任何 IM 适配器。

请求体（`DispatchPreviewRequest`）：

```json
{
  "content": "/help",
  "chat_type": "群聊",
  "sender_id": "preview-user",
  "group_id": "test-group",
  "mentioned": false,
  "draft_rule": null
}
```

`chat_type` 只能是 `私聊` 或 `群聊`；`mentioned` 为 `true` 时会在消息头部插入一个指向机器人的 `MentionElement`，用来测试「@机器人」条件；`draft_rule` 传一条完整的 `CombinedDispatchRule` 时，它会替换掉同 `rule_id` 的已存规则参与排序——这样能在保存前就验证草稿。

响应给出最终选中的规则，以及每条规则的判定：

```json
{
  "selected_rule_id": "system_help",
  "selected_workflow_id": "system:help",
  "rules": [
    { "rule_id": "system_help", "name": "帮助命令", "workflow_id": "system:help",
      "priority": 100, "enabled": true, "matched": true, "decision": "selected",
      "explanation": { "matched": true, "reason": null, "groups": [ ... ] } }
  ]
}
```

`decision` 五种取值：

| decision | 含义 |
| --- | --- |
| `selected` | 真实调度会选中这条 |
| `shadowed` | 匹配上了，但被更高优先级的规则先拿走 |
| `not_matched` | 条件不满足 |
| `indeterminate` | 无法确定（见下） |
| `disabled` | 规则已禁用 |

除 `decision` 外，每条结果还带四个**与示例消息无关的静态结论**（`DispatchPreviewRuleResult`）：

| 字段 | 含义 |
| --- | --- |
| `order` | 从 1 开始的匹配次序，与调度器实际判断顺序一致 |
| `catch_all` | 这条规则本身是不是无条件规则（会拦下所有消息） |
| `unreachable` | 它是否排在某条已启用的无条件规则之后，因而对**任何**消息都不会被判断到 |
| `shadowed_by_rule_id` | 造成静态不可达的那条无条件规则 ID |

区分清楚：`decision == "shadowed"` 只针对**当前这条示例消息**；`unreachable == true` 是**结构性问题**——不管发什么消息都轮不到它，通常意味着规则顺序配错了。

`explanation.groups` 会逐组、逐条给出 `matched` 与失败原因，这是排查「为什么我的规则不生效」最直接的工具——规则组之间是 AND，组内按 `operator` 决定 AND 还是 OR。

### 不需要示例消息的静态可达性分析：`POST /backend-api/api/dispatch/reachability`

如果你只想知道「规则顺序有没有配错」，不必构造示例消息。`POST /backend-api/api/dispatch/reachability`（`kirara_ai/web/api/dispatch/routes.py:70`）只做静态分析：不创建条件实例、不取样随机概率、不访问 IM 实例，因此完全无副作用。

请求体只有一个可选字段：

```json
{ "draft_rule": null }
```

传入草稿时，若其 `rule_id` 已存在则替换同 ID 的现有规则，否则作为新规则参与排序——这样能在保存前预判遮蔽关系。

响应是 `reachability` 数组，每项即上表那几个字段（`DispatchRuleReachability`，定义在 `kirara_ai/workflow/core/dispatch/reachability.py:62`）。判定逻辑集中在 `analyze_dispatch_reachability()`，**已禁用的规则既不会遮蔽后续规则，也不会被标记为不可达**（它本来就不参与匹配）。

`GET /backend-api/api/dispatch/rules` 的响应里也直接带上了同一份 `reachability` 字段，所以规则列表页不用额外发请求就能标出「永远不会触发」的规则。这套语义只在 `reachability.py` 里定义一次，界面不再自己推导，避免界面与调度器对同一件事给出不同判断。

### 试运行的两个诚实的「不确定」

`CombinedDispatchRule.explain_match()` 刻意不伪造结果：

- `random`（随机概率）规则**不取样**，返回 `matched: null`，原因写「随机概率规则在试运行中不取样」
- `im_instance`（IM 实例）规则需要真实的消息来源，返回 `matched: null`，原因写「IM 实例条件需要真实运行中的消息来源，当前试运行无法确定」

一旦某条更高优先级的规则结果不确定，后面所有匹配上的规则都只会被标成 `shadowed` 而不是 `selected`（`has_indeterminate_predecessor` 逻辑），因为真实运行时到底谁命中取决于那次取样。

配置有误的条件（比如 `config` 字段拼错）会在 `explanation` 里带上 `条件无法评估：...`，与 `match()` 的行为保持一致——无效条件不构成可用匹配结果。

```bash
curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"content":"/help","chat_type":"群聊","sender_id":"u1","group_id":"g1"}' \
  http://127.0.0.1:8080/backend-api/api/dispatch/preview
```

---

## 5. 其他可查的状态

| 接口 | 能看到什么 |
| --- | --- |
| `GET /backend-api/api/system/status` | 运行时长、活跃适配器数、活跃 LLM 后端数、已加载插件数、工作流数、内存/CPU 占用、版本、Python 版本、是否设置了代理 |
| `GET /backend-api/api/system/readiness` | 有界本地检查：数据目录、配置、工作流、调度目标、IM、LLM、可选 MCP；不返回密钥 |
| `GET /backend-api/api/mcp/statistics` | MCP 服务器总数、stdio/sse 各多少、已连接/已断开/错误各多少、工具总数 |
| `GET /backend-api/api/mcp/servers` | 每台服务器的连接状态（`disconnected`/`connecting`/`connected`/`disconnecting`/`error`） |
| `GET /backend-api/api/mcp/tools` | 当前所有可用工具及其 JSON Schema |
| `GET /backend-api/api/llm/auto-detect-schedule` | 各后端的检测间隔、上次执行时间、当前模型数 |
| `GET /backend-api/api/plugin/plugins` | 每个插件的名称、包名、版本、是否内置、是否启用、是否需要重启 |
| `GET /backend-api/api/block/types` | 全部区块类型及其端口、配置项、颜色、说明（下拉框候选项在这里被求值） |
| `GET /backend-api/api/dispatch/types` | 全部可用的规则类型名 |

---

## 6. 明确不存在的观测能力

写清楚边界比含糊其辞有用：

- **没有匿名指标/监控端点**：没有 Prometheus `/metrics`；readiness 与 `GET /backend-api/api/system/status` 都需要鉴权，不适合直接作为匿名容器探针。
- **没有工作流执行历史**：跑过哪些工作流、每个节点花了多久、中间值是什么，都没有持久化。只有 LLM 请求那一层有记录。
- **投递时间线不落库**：各阶段耗时可以序列化、可以进日志，但没有持久化表，
  因此**不能**按时间范围回查「上周 QQ 的平均发送耗时」。要长期留存需要自己
  在适配器发送完成后把 `to_dict()["delivery_durations"]` 写到自己的存储里。
- **没有分布式追踪**：`trace_id` 只在 LLM 请求内部有意义，`correlation_id` 能把
  同一回合的多次 LLM 调用串起来，但不会串成「一条消息 → 一次调度 → 一次工作流」的完整链路。
- **没有告警**：日志和追踪都只是被动记录，框架不会主动通知。
- **`WorkflowExecutionBegin` / `WorkflowExecutionEnd` 没有内置消费者**：事件确实发出来了，但要用起来必须自己写插件监听。
- **日志广播的下限是 `INFO`**：WebUI 控制台看不到 `DEBUG`。

前三条要补齐都需要写代码（监听 `WorkflowExecutionEnd` 落库 + 加一组只读接口），不是配置开关能打开的。

### 关于 token 用量与熔断状态的两点说明

这两项曾经属于「不存在的能力」，现在已经具备，但它们的**精度边界**必须写清楚：

- **`usage_source` 会出现 `estimated`**。供应商不返回 usage 时，
  `kirara_ai/llm/token_estimator.py` 会给出一个脚本感知的估算值
  （CJK 按字符计，拉丁按约 4 字符 1 token，标点各计 1），并标记为 `estimated`。
  它**不是**任何具体词表的精确复现，不能当作账单依据；用它是因为
  「0」是一个错误的断言，而「估算并标明是估算」不是。
  完全没有可测内容时仍保持 `unknown`，不会硬造数字。
- **熔断状态会跨重启保留**。`data/llm/circuit-state.json` 只持久化
  「已打开 / 半开」的熔断器及其打开时刻；恢复等待时间在停机期间继续流逝，
  因此重启不会让等待从头开始。**结果环形缓冲区不持久化**：错误率描述的是最近的
  真实流量，把重启前的窗口搬回来会让一个现在健康的上游被过期样本重新熔断。
  重启后由连续失败阈值接手。状态文件超过 24 小时或损坏时直接忽略。
  多 worker 部署下各进程仍各写各的文件，跨进程共享熔断状态仍未实现。
