# 工作进度

## 2026-08-23

- 已确认本轮目标是落实扩充后的 `1.txt`，不是把附件内容未经核对直接当作代码指令。
- 已读取上一轮交接结论，避免重做 Task 1、2、3、4、6、7、9。
- 已重新核对当前 Git 现场：`main`、`87dae24`、本地领先远端 2 个提交；唯一未跟踪文件为用户现有 handoff 备份，保持不动。
- 已建立扩充任务计划和证据记录；当前版本为 Python `3.3.0b11` / WebUI `3.3.0-b11`，尚未打对应 Tag 或发布。
- 已完成 Task 5 / Task 8 的新鲜只读核验：WebUI 单元测试 13/13 通过，类型检查通过，历史升级文档存在且保持冻结属性。
- 已完成第 17 至 24 组逐域矩阵，并查看 CC Switch 20 张截图；确认应服务端化吸收 Provider 状态、故障转移证据、成本字段和资源恢复模型。
- 用户已明确要求按附件执行；方案阶段不再等待重复确认。发布、Tag、Release 和镜像推送仍须在全部门禁后单独确认。
- 已识别四个主要实现缺口：Provider 流式连续性、usage/价格快照、OneBot durable outbox、安全 Skill/Prompt/Session 生命周期。
- 已完成 Task 5：动态代码节点端口、未知历史端口占位与诊断、连线双向转换、严格新连线校验、兼容保存导出、单次导入历史事务、不可变有界 undo/redo、异步及异常恢复、画布初始化代际隔离和真实节点尺寸布局均已落地。
- Task 5 新鲜验证证据：`workflow-data.test.ts` 与 `workflow-editor.test.ts` 合计 33/33 通过；WebUI `type-check`、`build-only` 和 `git diff --check` 通过；`graphify update .` 完成并生成 6507 个节点、15452 条边、355 个聚合社区，未产生新的可见工作树文件。
- 已静态复核 Canvas 集成路径：工作流切换和空工作流清理受初始化代际保护，自身 emit 数组回传不会重复初始化，导入前 flush 且导入后恢复画布状态，动态端口边保存加载不丢失端点语义。
- 已完成 Provider 与成本核心：稳定优先级、有限重试、总 deadline、取消传播、三态熔断、流式首片段前切换、首片段后禁止拼接、首片段/静默超时、逻辑 trace 关联 attempts、usage 来源、缓存读写 Token、TTFT、版本化价格和不可回写成本快照均已落地。
- Provider 与成本新鲜验证证据：LLM failover、usage/pricing、tracing 和迁移测试合计 55/55 通过；仅有 2 条 Alembic `prepend_sys_path` 弃用警告。`git diff --check` 通过；`graphify update .` 完成并生成 6609 个节点、15823 条边、363 个社区。
- 已确认 OneBot 现有边界：反向 WebSocket、Token/role 校验、多账号路由、接收者级串行、分页、action timeout 和入站媒体防护已具备；持久 outbox、稳定 delivery id、重启恢复、明确的上游受理/结果未知/死信状态仍缺失。
- 下一步：以失败测试实现独立 OneBot outbox 服务、ORM/迁移、Adapter 集成和无隐私分层健康快照。

## 2026-08-24

- 已按用户约束逐张读取 `ccs截图` 的 20 张界面截图，并即时记录到 `docs/superpowers/plans/ccs-ui-notes.md`；后续不再读取或引用原图。
- 已核对 CC Switch Prompt、MCP、Session 的前后端核心实现，确认其真实行为包括应用级启停、配置同步、导入报告、状态刷新、删除保护、文件 revision/备份回滚、Session 路径校验和全文检索。
- 已确认用户批准进入统一 Agent Runtime 实施；Skills 以仓库和 `skills.sh` 搜索为主入口，ZIP 仅作离线导入/恢复。
- 已确认 Prompt、Skills、MCP、Memory、Session 必须真实参与对话；`im_onebot_adapter` 继续作为项目内置适配器，不提供重复安装入口。
- 当前阶段调整为 CC Switch 测试对照和 Agent Runtime 规格冻结，随后直接进入失败测试驱动实现。
- 用户补充跨渠道关系要求：WeCom、QQ、Telegram 等适配器均需接入同一 Agent Runtime，并与模型上游、Prompt、Skill、MCP、Memory、Session 建立可审计的使用关系；不允许只实现某一个渠道的特例。
