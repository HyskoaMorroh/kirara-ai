# 可观测性：怎么看清系统在干什么

本文只写**当前版本真实存在**的观测手段，并明确标出哪些东西现在看不到。所有 API 路径都使用统一前缀 `/backend-api/api`（`kirara_ai/web/app.py:285`），除 `POST /backend-api/api/auth/login` 外都需要 `Authorization: Bearer <token>`（`kirara_ai/web/auth/middleware.py` 的 `require_auth`）。

---

## 1. 日志

日志配置集中在 `kirara_ai/logger.py`，进程一启动就生效：

| 去向 | 级别 | 细节 |
| --- | --- | --- |
| 控制台 | `DEBUG` | 带颜色，格式为 `时间 \| 级别 \| tag \| 消息` |
| 文件 `<DATA_PATH>/logs/log_{YYYY-MM-DD}.log` | `DEBUG` | 每天午夜轮转，保留 7 天，旧文件压缩为 zip；默认即 `data/logs/`，可用 `KIRARA_LOG_DIR` 覆盖 |
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

注意：WebSocket 与内存缓冲的门槛都是 `INFO`，**`DEBUG` 日志只在控制台和文件里能看到**。排查区块级细节（例如 `_can_execute` 判断某个输入未满足）必须去翻 `<DATA_PATH>/logs/`（默认 `data/logs/`）下的文件。

### 启动 readiness

`GET /backend-api/api/system/readiness` 是鉴权后的本地、有界、密钥安全诊断。响应包含 `ready`、`timestamp` 和按固定顺序排列的 `checks`：`data_directories_writable`、`configuration_parseable`、`workflows_valid`、`dispatch_targets_exist`、`im_available`、`llm_available`、`mcp_health`。每项给出 `status`、摘要、修复建议和不含敏感值的 evidence。

它不主动调用远端 LLM 来证明模型可回答，也不保证 MCP 远端持续可用。未配置 MCP 为 `skip`，部分 MCP 不可用通常为 `warn`。它需要 Bearer 鉴权，因此不能直接替代容器的匿名 TCP healthcheck。

`im_available` 的 evidence 分三层：**连接层**（`connected_count`、`waiting_count`、
`credential_rejected_count`、`upstream_refused_count`、`initializing_count`、
`reconnecting_count`、`stale_count`、`disconnected_count`、
`storage_unavailable_count`）说的是 Kirara 与 OneBot 实现之间的连接；
**登录层**（`qr_waiting_scan`、`qr_expired`、`qr_scanned`、`qr_succeeded` 等，
仅在适配器配置了上游日志路径时出现）说的是 OneBot 实现与 QQ 之间的登录；
**投递层**（`outbox_ambiguous_count`、`outbox_dead_letter_count`）说的是有没有
被隔离的出站投递。
只差扫码时 remediation 直接指向「去扫码」而不是「查连接」——两者处置相反，
合并成一个数字会让人查错方向。详见
[`QQ_ONEBOT_OPERATIONS.md`](QQ_ONEBOT_OPERATIONS.md) 第六节。

> **投递层那两个数只要大于 0 就说明有人收不到消息**，值得单独接告警。
> 同一收件人序列里存在更早的 `ambiguous` 或 `dead_letter` 时，后面所有投递会被
> 直接跳过而不发送——于是链路显示 `connected`、日志里没有错误、投递接口每次都
> 「成功返回」，而某个群从此收不到任何回复。用户看到「机器人不理我了」，
> 运维看到一切正常。
>
> `ambiguous` 多半来自一次 `docker compose down`：进程被杀时留在 `sending` 的投递
> 结果未知，启动恢复把它们隔离而不是重发（重发可能造成重复消息，那个代价更高）。
> 处置是去 QQ 客户端确认那一页到底有没有送达，再决定人工补发或忽略——
> **不要重试**，那正是这个状态被刻意隔离要避免的事。
>
> 队列计数一直在采集（健康快照的 `outbox` 字段，同时充当存储写入探针），
> 此前没有任何消费方读它——一个采集了却无人消费的指标，与没有采集没有区别。

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
`usage_source`（`provider` / `provider_partial` / `estimated` / `unknown`）、
`ttft_ms`（首字节毫秒数）、
`attempt_count` 与 `attempts_json`（每次 Provider 尝试的顺序与失败原因）、
`cost_snapshot_json`（请求当时的价格快照）、`status`（`pending` / `success` / `failed`）、
`error`、`error_category`，以及可选的 `request_json` / `response_json`。

关于其中三个容易误读的字段：

