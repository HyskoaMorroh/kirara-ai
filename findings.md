# 需求与证据记录

## 现场基线

- 分支：`main`
- HEAD：`87dae24 docs: 回填计划完成状态并记录交接现场`
- `origin/main` 为 `8a756bb`；本地领先 2 个提交，尚未推送。
- 工作树唯一现有变化是未跟踪文件 `docs/superpowers/plans/2026-08-23-handoff.prev.md`；不得修改、移动、删除、暂存或清理。
- `pyproject.toml` 当前为 `3.3.0b11`，WebUI 为 `3.3.0-b11`；尚未打对应 Tag 或发布。
- 已有 Tag：`v3.3.0b8`、`v3.3.0b9`、`v3.3.0b10`。

## 证据分类

| 要求 | 当前证据 | 初步结论 |
|---|---|---|
| Task 5 undo/redo/performActionWithoutHistory | WebUI store、intent 和 canvas 已有入口，聚焦测试已覆盖深拷贝、有界历史、同步/异步/嵌套抑制和异常恢复 | 已具备；剩余聚焦动态端口、边往返和批量历史 |
| Task 8 升级文档 | graphify 可定位 `docs/UPGRADING_TO_3.3.0a7.md` | 需单独检查内容与历史冻结属性 |
| OneBot 基础链路 | 有反向 WebSocket、心跳、健康状态、事件转换、发送串行化和长消息分页 | 部分具备 |
| QQ 登录态与设备身份 | 未发现当前仓库拥有 QQ 二维码登录、设备身份和登录态持久化的完整证据 | 当前缺失或需受控适配器 |
| QQ/Compose 重启恢复 | 目前只有 `./data:/app/data` 和容器级健康检查证据 | 不足以证明登录恢复和适配器 ready |
| QQ 消息排版 | 有 OneBot renderer 和分页，但 QQ 富文本能力、LaTeX、表格、代码和部分失败幂等仍需验证 | 部分具备 |
| Provider 故障转移 | 已有稳定优先级、错误分类、有界重试、总 deadline、取消传播、三态熔断和流式首片段/静默边界 | 核心已完成，等待最终全量回归 |
| Token/成本统计 | 已有 `provider` / `estimated` / `unknown` 来源、缓存读写 Token、TTFT、版本化价格和请求成本快照 | 核心已完成，控制面展示仍待同步 |
| Skills/Prompt/Session | 有 workflow catalog 元数据；未形成带 manifest、权限、安装、升级、回滚和沙箱的完整生命周期 | 不应直接添加空页面 |
| 智能版本与发布绑定 | 已有版本脚本、发布工作流和测试改动，但需验证脏树、远端冲突、离线候选、事务恢复和载体漂移 | 部分具备 |

## Task 5 / Task 8 新鲜验证

- `webui/tests/workflow-editor.test.ts` 当前覆盖 13 个用例，包含 model-level `undo`/`redo`、深拷贝快照、有界历史、无操作历史、redo 失效、同步/异步/嵌套 `performActionWithoutHistory` 和异常恢复。
- `webui` 的 `npm run test:unit -- --run tests/workflow-editor.test.ts`：13/13 通过。
- `webui` 的 `npm run type-check`：通过。
- `webui/src/store/workflow-editor.ts` 当前存在 `undo`、`redo`、`performActionWithoutHistory`，并由 `WorkflowEditorIntent` 暴露；`WorkflowCanvas.vue` 的撤销、重做和恢复路径使用无历史包装。
- `docs/UPGRADING_TO_3.3.0a7.md` 当前存在，内容是 `3.3.0a7` 发布周期的历史冻结迁移记录，并明确当前通用流程应看 `UPGRADING.md`；它不应被改写成当前版本说明。
- 结论：Task 5 和 Task 8 不能再按旧 handoff 的“缺符号/缺文件”直接重做；后续只需在全量验收或新鲜回归暴露问题时做兼容修复。

## 第 17 至 24 组实施矩阵

| 组别 | 当前结论 | 首要证据/动作 |
|---|---|---|
| 17 需求与验收 | 已具备约束文本 | 以原始计划、最新 Git 现场和 `1.txt` 为准，附件仅作证据 |
| 18 QQ/OneBot 恢复 | 缺口较大 | 盘点数据目录、登录态/设备身份边界、Compose ready、重连状态和幂等；不能凭容器 liveness 宣称完成 |
| 19 QQ 消息 | 部分具备 | 复核统一消息中间表示、QQ 降级渲染、表格/代码/LaTeX、分页顺序、失败重试和延迟时间线 |
| 20 画布 | 部分具备 | 既有确定性缺失节点布局和重叠检测；还需真实尺寸、异步覆盖、坐标持久化、端口连线和大图回归证据 |
| 21 Provider | 当前不足 | 设计可观测优先级队列、错误分类、总 deadline、退避和 closed/open/half-open 熔断，保留旧 fallback 语义 |
| 22 统计与 Skills | 当前不足 | 先补真实/估算/未知 Token 与定价快照；Skills 必须先有 manifest、权限、审计、导入校验和回滚，不能只做页面 |
| 23 版本与发布 | 部分具备 | 验证单一版本源、远端 Tag 冲突、脏树、事务恢复、commit identity、镜像排除项和文档同步 |
| 24 流程与门禁 | 部分具备 | 继续保留只读子代理、外部调研、失败测试、graphify 更新和未验证风险清单；门禁不全不得发布 |

## 外部参考边界

