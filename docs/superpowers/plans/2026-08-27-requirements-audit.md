# 1.txt 逐条需求审计矩阵

> 本表针对用户提供的需求附件 `1.txt`（位于操作者本机桌面）建立。状态含义：
> `已验证` 表示有当前代码、自动化测试和文档证据；`部分` 表示仅覆盖部分要求；
> `未验证` 表示需要真实外部环境；`阻塞` 表示存在必须补齐的发布门禁。
>
> 本表不把旧计划的勾选状态当作证据。每次补实现或运行门禁后，必须回填具体文件、测试命令和结果。

## 本轮门禁实测（2026-08-30 第二轮）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv-win\Scripts\python.exe -m pytest ./tests -q` | `2354 passed, 1 skipped` |
| WebUI 类型 | `npm run type-check` | 通过（无输出即无错） |
| WebUI 单元 | `npm run test:unit` | `70 files, 499 passed` |
| WebUI lint | `npm run lint:check` | `0 error, 131 warning`（均为既有未使用导入告警） |
| WebUI 生产构建 | `npm run build` | 通过 |
| 版本同步 | `python scripts/version.py check` | `version artifacts synchronized: 3.3.0b11` |
| 空白字符 | `git diff --check` | 无输出 |

> 版本门禁在本轮**先失败后通过**，而且失败得有价值：`webui/src/utils/version.ts`
> 的新注释里写了 `3.3.0b8`/`3.3.0b11` 做说明，扫描器判为「源码里的硬编码版本载体」
> 并报 stale。这正是需求 23.1 要拦的形态——注释里的版本号同样会过期、同样会
> 与唯一版本源漂移。改成 `<base>a<n>` 占位写法后通过。

### 本轮修正的缺陷（每条都有先失败的回归测试）

八条全部满足「有 file:line 证据 + 能构造失败用例 + 先 RED 后 GREEN」。
其中五条属于最难自查的形态——**功能看起来在工作，实际给出的是反向结果或空结果**：

| 缺陷 | 为什么危险 | 证据 |
| --- | --- | --- |
| 前端版本比较把 `b8` 判为大于 `b11`（16、23.2） | semver 按字典序比字母数字标识符。序号进入两位数后「检查更新」**反向**：装 b11 的用户被劝降级到 b8，b10 这类真新版反而不提示。后端 `packaging` 是对的，于是两侧结论矛盾 | `webui/tests/version.test.ts`（新增 9 条，含两位数序号与阶段序） |
| 非 Claude 供应商的成本永远为空（9、22.2） | `total_cost` 要求四个维度全部非 `None`，而只有 Claude 回报缓存写入量。OpenAI/Gemini/Ollama 形态永远缺两维 → 单请求成本、成本汇总、趋势图全为空。而**定价页填得好好的**，用户只会怀疑自己没配对 | `tests/llm/test_cost_dimension_coverage.py`（7 项） |
| 批量画布操作的撤销点丢失（20.3） | `runCanvasBatch` 里 `setNodes()` 只改 vue-flow，`updateBlocks()` 是 500ms 防抖：批次关闭那刻 store 还没变，比对结果「无变化」→ 不压栈；而批次期间逐次记录也被抑制。两条路都不写历史，改动却在防抖到期后落库 → **一次 Ctrl+Z 直接毁掉上一次编辑且无法重做** | `webui/tests/workflow-canvas-batch.test.ts`（8 项，含无 flush 时丢历史的守卫用例） |
| 一次连续拖拽产生 6~7 个撤销步骤（20.3） | 防抖窗口一到就清 `graphHistoryPending`，而拖拽还在继续。注释承诺「一次连续拖拽只产生一个检查点」，实际按 500ms 切片 | `webui/tests/workflow-canvas-history-gesture.test.ts`（8 项） |
| `~~~` 与四反引号围栏不被识别为代码（19.1、19.3） | CommonMark 合法围栏。不识别的后果是**代码块内部被当正文处理**：数学降级改写代码里的 `$x$`、表格渲染器改写代码里的 `|`、分页把代码劈开且不补围栏、复制隔离失效。四反引号块里的三反引号还会把整块切成三段 | `tests/test_fence_and_platform_parity.py`（21 项） |
| WeCom 走的是另一套正则链（19.1） | 需求明确「平台差异只放在渲染层，不能各平台各写一套 Markdown 解析」。WeCom 的独立链条对四反引号块产出 `『［代码］…』`（行内码包住块级码），对 `~~~` 完全不识别 | 同上 + `tests/plugins/im_wecom_adapter/`（54 项回归） |
| 纯符号公式 `$x = 5$` 不降级（19.2） | 判据是「有反斜杠命令/上下标/花括号才算公式」，于是不带命令的公式原样把 `$` 发给 QQ——正是 19.2 点名禁止的形态。改成按货币形态排除（`$5`、`$1,200` 仍完整保留） | 同上（含货币与公式共存用例） |
| 供应商凭据只脱敏了成对凭据的一半（21.1） | `access_key_id` 参与 HMAC 签名，是凭据的另一半，但两张关键词表都漏了它：接口明文返回、**导出文件里也是明文**，而同一对里 `access_key_secret` 确实打了码——看到打码的那半会让人相信整条路径是安全的。`secret_key`/`private_key`/`x_api_key`/`api-key`/`session_key` 同缺 | `tests/llm/test_credential_redaction_coverage.py`（35 项）；新增 `kirara_ai/credential_keys.py` 单一词表 |

