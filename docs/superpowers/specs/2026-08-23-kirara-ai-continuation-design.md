# Kirara AI 3.3.0b11 Continuation Design Record

> 日期：2026-08-23
>
> 本记录承接 `2026-08-17-kirara-ai-excellence-overhaul.md` 与
> `2026-08-23-handoff.md`，用于说明本次执行为何选择这些修改边界。

## 1. 事实校准

本次执行以当前仓库现场为准，而不是旧交接提示词中的快照：

- 当前分支为 `main`，HEAD 为 `a2bc1be1ddf4989d0fb02529315fe10475d75af7`。
- 当前 Python 与 WebUI 发布身份为 `3.3.0b11`；原计划中的 `3.3.0a7` 与
  `docs/UPGRADING_TO_3.3.0a7.md` 是历史发布周期记录，不能改写成当前版本。
- `performActionWithoutHistory` 已存在于
  `webui/src/store/workflow-editor.ts`，并已有同步异常、异步 Promise、嵌套抑制、
  undo/redo 边界与快照隔离测试。旧交接中的“缺符号”结论已被当前源码证伪。
- 当前可证实的 Task 5 风险是 `WorkflowCanvas.vue` 用单一布尔值
  `_graphDataInitialized` 判断初始化完成；父组件复用同一 Canvas 实例切换到另一
  工作流时，没有工作流身份或加载代际校验。现有路由切换通常通过重新挂载规避，
  但真实组件复用路径没有回归覆盖。
- QQ 登录、设备身份、二维码和 PMHQ 注入属于外部 QQ/OneBot 运行时职责。Kirara
  当前是 OneBot V11 消费端；不能凭 Kirara 代码承诺 QQ 登录器能力。

## 2. 方案选择

### 2.1 Task 5：修复初始化契约，不重做历史系统

选择在 Canvas 内记录“初始化身份键”，由 `initialWorkflowId` 和可选的外部
加载代际组成；只有身份键变化时才重建画布图数据。保留现有的
`isEchoOfOwnEmit` 过滤、`performActionWithoutHistory`、`flushGraphData()`、
请求代次保护和 Store 的历史清理逻辑。

原因：

1. 这直接修复旧工作流数据可能残留到新工作流的根因，修改面小且与现有请求代次
   设计一致。
2. 每次 props 变化都初始化会覆盖本地编辑和 debounce 保存，属于更危险的表面修复。
3. 继续保留兼容入口，避免破坏已有组件调用方和历史行为。
4. `cc-switch` 的优势在 Provider 状态、故障转移队列和可观测性，不在 Kirara
   画布执行器；它的 React/Tauri/SQLite 架构不能直接移植到 Vue/Quart/YAML 边界。

### 2.2 Task 8：补当前运行文档与发布证据

不重做已经完成的版本、Docker CI、扩展和升级实现；只补充当前版本运维文档缺少的
证据边界：

- Compose 重启后分层区分容器 ready、OneBot WebSocket 重连、登录态/设备身份丢失、
  挂载错误和上游拒绝。
- 明确 QQ、OneBot、Kirara 数据、消息队列、日志和备份的容器路径与宿主机边界；
  不在文档中写入真实账号、Token、Cookie、二维码或服务器路径。
- 明确二维码生成时间、有效期、刷新与失败原因只能由外部 QQ/OneBot 实现提供，
  Kirara 仅记录适配器连接状态和诊断证据。
- 记录 QQ/Telegram/WeCom 链路时间戳要求，以及 Provider/统计/Skills 能力的当前
  支持边界，避免把“有页面”误写成“有执行链路”。

### 2.3 不纳入本次修改的能力

- 不把 `cc-switch` 的 Tauri、SQLite、外部客户端 live 配置或真实 Provider 数据结构
  引入 Kirara。
- 不在本次任务中新增第二套 Provider 执行器、成本账本、Session 文件删除器或可执行
  Skill 安装器；这些需求需要独立的数据模型、权限和迁移设计，不能伪装成 Task 5/8
  的小修复。
- 不修改、移动、删除、暂存或打包 `docs/LOGO.jpg`。
- 不纳入或打包 `PATHFINDER-2026-08-21/`。

## 3. 实施顺序

1. 为同一 Canvas 实例切换工作流补真实回归测试，并补
   `performActionWithoutHistory` 的公开兼容契约测试。
2. 以最小实现修改初始化身份键，验证旧响应和自发 emit 不会触发错误重建。
3. 核查 `docs/UPGRADING_TO_3.3.0a7.md` 与当前运行文档，新增当前版本的 QQ/OneBot
   恢复与诊断章节，并修正仅在证据支持范围内的表述。
4. 运行 WebUI 聚焦测试、类型检查、构建；运行后端相关测试、发布契约和敏感信息扫描。
5. 运行 `graphify update .`、`git diff --check`，检查受保护文件和发布目录边界。
6. 输出逐条发布门禁、当前版本、提交绑定、镜像标签和未验证风险。GitHub 推送、Tag、
   Release 与 Docker Hub 推送在此之后仍需最后一次明确授权。

## 4. 验收门禁

### 必须有证据

- 同一 Canvas 实例从工作流 A 切换到 B 后，节点、连线、属性和历史都属于 B。
- 切换到空工作流会清空 A 的节点和连线。
- Canvas 自己 emit 后父组件原样回传不会重复初始化。
- `performActionWithoutHistory` 同步和异步 action 期间不产生历史，异常后抑制状态恢复，
  嵌套调用保持正确。
- WebUI 类型检查、单元测试、生产构建通过；后端相关测试与发布契约通过。
- 文档不含真实凭据或二维码；受保护文件无变化；镜像/归档检查排除私有文件、缓存和
  `PATHFINDER-2026-08-21/`。

### 明确未验证

- 没有真实 QQ 账号、OneBot 上游或远程服务器时，不能证明扫码、quick login、PMHQ
  热更新和真实消息发送成功。
- 没有 Docker Hub 发布授权前，不执行远端镜像推送；本地 Docker 若不可用，必须报告为
  发布风险，不以静态 YAML 代替运行证据。

## 5. 参考依据

- 原始项目约束：`docs/superpowers/plans/2026-08-17-kirara-ai-excellence-overhaul.md`
- 当前现场：`docs/superpowers/plans/2026-08-23-handoff.md`
- 用户验收附件：桌面 `1.txt`，第 17.1 至 24.5 条
- 本地参考：`C:/Users/devin/OneDrive/Desktop/cc-switch-main` 与 `ccs截图`
- 同类项目：`n8n-io/n8n`、`langgenius/dify`、`BerriAI/litellm`、
  `open-webui/open-webui` 的公开仓库与文档
- Vue 组件行为依据：Vue 3 官方文档的 props/watch 生命周期说明；本次实现仍遵循
  当前项目既有 Composition API 与 Vue Flow 集成方式。