- `usage_source` 区分四种来源：`provider`（供应商回报了**全部四个维度**）、
  `provider_partial`（供应商确实回报了，但**不是全部维度**）、
  `estimated`（供应商没返回，由本地估算器按脚本感知的字符与图片常量估算）、
  `unknown`（连估算所需的可测内容都没有）。**估算值不是账单依据**：
  它存在的意义是让这类请求不再显示成 0 token、0 成本的「免费请求」——
  「0」是一个断言，「未知」不是，前者更糟。做严格成本核对时按 `usage_source`
  过滤出 `provider` 那一部分。

  > **`provider_partial` 为什么必须单列**：多数 OpenAI 兼容端点只回报
  > `prompt_tokens` / `completion_tokens`，不报缓存两维。这类响应的总额是
  > **补出来的**（缺失维度按 0 计价），而缓存读取的单价通常只有输入 Token 的
  > 1/5 到 1/10、缓存写入往往更贵。与四维齐全的请求显示成同一个词时，
  > 一份在缓存密集部署上系统性偏低的账单看起来与完全可信的账单毫无区别。
  >
  > 判据是**维度是否齐全**，不是「值是否为 0」：上游明确报 0 是一个事实
  > （报了、确实没命中），没报是一个空缺。把前者也标成 partial 会让绝大多数
  > 请求挂上一个没有意义的标记。

  > **「真实 Token」与「供应商返回的 Token」仍是同一类**：本项目的数据链路里
  > 这两者无法区分——我们能拿到的唯一「真实」就是供应商在响应里回报的那份，
  > 没有第二个独立信源可以交叉验证。按「是否经过独立核对」硬拆只会多出一个
  > 永远没有生产者的取值（`estimated` 曾经正是这样：有定义、有测试、主链路零调用）。
  > 若将来接入独立计量来源（例如网关侧旁路计数），再新增一个成员才有意义。
  > 四类里真正可分的那一刀落在「维度齐不齐」上，因此那才是 `provider_partial`。

  > **流式请求的用量按维度合并，不是取最后一片**。OpenAI 兼容端点常把用量拆在
  > 多个分片里回报：`prompt_tokens` 只在第一个带 usage 的分片出现（提示词在请求时
  > 就已确定），`completion_tokens` / `total_tokens` 要等生成结束才有值。
  > 此前聚合写的是整体替换，于是最后那片抹掉先前那份，**输入 Token 变成「未上报」**
  > ——而 `usage_source` 仍是 `provider`，界面上没有任何「数据不完整」的迹象，
  > 它看起来是一条正常的、便宜的请求。现在按字段合并：后到的非 `null` 值优先，
  > 后到的 `null` 不覆盖已有值（`null` 是「这片没提」，`0` 是「报了，确实没有」），
  > `source` 只升不降（不让一个没写 source 的收尾分片把请求打进「不明」）。
- `ttft_ms` 只有在真的观测到首字节时才有值。非流式请求没有首字节概念，
  该字段为空而**不是 0**——把「没测到」写成 0 会被读成「极快」。
- `cost_snapshot_json` 是请求完成时冻结的价格快照。后来改价**不会**改写历史账单。
  没有匹配价格版本时该字段为空，并在统计里计入「未定价请求」，而不是按 0 元计。
- `total_cost` / `cost_currency` 是那份快照的**投影**，写入时算一次、之后不再改。
  它们的存在只为让汇总回到 SQL：成本埋在一个 Text 列的 JSON 里时，
  统计必须把筛选后的每一行取回内存逐条解析，六个索引全部帮不上忙——
  而请求日志有分页保护、统计页没有。快照仍是权威来源，两列不参与任何计算。
  `NULL` 表示「没有定价证据」，与 `0`（定价过且确实免费）严格区分。
- **供应商没回报的维度不参与合计，也不阻断合计。** 一次请求的成本按四个维度
  分别计价（输入、输出、缓存读、缓存写），而各家回报的维度并不一样：Claude
  会给出缓存读与缓存写，OpenAI 只在有缓存命中时给缓存读，Gemini 与 Ollama
  两个缓存维度都不给。此前 `total_cost` 要求四个维度**全部**有值才求和，
  于是除 Claude 之外的供应商全部落入「有输入成本、有输出成本、总额为空」——
  单请求成本、成本汇总与趋势图一起空着，而定价页明明填好了，
  用户只会怀疑是自己没配对。现在的口径是：
  - 供应商回报了该维度 → 计价并计入合计；
  - 供应商**没回报**该维度（字段为 `None`）→ 该维度成本为 `NULL`，不计入合计；
  - 供应商回报的是 `0` → 计价为 0 并计入合计（「用了 0 个」是一个断言）；
  - 四个维度**一个都没有** → `total_cost` 仍为 `NULL`，即「没有定价证据」。

  换句话说，缺一个维度会让那一维的金额是「未知」，但不会把整张账单变成未知。
  想知道某一维到底是「0」还是「未回报」，看 `cost_snapshot_json` 里对应的
  `*_cost` 字段：`NULL` 是未回报，`0` 是回报了 0。