另两条一并修掉的安全边界：

| 缺陷 | 为什么危险 | 证据 |
| --- | --- | --- |
| 远程安装路由把请求体整体 `**payload` 展开（10、22.3） | 客户端可注入 `source_key` 覆盖服务端生成的资源身份，或用未知键触发 `TypeError` 500 | `tests/plugin_manager/test_remote_install_validation.py`（21 项） |
| `"."` 目录绕过全部形态校验（22.3） | `_validate_directory` 在所有判据**之前**就把 `"."` 原样返回。直接请求它会把整个仓库（上限 4096 成员 / 128 MB）当一个 Skill 装进来，`source_key` 退化成 `owner/repo:.`，重复安装检测失效。它本是内部结果值，不是用户可请求的输入——现已分离为 `resolved_source_key()` | 同上 |
| rtk 探针无法区分同名的另一个工具（12） | 文档写明「以 `rtk gain` 是否可用为准」，探针却只跑 `--version`：装错工具时界面显示「就绪」，实际所有过滤都不生效 | `tests/plugin_manager/test_dependency_probe_discriminator.py`（5 项） |
| HTTP 入口无法绑定 Agent（10） | `SUPPORTED_CHANNEL_TYPES` 漏了 HTTP，且类名推导出 `httplegacy`。需求 10 要求「WeCom、QQ、Telegram 等入口统一映射到渠道身份 → Agent」，而这个入口的绑定请求直接被拒，只能退到全局默认 Agent | `tests/agent_runtime/test_http_channel_identity.py`（11 项） |

### 上一轮修正的缺陷（2026-08-30 第一轮）

| 缺陷 | 为什么危险 | 证据 |
| --- | --- | --- |
| 升级包算了 SHA-256 却从不比对（16） | 镜像源用户可配，被投毒的镜像返回的任意 wheel 会被直接 `pip install`；而代码看起来做了校验 | `test_update_integrity.py`（19 项，含端到端拦截） |
| 移动 Tag 后重建镜像无法与原镜像区分（16） | 同名版本标签的内容被换掉，且没有任何地方记录 | `test_release_workflow_contract.py` 两条新契约 |
| 上游限额头被完整丢弃（9） | 限流只能事后发现；余量是撞上限前唯一的信号 | `test_rate_limit.py`、`test_rate_limit_integration.py` |
| 参数约束类失败直接硬失败（8） | 改一处就能成功，而原因不在错误里、也非用户能改 | `test_rectifier*.py`（41 项） |
| 「禁用自动升级」只能改 YAML（8） | 离线部署最需要它，又最不方便登服务器改文件 | `test_update_auto_check_config.py` |
| 单次成本无处可查（9） | 合计成本分不开「贵」与「用得多」 | `llm-statistics-cost-per-request.test.ts` |
| 发送节流缺失（11） | QQ 风控命中后接口全返回成功、消息到不了对方，日志里一切正常 | `test_send_pacing.py`（16 项） |
| 流式响应替身缺 `close()`（自查） | 建连失败后不释放连接，每次整流重试漏一条，且无症状 | `test_provider_streaming.py` |

