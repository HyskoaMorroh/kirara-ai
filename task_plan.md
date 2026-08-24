# kirara-ai 扩充任务实施计划

## Goal

依据本轮附件 TXT 的第 17 至 24 组新增要求，在不破坏原始计划红线、公共 API、配置格式、工作流语义和用户数据的前提下，完成证据核查、兼容实现、回归验证和发布门禁。附件、截图和外部项目只作为证据与设计参考；所有结论以当前仓库和新鲜测试为准。

## Phases

| Phase | Status | Scope |
|---|---|---|
| 1. 需求与现场基线 | complete | 已读原始计划、交接文档和扩充后的 `1.txt`；已核对分支、HEAD、远端、Tag、脏树和禁入目录 |
| 2. 只读调研与证据矩阵 | complete | 已核查 Task 5、Task 8 及第 17 至 24 组要求，区分已具备、部分具备、缺失、证据不足和不可照搬 |
| 3. 方案确认 | complete | 已依据用户“继续”及扩充后的 `1.txt` 确认进入实施，保留原始计划红线和发布门禁 |
| 4. Task 5 兼容修复 | complete | 已完成动态端口、未知历史端口保留、边保存加载、批量历史事务、画布初始化代际隔离和真实节点尺寸布局；33/33 聚焦单测、类型检查、构建与差异检查通过 |
| 5. Task 8 文档冻结项 | complete | `docs/UPGRADING_TO_3.3.0a7.md` 已单独核验为历史冻结记录，不改写为当前版本说明 |
| 6. Provider 与成本核心 | complete | 已完成流式首片段/静默超时、连续性、usage 来源、TTFT、价格版本、请求价格快照和统一 trace；55/55 聚焦测试通过 |
| 7. QQ/OneBot 可靠投递 | complete | 已实现状态分层、durable outbox、稳定 delivery id、受理/结果未知/死信/恢复和链路时间戳；最终仍需真实 QQ/OneBot 环境验收 |
| 8. 安全资源生命周期 | complete | 已建立 Skill/Prompt/Session 来源、版本、哈希、权限、默认禁用、导入校验、审计、更新与事务回滚基础设施 |
| 9. CC Switch 行为对照与 Agent Runtime 规格 | in_progress | 继续核对 Prompt/Skills/MCP/Session 源码和测试，形成行为矩阵并冻结 Kirara 等价设计 |
| 10. Agent Runtime 核心 | pending | 实现跨渠道 Agent 存储、Prompt/Skill 注入、MCP allowlist 与二次校验、同轮工具循环、Memory/Session 持久化和高影响操作确认 |
| 11. API、WebUI 与消息路由 | pending | 接入 Agent 管理、仓库与 skills.sh 搜索、资源绑定、Session 浏览恢复、Workflow/WeCom/QQ/Telegram 路由 |
| 12. 全量验收与发布门禁 | pending | 测试、构建、敏感信息扫描、镜像内容检查、文档同步；硬性门禁未满足则禁止发布 |

## Constraints

- 不重做 Task 1、Task 2、Task 3、Task 4、Task 6、Task 7、Task 9；发现回归只做证据充分的兼容修复。
- 不修改、移动、暂存、打包或发布 `docs/LOGO.jpg`。
- 不纳入 `.memsearch/`、`PATHFINDER-2026-08-21/` 和任何私有数据、密钥、二维码、缓存或测试产物。
- Windows Python 优先使用 `.venv-win/Scripts/python.exe`，可用时才使用其他仓库内解释器；WebUI 测试脚本使用 `test:unit`。
- 保留既有函数、变量、注释、配置格式、公共 API 和兼容入口；禁止破坏性迁移、静默 fallback、无限重试和强制联网。
- `docs/superpowers/plans/2026-08-23-handoff.prev.md` 是用户现有未跟踪文件；不得修改、移动、删除、暂存或清理。
- OneBot action 成功仅表示上游接口受理，不得写成 QQ 客户端已真实收信；无真实账号和上游环境时必须保留未验证标记。

## Next Step

读取 CC Switch 的 Prompt、Skills、MCP、Session 测试和 Kirara 现有执行边界，写出逐项对照矩阵及 `docs/superpowers/specs/2026-08-24-kirara-agent-runtime-design.md`。规格自审后直接以失败测试实现 Runtime 核心；最终门禁通过后先提交发布清单，未经用户最后明确确认不创建 Tag、不推送 GitHub、不创建 Release、不推送 Docker Hub。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| context-mode hooks 配置缺失 | 1 | 不修改用户环境；继续使用已通过运行时、SQLite 和服务器测试的 context-mode 能力 |
