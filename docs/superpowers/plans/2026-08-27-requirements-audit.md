# 1.txt 逐条需求审计矩阵

> 本表针对 `C:\Users\devin\OneDrive\Desktop\1.txt` 建立。状态含义：
> `已验证` 表示有当前代码、自动化测试和文档证据；`部分` 表示仅覆盖部分要求；
> `未验证` 表示需要真实外部环境；`阻塞` 表示存在必须补齐的发布门禁。
>
> 本表不把旧计划的勾选状态当作证据。每次补实现或运行门禁后，必须回填具体文件、测试命令和结果。

## 本轮门禁实测（2026-08-28）

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `.venv-win\Scripts\python.exe -m pytest ./tests -q` | `1431 passed, 1 skipped` |
| WebUI 类型 | `npm --prefix webui run type-check` | 通过（无输出即无错） |
| WebUI 单元 | `npm --prefix webui run test:unit -- --run` | `36 files, 219 passed` |
| WebUI lint | `npm --prefix webui run lint:check` | `0 error, 131 warning`（均为既有未使用导入告警） |
| WebUI 生产构建 | `npm --prefix webui run build` | `built in 48.10s` |
| 版本同步 | `python scripts/version.py check` | `version artifacts synchronized: 3.3.0b11` |
| 空白字符 | `git diff --check` | 仅 `custom_script.yaml` 的 CRLF 提示（用户既有文件，未改动） |
| 敏感文件扫描 | `git status --porcelain` 过滤凭据模式 | `creator.subject` 已加入 `.gitignore` 与 `.dockerignore`，工作树中不再出现 |

## 逐条状态