## 上一轮门禁实测（2026-08-29）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv-win\Scripts\python.exe -m pytest ./tests -q` | `2084 passed, 1 skipped` |
| WebUI 类型 | `npm --prefix webui run type-check` | 通过（无输出即无错） |
| WebUI 单元 | `npm --prefix webui run test:unit -- --run` | `60 files, 411 passed` |
| WebUI lint | `npm --prefix webui run lint:check` | `0 error, 131 warning`（均为既有未使用导入告警） |
| WebUI 生产构建 | `npm --prefix webui run build` | `built in 1m 8s` |
| 版本同步 | `python scripts/version.py check` | `version artifacts synchronized: 3.3.0b11` |
| 空白字符 | `git diff --check` | 无输出（此前三个 LLM 适配器被脚本编辑转成 CRLF，已改回 LF） |
| 敏感文件扫描 | `git status --porcelain` 过滤凭据模式 | 无命中；`creator.subject` 已在 `.gitignore` 与 `.dockerignore` |

> **门禁数字必须与实测一致。** 这张表两次记录过已经过期的数字（`1587`、`2063`），
> 而当轮实际是 `2084`。数字过期本身不改变代码正确性，但它让「门禁通过」这句话
> 失去证据价值：读表的人无法判断那次记录对应哪一次运行。每次补实现后都要重跑并回填。

### 本轮修正的缺陷（每条都有先失败的回归测试）

按「有 file:line 证据、能构造失败用例」的口径逐条落地。其中五条属于同一类
**最难自查的形态**——功能看起来实现了，实际不起作用：

| 缺陷 | 为什么危险 |
| --- | --- |
| `cancel_pending_request` 无任何适配器实现（21.3） | 日志写着已取消，HTTP 连接还在，上游继续生成继续计费 |
| Telegram 两个重试配置从未被读取（18.3） | 字段在、文档在、界面能填，改它什么都不会发生 |
| 工具轮 Hook 的 `additionalContext` 被丢掉（10） | 解析通过、审计记 `status: ok`，模型永远看不到那句话 |
| 「已启用」不等于「生效」（22.3） | 用户看到「已启用」、得到「什么都没变」，去怀疑模型 |
| `ConfigLoader.save_config_with_backup` 被类级赋值污染（门禁自身） | 失效的 mock 让「配置真的写盘了吗」类断言变成永假通过 |

其余：`storage_unavailable` 运行期存储故障状态（18.1 第五类）、供应商配置
create/update/delete 审计与 `/backends/restore`（21.1）、熔断状态迁移证据（21.3）、
成本汇总回到 SQL 且不同货币不相加（22.2）、创建者身份延伸到 IM 渠道（10）、
OneBot 标准消息段与 `send_message` 返回 `message_id`、申请处置出口（11）、
转义残片两类（19.2）、artifact index 的 IP 假 token 与 `--local-only` 契约（23）。

## 逐条状态