- **不同货币不相加。** 汇总按 `cost_currency` 分组，`overview.total_cost`
  只是金额最大的那个币种的合计，其余在 `overview.cost_by_currency` 里逐一列出。
  把两种货币加进同一个数字不会报错，得到的却是一个没有单位的数——
  混币部署里那是最难发现的一类错误，因此界面在出现第二种货币时会明确提示。
- **重试与故障转移是两项，不是一个数。** `attempt_count` 分不开它们：
  同一家重试 3 次与切换 3 家各试 1 次都是 3，而处置完全相反——前者调超时与
  退避，后者查供应商健康与熔断。因此另有两列：

  | 字段 | 含义 |
  | --- | --- |
  | `retry_count` | 相邻两次尝试的 provider **相同**的次数 |
  | `failover_count` | 相邻两次尝试的 provider **不同**的次数 |

  按**相邻**比较而不是去重计数：`A → B → A` 是两次转移，去重后（2 家 − 1）
  只会算成 1 次，但实际发生了两次切换，每次都付了一遍连接与首字节成本。
  两者 `null` 表示「没有 attempt 数据」（旧记录、第三方调用方、未走故障转移
  路径），与 `0`（确实一次成功）严格区分。统计里对应
  `latency.avg_retry_count` / `latency.avg_failover_count`，CSV 导出与请求详情
  也分列显示。

各家上游的用量字段映射（流式与非流式两条路径口径一致）：

