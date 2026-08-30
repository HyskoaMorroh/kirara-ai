# 2026-08-28 接力实施计划（1.txt 全量收口）

> 现场基线：分支 `main`，HEAD `77ce6c7`，版本 `3.3.0b11`。
> 门禁基线（本轮开工前实测）：backend `1261 passed, 1 skipped`；webui `type-check` 通过、`187 passed`。
> 证据来源：四路只读子代理审计（OneBot/渲染、LLM/统计、画布、Agent 运行时）。
> 本文件只记录本轮要做的事与判定口径，不复述已完成任务。

## 判定口径

- 「缺陷」= 有 file:line 证据、能构造失败用例的行为错误。
- 「缺口」= 1.txt 明确要求、当前代码没有对应实现路径。
- 「未验证」= 需要真实 Docker / QQ 扫码 / 多 Provider 上游，本机无法证明。
- 每批次顺序固定：先补失败回归测试 → 最小范围改代码 → 跑聚焦测试 → 记录证据。

## 批次 A：高危正确性缺陷（数据丢失 / 无界等待）

- [x] A1 `webui/src/api/llm.ts` 的 `LLMBackend` 类型缺 `priority`、`participate_in_failover`、
      `max_retries`、`retry_backoff*`、各 `*_timeout_seconds`、各 `circuit_*` 字段。
      后端 `LLMBackendUpdateRequest` 接受这些字段，前端表单不回传，
      从 WebUI 保存一次就把调好的容错参数全部重置为默认值。
      → 补齐类型与新建默认值，模型管理页新增三组可编辑项；`llm-backend-resilience` 覆盖往返。
- [x] A2 流式请求总截止时间读的是遗留键 `request_timeout_seconds`，
      同步路径已改读 `non_stream_timeout_seconds`。只配新键的后端，流式仍按 60s 旧默认值。
      → 新增 `stream_total_timeout_seconds` 并让流式路径优先采用。
- [x] A3 `LLMManager.get_llm` 仍随机挑选后端（源码带 TODO），绕过优先级队列。
      → 复用 `get_provider_candidates`，队列为空才回退。
- [x] A4 OneBot/QQ 出站队列退避 `retry_delay * 2**(attempt-1)` 无抖动、无上限；
      `max_attempts=10` + `delay=60` 时最后一次等待约 8.5 小时。
      Telegram/WeCom 队列没有指数项，与前两者行为不一致。
      → 统一到 `kirara_ai/im/outbox_backoff.py`（指数 + 5 分钟上限 + 只提前的抖动）。
- [x] A5 WeCom 媒体临时目录用 `os.getcwd()` 拼接，绕过 `DATA_PATH`，工作目录不同即写错位置。
- [x] A6 画布两套回退节点尺寸（`useLayout` 的估算值 vs `WorkflowCanvas` 的 240×140），
      导致空位搜索、重叠告警、`focusNode` 居中在首次测量前判断错误。
      → 统一走 `estimateNodeSize` / `estimateBlockTypeSize`。
- [x] A7 节点列表面板新增节点走的是裸 `project()`，跳过网格吸附与空位搜索，
      可以正好压在既有节点上；拖拽路径有防护，点击路径没有。
- [x] A8（新发现）跨字段超时预算无校验：首字节 + 静默可超过流式总超时，
      重试退避总量可超过非流式总超时。→ 仅对显式写入的总超时做校验，旧配置仍可加载。

## 批次 B：连接状态与重启恢复（1.txt 18）

- [x] B1 `AdapterHealthSnapshot.status` 只有 connected/waiting/disconnected/stale，
      缺 18.1 要求的「容器刚启动」「凭据丢失」「上游拒绝」三态与断开原因。
      → 新增 `initializing`/`credential_rejected`/`upstream_refused` 与固定原因码，
      readiness 与适配器详情页同步；未知状态降级而不 500。