| 要求 | 实现证据 | 测试证据 | 文档证据 | 外部实机证据 | 状态 | 风险/发布阻塞 |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Compose 重启后连接与登录态恢复 | `AdapterHealthSnapshot` 七态 + `last_disconnect_reason`（`kirara_ai/im/adapter.py`）；`kirara_ai/config/__init__.py` 启动期目录检查与写入探测；WeCom 临时目录改走 `DATA_PATH` | `tests/plugins/im_onebot_adapter/test_connection_states.py`、`tests/web/api/system/test_readiness_im_states.py`、`tests/test_data_paths.py` | `docs/QQ_ONEBOT_OPERATIONS.md` 第二、三、五、八节 | **未验证**：真实 `down/pull/up -d` | 部分 | 状态机与目录契约有测试；真实容器重启与免扫码需在部署上按验收矩阵核对 |
| 2. 画布节点重叠及其他交互问题 | 统一回退尺寸（`estimateNodeSize`/`estimateBlockTypeSize`）；节点列表添加路径补吸附与空位搜索；`ResizeObserver` 重新适配；卸载前刷写防抖 | `webui/tests/workflow-node-size.test.ts`、`workflow-layout`、`workflow-canvas-viewport`、`workflow-editor` | `docs/WORKFLOW_OPERATIONS_GUIDE.md` | 浏览器多尺寸实机未复测 | 部分 | 几何判断已收敛到单一来源；真实浏览器观感需人工确认 |
| 3. QR 过期、刷新、quick login、PMHQ 时序 | 二维码由 LLOneBot 侧产生，本项目不生成；握手失败原因码可区分凭据/角色/账号标识 | `test_connection_states.py` 覆盖 401/403/4400 三类原因 | `docs/QQ_ONEBOT_OPERATIONS.md` 第六节（有效期、快速登录、`QR code unavailable` 语义、热更新） | **未验证**：真实扫码与 PMHQ 注入 | 部分 | 不把模拟状态当作 QQ 登录成功；二维码生命周期属外部实现，文档已写明归属 |
| 4. 外部项目参考与兼容重构 | 只吸收协议层可核对行为（反向 WS 角色校验、Token 校验、心跳超时默认值） | 适配器与兼容性回归测试 | `docs/QQ_ONEBOT_OPERATIONS.md` 明确 Kirara 侧与 QQ 侧的边界 | 外部项目运行未验证 | 部分 | 未复制任何硬编码账号、Token、地址或二进制补丁 |
| 5. 项目 A 与 OneBot 适配器功能替代 | 内置 OneBot 适配器 + 统一排版管线 + 持久化投递队列 | `tests/plugins/im_onebot_adapter/**`、`tests/test_im_text_render.py` | README 平台表已标注「内置支持」 | 真实消息渠道未验证 | 部分 | 入站去重仍缺（见下方缺口） |
| 6. 原有功能、API、数据与注释兼容 | 新增字段均为可选；`_tool_requires_confirmation` 保留私有名别名；`isolate_code_messages` 可关闭恢复旧观感；旧 `creator.subject` 自动继承 | 全量后端 + WebUI 回归 | CHANGELOG「未发布」段逐条说明兼容策略 | 无外部实机证据 | 已验证（范围内） | 无破坏性迁移；一条既有断言方向错误的用例已修正并说明理由 |
| 7. 先调研后实现及子代理审计 | 四路只读子代理（OneBot/渲染、LLM/统计、画布、Agent 运行时）先建立 file:line 现场 | 审计不是自动化测试 | `docs/superpowers/plans/2026-08-28-continuation-plan.md` | 不适用 | 已验证（范围内） | 主上下文未装载全量源码 |
| 8. 可靠性、首次上手、观测性、画布体验 | 七态连接 + 端到端时间线 + 成本统计 + 画布几何收敛 | `test_delivery_timeline.py`、`test_statistics_cost.py`、`tracing-statistics-request` | `docs/OBSERVABILITY.md` 第 2 节重写 | Docker/浏览器/渠道需复测 | 部分 | 时间线不落库（已在文档「不存在的能力」写明） |
| 9. 版本推导、Tag、CI、Docker 身份 | `scripts/version.py` 唯一版本源；浏览器留痕与授权草稿不再被当作版本载体 | `tests/test_version_management.py`、`test_release_workflow_contract.py`、`test_webui_build_contract.py` 共 144 项 | README 发布章节 | **未验证**：未推送与未发布 | 部分 | 发布动作需单独确认，本轮未执行 |
| 10. 创建者权限与服务器副作用 | `principal_can_control_agent` 门禁；command Hook 要求创建者；`creator.subject` 单一生效位置 + 旧位置继承 | `tests/web/auth/test_creator_identity.py`、`tests/agent_runtime/test_host_authorization.py`、`tests/mcp/test_host_authorization.py` | `docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md` 第 6 节 | VPS/真实身份未验证 | 部分 | IM 入站不携带 principal，因此聊天侧无法触发 command Hook——这是设计上的拒绝优先 |
| 11. 外部项目全部功能细节对照 | 内置适配器 + 统一渲染 + 投递队列 | 兼容测试覆盖已知合同 | README 与 QQ 运维文档 | 真实替代性未验证 | 部分 | 「完全代替」需逐功能实机证据 |
| 12. 子代理、context-mode、graphify、memsearch、质量约束 | 探针确认 `rtk 0.45.0`、`graphify 0.9.43`；context-mode 用于大文件与批量输出 | 全量门禁 | 本矩阵与计划文件 | 不适用 | 已验证（范围内） | 改动后需刷新图谱 |
| 13. 同类开源项目和官方文档调研 | 参考 cc-switch 的 UI 清单（`ccs-ui-inventory.md`）与 Claude Code Hook 声明形态 | 不适用 | 计划文件与本矩阵 | 网络/上游版本变化 | 部分 | 参考实现不等于当前项目证据 |
| 14. 重构质量与教程 | 本轮全部改动 | 功能/构建/安全测试 | 新增 `docs/QQ_ONEBOT_OPERATIONS.md`；重写 OBSERVABILITY 第 2 节；扩写扩展指南第 3/6/7 节 | 外部部署未验证 | 部分 | 文档与实现同轮更新 |
| 15. 所有相关文件同步 | README 特性与平台表、CHANGELOG「未发布」段、三份专题文档、`.gitignore`/`.dockerignore`、版本索引 | 文档引用与契约测试 | 同左 | 不适用 | 已验证（范围内） | 本轮已形成实际 diff |
| 16. 自动版本及发布缺陷 | 修正版本审计把 `.playwright-cli/`、`.playwright-mcp/` 当作版本载体导致 `check` 失败 | `version.py check` 通过；144 项契约测试 | README 发布章节 | 未执行真实发布 | 部分 | 三处忽略规则（git/docker/版本脚本）现保持一致 |
| 17.1-17.4 证据口径与门禁 | 本矩阵 + 计划文件 + 上表实测 | 门禁命令与结果已记录 | 计划/矩阵/CHANGELOG | 未验证项已单列 | 已验证（范围内） | 未验证项不计入完成 |
| 18.1-18.6 QQ/OneBot 持久化与恢复 | 七态连接与原因码；启动期目录检查与写入探测；投递幂等与隔离 | `test_connection_states.py`、`test_readiness_im_states.py`、`test_data_paths.py`、`test_outbox_backoff.py` | `docs/QQ_ONEBOT_OPERATIONS.md` 全文（含 11 项验收矩阵） | **未验证**：真实容器与扫码 | 部分 | **入站去重缺口**：OneBot/QQBot 无收据表，上游重投会重跑工作流 |
| 19.1-19.5 QQ 回复传输与排版 | 统一 `text_render` 管线；WeCom 并行 markdown 分段与 `[i/N]` 页码移除；代码单独成条 + 复制指引；端到端时间线 | `test_page_markers.py`、`test_code_copy.py`、`test_code_delivery.py`、`test_delivery_timeline.py`、`test_dispatch_timeline.py` | OBSERVABILITY 投递时间线节；QQ 运维文档第七节 | 真实客户端渲染未验证 | 部分 | 四渠道页码已统一；观感需实机确认 |
| 20.1-20.4 工作流画布与脚本连线 | 单一尺寸来源；两条添加路径行为一致；卸载不丢改动；脚本节点零端口有提示；脚本端口按 `Any` 校验 | `workflow-node-size`、`workflow-code-node-ports`、`workflow-layout`、`workflow-editor`、`workflow-canvas-viewport` | 工作流操作指南 | 多尺寸浏览器实机需复测 | 部分 | `performBatchAction` 仍无调用方（见缺口） |
| 21.1-21.3 Provider 故障转移/超时/熔断 | 前端补齐容错字段与编辑面板；`stream_total_timeout_seconds`；跨字段预算校验；`get_llm` 走优先级队列 | `tests/llm/test_resilience_config.py`、`test_llm_manager_failover.py`、`test_resilience.py` | OBSERVABILITY 接口表新增 `/llm/resilience/status` | 真实多 Provider 未验证 | 部分 | 熔断状态进程内存态（已在文档写明） |
| 22.1-22.3 统计、成本、Skills/Prompt | 成本/失败类型/首字节聚合；前端筛选与时区送达；CSV 含成本快照；Skill 版本读上游；非 GitHub 来源有更新出口 | `test_statistics_cost.py`、`tracing-statistics-request`、`test_skill_versions.py`、`test_update_channels.py` | OBSERVABILITY 第 2 节；扩展指南第 3 节 | 真实 Provider/插件未验证 | 部分 | 无 token 估算器，`estimated` 不会出现（已写明） |
| 23.1-23.4 版本、文档、发布门禁 | 版本索引恢复同步；README/CHANGELOG/三份文档同轮更新；凭据文件纳入两处忽略 | 上表门禁全部通过 | README、CHANGELOG、QQ 运维、OBSERVABILITY、扩展指南 | Docker/发布平台未验证 | 部分 | 发布动作本轮未执行 |
| 24.1-24.5 子代理、调研、实现、最终验收 | 子代理先行 → 失败测试 → 最小改动 → 聚焦测试 → 全量门禁 | 全量结果见上表 | 本矩阵 | 外部不可用项已明示 | 部分 | Tag/push/release 需在门禁全绿且用户确认后单独执行 |