| 上游 | 输入 | 输出 | 缓存命中 | 缓存写入 |
|---|---|---|---|---|
| OpenAI 兼容 | `usage.prompt_tokens` | `usage.completion_tokens` | `usage.prompt_tokens_details.cached_tokens` | 上游未提供 |
| Claude | `usage.input_tokens` | `usage.output_tokens` | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` |
| Gemini | `usageMetadata.promptTokenCount` | `usageMetadata.candidatesTokenCount` | `usageMetadata.cachedContentTokenCount` | 上游未提供 |
| Ollama | `prompt_eval_count` | `eval_count` | 上游未提供 | 上游未提供 |

> **上游没回报用量时，适配器交出的是「无」而不是 0。** 缺失字段兜底成 0 再
> 构造用量对象，会让这条请求被标成 `provider`（看起来像供应商亲口所说）
> 并跳过估算器，最终呈现为一条「0 Token、0 成本」的免费请求。
> 现在这种情况一律落到 `estimated`（有可测内容）或 `unknown`（没有）。

**请求与响应正文默认不记录。** `LLMTracer` 三个事件处理器都会先检查 `config.tracing.llm_tracing_content`（默认 `False`，见 `kirara_ai/config/global_config.py:152`）。要看完整 prompt 和回复，去「设置 → 系统设置 → LLM请求记录时包含完整内容」打开（`TracingCard.vue`，写入接口 `POST /backend-api/api/system/config/tracing`）。这是隐私与磁盘占用的权衡，打开后聊天正文会进数据库。

### 界面

前端在 `webui/src/views/tracing/`：

| 文件 | 作用 |
| --- | --- |
| `TracingView.vue` | 追踪模块的容器路由 |
| `UsageStatisticsView.vue` | **使用统计**：趋势、Provider / 模型分布、成本汇总，带 Provider / 模型 / 时间范围筛选 |
| `DeliveryTimelineView.vue` | 投递时间线：按渠道比较回复各阶段耗时 |
| `tracing.vm.ts` | 通用追踪视图模型（分页、筛选、WebSocket 实时推送） |
| `llm/LLMTraceList.vue` | 请求列表 + 顶部统计卡片 + 多维筛选 + CSV 导出 |
| `llm/LLMTraceDetail.vue` | 单条请求详情：时间、耗时、错误信息、token、用量来源、首字节、尝试次数、成本快照 |
| `llm/llm-tracing.vm.ts` | LLM 专属的表格列、统计卡片与详情字段定义 |

路由为 `/tracing`（重定向到使用统计）、`/tracing/statistics`（使用统计）、
`/tracing/llm`（请求日志）、`/tracing/llm/detail/:traceId`（单条详情）、
`/tracing/delivery`（投递时间线），见 `webui/src/router/index.ts`。

「使用统计」是这三块的入口页：图表由共享的 `LLMStatistics.vue` 渲染，
请求日志与成本定价用链接跳转而**不在本页重做**——重做会立刻产生两套口径。
本页提供 Provider / 模型 / 时间范围 / **时区**筛选与 CSV 导出；
时区默认取浏览器时区但可以改（后端接受任意 IANA 名），跨时区对账时需要看到
对方眼里的「今天」。引导页保留一个无筛选的概览并链到本页。

统计卡片按数据可得性展示：总请求数、请求中、成功、失败、总 Token，
输入 / 输出 / 缓存读写四类 Token 与缓存命中率，
以及总成本（附计价货币）、未定价请求数、平均首字节。
**筛选条件与浏览器时区会一并送到统计接口**，因此列表与卡片始终描述同一批数据；
不传时区会让后端按服务器时区分桶，跨时区用户看到的「今天」是错的。

#### Token 四类拆分与缓存命中率

`overview` 里除 `total_tokens` 外另给四项合计
（`total_prompt_tokens`、`total_completion_tokens`、`total_cached_tokens`、
`total_cache_write_tokens`）与 `cache_hit_rate`，分组统计与日 / 时分桶各自也带这四项。

**为什么不能只有一个总数**：输入 Token 的单价通常是缓存读取的 5~10 倍。
一份「总 Token 完全没变」的账单，在缓存命中率从 80% 掉到 0% 时成本会翻几倍，
而只显示总量的页面在这两种情况下给出的数字**一模一样**。
另一半理由是处置方向：「输出涨了」查 prompt 与 `max_tokens`，
「输入涨了」查上下文与历史长度——合成一个数就把该查什么留给读者猜。

命中率口径：`cache_hit_rate = 缓存读取 /（输入 + 缓存写入 + 缓存读取）`，
与上游计价口径一致（三者相加才是这次请求真正付费的输入侧总量）。

**`null` 与 `0` 是两件事**，一路保留到界面：

| 值 | 含义 | 处置 |
| --- | --- | --- |
| `total_cached_tokens: null` | 这批请求里没有任何上游报过缓存用量 | 查上游是否返回 usage（很多兼容端点不报缓存字段） |
| `total_cached_tokens: 0` | 上游报了，确实没命中 | 查提示词前缀是否稳定 |
| `cache_hit_rate: null` | 同上，命中率未知 | 界面显示「未上报」并附一句说明，不显示 0% |

把未知显示成 0% 会让人去排查一个并不存在的缓存失效问题。
趋势分桶里缓存两项按 0 累加而非 `null`——折线中间出现 `null` 会断开，
而「这个小时没有上游报缓存」在趋势图上与「报了 0」没有不同的处置。

#### 趋势分桶的取数规模

日 / 时分桶在 **SQL 侧**聚合：按 15 分钟槽 × 状态 × 币种 `GROUP BY`，
取回的行数由**时间跨度**决定而不是请求数。此前是把区间内每一行的十列 SELECT
回来再用 Python 累加——默认视图看不出问题（只取近 30 天与近 24 小时），
但调用方传入显式时间范围时那两个兜底过滤器被跳过，「导出全年趋势」
等于一次全区间物化，行数与内存、与响应时间线性相关。

槽长取 15 分钟而不是整小时，因为它是所有 IANA 时区偏移的公约数
（含印度 +05:30、尼泊尔 +05:45）：按整小时分槽会把这些时区的一个槽劈到两个
本地小时里，趋势图上表现为两根都偏低的柱子。**日界仍在 Python 里用真正的
`astimezone` 换算**——只有它认得 DST 与半小时偏移；数据库按存储值截断再搬时区
会让跨时区对账整体错一天，而那种错误不会报错，只会给出一个看起来正常的数字。

响应形状与字段语义逐字段不变：`daily_stats` / `hourly_stats` 仍是按时间升序的
列表，每项带 `date` / `hour` 键。

#### 分组成功率

`providers` / `models` / `backends` 等分组各自带
`success_requests`、`failed_requests`、`pending_requests` 与 `success_rate`。

`error_categories` 回答的是「在失败什么」，回答不了「谁在失败」：
一个 `timeout` 分组里可能混着三家供应商。而故障转移队列该把哪家排后面，
依据正是各家的成功率；没有这一项只能翻请求日志人工计数。
它同时区分「一家慢」与「一家坏」——`avg_duration` 偏低也可能是大量快速失败
把均值拉下来，而慢请求超时被计入了失败。

`pending` 不进成功率分母（还在跑的请求既不是成功也不是失败，
算作失败会让正在进行的长请求把成功率压下去）；一条都还没有结论时
`success_rate` 是 `null`，界面显示「未知」。报 0% 会让一家刚配好、
只有一条在途请求的供应商看起来是最差的那一个。

#### 成本趋势

日 / 时分桶各自带 `cost`、`cost_currency`、`cost_by_currency` 与
`unpriced_requests`。这条曲线回答的是一个只有它能回答的问题：
**「这个月贵了三倍，是哪天开始的？」** 只有一个 30 天合计时，
它只能靠手工二分时间范围反复改筛选条件重查，而账单异常恰恰最需要快速定位到
某一天（换了模型、上了新流量、缓存失效）。

- 成本取的是写入时冻结的快照投影列，**不是现价**——历史账单不会被后来的改价改写。
- **不同货币不画在同一条线上**：界面按币种各画一条，币种集合从数据里推导。
  与 `overview` 同一口径，`cost` 只是主币种（金额最大者）的合计。
- **未定价请求单列**并在 tooltip 里标出。按 0 元并入当天合计，会把
  「有请求没匹配到价格版本」显示成「这天便宜」——两个完全不同的结论。
- 没有任何定价证据的那一天 `cost` 为 `"0"`、`cost_currency` 为 `null`（不编币种）。
- 成本单独一张图而不是并进 Token 趋势：金额与 Token 数差好几个数量级，
  同框时其中一条必然被压成一条平线。

筛选维度：回合 ID、模型、供应商、后端、请求状态、失败类型、用量来源、
请求时间范围（带时区的 ISO-8601）与关键词。「导出 CSV」使用同一份筛选条件，
超过单次上限时会明确提示已截断并要求收窄条件。

**时间范围预设的日界按所选时区算。** 除日历选择器外另有今天 / 近 24 小时 /
近 7 / 14 / 30 天。两点需要知道：

- 「今天」与「近 24 小时」不是同一个问题：上午九点时前者只覆盖 9 小时，
  后者跨到昨天下午。
- 多天预设从**当天零点**起算（`7d` = 含今天的 7 个日历日），不是「now 减
  7×24 小时」——后者首尾各半天，日趋势图第一根柱子永远偏低，会被读成
  「那天用量下降」。跨夏令时的那一天只有 23 或 25 小时，逐日回退而非减固定 24 小时。

日界跟随**时区筛选器**而不是浏览器时区：一个 UTC+8 的查看者选了 `UTC` 之后，
按本地时间切出来的「今天」会横跨上游眼里的两天，而这类错位不会报错。
直接改日历时预设自动切回「自定义」，避免标签与实际区间不一致。

**「未标注」维度要用独立参数。** 空串在参数解析层被当成「没填」丢掉，所以
`provider=""` 无法表达「只看没有 provider 的记录」。改用
`provider_unset=1`（同理 `backend_unset` / `model_unset` /
`error_category_unset` / `usage_source_unset`）；统计接口与请求日志接口
共用同一语义，因此同一筛选条件在两个页面得到同一个结果集。
同时给出 `provider=openai` 与 `provider_unset=1` 是矛盾条件，返回 400——
静默丢掉其中一个会让人以为筛选生效了。

### 对应接口

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/tracing/types` | 列出已注册的追踪器类型（当前只有 `llm`） |
| `POST /backend-api/api/tracing/llm/traces` | 分页查询，body 支持 `page`、`page_size`、`model_id`、`backend_name`、`provider`、`status`、`error_category`、`usage_source`、`correlation_id`、`start_time`、`end_time`、`query` |
| `GET /backend-api/api/tracing/llm/detail/<trace_id>` | 单条详情 |
| `GET /backend-api/api/tracing/llm/statistics` | 总览（含成本）+ 首字节/尝试次数摘要 + 每日与每小时分桶 + 按模型/后端/供应商/用量来源/失败类型分组，支持同一套筛选参数与 `timezone` |
| `POST /backend-api/api/tracing/llm/export` | 导出筛选结果，`format` 为 `json` 或 `csv`，`limit` 1–10000 |
| `GET /backend-api/api/llm/resilience/status` | 各 Provider 的熔断状态、最近尝试、最近状态迁移与上游限额余量 |
| `POST /backend-api/api/llm/backends/<name>/circuit/reset` | 手动把一个 Provider 的熔断器清回 `closed` 并撤销持久化隔离（创建者身份，需确认） |
| `WS /tracing/ws` | 实时推送新的追踪事件 |

