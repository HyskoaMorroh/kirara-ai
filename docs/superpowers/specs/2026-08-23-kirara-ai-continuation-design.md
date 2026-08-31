# Kirara AI 3.3.0b11 Continuation Design Record

> 日期：2026-08-23
>
> 本记录承接 `2026-08-17-kirara-ai-excellence-overhaul.md` 与
> `2026-08-23-handoff.md`，用于说明本次执行为何选择这些修改边界。

## 1. 事实校准

本次执行以当前仓库现场为准，而不是旧交接提示词中的快照：

- 当前分支为 `main`，HEAD 为 `87dae24`；`origin/main` 为 `8a756bb`，本地领先
  2 个提交。用户现有未跟踪 handoff 备份文件保持不动。
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
4. 桌面端参考实现的优势在 Provider 状态、故障转移队列和可观测性，不在 Kirara
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

### 2.3 Provider Resilience Gateway 与流式边界

不新增彼此竞争的第二套 Provider 选择器，而是在现有 `LLMManager.execute_chat()`、
`resilience.py` 和 adapter 协议上形成统一 Resilience Gateway：

- 同一逻辑请求使用稳定 trace id；每个候选 Provider 生成有序 attempt 记录，并持久化
  优先级、模型、错误分类、重试次数、熔断状态和最终选择。
- 保留同步非流式兼容入口；为具备流式能力的 adapter 增加可选流式协议，分别约束首字节
  timeout、字节间静默 timeout、总 deadline 与取消传播。
- 首个可见片段输出前，可按明确的可重试错误切换 Provider；输出后发生错误时立即终止该
  响应并记录“部分输出”，不得静默切换后拼接，避免重复内容。
- 认证、参数、内容策略等不可重试错误直接停止；退避、重试和熔断参数集中校验并设置
  上限。

### 2.4 Usage、价格版本与成本账本

在现有 tracing/统计存储上扩展请求级不可变事实，不用字符数冒充供应商真实 Token：

- Token 来源固定为 `provider`、`estimated` 或 `unknown`；分别记录输入、输出、缓存读取、
  缓存写入 Token，缺失值保持未知。
- 记录请求开始、首字节、完成、状态、Provider、模型、attempt 次数、错误类型和 trace id；
  TTFT 只在确有首字节证据时计算。
- 价格按 Provider、模型、输入/输出/缓存维度和生效时间版本化。请求完成时冻结价格版本、
  单价、币种和计算结果快照；后续改价不回写历史请求。
- 导入价格先校验并事务写入，支持导出和恢复；统计查询使用分页、筛选和索引，不一次加载
  全量明细。

### 2.5 OneBot durable delivery 与恢复

Kirara 不承担外部 QQ 登录器职责，但必须可靠管理自己到 OneBot 的消息投递边界：

- 发送前写入 durable outbox，使用稳定 delivery id、接收者顺序号和内容摘要；重启后恢复
  未完成记录，同一接收者仍保持有序。
- OneBot action response 只记为“上游接口已受理”；它不是 QQ 客户端真实收信回执。仅在
  明确未受理时自动重试；结果不确定时进入人工可诊断状态，避免重复发送。
- 记录 queued、sending、accepted、retry_wait、ambiguous、dead_letter 等状态和有限重试
  证据；分页共享逻辑消息 id，并逐页保存进度。
- 状态 API 分开报告容器 ready、WebSocket connected、外部登录/设备状态未知或异常、目录
  检查和上游拒绝，不能用单一绿色状态掩盖问题。

### 2.6 Skill、Prompt 与 Session 安全生命周期

引入共同的资源注册与审计边界，再接控制面，不创建只有标签而无执行链路的页面：

- Skill manifest 必须含稳定 ID、版本、来源、内容哈希、入口和权限声明。ZIP 导入在隔离
  临时目录完成，拒绝绝对路径、路径穿越、链接逃逸、重复 ID、无授权脚本和版本降级。
