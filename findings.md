# 需求与证据记录

## 现场基线

- 分支：`main`
- HEAD：`8a756bb release: prepare v3.3.0b10`
- `origin/main` 与当前 HEAD 一致。
- 工作树已有版本、文档、CI、测试和 WebUI 改动；不得回滚或覆盖。
- `pyproject.toml` 当前为 `3.3.0b11`，但未提交、未打 Tag、未发布。
- 已有 Tag：`v3.3.0b8`、`v3.3.0b9`、`v3.3.0b10`。

## 证据分类

| 要求 | 当前证据 | 初步结论 |
|---|---|---|
| Task 5 undo/redo/performActionWithoutHistory | WebUI store、intent 和 canvas 已有入口，但需真实测试和导出 API 核对 | 证据不足，优先核验 |
| Task 8 升级文档 | graphify 可定位 `docs/UPGRADING_TO_3.3.0a7.md` | 需单独检查内容与历史冻结属性 |
| OneBot 基础链路 | 有反向 WebSocket、心跳、健康状态、事件转换、发送串行化和长消息分页 | 部分具备 |
| QQ 登录态与设备身份 | 未发现当前仓库拥有 QQ 二维码登录、设备身份和登录态持久化的完整证据 | 当前缺失或需受控适配器 |
| QQ/Compose 重启恢复 | 目前只有 `./data:/app/data` 和容器级健康检查证据 | 不足以证明登录恢复和适配器 ready |
| QQ 消息排版 | 有 OneBot renderer 和分页，但 QQ 富文本能力、LaTeX、表格、代码和部分失败幂等仍需验证 | 部分具备 |
| Provider 故障转移 | 当前 LLM Provider 选择主要为随机选择；已有模型级 fallback | 不满足队列、熔断和总 deadline 要求 |
| Token/成本统计 | 有 Token 字段、统计 API 和 tracing；缺失 usage 时存在写入 0 的风险 | 不满足真实性与定价快照要求 |
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

## 工具证据

- `rtk 0.45.0` 可用。
- `graphify 0.9.43` 可用；已有图可查询到 OneBot renderer、画布入口、版本和升级文档节点。
- `memsearch 0.4.17` 可用；已检索到历史工作结论，但历史记忆仅作辅助，当前仓库现场优先。
- `context-mode` 运行时、SQLite、服务器测试通过；Codex hooks 配置缺失，不擅自修改用户环境。