#### 熔断的触发与恢复证据`resilience/status` 的每一行除了当前状态，还给出 `recent_transitions`
（最近 10 次状态迁移，最早在前）。这一段解决的是「当前快照回答不了的问题」：
轮询间隔内发生的 open → half-open → closed 在快照里完全不可见，
于是「昨天下午 P1 被隔离过吗、隔了多久、是自己恢复的还是一直开着」
只能靠恰好抓到那一次轮询——那不是证据。

| `reason` | 含义 | 处置方向 |
| --- | --- | --- |
| `failure_threshold` | 连续失败达到 `circuit_failure_threshold` | 刚开始出问题，看上游是否短时抖动 |
| `error_rate` | 样本数够了且错误率达到 `circuit_error_rate_threshold` | 持续不稳定；若样本很小则应调高 `circuit_min_requests` |
| `recovery_timeout` | `circuit_recovery_timeout_seconds` 走完，转入半开 | 无需处理，这是恢复流程的一步 |
| `recovery_success` | 半开探测成功攒够 `circuit_recovery_success_threshold` | 已恢复 |
| `half_open_probe_failed` | 半开探测又失败，重新隔离 | 上游还没好；反复出现说明恢复等待时间偏短 |

每条记录只有六个固定字段（`from_state`、`to_state`、`reason`、`at`、
`failure_count`、`error_rate`），全部是数字或枚举字符串——这份数据会出到面板，
不带上游报文与凭据。`at` 与 `next_recovery_time` 一样是**单调时钟**，
只能用来算「多久以前」，不能当墙上时间格式化。
历史按 Provider 保留最近 64 条并覆盖写：它活在内存里，
一个持续抖动的上游不该把内存吃掉。