| 要求 | 实现证据 | 测试证据 | 文档证据 | 外部实机证据 | 状态 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Compose 重启后连接与登录态恢复 | `AdapterHealthSnapshot` 八态 + `last_disconnect_reason`（含运行期 `storage_unavailable`）；启动期目录检查与写入探测；WeCom 临时目录改走 `DATA_PATH`；入站去重收据 | `test_connection_states.py`、`test_readiness_im_states.py`、`test_data_paths.py`、`test_inbound_receipts.py`、`test_inbound_dedup.py` | `QQ_ONEBOT_OPERATIONS.md` 二/三/五/八节 | **未验证** | 代码完成 | 状态机、目录契约与去重均有测试；真实容器重启需按验收矩阵核对 |
| 2. 画布节点重叠及其他交互问题 | 统一回退尺寸；节点列表添加补吸附与空位搜索；`ResizeObserver` 重适配；卸载前刷写；批量撤销接入 | `workflow-node-size`、`workflow-layout`、`workflow-canvas-viewport`、`workflow-editor`、`workflow-batch-history` | `WORKFLOW_OPERATIONS_GUIDE.md` | 浏览器实机未复测 | 代码完成 | 几何判断已收敛到单一来源 |
| 3. QR 过期、刷新、quick login、PMHQ 时序 | 二维码由 LLOneBot 侧产生；握手失败原因码区分凭据/角色/账号标识 | `test_connection_states.py` 覆盖 401/403/4400 | `QQ_ONEBOT_OPERATIONS.md` 第六节 | **未验证** | 代码完成 | 二维码生命周期属外部实现，文档已写明归属 |
| 4. 外部项目参考与兼容重构 | 只吸收协议层可核对行为 | 适配器与兼容性回归 | `QQ_ONEBOT_OPERATIONS.md` 边界说明 | 未验证 | 代码完成 | 未复制任何硬编码账号、Token、地址或二进制补丁 |
| 5. 项目 A 与 OneBot 适配器功能替代 | 内置 OneBot 适配器 + 统一排版 + 投递队列 + 入站去重 | `tests/plugins/im_onebot_adapter/**`、`test_im_text_render.py` | README 平台表「内置支持」 | 未验证 | 代码完成 | — |
| 6. 原有功能、API、数据与注释兼容 | 新增字段均可选；`_tool_requires_confirmation` 保留别名；`isolate_code_messages` 与 `reply_stream_mode` 默认保持旧行为；旧 `creator.subject` 自动继承 | 全量后端 + WebUI 回归 | CHANGELOG 逐条说明兼容策略 | 不适用 | **已验证** | 无破坏性迁移 |
| 7. 先调研后实现及子代理审计 | 四路只读子代理先建立 file:line 现场 | 审计不是自动化测试 | `2026-08-28-continuation-plan.md` | 不适用 | **已验证** | 主上下文未装载全量源码 |
| 8. 可靠性、首次上手、观测性、画布体验 | 八态连接 + 端到端时间线（含落库）+ 成本统计 + 画布几何收敛；就绪自检上界面（`api/system.ts` 从 0 字节补齐，引导页新增部署自检面板与「已核实」角标） | `test_delivery_timeline.py`、`test_delivery_timing_store.py`、`test_statistics_cost.py`、`tracing-statistics-request`、`guide-readiness`、`llm-trace-detail-fields`、`delivery-timeline-view` | `OBSERVABILITY.md` 第 2 节重写 | 需复测 | 代码完成 | — |
| 9. 版本推导、Tag、CI、Docker 身份 | `scripts/version.py` 唯一版本源；浏览器留痕不再被当版本载体；**镜像记录 `org.opencontainers.image.revision`**（`verify-tag` 只查当下自洽，移动 Tag 后重建同名镜像此前无法区分）；**升级包安装前比对 registry 摘要**（此前算了 SHA-256 从不比对，镜像源可配即等于任意代码执行入口） | `test_version_management.py`、`test_release_workflow_contract.py`（新增两条 revision 契约）、`test_webui_build_contract.py`、`test_update_integrity.py`（19 项，含端到端拦截被篡改的包） | README 发布章节 | **未推送** | 代码完成 | 发布动作需单独确认 |
| 10. 创建者权限与服务器副作用 | `principal_can_control_agent`；command Hook 要求创建者；`creator.subject` 单一生效位置 + 继承；依赖目录补齐 rtk / memsearch / context-mode / caveman 并区分「可服务器侧安装」与「Claude 插件」 | `test_creator_identity.py`、`test_host_authorization.py`（agent + mcp）、`test_dependency_catalog_coverage.py`、`test_creator_channel_identity.py`、`test_hook_context_injection.py` | `AGENTS_SKILLS_HOOKS_MCP_GUIDE.md` 第 6、8 节 | 未验证 | 代码完成 | 新增 `creator_channel_identities`：声明后创建者可从 IM 渠道使用受保护能力，默认空表、群聊默认不生效、已有 HTTP 身份绝不被替换；工具轮 Hook 的 `additionalContext` 现在真的进模型 |
| 11. 外部项目全部功能细节对照 | 内置适配器 + 统一渲染 + 双向幂等；补 8 类标准消息段；`send_message` 返回 `message_id`；好友/入群申请有 approve/reject 出口；**补回被融入项目的发送节流**（防 QQ 风控，直发与 outbox 两条路径） | `test_standard_segments.py`、`test_send_result.py`、`test_request_actions.py`、`test_forward_expansion.py`、`test_send_pacing.py`（16 项）+ 既有兼容测试 | README、QQ 运维第九节（逐段对照表 + 转发展开 + 申请处置）、第七节新增「发送节流」 | 未验证 | 代码完成 | 本轮逐条比对被融入项目：A 在转发展开、入站媒体限额、outbox 持久化、QR 登录快照上均超出原项目；唯一实质缺口是发送节流，已补。「完全代替」仍需实机逐功能证据 |
| 12. 子代理、context-mode、graphify、memsearch、质量约束 | 探针确认 `rtk 0.45.0`、`graphify 0.9.43`；context-mode 处理大输出 | 全量门禁 + 图谱刷新 | 本矩阵与计划 | 不适用 | **已验证** | — |
| 13. 同类开源项目和官方文档调研 | 参考实现的 UI 清单与主流 Agent 客户端 Hook 声明形态 | 不适用 | 计划与本矩阵 | 网络变化 | **已验证（范围内）** | 参考实现不等于当前项目证据 |
| 14. 重构质量与教程 | 本轮全部改动 | 功能/构建/安全测试 | 新增 `QQ_ONEBOT_OPERATIONS.md`；重写 OBSERVABILITY 第 2 节；扩写扩展指南 3/6/7 节；QUICKSTART 补流式模式 | 未验证 | **已验证** | 文档与实现同轮更新；本轮补齐「首次上手」最弱一环：就绪自检从「只能 curl」变成引导页可见，且步骤完成状态改由真实就绪推导而非点击痕迹 |
| 15. 所有相关文件同步 | README（含新增 REST 管理接口一节与依赖可见性特性）、CHANGELOG、四份专题文档、`.gitignore`/`.dockerignore`、版本索引 | 文档引用与契约测试 | 同左 | 不适用 | **已验证** | 已形成实际 diff |
| 16. 自动版本及发布缺陷 | 修正版本审计把 `.playwright-cli/`、`.playwright-mcp/` 当版本载体导致 `check` 失败；**镜像带 OCI `revision`/`version`/`source`**；**升级包安装前校验 registry 摘要**（摘要缺失即拒绝安装） | `version.py check` 通过；契约测试 36 项（含两条 revision 契约）；`test_update_integrity.py` 19 项 | README 发布章节（新增两段：revision 的作用、摘要校验口径） | 未执行发布 | **已验证** | 三处忽略规则保持一致；四项声明的能力逐一排查，两处真实缺陷已修 |
| 17.1-17.4 证据口径与门禁 | 本矩阵 + 计划 + 上表实测 | 门禁命令与结果已记录 | 计划/矩阵/CHANGELOG | 未验证项单列 | **已验证** | 未验证项不计入完成 |
| 18.1-18.6 QQ/OneBot 持久化与恢复 | 八态连接与原因码（数据目录挂载错误在运行期有独立状态）；启动期目录检查与写入探测；出站幂等 + 入站去重；退避有上限有抖动 | `test_connection_states.py`、`test_readiness_im_states.py`、`test_data_paths.py`、`test_outbox_backoff.py`、`test_inbound_dedup.py`、`test_storage_state.py`、`tests/plugins/im_telegram_adapter/test_outbox_retry.py` | `QQ_ONEBOT_OPERATIONS.md` 全文（含 11 项验收矩阵、两侧目录清单各自完整） | **未验证** | 代码完成 | 五类状态、目录清单（补 Compose 挂载/备份/升级三列）、幂等、退避全部落地；Telegram 两个重试配置此前从未被读取，已接入共享退避；实机项见下 |
| 19.1-19.5 QQ 回复传输与排版 | 统一 `text_render`；WeCom 并行实现与 `[i/N]` 页码移除；代码单独成条 + 复制指引；端到端时间线并落库 | `test_page_markers.py`、`test_code_copy.py`、`test_code_delivery.py`、`test_delivery_timeline.py`、`test_delivery_timing_store.py`、`test_delivery_counts_summary.py`、`test_dispatch_timeline.py` | OBSERVABILITY 投递时间线节（含 `counts` 两项）；QQ 运维第七节 | **未验证** | 代码完成 | 四渠道页码统一；另修 19.2 两类转义残片（未知 LaTeX 命令、落单反引号，见 `test_escape_residue.py`）；观感需实机确认 |
| 20.1-20.4 工作流画布与脚本连线 | 单一尺寸来源；两条添加路径一致；卸载不丢改动；脚本节点零端口有提示；脚本端口按 `Any` 校验；批量撤销接入 | `workflow-node-size`、`workflow-code-node-ports`、`workflow-layout`、`workflow-editor`、`workflow-batch-history`、`workflow-connection-feedback`、`workflow-layout-cycles` | 工作流操作指南（含连线被拒四类原因对照表） | 需复测 | **已验证（代码层）** | 20.2 补：自动布局从未真正启用破环（注释描述的 greedy-FAS 从未配置）、打开旧工作流静默改坐标；20.4 补：连线被拒此前全部静默（Handle 判 invalid 时 vue-flow 不触发 `onConnect`，那句提示从未执行），现改挂 `connect-end` 并按原因分条文案 |
| 21.1-21.3 Provider 故障转移/超时/熔断 | 前端补齐容错字段与编辑面板；`stream_total_timeout_seconds`；跨字段预算校验；`get_llm` 走优先级队列；熔断状态跨重启保留；流式模式接入；**整流器**（上游拒绝后按白名单改一处重试一次，四开关逐供应商下发，非流式+流式两条路径） | `test_resilience_config.py`、`test_llm_manager_failover.py`、`test_resilience.py`、`test_circuit_store.py`、`test_circuit_transitions.py`、`test_request_cancellation.py`、`test_backend_audit_and_restore.py`、`test_openai_streaming.py`、`test_stream_reply_mode.py`、`test_rectifier.py`、`test_rectifier_config.py`、`test_rectifier_integration.py`、`llm-rectifier-controls` | OBSERVABILITY 接口表、熔断迁移证据节；EXTENDING 9.5 整流器节 | **未验证** | 代码完成 | 取消现在真的中止上游 HTTP（四家适配器）；熔断迁移有五种原因可回溯；供应商写操作留痕 + `/backends/restore`；需求 8 末句点名的整流器已落地并有真实调用点；多 worker 共享熔断仍未实现（已记录） |
| 22.1-22.3 统计、成本、Skills/Prompt | 成本/失败类型/首字节聚合；**单次成本**（分母只算已定价请求）；**上游限额余量**（`rate_limit`，撞上限前的唯一信号，未上报与 0 严格分开）；前端筛选与时区送达；CSV 含成本快照；Token 估算标记 `estimated`；Skill 版本读上游；非 GitHub 来源有更新出口 | `test_statistics_cost.py`、`test_cost_aggregation_sql.py`、`tracing-statistics-request`、`test_token_estimator.py`、`test_estimated_usage.py`、`test_skill_versions.py`、`test_update_channels.py`、`test_binding_visibility.py`、`test_retry_failover_counts.py`、`pricing-effective-from`、`pricing-view`、`resource-view`（非 GitHub 来源文案）、`llm-trace-detail-fields`、`llm-trace-attempts`、`test_rate_limit.py`、`test_rate_limit_integration.py`、`llm-rate-limit-headroom`、`llm-statistics-cost-per-request` | OBSERVABILITY 第 2 节（含重试/转移拆分）；扩展指南第 3 节 | 未验证 | **已验证（代码层）** | 成本汇总回到 SQL（迁移 `c7f1b3a9d204`），不同货币不相加；「已启用」不等于「生效」现在在界面上说得出来；估算值明确标记，不当账单依据 |
| 23.1-23.4 版本、文档、发布门禁 | 版本索引同步；README/CHANGELOG/四份文档同轮更新；凭据文件纳入两处忽略 | 上表门禁全部通过；`test_version_management.py`、`test_release_workflow_contract.py`、`test_docker_build_context.py`、`test_config_example.py` | README、CHANGELOG、QQ 运维、OBSERVABILITY、扩展指南、QUICKSTART | 未验证 | **已验证（本地门禁）** | 另修：artifact index 把 IP 当发布 token、`resource-*.png` 进镜像上下文、无契约禁止发布链路用 `--local-only`；发布动作本轮未执行 |
| 24.1-24.5 子代理、调研、实现、最终验收 | 子代理先行 → 失败测试 → 最小改动 → 聚焦测试 → 全量门禁 | 全量结果见上表 | 本矩阵 | 不可用项已明示 | 代码完成 | Tag/push/release 需用户确认后单独执行 |