- [x] B2 OneBot/QQBot 无入站去重；Telegram/WeCom 已有 receipt 表。
      → 新增 `im_inbound_receipts`（四渠道共用）与迁移 `a4d1f8c30e57`；
      事件身份取 `self_id`+`message_id`，缺失时退回 `self_id`+`user_id`+`time`；
      无法识别时照常处理但不去重；处理失败释放收据；启动重开未完成事件。
- [x] B3 `config/__init__.py` 启动期 `os.makedirs` 无权限/空间错误处理。
      → 给出「路径 + 原因 + 处置」，并探测写入（只读挂载最常见）。
- [x] B4 数据目录清单文档缺失。→ 新增 `docs/QQ_ONEBOT_OPERATIONS.md`。

## 批次 C：QQ 回复排版与链路观测（1.txt 19）

- [x] C1 WeCom 保留一套独立 markdown 分段路径，与 `im/text_render.py` 职责重复。
      → 移除并行实现，仅保留 `code_style="wecom"` 这一渲染差异。
- [x] C2 两种页码格式并存：`第 N 页 / 共 M 页` 与 WeCom 的 `[i/N]`。→ 统一。
- [x] C3 `delivery_timeline` 只有 5 个阶段，缺 `received_event`、`workflow_start`、
      `llm_first_byte`、`llm_done`；且被 `to_dict()` 排除、不落库。
      → 阶段补全、纳入序列化、提供 `delivery_durations()`；缺测阶段不输出 0。
      并新增 `im_delivery_timings` 表与迁移 `b5e2c94a17d8`，
      配 `GET /tracing/delivery/summary`、`/recent`：日志回答「刚才那条为什么慢」，
      落库回答「上周二慢在哪一段」。表中不含任何消息正文，会话键存摘要，
      未测到的阶段存 NULL，平均值只对测到该阶段的行求平均并给出样本数。
- [x] C4 QQ 无代码复制路径。→ 代码单独成条 + 复制指引，可用配置关闭恢复旧观感。

## 批次 D：统计、成本与容错可观测（1.txt 21/22）

- [x] D1 `UsageSource.ESTIMATED` 无任何生产者，无 token 估算器。
      → 新增 `kirara_ai/llm/token_estimator.py`（脚本感知：CJK 按字符、拉丁约 4 字符 1 token）；
      供应商返回过任何 usage（含 0，那是实测值）一律不覆盖；无可测内容仍保持 `unknown`。
- [x] D2 `get_statistics` 无成本汇总、无 `error_category`/`ttft_ms`/`attempt_count` 维度；
      CSV 导出不含成本快照。→ 全部补齐，成本取请求当时快照。
- [x] D3 统计前端丢掉 `providers`、`usage_sources`，调 `/statistics` 不带筛选与时区。
      → 筛选与时区送达，补列与 CSV 导出入口。
- [x] D4 `/llm/resilience/status` 与熔断复位在前端没有入口。
      → 接口在 OBSERVABILITY 文档列出；熔断状态改为跨重启保留
      （`kirara_ai/llm/circuit_store.py`），不再因重启把刚隔离的上游当健康重试。
- [x] D5 无跨字段超时校验。→ 见 A8。

## 批次 E：画布与脚本节点语义（1.txt 20）

- [x] E1 `internal:code` 类级 `inputs/outputs` 为空，新建脚本节点无 handle 且无提示。
      → 新增 `code_node_without_ports` 可操作提示。
- [x] E2 前端按用户选的端口类型拒绝连接，后端端口实际是 `Any`。→ 按 `Any` 处理。
- [x] E3 无 `ResizeObserver` / window resize 重新适配。→ 补上，并在用户手动调整视口后让位。
- [x] E4 `performBatchAction` 无调用方。→ 画布批次统一写历史；复制多节点与
      一键整理各产生一个撤销步骤，不再受 500ms 防抖窗口限制。

## 批次 F：运行时插件边界（1.txt 10/22.3）

- [x] F1 `data/creator.subject` 与 `data/web/creator.subject` 内容不同。
      → 生效位置唯一；无生效文件时继承旧位置（升级不掉线），两者冲突时记一次日志。
      两个路径已加入 `.gitignore` 与 `.dockerignore`。