#### 手动把一个被误隔离的上游放回队列

`POST /backend-api/api/llm/backends/<name>/circuit/reset`（创建者身份，
请求体 `{"confirmed": true}`）把该供应商清回 `closed`，并**同时删掉
`data/llm/circuit-state.json` 里那条记录**。两件事必须一起做：

- 只清内存的话，下一次状态重建会从文件里把 open 原地读回来——
  接口返回成功、面板显示已重置，而下一个请求仍然跳过这个供应商，
  日志里既没有错误也没有重置痕迹；
- 只删文件的话，本进程内仍在隔离期。

只重置指定的那一家。顺手清空整个状态文件等于把「重置一家」变成「取消所有隔离」，
而其余供应商可能正因真实故障被隔离着。撤销时保留文件原有的 `saved_at`：
重写成新时间戳会把其余供应商的「已经开了多久」清零，
让本该很快进半开的熔断器重新等满整个恢复窗口。

未知的后端名返回 404 而不是 200：重置一个已被删掉的供应商本身不算错，
但如果接口对拼错的名字也回 200，那么「重置成功」和「什么都没发生」看起来一模一样。

没有这个接口时唯一的办法是等满恢复窗口或重启进程，而重启会一并中断
所有正在进行的对话。

#### 上游限额余量：撞上限之前的唯一信号

`resilience/status` 每一行还带一个 `rate_limit` 对象，来自上游在**每个响应**里
返回的限额头（`x-ratelimit-limit/remaining-requests|tokens`、
`anthropic-ratelimit-requests|tokens-limit|remaining`、各类 `reset`、`retry-after`）。
此前这些响应头被完整丢弃，于是限流只能事后发现——请求开始报 429 才知道撞了上限，
而那时排队与重试已经在发生。

| 字段 | 含义 |
| --- | --- |
| `limit_requests` / `remaining_requests` | 请求数配额与剩余 |
| `limit_tokens` / `remaining_tokens` | Token 配额与剩余 |
| `reset_requests_seconds` / `reset_tokens_seconds` | 距配额重置的秒数 |
| `retry_after_seconds` | 上游明确要求的等待秒数，通常只在 429 时出现 |
| `request_headroom` / `token_headroom` | 余量比例 0–1，缺 limit 或 remaining 任一半时为 `null` |

三条口径必须记住：

* **整个 `rate_limit` 为 `null` = 上游不报这些头**（很多兼容端点如此），
  不是「余量为零」。0 表示余量真的用尽，是最该处置的状态；两者混同会让人去排查
  一个不存在的紧急情况。
* **只有 remaining 没有 limit 时不反推百分比。** 百分比需要分母，
  编一个分母会得到一个看起来精确的错数字。
* **请求数与 Token 分开。** 两者会分别见底且处置相反：请求数见底要降低发送频率，
  Token 见底要缩短上下文。合成一个数就分不开，而分不开时任何处置都是猜。