- 安装、更新和恢复采用“校验 -> 暂存 -> 原子替换 -> 审计”的事务流程；失败恢复旧版本。
  新 Skill 默认禁用，权限变更必须再次确认后才可启用。
- Prompt 记录来源、版本、启用状态、内容摘要和审计；Session 记录所有权、状态、转录索引、
  恢复点和保留策略。删除属于单独高风险动作，不与浏览或恢复混在一起。
- 控制面借鉴参考实现的来源、更新、备份、会话详情与状态信息架构，但服务端不接管用户
  桌面应用配置。

### 2.7 明确不照搬与受保护边界

- 不把参考实现的 Tauri 桌面壳、外部客户端 live 配置接管或真实用户 Provider 数据
  引入 Kirara；只吸收可验证的状态模型和交互信息架构。
- 不修改、移动、删除、暂存或打包 `docs/LOGO.jpg`。
- 不纳入或打包 `PATHFINDER-2026-08-21/`。
- 不修改、移动、删除、暂存或清理用户现有未跟踪 handoff 备份文件。

## 3. 实施顺序

1. 保留已通过的 Canvas 初始化与历史兼容实现，以失败测试补动态端口、边模型保存加载、
   批量动作单一历史点、真实尺寸优先和用户坐标保护。
2. 在现有 LLMManager 上补流式协议、首字节/静默 timeout、部分输出语义和 attempt 持久化；
   随后接 usage 来源、TTFT、价格版本和请求价格快照。
3. 为 OneBot 接入 durable outbox、有限恢复、死信和链路时间戳，再验证统一消息 IR 的数学、
   表格、代码与安全分页降级。
4. 实现资源 registry、Skill ZIP 安全导入与事务回滚，再接 Prompt/Session 生命周期和控制面。
5. 同步当前运行、部署、观测、升级、扩展和 QQ/OneBot 文档；历史升级文档保持冻结。
6. 运行 WebUI 和后端聚焦/全量测试、类型检查、构建、Compose 可执行范围检查、
   `graphify update .`、`git diff --check`、敏感信息扫描和发行物内容检查。
7. 输出逐条发布门禁、当前版本、提交绑定、镜像标签和未验证风险。GitHub 推送、Tag、
   Release 与 Docker Hub 推送在此之后仍需最后一次明确授权。

## 4. 验收门禁

### 必须有证据

- 同一 Canvas 实例从工作流 A 切换到 B 后，节点、连线、属性和历史都属于 B。
- 切换到空工作流会清空 A 的节点和连线。
- Canvas 自己 emit 后父组件原样回传不会重复初始化。
- `performActionWithoutHistory` 同步和异步 action 期间不产生历史，异常后抑制状态恢复，
  嵌套调用保持正确。
- 动态端口只允许合法方向和类型的边；边与端口经过保存、加载、撤销和重做后语义不丢失；
  批量动作只产生一个历史点。
- 流式请求在首字节前可受控故障转移，首个可见片段后失败不得静默拼接；总 deadline、静默
  timeout 和取消均有新鲜测试。
- usage 来源、TTFT、缓存 Token 和请求价格快照可审计，改价不影响历史成本。
- OneBot outbox 可在进程重启后恢复，状态区分 accepted、ambiguous 和 dead letter；不得把
  action ACK 表述成客户端已收信。
- Skill ZIP 路径逃逸、重复 ID、降级和未授权入口被拒绝；成功安装默认禁用，失败更新回滚。
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
- 本地参考：操作者本机桌面的参考实现源码与其界面截图目录
- 同类项目：`n8n-io/n8n`、`langgenius/dify`、`BerriAI/litellm`、
  `open-webui/open-webui` 的公开仓库与文档
- Vue 组件行为依据：Vue 3 官方文档的 props/watch 生命周期说明；本次实现仍遵循
  当前项目既有 Composition API 与 Vue Flow 集成方式。
