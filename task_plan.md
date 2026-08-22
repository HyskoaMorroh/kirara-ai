# kirara-ai 扩充任务实施计划

## Goal

依据 `C:\Users\devin\OneDrive\Desktop\1.txt` 的第 17 至 24 组新增要求，在不破坏原始计划红线、公共 API、配置格式、工作流语义和用户数据的前提下，完成证据核查、兼容实现、回归验证和发布门禁。未完成方案确认前不修改业务代码、不创建 Tag、不推送 GitHub 或 Docker Hub。

## Phases

| Phase | Status | Scope |
|---|---|---|
| 1. 需求与现场基线 | complete | 已读原始计划、交接文档和扩充后的 `1.txt`；已核对分支、HEAD、远端、Tag、脏树和禁入目录 |
| 2. 只读调研与证据矩阵 | complete | 已核查 Task 5、Task 8 及第 17 至 24 组要求，区分已具备、部分具备、缺失、证据不足和不可照搬 |
| 3. 方案确认 | complete | 已依据用户“继续”及扩充后的 `1.txt` 确认进入实施，保留原始计划红线和发布门禁 |
| 4. Task 5 兼容修复 | in_progress | 以新鲜回归测试核验 `undo`、`redo`、`performActionWithoutHistory`，仅修复证据充分的兼容问题 |
| 5. Task 8 文档冻结项 | pending | 单独核验 `docs/UPGRADING_TO_3.3.0a7.md` 的存在、内容和历史版本属性 |
| 6. QQ/OneBot 可靠性与消息链路 | pending | 完成持久化清单、状态分层、重连、排版、分页、幂等和诊断的可测试实现 |
| 7. Provider/统计/Skills/版本增强 | pending | 按兼容边界实现故障转移、Token 真实性、安全导入和智能版本门禁 |
| 8. 全量验收与发布门禁 | pending | 测试、构建、敏感信息扫描、镜像内容检查、文档同步；硬性门禁未满足则禁止发布 |

## Constraints

- 不重做 Task 1、Task 2、Task 3、Task 4、Task 6、Task 7、Task 9；发现回归只做证据充分的兼容修复。
- 不修改、移动、暂存、打包或发布 `docs/LOGO.jpg`。
- 不纳入 `.memsearch/`、`PATHFINDER-2026-08-21/` 和任何私有数据、密钥、二维码、缓存或测试产物。
- `.venv/` 不可用；Windows Python 使用 `.venv-win/Scripts/python.exe`；WebUI 测试脚本使用 `test:unit`。
- 保留既有函数、变量、注释、配置格式、公共 API 和兼容入口；禁止破坏性迁移、静默 fallback、无限重试和强制联网。

## Next Step

先完成 Task 5 / Task 8 的新鲜门禁核验，再从 QQ/OneBot、消息格式化、Provider、统计和安全导入的失败回归测试开始实施；每个改动后运行聚焦测试、`graphify update .`、`git diff --check` 和敏感信息扫描，所有发布门禁通过前不创建 Tag、不推送 GitHub、不推送 Docker Hub。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| context-mode hooks 配置缺失 | 1 | 不修改用户环境；继续使用已通过运行时、SQLite 和服务器测试的 context-mode 能力 |