余量只保留**最近一次**，不留历史：它是当下的状态，十分钟前的余量对「现在能不能发」
没有意义。趋势由追踪表负责。采集失败绝不影响请求——限额头是上游给的，
一个解析异常不该让一条本已成功的请求失败。

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
| `llm_first_byte` | 模型首字节。**只有流式模式测得到** |
| `llm_completed` | 模型输出完成 |
| `formatting_started` / `formatting_completed` | 排版与分页，附分段数量 |
| `send_started` | 开始调用平台接口 |
| `send_succeeded` / `send_failed` | 发送结果，附重试次数与错误类型 |

`delivery_durations()` 据此换算出 `queue_seconds`、`llm_first_byte_seconds`、
`llm_generation_seconds`、`formatting_seconds`、`send_seconds`、`total_seconds`。
**缺少证据的阶段不会输出**，不会用 0 顶替。四个渠道使用同一套阶段命名，
可以横向比较同一问题在 QQ、Telegram、WeCom 上的耗时分布。

> **`send_seconds` 在 QQ 上包含发送节流**，这一点必须知道，否则会把自家的
> 主动等待读成网络慢。多页回复的页与页之间会主动等待以避开 QQ 风控
> （`send_pacing_*`，见 QQ 专项文档），而那段等待发生在 `send_started` 与
> `send_succeeded` 之间。判断方法：一次投递的节流总额有上界
> （页间最小等待 × 间隙数 + `send_pacing_maximum_total_seconds`），
> `send_seconds` 明显超出这个上界才说明是真实的上游往返慢。
>
> Telegram 与 WeCom 没有节流（那是 OneBot 私有的），所以同一条回复在这三家上的
> `send_seconds` 天然不可直接相减——这也是「只有 QQ 慢」这类报障的常见来源。
>
> **增量投递成功时这两个阶段仍然会记**，标 `delivery=incremental`。那条路径不走
> 适配器的 `send_message`（内容已经写在被改写的那条消息上），如果不补记，
> 投递耗时看板上这一轮会凭空消失。

> **首字节需要开流式**：`llm_first_byte` 由流式聚合路径在收到第一个非空文本
> 片段时记录。把 `reply_stream_mode` 设为 `aggregate` 才会有这一项，
> 因此 `llm_first_byte_seconds` 与 `llm_generation_seconds` 也只在流式下出现。
> 非流式请求在 HTTP 响应到达前没有任何可观测的中间事件——拿响应到达时刻
> 冒充首字节会把「思考 20 秒、吐字 1 秒」记成「首字节 21 秒、生成 0 秒」，
> 正好反过来，所以这里坚持留空。

时间线在 Agent 路径与遗留工作流路径上都会记录并落库：
Agent 路径由 `WorkflowDispatcher` 完成；遗留工作流路径的回复对象由工作流自己
构造、dispatcher 拿不到，因此由 `SendIMMessage` 把入站阶段拼到回复上并写入
同一张表。两条路径口径一致，统计可以直接比较。

时间线会随 `IMMessage.to_dict()` 一起序列化，可进日志或接口响应；
`WorkflowDispatcher` 在 DEBUG 级别打印一行各阶段耗时汇总。
QQ 侧的具体排查顺序见 [`QQ_ONEBOT_OPERATIONS.md`](QQ_ONEBOT_OPERATIONS.md) 第七节。

#### 历史耗时按时间范围回查

日志只能回答「刚才那条为什么慢」。「上周二 QQ 慢是模型还是发送」需要落库的行，
存在表 `im_delivery_timings`（`kirara_ai/im/delivery_timing_store.py`）：

| 接口 | 用途 |
| --- | --- |
| `GET /backend-api/api/tracing/delivery/summary` | **单渠道**视角：聚合各阶段平均值、最大值与样本数，以及分段数量与重试次数的平均/最大/样本数，支持 `channel`、`start_time`、`end_time`（带时区的 ISO-8601） |
| `GET /backend-api/api/tracing/delivery/compare` | **跨渠道**视角：所有渠道的同一组阶段并排返回，支持 `start_time` / `end_time`（不接受 `channel`） |
| `GET /backend-api/api/tracing/delivery/recent` | 最近若干条逐条耗时，支持 `channel` 与 `limit`（1–1000） |

界面在**「可观测性 → 投递时间线」**，一页里同时给出单渠道明细与跨渠道对比表。

