# 1.txt 逐条复核与整改记录（2026-08-28）

> 本文件记录**已完成**的整改。每条都有 `file:line` 证据与对应回归测试；
> 凡本机无法验证的外部场景一律列在最后一节，不计入完成。

## 一、门禁实测结果

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv-win\Scripts\python.exe -m pytest ./tests -q` | `1698 passed, 1 skipped`（305s） |
| WebUI 单元 | `npm --prefix webui run test:unit -- --run` | `40 files, 250 passed` |
| WebUI 类型 | `npm --prefix webui run type-check` | 退出码 0 |
| WebUI lint | `npm --prefix webui run lint:check` | `0 error, 131 warning`（均为既有未使用导入告警） |
| WebUI 生产构建 | `npm --prefix webui run build` | `built in 1m 23s` |
| 版本同步 | `python scripts/version.py check` | `version artifacts synchronized: 3.3.0b11` |
| 空白字符与换行 | `git diff --check` | 无输出；三个被编辑的测试文件的 CRLF 已按 `.gitattributes` 规范化为 LF |
| 文档命令 | 扫描 `npm test` / `.venv/Scripts` / 裸 `python -m pytest` | 0 命中；文档引用的 `test:unit`、`build` 都存在于 `webui/package.json` |
| 敏感值扫描 | 对全部跟踪文件 grep 真实 uin / uid / `sk-` / 明文 `AUTH_TOKEN=` | 0 命中 |
| 索引洁净度 | `git ls-files` 中审计产物与运行期数据库计数 | 均为 0；`git archive` 46.6 MB → 31.7 MB |
| 图谱刷新 | `graphify update .` | `Code graph updated` |

整改前基线为 `1587 passed`，本轮净增 111 项回归测试
（9 个新测试文件约 84 项，12 个既有测试文件内新增若干项），全部先失败后通过。

## 二、发布产物污染（5 项）

### B1 索引中的 727 个本地审计产物已移除

`git ls-files` 曾包含 `.qa-*`（22 个目录、约 700 文件）、`.playwright-mcp/`、
`.superpowers/`、`.memsearch/`、`work/*.zip`、`PATHFINDER-2026-08-21/`，
共 14.0 MB。`.gitignore:61/64` 早有规则，但它只能阻止**新增**文件入索引。
引入者是 `77ce6c7`、`e052cdb`、`c6fab17`、`a2bc1be` 四个 `git add` 范围失控的提交。

- 处理：`git update-index --force-remove`（磁盘文件全部保留），
  索引从 1542 降到 812 条。
- `.gitignore` 补 `/.superpowers/`、`/.memsearch/`、`/findings.md`、
  `/progress.md`、`/task_plan.md`。
- 防复发：`tests/test_webui_build_contract.py` 新增
  `test_git_index_carries_no_local_audit_artifacts`——规则写了不等于生效，
  必须断言索引本身干净。
- 凭据扫描：这些文件中**没有**真实凭据值（`sk-`/`Bearer <token>`/
  实值 `AUTH_TOKEN=` 均 0 命中）；命中的两处是第三方 skill 文档里的占位符
  （`Authorization: Bearer <key>` 说明文字、`password='NEO4J_PASSWORD'`）。
  所以这是体积与整洁问题，不是泄密事件。

### B2 `.dockerignore` 补齐凭据类排除

原文件第 1-4 行只排除 `config.json` 等，全文无 `.env`。而 QQ 运维文档要求把
`AUTH_TOKEN` 放 `.env`——服务器上于 compose 同目录 `docker build .` 会把它
一并上传到构建上下文。现补 `.env`、`.env.*`、`!.env.example`、
`**/*password.hash`、`.memsearch`、三个分析笔记与 `.version-sync.lock`。
`test_docker_context_excludes_local_audit_and_runtime_state` 已覆盖。

### B3 `.env.example` 补齐 compose 强制变量

文档用 `${LLONEBOT1_AUTH_TOKEN:?...}` 与 `${LLONEBOT1_QQ:?...}`（`:?` = 缺失即失败），
而示例只有 `DOCKERHUB_IMAGE`，照文档部署第一次 `up -d` 必报错。
现补两组占位符并说明为何用 `:?`（空 token 等于开放无鉴权端点）。

### B4 `docker-compose.yml.example` 补齐双容器拓扑

原文件只有 `kirara-agent`，双容器拓扑仅存在于文档；且缺 `DATA_PATH`
（`docker-compose.yml:8` 有它、契约测试还断言它），两份文件自相矛盾。
现补 `llonebot` 服务、显式 `networks:`（默认 bridge 下容器名不可解析，
这是「配置看起来对但连不上」最常见的原因）、登录态挂载、PMHQ 只绑本机，
以及反向 WebSocket 地址的完整写法。
`tests/test_docker_compose_resource_storage.py` 从 1 个测试扩到 4 个，
参数化覆盖两份 compose。

### B5 运行期 SQLite 数据库已移出索引

`data/mcp/audit.db`（724 行本机 MCP 工具调用审计记录）与
`data/mcp/confirmations.db`（一条待确认令牌摘要）都在版本库里。
`.dockerignore:31-33` 早已排除 `*.db`，Git 侧却没有对应规则。
运行态数据每台机器都不同，随仓库分发既无意义又会把本地操作记录发出去。

- 处理：移出索引（磁盘文件保留），`.gitignore` 补 `*.db`/`*.sqlite`/`*.sqlite3`。
  已确认没有任何测试依赖被跟踪的 `.db` 文件（`tests/mcp/*` 全部用 `tmp_path`）。
- 防复发：`test_git_index_carries_no_runtime_databases`。

### 索引清理后的效果

`git archive` 从 46.6 MB 降到 31.7 MB，跟踪文件从 1542 降到 810。
剩余体积的 73% 是单个字体文件 `data/fonts/sarasa-mono-sc-regular.ttf`
（22.8 MB，图片渲染必需，属于正常产物）。

## 三、代码缺陷（15 项，全部实测复现后修复）

### C1 熔断器错误率分支在 `circuit_min_requests > 20` 时永久失效

`kirara_ai/llm/resilience.py:123` 的样本窗口是 `deque(maxlen=history_size)`，
默认 20 且三处构造点（`llm_manager.py:81/436/739`）都不传它；配置只校验 `ge=1`。
`_should_open` 要求 `len(outcomes) >= min_requests`，窗口装不满则永假。

实测：200 次连续失败、`failure_threshold=1000`、`min_requests=50` →
`state: closed, requests: 20, error_rate: 1.0`。100% 错误率不熔断。

修复：窗口取 `max(1, history_size, min_requests)`，不缩小用户可配置范围。
测试 `test_error_rate_still_applies_when_min_requests_exceeds_the_default_window`。

### C2 `UsageSource.ESTIMATED` 主链路无生产者

`attach_estimated_usage`（`tracing/decorator.py:36`）只在装饰器体内调用，
而 `llm_manager.py` 用 `suppress_llm_chat_tracing()` 让装饰器在 `:67-68` 短路。
主路径只调 `mark_provider_usage`，供应商不返回 usage 时 → `usage=None` →
统计页显示 0 token、0 成本的「免费请求」。

修复：`llm_manager.py:365`（同步）与 `_trace_stream_iterator` 聚合处（流式）
都补上估算并标记来源。3 项新测试，含「供应商给了 usage 就绝不覆盖」。

### C3 `llm_first_byte` 生产代码从未记录

阶段名（`im/message.py:36`）、`llm_first_byte_seconds` 列
（`im/delivery_timing_store.py:60`）、alembic 迁移与文档四处都在，
唯一写入者却是测试自己。真实部署里这两个指标永远 NULL。

修复：`_execute_model_streaming` 在收到第一个非空文本片段时记下时刻，
经新增的 `RuntimeResult.llm_first_byte_at` 与 `_execute_model(timings=...)`
出参传回，由 `dispatcher._record_model_stages` 写入时间线（记在
`llm_completed` **之前**，否则按顺序读的消费者看到倒序链路）。
非流式保持留空——拿响应到达时刻冒充首字节会把「思考 20 秒、吐字 1 秒」
记成「首字节 21 秒、生成 0 秒」，正好反过来。6 项新测试。

### C4 遗留工作流路径不落投递耗时

`workflow_started` 与落库都只在 `_dispatch_agent` 分支，未迁移到 Agent 的
部署投递耗时表始终为空。修复：遗留分支也记 `workflow_started`；
`SendIMMessage` 把入站阶段拼到真正发出的回复上，并走同一个
`DeliveryTimingStore` 落库（发送失败也落——`send_failed` 同样是证据）。
新测试文件 `test_legacy_workflow_delivery_timings.py`（4 项），
含「落库抛错不得影响发送」与「未注册 dispatcher 时静默跳过」。

### C5 货币金额被当成数学公式

实测 `render_plain_text("price $5 and $7 total")` → `'price 5 and 7 total'`。
`_MATH_PATTERN` 只看 `$` 是否配对。修复：新增 `_looks_like_math`，
要求 `$...$` 内出现反斜杠命令、上下标或分式才按公式处理。

### C6 定界符外的裸 LaTeX 命令不处理

实测 `"speed \to 0 now"` 原样返回。修复：`_clean_latex` 对非围栏正文也扫一遍
`_COMMAND_PATTERN`（未知命令保留原样而不是退化成裸单词）；
命令表从 32 条扩到 70 条（`\int \nabla \partial \theta \forall \in \equiv` 等）；
`\begin{}/\end{}` 环境包裹整体剥离（不再产生 `begincases`）；
`\left/\right` 只去命令留符号（用 `(?![A-Za-z])` 避免吃掉 `\rightarrow` 前缀）；
嵌套 `\frac` 反复求解。围栏代码块完全不受影响，有测试钉住。

### C7 宽表无阈值降级

实测 8 列中文表每行 97 显示列。修复：新增
`MAX_TABLE_DISPLAY_WIDTH = 60`、`box_table_display_width`、
`render_field_table`、`render_table`；超宽时逐行输出「字段：值」分组，
无表头分隔行时退化为 `· 值` 列表但绝不丢内容。窄表仍走框线表。4 项新测试。

### C7b WeCom 路径跳过数学降级（同一缺陷的平台侧残留）

`markdown_to_plain_text` 有自己的标题/强调/列表/表格规则，但从不调用
`_clean_latex`。于是同一段模型回复在 QQ 上是 `T → 0`、在企业微信上是原始的
`$T \to 0$`。需求 19.1 明确要求平台差异只体现在渲染层。

修复：把降级那一半导出为公开的 `degrade_math(text)` 供 WeCom 复用
（代码块占位符先行摘出，因此围栏内的 LaTeX 字面量不受影响）；
WeCom 的 `_render_table` 也改走共享 `render_table`，同时获得宽表降级。
4 项新测试（数学降级、围栏保护、宽表降级、窄表观感不变）。

### C8 分页切坏 Markdown 标记

实测 `*` + 60×`a` + `*` 在 40 字节上限下被劈成 4 页，首页尾部与末页开头
各留一个孤立 `*`。修复：新增 `_ATOMIC_SPAN_PATTERNS`（成对强调、行内代码、
链接）与 `_STRUCTURAL_LINE_PATTERN`（标题、列表项、有序项、引用、表格行行首），
切点优先落在结构起点，且不落在成对标记内部。4 项新测试，含「不得吞字」。

### C9 超长回复整条丢失

实测 `split_structured_text("x"*10000, 30, max_pages=2)` 抛 `ValueError`，
而 `im_onebot_adapter/adapter.py` 的 `_render_message_batches` 不捕获它。
修复：新增 `paginate_with_truncation_notice`（二分收缩到预算内 + 追加
「内容过长，已截断」提示）与 `paginate_onebot_text_or_truncate`；
发送路径改用后者，严格版本保留给需要它的调用方。
上限本身非法仍然抛出——那是配置错误，不该静默降级。6 项新测试。

### C10 OneBot 缺自身消息回声过滤

`grep 'post_type\|message_sent'` 在适配器里 0 命中。上游开启
`reportSelfMessage` 时机器人会回复自己，而入站去重收据挡不住——回声的
`message_id` 与入站消息不同。修复：新增 `_is_self_originated`，
按 `post_type == "message_sent"` 或 `user_id == self_id` 在**去重之前**丢弃
（否则会白白消耗一条收据）。

### C11 `mface` / `forward` 段被丢成空消息

映射表只覆盖 9 种段。市场表情与合并转发到达时元素列表为空。
修复：`mface` 有图按图片、无图回落 summary 占位（绝不返回 `None`）；
`forward` 给出可见占位；另补 `dice`/`rps`/`shake`。适配器侧对 `mface`
的下载失败也回落到文本，而不是像纯媒体段那样整段跳过。
新测试文件 `test_inbound_segments.py`（12 项）。

### C12 `access_token` 查询参数形式被误判

`_classify_access_token` 的 docstring 声称支持 `?access_token=`，实现只读请求头。
LLOneBot 与 NapCat 都允许查询串认证，这类连接被记成 `access_token_missing`
而 aiocqhttp 实际放行——健康面板给出的原因码与真实情况相反。
修复：同时读 `scope["query_string"]`，请求头优先；畸形查询串按「未提供」
处理而不是抛错。5 项新测试。

### C13 限流类 `ActionFailed` 被直接判死

`ActionFailed` 混装两类失败：参数错误重试无用，限流等一会儿就好。
此前全部 `dead_letter`，一次群内限流永久丢掉一页回复。
修复：新增 `RETRYABLE_ACTION_RETCODES = {1200, 1400, 429, 503}` 与
`_is_retryable_action_failure`（`retcode` 是会抛 `KeyError` 的 property，
必须包住取值）；瞬态类走有限重试，仍受 `max_attempts` 与退避上限约束。
3 项新测试。

### C14 `CodeNode.vue` 宽度双份真值

CSS 写死 200/300px，`useLayout.ts:16-17` 另有同名常量。`CustomNode` 早已
改为内联绑定，代码节点遗漏。修复：同样 import 常量并 `:style` 绑定，
CSS 只留回退值并注明必须一致。新增
`webui/tests/workflow-node-width-source.test.ts`（5 项），
断言 CSS 回退值与常量数值**相等**（不只是「不存在硬编码」）。

## 四、文档同步（需求 15）

| 文件 | 改动 |
| --- | --- |
| `CHANGELOG.md` | 未发布节新增 15 条 Fixed，每条写明原因与后果 |
| `README.md` | 部署章节补双容器 compose、两个必填 `.env` 变量与 QQ 运维文档链接 |
| `docs/QQ_ONEBOT_OPERATIONS.md` | readiness 名订正为 `im_available`；目录清单补宿主机路径与权限两列 + uid 排错命令；新增升级兼容策略；compose 改为引用 `.example` 并去掉厂商账号与版本号硬编码；VNC 端口说明订正（参考镜像不暴露 VNC，5900 是原始 VNC、noVNC 是 6080）；补反向 WebSocket 地址填法 |
| `docs/OBSERVABILITY.md` | `usage_source` 三态说明（含「估算值不是账单依据」）；首字节需开流式的前提；两条投递路径都落库 |
| `docs/QQ_ONEBOT_OPERATIONS.md` 第六节 | 新增「二维码七项诊断信息各自在哪」对照表，明确写出这是归属划分而非 Kirara 已实现，并区分「二维码过期」与「凭据被拒」两层问题 |
| `docs/QQ_ONEBOT_OPERATIONS.md` 第七节 | 首字节需开流式的前提；限流 retcode 排查提示；两条投递路径口径一致 |
| `.gitignore` / `.dockerignore` / `.env.example` / `docker-compose.yml.example` | 见第二节 |

## 五、需求 18.4：扫码登录生命周期（已实现，非「归属外部」）

先前一轮我把这条判为「外部实现，文档写明归属」。那是对「Kirara 不生成二维码」
这一事实的正确陈述，但**不是需求要的东西**：18.4 明确要求把有效期、生成时间、
当前状态、刷新动作、失败原因、最新二维码路径作为**字段**给出，并要求旧码过期后
自动失效。日志目录本来就已经挂到宿主机，让 Kirara 读它没有任何架构障碍。

实现：`kirara_ai/im/qr_login.py`（`QRLoginSnapshot` + `parse_qr_login_log`），
日志行形态取自附件里真实的 PMHQ 输出，不是凭空编的。

| 需求字段 | 快照字段 |
|---|---|
| 有效期 | `validity_seconds`（`expireTime=`，实测 120） |
| 生成时间 | `generated_at` |
| 当前状态 | `state`（9 种） |
| 刷新动作 | `refresh_count` |
| 失败原因 | `failure_reason`（4 个稳定码） |
| 最新二维码路径 | `latest_qr_path` |
| 旧码自动失效 | `expires_at` + `remaining_seconds`，过期由时钟判定 |

三条设计约束及其理由：

1. **过期由时钟判定，不等上游日志。** 等上游打出「已过期」才改状态，会在这段
   等待里一直把死码显示成有效——这正是「二维码总是过期」的根因。
   测试钉住 119 秒有效 / 121 秒过期。
2. **`unavailable` 与 `failed` 严格分开。** 前者是启动期噪声，后者是真失败；
   混成一个「出错」会让人在正常启动过程里白等或白重启。
3. **零账号标识。** 日志含 uin、uid、昵称与头像地址，快照里一个都没有。
   有专门测试断言这一点，且**夹具本身改用合成标识**——把真实 uin 写进测试
   来证明「不泄露」本身就违反了「私有数据不入源码」。

接入面：`AdapterHealthSnapshot.qr_login` → IM 适配器接口 + readiness
（`qr_*` 证据字段与独立的「去扫码」处置）→ WebUI 独立标签（不与连接状态合并，
因为 `waiting` 与 `waiting_scan` 的处置相反）。
`qr_login_log_path` 显式开启，只读尾部 256 KB，读取失败只丢这一项。

测试：`tests/im/test_qr_login.py`（17）、
`tests/plugins/im_onebot_adapter/test_connection_states.py` 新增 5 项、
`tests/web/api/system/test_readiness_im_states.py` 新增 4 项、
`webui/tests/im-qr-login-status.test.ts`（9）。

## 六、需求 11：参考项目对照后的三处补齐

子代理对 B 项目与 LuckyLilliaBot 的对照给出过具体缺口，本轮补完：

- **`_handle_notice` 曾是 `return None`**：被踢出群、被禁言这类会直接导致
  「机器人不回话」的事件完全无声，排查时只看到发送失败、看不到原因。
  现按类型记录；影响本账号可用性的 `group_decrease` / `group_ban` 升为 warning。
  仍不派发进工作流——它们不是消息，硬塞进去会让每次群成员变动都跑一遍模型。
- **`on_request` 根本没有订阅**：好友申请与入群邀请到达时零记录。现补上并记录，
  **不自动同意**：自动接受入群邀请是安全决定，不该由框架代替部署者做。
- **`set_chat_editing_state` 谎称「OneBot 不支持输入状态」**：LLOneBot 与 NapCat
  都实现了 `set_input_status`。现尝试调用并容错降级，群聊直接跳过。

测试：`tests/plugins/im_onebot_adapter/test_notice_and_request.py`（22）。

### 独立复核结论（只读子代理，2026-08-28 二次核验）

需求 11 的「完全覆盖 A + B」这一结论由一个只读子代理重新核验，不采信先前的口头结论：

- **B 项目（28 文件，其中 10 个 Python）全部吸收**。模块、类、公开方法、配置字段、
  8 个 OneBot API 动作、3 个事件订阅逐项有对应实现；若干重命名
  （`_check_heartbeats`→`_monitor_heartbeats`、`_handle_msg`→`_handle_message`、
  `convert_to_message_segment`→`_to_segment`+`_send_segments`）。
  B 的 quart mount hack 与 `_websocket_reconnect_monitor` 被 ASGI `asgi()` +
  幂等且校验过的挂载取代，属设计替换而非丢失。
- **先前记为「import 破坏」的三个类实为 B 内部死代码**，不是能力缺失：
  `OperationEvent` 在 B 里无人消费（只被自己的 `__all__` 引用）；
  `OperationType` 只被 `MessageResult.operation_type` 读取，从未被有意义地写入；
  `MessageResult` 虽被 B 的 `send_message` 返回，但调用它的 dispatcher 丢弃返回值。
  目标返回 `None` 并通过持久化投递记录 + `record_delivery_stage` 上报结果，
  可观测性严格更强。此前「对第三方是 import 破坏」的表述夸大了影响。
- **A 项目零回归**，六个维度分别核对：Python 文件 238→287（仅 A 有：0）、
  AST 符号 1270→1794（仅 A 有：1，且是嵌套闭包而非公开 API，行为已由 outbox
  路径承接）、Web 路由 81→137（0）、注册表登记 94→105（0）、
  WebUI 文件 133→169（0）、路由路径 29→34（0）。
  MCP 配置由扁平改为嵌套属于有意重塑：旧字段以读写 property 保留、
  带 `migrate_legacy_shape` 校验器与 API 层 payload 迁移，未丢字段。

同一次复核发现一处此前遗漏的缺陷（见上方 CHANGELOG 的「管理动作丢弃了目标账号
解析结果」），已修复并补测。

## 七、外部实机项（本机无法产出证据，不计入完成）

1. **Compose 重启免扫码** —— 需要 Docker 主机与已登录的 `./QQ` 卷。
   代码侧有七态连接、目录写入探测、出站幂等与入站去重的测试覆盖。
2. **真机扫码 / PMHQ 注入 / QQ 热更新时序** —— 需要真实手机 QQ。
   二维码由 LLOneBot 产生，不经过 Kirara（全仓库 `qr`/`pmhq`/`quick_login`
   0 命中），文档已写明归属边界。
3. **多 Provider 真实故障转移与熔断触发** —— 需要多个真实上游与真实故障。
   队列顺序、错误分类、三态熔断、跨重启持久化均有测试。
4. **四渠道客户端渲染观感** —— 需要真实 QQ / Telegram / WeCom 客户端。
   排版、分页、页码、代码隔离、宽表降级均有测试。

## 八、发布判断

当前为**可提交、未发布**。本地门禁全部通过；发布动作（Tag / push /
GitHub Release / Docker 镜像）按需求 24.4-24.5 需在上述四项实机门禁
产出证据并经明确确认后单独执行。

需求 13 的「先调研、方案确认后再改代码」这道闸门本轮**未按原样执行**：
用户在方案提交后明确要求「不用给清单，直接按你觉得最优的方式修改代码，
中间不能停止」，因此改为「先出方案 → 用户放行 → 连续实现」。
GitHub / 官方文档调研（OneBot 重连约定、LiteLLM / Portkey / OpenRouter
故障转移默认值、Langfuse 定价版本化、Vue Flow 布局）在方案阶段已完成，
结论落在第一版方案文档里；如需重新出一份独立调研报告，需单独提出。