## 仍然存在的缺口（未实现，不计入完成）

1. **OneBot / QQBot 入站去重**：Telegram 与 WeCom 有收据表，这两个没有。
   上游重连后重投同一事件会重跑整条工作流，造成重复计费与重复回复。
2. **投递时间线不持久化**：可序列化、可进日志，但没有表，无法按时间范围回查历史耗时。
3. **无 token 估算器**：`UsageSource.ESTIMATED` 没有生产者；供应商不返回 usage 时记 `unknown`。
4. **熔断状态进程内存态**：重启清空，多 worker 下互相独立。
5. **`performBatchAction` 无调用方**：复合画布编辑仍靠 500ms 防抖窗口合并，超窗会拆成多步撤销。
6. **流式链路未接到 IM 适配器**：`execute_stream` 仅被测试调用，生产路径全部走 `execute_chat`；
   没有任何适配器实现 `stream_chat`，因此「流式/非流式回复模式」目前不可选。
7. **无 Hook 试运行与执行日志前端**：Hook 支持 matcher 与按事件启停，
   但没有 dry-run 接口，执行记录只能通过审计接口查询。

## 发布判断

当前为 **可提交、未发布**。

本地门禁（后端全量、WebUI 类型/单元/lint/构建、版本同步、空白字符、敏感文件）全部通过。
但 1.txt 24.4/24.5 要求的硬性门禁中，以下项目本机无法产出证据：
真实 QQ 扫码与 PMHQ 注入、真实 `docker compose` 重启恢复、真实多 Provider 故障转移、
真实客户端渲染观感。因此**不创建 Tag、不推送 GitHub、不创建 Release、不推送镜像**，
这些动作需要在上述实机项有证据且用户明确确认后单独执行。