**为什么需要 `compare` 而不是切换 `summary` 的 `channel`。** 19.5 要回答的问题是对比
式的：「QQ 慢，是 QQ 这条链路慢，还是模型本来就慢（三个渠道一样慢）」。切三次下拉
框得到的是三次独立查询，对比这件事被推给了读者的短期记忆——而三个渠道 × 六个阶段
的数字没有人能靠记忆比对。对比表里高亮该阶段最慢的渠道，且只在**至少两个渠道都
测到**这一阶段时标注：一个渠道独有的数字不构成对比。

三条约束是这张表能用且能放心用的前提：

- **不存任何消息正文。** 只有渠道、适配器实例、会话键的 SHA-256 摘要、时长与计数。
  会话摘要让「这个会话是不是一直慢」可查，但无法反推原始会话。
- **没测到的阶段存 NULL，不存 0。** 平均值只对「测到该阶段」的行求平均，
  并同时给出样本数：非流式请求没有首字节，把它们按 0 计入会得到一个不存在的数字。
- **发送段拆成三个数。** `send_seconds` 是整段（用户等了多久），
  `send_pacing_seconds` 是我们为防刷屏**主动等**的时间，
  `send_upstream_seconds` 是上游**真的慢**的时间。19.5 点名「发送限流」不能与
  「LLM 慢」混成一个「QQ 慢」，而这两件事的处置相反：前者调 `send_pacing` 配置，
  后者查上游。不拆开时，一条十页回复因节流等了 20 秒会显示成「平台发送 20 秒」，
  运维去查 QQ 而 QQ 什么问题都没有。这两列上 **`0` 与 `NULL` 含义相反**：
  `0` 是「测了，这次没等」，`NULL` 是「这条链路没测量节流」（Telegram / WeCom
  没有节流概念、第三方适配器不上报）。
- **保留期有界。** 默认 30 天，与 LLM 追踪一致，启动时清理，不会无限增长。

需求 19.5 的九项里，前七项是时间戳（折算成 `phases` 下的阶段耗时），
后两项是计数，在 `counts` 下：

| 字段 | 含义 |
| --- | --- |
| `counts.segment_count` | 一条回复被拆成几页。回答「这批慢投递是不是因为分了很多页」 |
| `counts.retry_count` | 投递重试了几次。回答「慢是因为上游拒过几次」 |

两者与阶段耗时同一条口径：只对**测到该值**的行求平均并给出样本数；
一个都没测到时 `avg` / `max` 为 `null` 而不是 `0`——`retry_count: 0` 是一个论断
（「都没重试过」），会让人以为链路一切正常，而实际只是没有数据。
第三方适配器不带 details 时该值为 NULL，不参与平均。

```bash
# 单渠道明细
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/summary?channel=onebot"

# 跨渠道对比（19.5 的「可比链路耗时」）
curl -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8080/backend-api/api/tracing/delivery/compare"
```

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
| `GET /backend-api/api/llm/auto-detect-schedule` | 各后端的检测间隔、上次执行时间、当前模型数、调度循环是否在运行（界面：**模型 → 自动检测计划**） |
| `GET /backend-api/api/plugin/plugins` | 每个插件的名称、包名、版本、是否内置、是否启用、是否需要重启 |
| `GET /backend-api/api/block/types` | 全部区块类型及其端口、配置项、颜色、说明（下拉框候选项在这里被求值） |
| `GET /backend-api/api/dispatch/types` | 全部可用的规则类型名 |

---

## 6. 明确不存在的观测能力

写清楚边界比含糊其辞有用：

- **没有匿名指标/监控端点**：没有 Prometheus `/metrics`；readiness 与 `GET /backend-api/api/system/status` 都需要鉴权，不适合直接作为匿名容器探针。
- **没有工作流执行历史**：跑过哪些工作流、每个节点花了多久、中间值是什么，都没有持久化。只有 LLM 请求与回复投递耗时两层有记录。
- **没有分布式追踪**：`trace_id` 只在 LLM 请求内部有意义，`correlation_id` 能把
  同一回合的多次 LLM 调用串起来，但不会串成「一条消息 → 一次调度 → 一次工作流」的完整链路。
- **没有告警**：日志和追踪都只是被动记录，框架不会主动通知。
- **`WorkflowExecutionBegin` / `WorkflowExecutionEnd` 没有内置消费者**：事件确实发出来了，但要用起来必须自己写插件监听。
- **日志广播的下限是 `INFO`**：WebUI 控制台看不到 `DEBUG`。

第一、二条要补齐都需要写代码（监听 `WorkflowExecutionEnd` 落库 + 加一组只读接口），不是配置开关能打开的。

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