- `chatgpt-mirai-qq-bot-onebot-adapter-main`：参考 OneBot 事件边界、心跳、连接状态和消息元素转换。
- `LuckyLilliaBot-main`：参考账号隔离、登录生命周期、上线 catch-up、热加载和设备身份独立持久化。
- `llonebot.nix-main`：参考 QQ 配置、机器人数据、媒体缓存的分卷持久化与 Compose 组织。
- `cc-switch-main`：参考 Provider 元数据、原子写入、失败回滚、Usage 事件、备份、哈希和 Skill 所有权。
- 不能复制账号、Token、Cookie、私有地址、私有协议 hook、native binary 或不可维护补丁。

## CC Switch 截图与源码结论

- 20 张截图展示了 Provider 路由状态、故障转移队列、操作记录、用量趋势、Provider/模型统计、输入/输出/缓存价格、价格目录、Skill 来源与备份、Prompt 启停、Session 转录和恢复，以及 MCP 多客户端状态。
- 可吸收的是信息架构、状态模型、失败证据、成本字段和资源恢复流程；Kirara 应把它们实现为服务端控制面与持久化服务。
- 不照搬 Tauri 桌面应用对其他客户端配置文件的接管方式，也不把截图当作 Kirara 已完成证据。
- Skill 导入必须先校验来源、版本、哈希、路径、重复 ID 和权限，安装后默认禁用；只有明确启用后才能进入执行链路。

## 当前核心缺口

1. OneBot 投递：缺 durable outbox、稳定 delivery id、上游 action 受理、死信、重启恢复和明确的“接口受理不等于客户端收信”状态。
2. Skill/Prompt/Session：现有扩展 manifest 和 workflow catalog 不等于安全安装器，仍缺来源、哈希、权限确认、默认禁用、重复 ID/降级保护、事务回滚和审计。
3. 控制面与运行文档：仍需把 Provider 成本、OneBot 分层状态、投递队列和资源恢复能力按服务端边界呈现，并完成 Compose 持久化说明。

## Agent Runtime 决策（2026-08-24）

- 用户已批准按渐进式服务端可靠性平台路线实施，但要求先仔细核对 CC Switch 的同类功能细节。
- 目标运行链路固定为：收到消息 -> 选择 Agent -> 注入 Prompt/Skills/Memory -> 按权限提供 MCP 工具 -> 模型调用工具 -> 工具结果回传同一轮 -> 回复用户 -> 保存 Memory 与 Session。
- Prompt、Skills、MCP 和 Session 必须进入上述真实对话链路，不能只有资源管理页面。
- Skills 的主要在线入口是仓库发现和 `skills.sh` 搜索；ZIP 只保留为离线导入、迁移或备份恢复入口。
- `kirara_ai/plugins/im_onebot_adapter` 是内置适配器，不得在插件市场或资源页要求用户再次安装。
- CC Switch 的桌面职责是把资源同步到多个外部客户端；Kirara 的等价职责是把版本化资源绑定到 Agent、路由和 Workflow，并在每次运行时生成可审计快照。
- CC Switch Session 是外部客户端记录浏览器；Kirara 还必须拥有自己的 Agent Session、消息、工具调用、确认状态和恢复点。
- MCP 的配置启用不等于执行授权。运行时必须按 Agent/Session/Workflow 交集构造 allowlist，并在真正执行工具前再次校验。
- 发送、删除、修改、发布、付款等高影响工具调用必须进入等待确认状态；确认前不得执行，拒绝或过期后把结构化结果回传模型。
- 新增跨渠道要求：WeCom、QQ、Telegram 等所有 IM 适配器都必须通过同一条 Agent Runtime 关系链连接到模型后端/备用链、Prompt、Skill、Memory、MCP 和 Session；不能只给某一个适配器单独接入。
- 渠道关系的最小身份维度是 `channel_type + adapter_instance + account_scope + conversation_scope + sender_scope`。这些值进入 Agent 选择、Session 主键、权限判定和审计，但日志与控制面必须脱敏。
- 模型关系不只保存一个模型名：Agent 需要保存模型能力要求、主模型/备用模型优先级、Provider 约束、上下文预算、工具调用能力和成本/可观测策略；运行时交给现有 `LLMManager` 的 provider queue 执行。
- Prompt/Skill/MCP 的绑定需要支持 Agent 默认值、渠道/账号覆盖、会话覆盖和 Workflow 覆盖；有效配置按优先级合并，任何更窄范围都不能扩大上游权限或工具 allowlist。

## Provider 与成本完成证据

- 流式首片段前允许按策略切换 Provider；一旦首片段已交给调用方，后续失败不会拼接另一 Provider 的输出。
- 成功、失败、取消、流式中止和未开始消费即关闭都关闭同一个逻辑 trace；attempts 与最终 Provider 关联到该 trace。
- usage 明确区分上游返回、估算和未知；历史成本保存当次价格版本与快照，不因后续目录更新而回写。
- 聚焦测试 55/55 通过；迁移警告仅为 Alembic 配置弃用提示，不影响结果。

## 工具证据

- `rtk 0.45.0` 可用。
- `graphify 0.9.43` 可用；已有图可查询到 OneBot renderer、画布入口、版本和升级文档节点。
- `memsearch 0.4.17` 可用；已检索到历史工作结论，但历史记忆仅作辅助，当前仓库现场优先。
- `context-mode` 运行时、SQLite、服务器测试通过；Codex hooks 配置缺失，不擅自修改用户环境。