- [x] F2 远端 Skill 版本是合成的，降级保护形同虚设。→ 读取上游 `version`，
      仅在高于已装版本时采用，否则本地递增。
- [x] F3 `check_updates` 只覆盖 `provider == "github"`。→ 其余来源返回
      `update_channel_supported: false` 并说明获取新版本的方式。
- [x] F4 Hook 无按事件启停、无 matcher 过滤。→ 支持 `matcher`（正则或工具名列表，
      整名匹配）与 `enabled`；未声明 matcher 时行为不变。
      并新增 `GET /agents/hooks` 与 `POST /agents/hooks/<id>/preview`
      两个只读接口（不执行 handler、不启动进程）与对应界面，
      让「声明写错」在上线前暴露而不是在生产路径上暴露。
- [x] F5 确认判定只作为私有方法暴露给 HTTP 路由。
      → 提升为公开 `tool_requires_confirmation`，保留私有名别名。
- [x] F6 会话只有绑定接口，无列表/查看/删除/导出，待确认队列无前端。
      → 新增四个接口 + Agent 页面只读列表与清空/删除；返回值不含对话正文与工具参数。

## 批次 G：文档与发布门禁（1.txt 15/23）

- [x] G1 新增 QQ/OneBot 部署与排障文档（七类状态、目录清单、Compose 验收矩阵）。
- [x] G2 README 补 QQ/OneBot、容错、统计、Skills 章节与特性清单；无真实凭据。
- [x] G3 CHANGELOG 新增「未发布」段，逐条记录本轮改动与未验证项。
- [x] G4 回填 `2026-08-27-requirements-audit.md` 证据列与门禁实测结果。
- [x] G5 全量门禁 + `git diff --check` + 敏感信息扫描 + 版本索引同步 + graphify 刷新。

## 批次 H：流式回复模式（1.txt 4）

- [x] H1 `execute_stream` 无生产调用方，任何适配器都没有 `stream_chat`，
      因此「流式 / 非流式输出」不是一个可选项。
      → OpenAI 兼容适配器实现 `stream_chat`（SSE 解析，单个坏帧不中断整条流）；
      新增 `agent_runtime.reply_stream_mode`（`off` 默认 / `aggregate`）。
      `aggregate` 走流式请求再整段投递——**不逐字推送**，因为三个 IM 平台
      都不支持对已发出消息逐字编辑，逐字推送只会变成几十条碎片消息；
      真正的收益是首字节超时、静默超时与首字节前的故障转移开始生效。
      工具轮次始终非流式（聚合文本会丢掉 `tool_calls`）；未实现流式的适配器自动回退。

## 完成情况

1.txt 中所有可由代码实现的条目均已落地，并各自带有先失败的回归测试。
四项外部实机门禁（Compose 重启、QQ 扫码/PMHQ、多 Provider 真实故障、
客户端渲染观感）本机无法产出证据，已在
`2026-08-27-requirements-audit.md` 单列，并按 1.txt 24.4/24.5 拦住发布动作。

四项非 1.txt 要求、当前仍未实现的能力（多 worker 共享熔断、工作流逐节点历史、
匿名指标端点、主动告警）也已明确记录，避免「文档没提」被误读成「已经做了」。


## 明确的未验证项（不得声称已验证）

- 真实 `docker compose down/pull/up -d` 后的连接恢复。
- 真实 QQ 扫码、PMHQ 注入、QQ 热更新时序。
- 真实多 Provider 上游的故障转移与熔断触发。
- 真实 QQ/Telegram/WeCom 客户端上的渲染观感。

这四项不是「没做」而是「本机做不了」：需要真实 QQ 账号、真实 Docker 主机与真实上游。
相应代码路径都有自动化测试，但**测试覆盖不等于实机验证**，二者不能混同。
核对方式见 `docs/QQ_ONEBOT_OPERATIONS.md` 第八节的 11 项验收矩阵。