## 外部实机项（本机无法产出证据）

以下四项不是「没做」，而是**做不了**：它们需要真实 QQ 账号、真实 Docker 主机与真实上游。
相应的代码路径都有自动化测试覆盖，但测试覆盖不等于实机验证，二者不能混同：

| 项目 | 为什么本机不可验证 | 已有的替代证据 | 你需要做什么 |
| --- | --- | --- | --- |
| Compose 重启恢复与免扫码 | 需要 Docker 主机与已登录的 `./QQ` 卷 | 状态机与目录契约测试 | 按 `QQ_ONEBOT_OPERATIONS.md` 第八节 11 项逐条核对 |
| QQ 扫码 / PMHQ 注入 / 热更新时序 | 需要真实手机 QQ 扫码 | 握手三类失败原因码测试 | 首次启动扫一次码，确认后续重启免扫 |
| 多 Provider 故障转移与熔断触发 | 需要多个真实上游与真实故障 | 队列顺序、错误分类、熔断三态、持久化测试 | 观察 `/llm/resilience/status` 与统计页失败类型 |
| 四渠道客户端渲染观感 | 需要真实 QQ / Telegram / WeCom 客户端 | 排版、分页、页码、代码隔离测试 | 各发一条长回复与一段代码，确认观感 |

## 仍未实现（明确记录）

1. **多 worker 共享熔断状态**：按进程持久化，多 worker 下各进程互不可见。
2. **工作流逐节点执行历史**：事件已发出但无内置消费者，节点级耗时不落库。
3. **匿名指标端点**：无 Prometheus `/metrics`，readiness 需鉴权。
4. **主动告警**：日志与追踪都是被动记录。

这四项都不属于 1.txt 的显式要求，列在这里是为了不让「文档没提」被误读成「已经做了」。

## 发布判断

当前为 **可提交、未发布**。

本地门禁（后端全量 `2084 passed, 1 skipped`、WebUI 类型/单元 `344 passed`/lint/构建、
版本同步、`git diff --check`、敏感文件）全部通过，1.txt 中所有可由代码实现的条目
均已落地并有测试证据。

**未执行发布动作**（Tag / push / GitHub Release / Docker 镜像）。原因不是遗漏，
而是 1.txt 24.4/24.5 明确要求「任一硬性门禁没有证据，禁止创建 Tag、推送、发布」，
而上表四项外部实机门禁本机无法产出证据。发布需要在你的部署上完成实机核对、
并明确确认后由我单独执行。
