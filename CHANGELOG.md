# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的分类方式，记录**源代码、默认配置、部署文件、文档与测试**变化。

比较基线为 `3.2.0`，比较目标为 `3.3.0b17`。本文件记录源码与发布行为的对应关系；实际发布状态以 GitHub 和镜像仓库为准。

> 不纳入比较：`.git/`、编辑器缓存、测试缓存、运行日志、`data/db/`、记忆/媒体/插件运行数据、虚拟环境和任何本地密钥或密码文件。这些内容会随机器和使用状态变化，不属于可复现的产品功能。

## [3.3.0b17]

本轮修的是三处**不报错**的缺陷：全新部署解析不到 Agent 导致六个渠道全部不回话、
同一个失败在四个渠道说四套话（其中两个完全静默）、以及 Agent 的模型链要手打
而那些名字就在另一个页面上。第三处顺带查出动态配置表单的占位符表达式有运算
优先级错误，`examples` 从来不生效。

三处的共同形态是「界面上看不出问题」：没有报错、没有白屏，只是行为不对——
第一处要等到用户在 IM 里发第一句话，第二处要等到出故障，
第三处要等到某次真实对话解析不到那个拼错的模型 ID。

另有一处不在产品代码里而在门禁里：`3.3.0b16` 的镜像发布被一条 integration 用例
挡住，而它失败的原因是 npm 注册表慢——门禁在赌网络，且挡住的是发布。

### Fixed

- **一条 integration 用例挡住了镜像发布，而它失败的原因是 npm 注册表慢**（需求 23.3、24.4）：
  `v3.3.0b16` 的 `Docker build latest` 被

      FAILED tests/agent_runtime/test_context7_integration.py::
          test_real_context7_mcp_completes_agent_turn_after_model_failover

  挡下。日志时间戳指出原因：`05:28:53 Connecting to MCP server context7` →
  `05:30:53 连接到 MCP 服务器 context7 超时`，正好 120 秒，也就是
  `startup_timeout_ms` 的默认值。

  `require_tool("npx")` 只问「PATH 上有没有 `npx`」。GitHub runner **装了 Node**，
  于是它不 skip，用例接着做一次真实的 `npx -y @upstash/context7-mcp`——那是一次
  **联网下载**，被塞进 MCP 的连接预算里。它是网络竞态而非代码缺陷，证据是同一个
  提交上三次运行互相矛盾：ubuntu-py3.11 在 `Run Tests` 里过、在 `Docker build latest`
  里失败；ubuntu-py3.13 反过来。

  判据换成「这个包能不能**从本地缓存**拉起」：新增 `require_npx_package()`，用
  `npm exec --offline`（cache 模式 `only-if-cached`）探一次，缓存里没有就立刻以
  `ENOTCACHED` 失败并 skip（实测 0.7 秒），绝不联网；命中时 1.0 秒，且执行的是
  `node --version` 而不是 context7 自身，所以探针不会真的拉起一个 MCP 进程。
  三种处境因此分开：没装 Node、装了 Node 但包没缓存、包已缓存（真的跑）。
  探针自身也有 90 秒上界——一个卡住的探针与它要防的那个超时是同一种故障。

  写这条修复时踩到第二层：`subprocess.run(["npm", ...])` 在 Windows 上抛
  `FileNotFoundError`，因为 npm 是 `npm.cmd` 而不带 shell 时只补 `.exe`。
  那会让探针退化成「在 Windows 上永远 skip」——等于把这条用例在半个矩阵上删掉，
  且完全没有症状。改为用 `shutil.which("npm")` 解析出的完整路径。

  三个调用点全部改判据，并由 `tests/utils/test_external_tools_guard.py`（11 项）
  锁住：探针不联网、`--offline` 不能被去掉、三种处境各自的结果、以及**没有任何
  一处调用点回到只看 `npx` 在不在**——漏改一处会在那一条上恢复原样，而本机
  （包已缓存）看不出区别。

- **本机在用的插件预置进项目，拉取镜像后默认装好（需求 4）**：
  内置目录从 20 条扩到 52 条：31 个随包技能、5 个随包角色提示词、
  12 个 MCP 模板（补齐 puppeteer / everything / ui5 / notion）、
  1 个记忆策略、1 个审计 Hook。其中 36 条带 `bundled_dir`，
  正文随 wheel 与镜像分发，**安装不出网**——实测 51 条离线可装、耗时 2.1 秒。

  **为什么不是把 115 个技能全塞进来**：先按「里面有什么」分类而不是按名字猜。
  本机 `~/.cc-switch/skills/` 共 117 个目录、135MB，其中含脚本或二进制的 31 个
  占了 **127MB**（`gorden-ppt-skill` 一个就 101.84MB）。运行时镜像只装了
  Python、ffmpeg 与 libmagic1，没有 Node、也没有那些脚本要的解释器与依赖，
  装进去只会得到一个「界面显示正常、一使用就失败」的技能——比不装更糟。
  因此只收**纯文档**技能，并按本项目场景（调试排障、代码质量、测试、API 设计、
  规划、安全、可观测、前端设计）筛出 31 个，共 2.5MB。
  这条纪律由 `tests/plugin_manager/test_bundled_resources.py` 的
  `FORBIDDEN_SUFFIXES` 守着：新增一个带 `.sh` / `.js` / `.py` 的随包技能会让它红。

  `node_repl` 与 `context-mode` 两条 MCP **不收**：它们的命令是本机绝对路径
  （Codex 自带运行时、全局 npm 目录），在别人的机器与容器里都不存在。
  预置一条死配置，而界面上它与其他模板同形——测试单独钉住「模板命令不得是
  绝对路径」。

  Claude Code 的 `agents/*.md` 按 **prompt 类资源**接入而不是 Agent：
  本项目的「Agent」是 `AgentDefinition`（模型链 + 资源绑定），
  而那些文件是行为说明，一个都没有模型链与渠道绑定。

  **预置不等于放权**：新增 `test_preset_permission_boundary.py`（53 项）证明
  这条边界在预置之后仍然成立——随包资源一条都不许声明 `process.execute` /
  `filesystem.write` 等能改服务器的权限（唯一例外 `hook:ai-debug` 单独钉住并
  说明理由）；默认配置下 `creator_channel_identities` 为空；
  没有 principal 时 `principal_can_control_agent()` 一律返回 False，
  即 IM 渠道默认拿不到能操作 VPS 的工具。白名单空 + 门禁拒绝是「默认安全」
  的两半，少任何一半预置就等于默认开放。

  修复过程中撞到三处自身缺陷：**打包端摘要不按路径排序**，而校验端
  `_content_hash()` 排序，于是多文件资源安装被判
  「resource content digest does not match manifest」——单文件资源上两种顺序
  恰好一致，所以这个错在提示词、记忆、MCP 上完全没有症状；
  **`install()` 按 `type == "skill"` 判随包**，让随包 prompt 落到读
  `item["content"]` 的分支抛 `KeyError: 'content'`，判据改为「有没有
  `bundled_dir`」；**无条件读 `item["version"]`** 让「重装一个已装的远端技能」
  抛 `KeyError: 'version'`（在线搜索合成的条目没有这个键）。

- **`404` 被判成网络错误，一次拼错的地址打满整条故障转移队列（需求 21.2）**：
  现场日志里一条 Telegram 消息触发了 **72 次** LLM 尝试、全程 1.06 秒，
  最后抛 `FailoverExecutionError`。但其中只有 **3 次**真的发出了 HTTP 请求——
  三个模型各一次，全部 `404 page not found`；其余 69 次是三个 Provider 的
  熔断器被打开之后的空转。

  根因在 `classify_llm_error`：状态码分支只显式认 401/403/408/429/5xx，其余落进

      if isinstance(error, (ConnectionError, OSError)):
          return ErrorCategory.NETWORK

  而 `requests.exceptions.HTTPError` 的继承链是
  `HTTPError → RequestException → OSError`。于是 `raise_for_status()` 抛出的
  **任何** 4xx 都被判成可重试的网络错误。`404` 的含义是「这个上游没有这个路径
  或这个模型」，换一家不会变、重试一次也不会变。

  **既有测试为什么没发现**：`tests/llm/test_resilience.py` 用自定义的
  `HttpError(Exception)` 构造，那不是 `OSError` 的子类，永远走不到那条分支。
  测试覆盖了「带 status_code 的异常」，但没覆盖「真实 HTTP 客户端抛出的异常」，
  而生产里只有后者。新测试因此一律用真实的 `requests.Response.raise_for_status()`
  构造，并单独钉住 `isinstance(http_error(404), OSError)` 这条事实——
  它解释了缺陷为什么存在，也防止「换个基类」的错误修法。

  同一根因还有第二个受害者：`requests` 的 `ReadTimeout` / `ConnectTimeout`
  也是 `OSError` 子类却**不是**内置 `TimeoutError` 子类，因此超时一直被归类成
  `network`。两者都可重试，所以转移行为没错，但追踪与统计里「超时」会显示成
  「网络错误」——排查时会去查网络，而实际要调的是超时预算。新增
  `_is_timeout_error()` 按类名匹配，同时覆盖 requests 与 httpx，
  不为此 import 一个可选依赖。

  `501 Not Implemented` 归在**可重试**一侧：故障转移的判据是「换一家会不会
  变好」，不是「重试会不会变好」——另一家上游可能实现了这个方法。

- **追踪 WebSocket 主动断开时弹「连接追踪系统失败」（需求 22）**：
  用户在「系统记录 → 使用统计」页看到这条红色提示，而那一页从头到尾没有
  `connectWebSocket` 调用——它只发 `GET /tracing/llm/statistics`。
  提示来自**上一个页面**遗留的 socket。服务端日志排除了鉴权与路径问题
  （`GET /tracing/ws` 全部返回 `101`），同时暴露出第二个症状：
  一秒内握手两次。

  两处句柄泄漏：`disconnectWebSocket()` 只把 `onclose` 置空就 `close()`，
  而浏览器在连接尚未 OPEN 时关闭会**补发一个 `error` 事件**；
  `connectWebSocket()` 关旧 socket 前也没摘处理器，旧 socket 的 `onclose`
  见 `wasClean === false` 就排一次重连——「关掉旧连接」反而又开一条。
  两处改用同一个 `detachAndClose()`：摘掉四个处理器再关。

- **`reconnecting` 只在「掉线时恰好有人看着面板」的情况下才生效（需求 1、18.1）**：
  需求 1 的报障是 `docker compose down && pull && up -d` 之后 QQ 显示未连接。
  `reconnecting` 状态就是为它加的——上游反向 WebSocket 掉线后自己会回连，
  这段时间报「正在重连」而不是「未连接」，否则操作者会去重查地址与 Token，
  而那两项从来没错。

  但那条路径有个前提没被满足：`_ever_connected` 此前**只在
  `get_health_snapshot()` 里**被置位。也就是说它记的不是「上游连上过」，
  而是「上游连着的时候有人读过一次状态」。而 `_note_upstream_disconnected()`
  在 `_ever_connected` 为假时直接返回、不开重连窗口。于是：连上 → 掉线 →
  期间没有任何一次快照读取 → 窗口没开 → 状态是 `disconnected`。

  现场恰好落在这个组合里，因为 compose 重启通常发生在没人看面板的时候：
  `down` 之后进程重建、计数器归零，上游拨入、几分钟后镜像 pull 完又断开重连，
  整个过程没有一次 HTTP 读取，于是面板打开时看到的正是「未连接」。
  换句话说，为这个报障加的状态在这个报障自己的场景里不生效。

  置位点移到 `_handle_meta` 里链路真的活着的那一刻。心跳与 `lifecycle connect`
  一并算：两者都证明链路活着，而 Kirara 重启后上游可能不重发
  `lifecycle connect`、直接继续发心跳，只认 lifecycle 会漏掉这种真实形态。
  快照里那一处保留为兜底（覆盖不经过元事件填充 `connections` 的路径），
  但不再是唯一置位点。

  另外三个适配器不受影响，已逐个核对：Telegram 与企业微信的 `_ever_started`
  在 `start()` 里置位（读快照那处只是补一次），QQ 官方机器人没有重连窗口——
  它们都不是「上游拨入」模型，没有这个状态。

  回归测试 `tests/plugins/im_onebot_adapter/test_ever_connected_latch.py`（5 项）
  的核心用例全程走 `_handle_meta`、掉线前**零次**读快照，坏版本在那一条上
  返回 `disconnected`。

- **全新部署无法回复任何消息：解析不到 Agent，四个渠道各说一套话（需求 10、19.5）**：
  用户在 Telegram 发一句话，收到的是

      Workflow execution failed, please try again later:
      No Agent is configured for this channel identity

  三个独立缺陷叠在这一条现象上。

  **一是全新部署没有任何 Agent。** `AgentRegistry.resolve()` 的优先链是
  `session_agent_id` → 已持久化会话 → 账号绑定 → 渠道绑定 → `_default_agent_id`，
  五级全空时抛 `AgentConfigurationNotFound`。而四个 IM 适配器、HTTP 旧接口和
  WebUI 路由全部走 `require_agent=True`——也就是说，一个刚 `up -d` 起来的实例，
  在用户手动建出第一个 Agent 之前，**任何渠道都不会回复任何消息**。
  现于 `init_agent_runtime` 里补 `provision_default_agent()`：仅当注册表
  **完全为空**时建一个 `default-agent`，绑定 `prompt` 与 `memory` 两类资源。
  三条边界写进测试：注册表非空时一律不动（不覆盖用户配置）；没有具备 Chat 能力位的
  模型时**不建**（否则把「你还没配模型」这个清楚的提示，换成一次运行时解析失败）；
  不绑 `mcp` / `hook`（那两类会拉起进程，冷启动时不该由兜底逻辑代为决定）。
  provisioning 自身失败只降级、绝不阻断启动。

  **二是那句英文是全项目唯一暴露给用户的英文报错**，且内容对不知道 Agent 是什么的人
  零信息。而同一个失败在另外三个渠道上呈现完全不同：企业微信有一套按错误类型分派的
  中文说明（超时 / 认证 / 限流 / 网络），OneBot 与 QQ 官方机器人**只记日志**——
  用户那侧完全静默。静默是三者里最糟的：用户无法区分「机器人挂了」与「我的消息没发出去」，
  只会反复重发，而每次重发都再走一遍那条失败链路。现抽出
  `kirara_ai/im/dispatch_failure.py`，四个适配器共用；分类判据是「用户接下来该做什么」
  而不是异常继承关系（认证失败与参数错误都是 4xx，但一个去改凭据、一个去改请求），
  且**配置缺失不说「稍后再试」**——重试永远不会成功。原始异常文本截断后保留，
  因为运维拿它去搜日志。

  **三是给 OneBot / QQBot 补的失败回复必须让取消先落地。**
  `asyncio.CancelledError` 继承 `BaseException`，顺序反了会在正常停机时给每个在途会话
  都发一条「处理失败」。发送提示本身失败时单独接住并把原始异常继续上抛——原因在那里面。

- **模型优先链要手打模型 ID，而这些名字就在另一个页面上（需求 8、10）**：
  「新建 Agent」页的「主模型 ID」与「Provider 白名单」原来是纯文本框，
  而模型名与后端名**就在 `GET /llm/backends` 的返回里**，也就是「模型配置」页显示的那些。
  手打的后果不是「多打几个字」：模型 ID 拼错不会当场报错，Agent 保存成功，
  直到某次真实对话解析不到那个模型才失败——那时看到的是运行时错误，
  与「我三天前拼错了一个字母」看不出关系。

  新增 `webui/src/views/llm/agentModelChoices.ts` 汇总候选，判据与后端
  `_chat_capable_models` 对齐：同时看 `type === 'llm'` 与 Chat 能力位
  （`ability & (1 << 1)`）。只看 `type` 会放进一个不能聊天的 llm，界面列得出、
  启动兜底选不中。停用后端的模型仍然列出但标「（未启用）」——直接过滤会让
  「我明明配过这个模型」变成找不到的东西；可用的排前面。同一模型 ID 只出现一次并
  记下所有提供它的后端（多后端提供同一模型是故障转移的正常形态）。
  控件用可筛选**可创建**的 `n-select`：做成纯下拉会让「模型来自尚未登记的后端」与
  「先配 Agent 再配供应商」两种正当用法变得不可能，因此拼错只提示不拦保存。
  Provider 白名单不过滤停用后端也不排到后面——白名单是策略声明，
  而一家后端今天停用明天启用是常事。

- **动态配置表单的占位符一直显示默认值，`examples` 永远不生效（需求 15）**：
  用户指着模型配置页的 `Api Base` 问「这个网址为何不能修改」。那一格显示
  `https://api.openai.com/v1` 是**占位符**（字段本身可编辑，后端
  `OpenAIConfig.api_base` 声明了该默认值），不是已填的值。顺着那行读下去是一处
  没有症状的缺陷：

      property.examples?.[0] || (property.default !== undefined && ...) ? String(property.default) : ''

  `||` 的优先级高于 `?:`，整个条件被求成 `(examples?.[0] || default存在)`，
  为真时**一律**取 `String(property.default)`——`examples` 从来不会被用到。
  它不报错、不白屏，只是所有给了示例值的字段都在显示默认值，而 `examples` 恰恰是
  「照这样填」那个提示（`owner/name` 之于一个仓库坐标字段），`default` 是
  「不填就用这个」——后者不需要提示，因为它反正会生效。现抽到
  `webui/src/components/form/fieldPresentation.ts`：`examples[0]` > `default` > 空串，
  `false` 与 `0` 当有效值（用 `||` 判断会跳过它们，而默认关闭的开关、默认 `0` 的超时
  都是正常声明），只读只认显式 `true`（把「没声明」当只读会让一整页表单静默变成不可填）。
  放在 `.vue` 里只能靠 grep 源码「验证」，而那种断言看得见字符串、看不见运算优先级。

## [3.3.0b15]

本轮以「1.txt 逐条要求」为验收口径，先由只读子代理建立现场证据，再对每一处
**有 file:line 证据、可构造失败用例**的缺陷补回归测试、改最小范围代码、跑聚焦测试。
未能在本机验证的外部场景（真实 Docker 重启、真实 QQ 扫码、真实多 Provider 上游、
真实客户端渲染）一律标记为未验证，不计入已完成。

补充一轮现场报障（compose 重启后 QQ 显示未连接、工作流块重叠、二维码总是过期、
流式与非流式、「系统显示成功到收到回复间隔很久」、回复排版含 `$\to` 残片）的定位与
修正也记在本节。这一轮的证据链有两条与前几轮不同的地方：一是把用户贴出的**原始
回复文本与原始日志行**直接喂进渲染与解析函数复现（而不是构造相似输入），
二是对「Telegram / WeCom 没有这个现象」这类对照说法逐条核实——其中「节流只有 QQ 有」
成立，而「LaTeX 完全没处理」不成立（处理存在，缺的是若干同义命令与配对边界）。

### Fixed（发布后补修）

- **`webui/yarn.lock` 与 `package.json` 对不上，CI 从干净环境装不起来**：
  上一个提交给 `package.json` 加了五个依赖（`date-fns` / `highlight.js` /
  `semver` / `@codingame/monaco-vscode-configuration-service-override` /
  `vscode-languageclient`）而没有更新锁文件。逐条核对差异：
  `highlight.js` 要 `^11.11.1` 而锁里键是 `^11.8.0`；`vscode-languageclient`
  要 `^9.0.1` 而锁里是 `~9.0.1`；`semver` 要顶层 `^7.7.1` 而锁里只有传递依赖的
  `^7.3.6/7.5.4/7.6.3`。于是 `yarn install --frozen-lockfile` 直接失败：
  `error Your lockfile needs to be updated`。

  **为什么本机 1172 个测试全绿却没发现**：本机一直用 `npx --no-install vitest`
  与 `npx --no-install vue-tsc` 跑，`node_modules` 早就装好了，锁文件从未被校验过。
  CI 是干净环境、必须从锁文件重建，因此只在那里暴露。这是验证方式的盲区——
  把「测试全绿」当成了「装得起来」。

  重新解析锁文件时还踩到第二层：本机 `~/.npmrc` 指向 `registry.npmmirror.com`，
  于是新解析出的三条 `resolved` 带上了镜像地址。
  `tests/test_webui_build_contract.py` 的白名单守卫当场抓住了它——
  那条测试正是为「镜像地址漏进锁文件」写的。改用
  `yarn install --registry https://registry.npmjs.org` 重新生成后通过，
  并实测 `yarn install --frozen-lockfile` 退出码 0。

- **`context-mode` 与 `caveman` 的依赖建模是错的**（需求 10）：
  两条登记项此前标为 `kind="claude-plugin"`、`install_supported=False`、
  探测宿主 `claude --version`，理由写着「装在操作者自己的 Claude 配置里，
  不是服务器运行时组件」。实测推翻了这个前提：

  ```
  npm view context-mode version   ->  1.0.169
  npm ls -g --depth=0             ->  context-mode@1.0.169
  package.json  "bin": {"context-mode": "./cli.bundle.mjs"}
  自述：Works with Claude Code, Gemini CLI, VS Code Copilot, OpenCode, Codex CLI
  ```

  它是 npm 上有 `bin` 入口的普通包，不绑定任何宿主，完全可以装到 Linux VPS 上。
  按 `claude --version` 探测会**两个方向都答错**：装了 `context-mode` 但没装
  Claude CLI 的 VPS 报 `missing`，装了 Claude CLI 却没装 `context-mode` 的机器
  报 `ready`。现改为 `kind="cli"`、探测 `context-mode --version`、
  安装 `npm install -g context-mode`。

  `caveman` 一并纠正但结论不同：它同样有自己的可执行文件（本机
  `caveman-installer@2.0.0`，`bin: caveman`），但公共 npm 上
  `caveman-installer` 是 404、`caveman` 是一个无关的 JS 模板引擎。
  因此它保持只探测不代装——**原因是分发渠道而不是「它是插件」**，
  运维指引里写明这两个名字各是什么：猜一条 `npm i -g caveman` 会装上模板引擎，
  命令存在、探测通过、功能完全不对，比报 `missing` 更糟。

  两条同时加入 `_SKILL_NAME_DEPENDENCY_IDS`：此前被排除让需求 10 点名的五个工具
  里有两个永远不参与技能就绪判定，技能广告不会说「服务器上没有这个命令」，
  模型照着一份它执行不了的说明自信作答。「能不能代装」与「要不要参与就绪判定」
  是两个问题——后者的判据是「这台机器上有没有这个命令」，而那对两者都有答案。


### Added

- **两个体检脚本：把「撞一个修一个」换成「一次列全」**（需求 14）：
  前几轮的缺陷是一处一处踩出来的——改正文搜索时踩到名称字段不存在，
  回答 filesystem 预设那个问题时撞见受管 MCP 配不了。找到一种形态之后
  应该扫这一整类，而不是等下一次再踩。

  `scripts/audit_api_field_coverage.py` 比对「后端响应里的键」与「前端声明或
  读取过的字段」。这一类已经出过四次（cache_write tokens、会话渠道身份、
  pricing display_name、资源 name/description），共同形态是后端算了、
  接口返回了、TypeScript 类型没声明，于是前端读到 `undefined`，
  而 `tsc` 发现不了（缺一个可选字段完全合法）。
  **本次核查结论：15 处候选逐个读完，没有一处是真缺陷**——
  `expires_in` / `deleted_count` / `conflicts` / `missing` 都在错误分支或前端
  不读的返回里，`truncated` 前端读的是 `X-Export-Truncated` 响应头。
  脚本自己的两处误报也已修掉（只认多行 `export interface`，而这个库大量
  把响应类型写成单行或 `http.get<{...}>` 内联）。

  `scripts/audit_source_grep_tests.py` 分类哪些测试**只**读源码文本。
  这是「测过了但其实没测」的存量清单：`documentAuthoring` 那次正则丢反斜杠
  之所以能瞒过测试，就是因为断言是 `expect(source).toContain('正文不能为空')`
  ——字符串在，行为不在。首次运行：448 个测试文件里 21 个是纯 grep，全在前端。
  **已清到 10 个，且这 10 个都登记了同题的行为测试**（见下面那条护栏）。
  源码 grep 本身不是错的——import 清单、`data-test` 钩子、逐字文案、
  「不该有某段代码」这类否定断言确实只活在源码文本里。坏的形态是
  **整个文件只 grep**：那时没有任何一行产品代码被执行过，
  一次等价重构会让它红（把 `!!entry.error` 写成 `Boolean(entry.error)`），
  而一次真正的行为退化不会（把 `&&` 写成 `||`）。

- **护栏：纯 grep 测试必须有同题的行为测试兜着**（需求 14）：
  `tests/test_grep_only_tests_have_behaviour.py`。配对写在显式表里而不是靠
  命名约定推——一个测试该由谁兜底是人的判断，写下来可以复核，
  靠猜文件名会在改名时静默失效。答案允许是「后端已覆盖」（填 `None`），
  但必须写明理由：`agent-reply-stream-mode` 与 `llm-circuit-reset-control`
  就是这一类，它们的判断分别在 `resolve_reply_stream_mode`（三份后端用例）
  与熔断重置的服务端语义里，前端只是把选项摆出来并原样提交。
  三条附加断言防止这条护栏自己变成空的：登记的兜底文件必须存在、
  必须真的 `import ../src/` 下的模块（否则它可能又是一份 grep）、
  以及普查脚本自己要能分辨两种文件（否则上面几条全是恒真断言）。

- **内置 Hook 每轮多付约 7 秒的进程启动开销**（需求 10 / 14）：
  `kirara_ai/agent_runtime/__init__.py` 原来是六行 eager import。
  而这个包同时是一个**独立命令入口**的父包：内置 `hook:ai-debug` 的五个事件各自起
  `python -m kirara_ai.agent_runtime.audit_hook_command <Event>` 子进程，
  而 `-m` 按 runpy 的规定必须先导入父包。于是那个自称「零依赖」的命令
  每次都要先把 executor 拉进来，连带 pydantic 与 asyncio。

  实测（本机，5 次取平均）：`-m` 走包 `__init__` 是 **1.52s**，
  直接跑该文件是 **0.14s**——每次多付 1.38s。内置 hook 一轮对话触发五个事件，
  也就是**每轮多付约 7 秒**，而每个 hook 的 `timeout_ms` 是 **5000**。
  这不是测试环境的怪相：生产里每一轮都在付，且负载稍高就会真的超时，
  那时 `_terminate_process_tree` 杀掉子进程、那个事件记成失败——
  症状是「hook 有时不触发」，而原因在一个与 hook 无关的文件里。

  改成惰性导出（PEP 562 的 `__getattr__` + 显式映射表）后
  `-m` 降到 **0.10s**，一轮五个事件从 7.6s 降到 0.58s。
  `from kirara_ai.agent_runtime import AgentRegistry` 这类写法一字不改。
  顶层 `kirara_ai/__init__.py` 早就因为同样的理由是惰性的，这里补上同一条。

  `tests/agent_runtime/test_lazy_package_exports.py` 钉住两种**没有症状**的坏法：
  映射表漏一个名字（`from ... import X` 抛 `AttributeError`，读起来像 API 被删了）、
  以及有人在别处加回一行 eager import（性能悄悄退回去而所有测试照样绿）。
  它在干净子进程里断言「导入包之后 `sys.modules` 里只有包本身」，
  并同时验证取用之后子模块**真的**被加载（否则一个永远返回 `None` 的假实现也能通过），
  以及命令入口的协议输出与非法事件拒绝都没变。

- **端到端锁住「前端一个输入框自动带上五类插件」**（需求 10）：
  `tests/web/api/llm/test_webui_chat_plugin_chain.py`。
  这条链此前**在测试里是断开的**：`test_persistent_resource_runtime.py` 证明
  「持久化资源能驱动一次真实回合」，但它直接调 `executor.run()`；
  而前端点「发送」走的是 `POST /api/llm/chat`，中间还隔着 HTTP 鉴权、
  `WebUIAdapter`、`WorkflowDispatcher`、渠道身份解析、Agent 注册表解析——
  那一整段只有用替身（`_ChatRuntime`：把 `options` 记下来就返回，
  **没有任何资源被读过**）做的契约测试。两头都绿，中间没人验证，
  正是本项目反复出现的缺陷形态。
  新用例把真实件全部接上（真的生命周期服务、目录安装、Agent 注册表、
  Hook 运行时、运行时执行器、派发器、Quart 应用与鉴权），只替换两处**外部依赖**：
  LLM 上游与 MCP 传输层（`context7` 要 `npx` 拉进程，而这条问的是
  「工具有没有被广告、结果有没有回到对话里」，真进程那一路仍由 integration 用例覆盖）。
  **请求体里一个插件名都不出现**——这是「自动选择」的判据：只发一句话和
  `session_id`，五类插件靠「渠道身份 → Agent → 绑定」自己被选中。
  十一条断言分别钉住：提示词进 system、记忆策略进 system、MCP 工具出现在
  **第一轮**的 tools（第二轮不算，模型是靠第一轮决定要不要调用的）、
  工具真的被调用且结果回到第二轮、五个 Hook 事件在这一轮真的触发、
  **未绑定的资源不被带上**（「全都带上」与「按绑定选中」在成功路径上看起来一样）、
  会话落到 VPS 数据目录、`session_key` 由渠道身份构成、以及第二轮复用同一 Agent。
  第十一条是需求 10 那句「其他使用者……一律忽视，但是仍然会进行正常的 AI 结合
  插件处理后的回复」的双向判据：非属主的请求**仍然得到回复且提示词照常生效**
  （一律 403 就违反了后半句），但拿不到任何工具、也不跑命令型 Hook。

- **第三个体检脚本：后端建好了、界面到不了的路由**（需求 14）：
  `scripts/audit_unreachable_routes.py`。这一类此前出过三次，每次都是
  「能力建好、测过、写进文档，就是用不了」——`/llm/auto-detect-schedule`
  三条（文档里明写「没有对应的 WebUI 界面」）、`/tracing/llm/export`
  （存在数月无按钮）、`/resources/imports`（scp 上去的包没有入口安装）。
  这种形态对用户读起来与「没做」完全一样。
  180 条路由过一遍，扣掉被批量接口取代的细粒度路由、机器面向的端点、
  当链接打开而非 fetch 的下载地址之后，剩下**两处真实缺口**（见下两条）。
  脚本自身修了三处误报：`${encodeURIComponent(id)}` 里的括号让插值正则提前
  收尾（一度把 28 条资源路由全报成不可达）、查询串没剥（`?${params}` 让 6 条
  真在调用的路由落榜）、以及 `${getApiPrefix()}/traces` 这种前缀助手。
  报告里直接印出「哪些原因是正当的」，避免下一次把同一批正当项重读一遍。

- **Agent 可以删除：`DELETE /agents/<id>` 接上界面**（需求 10）：
  `AgentRegistry.remove()` 写得很完整——默认 Agent 不能删、还有渠道绑定的不能删、
  还有账号或会话绑定的不能删，三条各自抛带原因的 `ValueError`。
  而界面上一个 Agent 只能新建和编辑：**建错一个名字就永久留在列表里**，
  而它仍然参与「渠道身份 → Agent」的解析——一条发到那个渠道的消息会落到它身上。
  确认框里写出 Agent 的名字（显示名优先、回落到 ID）而不是「确定删除吗」：
  列表里的行看起来很像，而这个操作会一起带走模型链、资源绑定与渠道关系。
  后端那三句拒绝**原样显示**，不换成「删除失败」——它们都是用户能照做的
  （先改默认、先解绑），通用文案会把可解的问题变成死胡同。
  新建中的 Agent 不显示删除按钮（它还不存在，按钮会被读成「取消新建」），
  删除后先清空编辑器再重取列表（留着一个已不存在的 Agent，
  下一次「保存配置」会把它重新建出来）。

- **供应商配置可以回滚：`POST /llm/backends/restore` 接上界面**（需求 8）：
  `save_config_with_backup` 每次写入前留一份 `config.yaml.bak`，这条接口把
  `llms.api_backends` 单独取回来（**不是**整份配置回滚——那会把同一时间改过的
  Web 端口、IM 适配器、工作流一起退回去）。定价目录早就有「恢复」按钮，
  供应商配置这条更敏感的路径（凭据、容错参数、路由都在里面）反而只能登服务器
  手工编辑 YAML。
  按钮与「导出配置 / 导入配置」并列，因为三者都作用于「全部供应商」这一层，
  放进列表侧栏会被读成「恢复选中的那一个」。
  确认文案说清丢的是**最后一次保存的供应商改动**——「恢复备份」听起来是安全操作。
  提示里分开报 `restored_count` 与 `loaded_count`：两个数字不等意味着有后端
  恢复了但起不来（Key 失效、地址不通），只报一个会让那种情况看起来完全成功。
  404 说成「还没有可恢复的备份：首次保存之后才会生成」——
  没有可恢复的东西是正常状态，不是故障。

- **受管 MCP 资源终于能在本机配置：`PUT /resources/<id>/runtime`**（需求 10）：
  `mcp:filesystem` 的描述写着「启用前必须在 args 末尾追加允许访问的目录」，
  而这件事**在产品里做不到**。受管 MCP 资源住在资源注册表里，
  唯一的编辑路由 `PUT /mcp/servers/<id>` 只在 `config.mcp.servers` 里查找，
  因此它对任何受管资源都返回 404——尽管 `MCPList.vue` 给每一行都渲染了「编辑」按钮。
  实测：装完 `mcp:filesystem` 之后 `config.mcp.servers` 是空的，
  args 是 `['-y', '@modelcontextprotocol/server-filesystem']`，没有目录、没有 `roots`。
  于是一个「已启用」的 filesystem 服务器连得上、却没有任何可操作范围，
  而界面上看不出这一点。
  **不改归档里的 `server.json`**：那份声明有 `content_sha256` 护着，
  它是「目录发布了什么」；一个本机目录白名单是「这台机器允许什么」。
  混在一起会让每配一个目录都变成一次版本递增加一次备份，
  并且升级时用本机路径覆盖上游的新声明。
  因此新增的是**运行时覆盖**：存在可变的注册表记录里（`runtime_overrides`），
  由 `MCPServerManager._configured_servers()` 在构造 `MCPServerConfig` 时合并。
  可覆盖的键按构造只有 `extra_args` / `env` / `headers` / `cwd` / `roots` /
  `startup_timeout_ms`——`command` / `args` / `type` / `url` / `id` **不可覆盖**，
  那是摘要保护的身份，放开它们等于让「配一个可读目录」可以把 `npx` 换成任意程序。
  `extra_args` 是**追加**而不是替换：包名留在摘要保护的那一段里，
  上游后续给 base args 新增的参数也继续生效。
  覆盖在升级与回滚后存活（它描述的是这台机器，与装的是哪一版无关），
  删除资源时跟着消失（不留给下一个同名资源）。
  接口限创建者：一个目录白名单正是需求 10 说的「通过插件修改服务器内容或
  执行文件操作」的范围本身。`env` / `headers` 的值在**所有**资源响应里打掩码，
  与 `GET /mcp/servers` 的 `_redact_transport` 同一条规则——
  两个接口回的是同一份东西，一个遮一个不遮等于遮了也没用。

- **提示词可以从界面创建与改写：`POST /resources/documents` + `PUT /resources/<id>/documents`**（需求 10）：
  需求 10 点名「Claude 提示词管理」，参考界面上那一页有一个「+ 添加提示词」按钮。
  本项目此前的状态是**读得出、装得进、改不了、建不了**：`GET /<id>/content` 能看正文，
  三条安装路径（ZIP 上传 / 内置目录 / GitHub 与 skills.sh）都在，
  而「自己写一条」**没有任何接口**——用户得按 `manifest.json` 的八个必填字段手算
  `content_sha256`（还是 `path:size:sha256` 逐行拼接再哈希这种非常规算法）、
  打成 ZIP、再上传。
  提示词这个类型的**全部内容就是正文**：没有可执行文件、没有依赖、没有外部来源。
  要求为一段纯文本走一遍归档打包与摘要计算，等于把这个类型最主要的用法排除在
  产品之外。而 `_install_builtin()` 早就在服务器侧做着完全一样的事——
  缺的只是把它开放给用户内容的那一层。
  **没有因此放弃完整性契约**：新增的 `author_document()` 走的是与内置目录条目
  逐字节相同的打包路径（同一套摘要算法、同一个 `install_archive`），
  落盘后与一条内置提示词同形，`read_entry` 的摘要校验照常生效。
  改正文走**版本递增**而不是就地改文件：`content_sha256` 把清单与文件绑在一起，
  就地改的后果不是「改了没生效」，而是这个资源在下一次载入时直接失败。
  五条边界：只接受 prompt / memory / session（skill 的正文会被当作行为说明执行、
  hook 能起进程、mcp 启用即拉进程，那三类必须继续走「打包 + 审阅 + 显式确认」）；
  装完保持停用并需要确认（提示词会进系统提示词、改变每一轮回复，「保存即生效」
  会让一次手误立刻作用到所有对话）；权限只有 `workflow.read`；创建者身份
  （与其他写盘路由同一边界，已并入 `test_creator_only_routes.py` 的契约清单）；
  摘要由服务器算，请求方提交 `content_sha256` 直接 400。
  更新路径的类型从**已安装的注册表**读而不是请求体——否则可以先上传一个 skill
  的 ZIP、再用纯文本接口改它的正文，绕过打包与审阅。
  界面：工具栏「新建提示词」与「发现并安装」并列（后者从外部拿现成的包，
  前者写自己的内容），纯文本类型的行上多一个「编辑正文」图标，
  编辑时预填当前正文与一个递增的版本号（后端要求严格递增，让用户自己猜
  等于把一个必然的约束留给他去撞）。
  端到端证据在 `tests/agent_runtime/test_persistent_resource_runtime.py`：
  「装进去了、摘要对得上」不足以说明这条路径有用——一个装得进去却进不了请求的
  资源，在界面上与一条生效的提示词长得一模一样。该用例走完
  `author_document()` → `resolve_binding()` → `executor.run()`，断言正文出现在
  `messages[0]`（system）里；并且改正文之后**新版本的正文到达模型、旧版本的不再出现**
  ——少了后半句，一个「读到了旧版本」的实现也能通过前半句，
  而那种缺陷的症状是「我改了提示词，模型行为没变」。

- **故障转移队列可在看到队列的地方排序：`PUT /llm/resilience/queue` + 容错面板上移/下移**（需求 8）：
  需求 8 的队列语义是「按队列优先级选择供应商（P1 优先）」。排序此前只能在供应商编辑
  表单里逐个填 `priority` 数字：想把 P3 提到 P1，得先记住另外两家各是多少、再算一个
  中间值填进去，而这三次编辑分散在三个不同的表单里。队列页（容错状态）能看到实际
  次序却**只读**——看的地方和改的地方分离。
  新接口按「一次给出整条队列的新次序」工作，而不是「把某一家改成某个数字」：
  后者会经过中间态——把 P3 改成 1 的那一刻队列里出现两个 1，而相等优先级的相对次序由
  `active_backends` 的列表下标决定，也就是用户看不见的东西。
  三条边界写进实现：**必须给全**这条队列的每一家（缺一个，它落在哪里取决于旧数字，
  而用户以为排的是整条队列）；**复用已在用的数字**只交换「谁拿哪个」（同一家可以同时
  服务多个模型，凭空抬高它的 priority 会连带改动另一条此刻不在屏幕上的队列）；
  **相等的数字要拆开**（全是默认 100 时多重集重排是恒等变换，保存成功而次序没变，
  正是「新建三个供应商后拖不动」的现场表现）。落盘失败时内存里的次序退回原值——
  不退等于「界面显示新次序、重启后回到旧次序」，而这个差异没有任何地方会报出来。
  权限与重置熔断同一边界（`require_creator`）：它改变服务器接受流量的方式。
  界面用上移/下移按钮而不是拖拽：键盘可达是硬要求，拖拽要额外补一套键盘操作才等价，
  两套交互对同一件事时出错很难判断是哪一套没生效。

- **熔断参数改完立刻生效，不必重启**（需求 8）：五个 `circuit_*` 参数此前只在熔断器
  **被创建的那一刻**被读进去——`_initialize_resilience_state()` 用 `setdefault` 建
  breaker，已存在的那个不会被新参数重建。而 `PUT /llm/backends/<name>` 的 unload / load
  两个分支各有条件（未加载的不会 unload、停用的不会 load），编辑一个 `enable=False`
  的后端时两者都不成立：用户把失败阈值从 8 改成 3、界面提示保存成功，
  而下一次故障仍按 8 次才熔断。这个后端也不在 `get_resilience_status()` 的行里
  （那里只遍历 `active_backends`），所以界面上连「重置熔断」这个变通入口都没有。
  新增 `CircuitBreaker.reconfigure()` 就地换阈值并**保住运行时状态**：当前是否熔断、
  在途计数、已积累的成败样本、迁移历史全部保留。把 breaker 整个替换掉等于每次编辑
  配置都取消一次正在生效的隔离——用户改的是超时数字，被改掉的是「这家已经被隔离」，
  那比参数晚生效更糟。样本窗口跟着 `min_requests` 走（窗口比它小时错误率阈值
  被静默关掉），收窄时保留最近的样本而不是最早的。

- **Gemini 与 Ollama 接入请求整流器，规则按各家请求体形状分派**（需求 8）：
  供应商编辑页上有五个整流开关，而这两家适配器此前连 `rectify_request` 都没有
  import——那些开关对它们**从未参与任何决策**。
  照搬 `messages` 形状的规则会更糟：`rectify_thinking_budget` 不做键存在性校验就写入
  `body["thinking"]` 与 `body["max_tokens"]`，这两个是 Anthropic 的顶层字段。
  Gemini 对未知顶层键直接返回 400 INVALID_ARGUMENT，而真正的预算位
  （`generationConfig.thinkingConfig.thinkingBudget`）反而没被改——一次「整流」把可重试
  的错误变成必然失败。
  现在由 `detect_payload_shape()` 识别形状（`messages` / `gemini` / `ollama`），规则表
  按形状分派：Gemini 改 `contents[*].parts` 与 `generationConfig`，Ollama 改并列的
  `images` 数组与顶层 `think`。某家没有对应位置的规则在那家**不出现**（Gemini 不回传
  思考签名、Ollama 的思考只有开关没有预算），而不是空转出一个「试过了但没改成」。
  新增一类整流「模型不支持思考」：与预算越界分开，因为动作不同——预算越界改一个数字，
  不支持思考要把整个配置去掉，合成一条会让只需降档的请求彻底失去思考能力。
  两家的非流式与流式两条路径都接，且每类只改一次。

- **创建者渠道身份可从界面声明：`creator_channel_identities` 接进配置读写**（需求 10）：
  需求 10 的前半句「只有创建者能通过插件修改服务器内容」在默认部署下**此前无法成立**。
  `global_config.py` 里那段类注释自己承认了这个形态：「结果不是『非创建者不行』，
  而是『所有人都不行』，包括创建者本人：MCP 工具列表恒空、command Hook 恒被拒」。
  桥（`creator_channel_identities`）早已架好，运行时也真的在读它——断的是**配置入口**：
  `GET /system/config` 不返回这个字段，`POST /system/config/agent-runtime` 的可写键
  集合里没有它，WebUI 里搜不到任何编辑处。唯一的配置方式是登服务器手改 `config.yaml`
  再重启。用户在 QQ 里对自己的机器人说「帮我装个 skill」，得到一次正常回复、
  工具一个没生效，而界面上没有任何地方解释为什么。
  现在可读、可写、可在「Agent 运行时」面板逐条增删。五项边界校验：
  渠道名必须在 `SUPPORTED_CHANNEL_TYPES` 内（写错会静默匹配不上任何消息）、
  发送者标识非空（空标识会匹配上谁？这个问题不该留给运行时回答）、
  两个可选字段要么省略要么非空、`allow_group_chat` 必须布尔、多余字段直接拒绝
  而不是忽略（静默丢弃拼错的键会让用户以为设置生效了）。
  三处刻意保留的语义：**空数组是有效值**（撤销全部声明）而不是「没填」；
  **没提交这个键时保留原值**（与该端点其余字段的 `exclude_unset` 一致——把创建者
  身份清空的后果是聊天侧插件能力全废）；**`allow_group_chat` 缺省 `False`**
  （群里所有人都看得到创建者发的指令并照抄，把宿主操作暴露在多人可见会话里要显式打开）。

- **`SessionEnd` / `SubagentStart` / `SubagentStop` 三个 Hook 事件真的被派发**（需求 10）：
  `HOOK_EVENTS` 声明 11 个事件，executor 此前只派发 8 个。剩下三个通过了声明校验、
  落了盘、在界面上显示为「已启用」，运行时一次都不触发——而 `hooks.py` 甚至专门为
  `SubagentStop` 写了 `decision: block` 的解析分支，代码本身认为这个事件会来。
  用户挂一个 `SessionEnd` 钩子做清理、挂 `SubagentStop` 审计队友委派，
  调试到最后才发现钩子从来没跑过，没有任何一处提示过他。
  `SubagentStart` / `SubagentStop` 派发在 `_delegate_to_teammate` **内部**而不是调用处：
  这样无论将来从哪条分支触发委派，两个事件都必然成对出现；`Stop` 放在 `finally` 里，
  委派失败、队友无输出、异常三条路径都会闭合，不会在审计里留下一个永不结束的
  `SubagentStart`。`agent` / `snapshot` 由调用处传入而不是方法内重新解析——
  重新解析会拿到与本轮快照不同的资源版本，Hook 看到的就不是这一轮真正生效的绑定。
  新增 `tests/agent_runtime/test_hook_event_contract.py` 双向锁住契约：声明了必须能派发，
  派发了必须已声明（后者缺失时声明校验会拒绝用户为它写钩子，于是那个派发点永远没有消费者）。

- **会话渠道身份持久化，`SessionEnd` 才有真实的 `ChannelContext`**（需求 10）：
  会话文件按 `[session_key, agent_id]` 的 SHA-256 摘要命名，**原始渠道身份从未存进文件**。
  而 Hook 需要一个真实的 `ChannelContext`。两条错误的走法都被排除：用占位 context 派发
  会让 Hook 拿到虚构的渠道身份写进审计——比缺一个事件更糟，审计记录从此不可信；
  把 `SessionEnd` 从契约里删掉是把「没实现」改写成「不支持」，而三处代码都为它留了位置。
  选的是让会话文件记住自己的身份：`save_history` 落盘五个标识，
  新增 `read_session_metadata` 供清理路由反查。旧文件读不到就跳过派发，
  但**清理照常成功**——因为拿不到身份就拒绝清理，等于让用户永远删不掉升级前的会话。
  `clear_history` 保留身份：丢了的后果是这个会话从此再也派发不出 `SessionEnd`。

- **`runtime_dependency` 声明被真正消费，界面能说出「缺 uvx」**（需求 10）：
  8 个 stdio MCP 预设各自声明了需要什么运行时（`mcp:fetch` / `mcp:time` 是 `uvx`，
  其余 6 个是 `npx`），但这个字段**从来没有人读**。三层都断：
  ① `uvx` 连登记项都没有——登记表里只有 `uv --version` 的 `python-tooling`，
  而 `uv` 装了不等于 `uvx` 可用（uvx 是 uv 0.3 起才分发的独立入口）；
  ② `dependency_ids_for_resource` 硬编码只认 `mcp:context7` / `agent-browser` / `graphify`
  三个名字，其余 7 个预设一律返回空列表，而空列表的含义是「这个资源不需要任何系统依赖」；
  ③ 前端从不渲染后端 `project_dependencies` 已投影的四个字段，`CatalogItem` 的 TS 类型里
  一个都没有。净效果：装 `mcp:fetch`、启用、绑定全部成功，机器上没有 uvx，
  界面唯一的线索是 MCP 面板显示「连接失败 / 工具数 0」——没有一处说缺什么，
  用户会去查网络、查配置、查 API Key。
  新增 `uvx-runtime` / `npx-runtime` 两条登记项与 `_RUNTIME_DEPENDENCY_IDS` 映射，
  前端抽出 `ResourceDependencyProjection` 共享接口并在资源表格状态列渲染提示。
  一处刻意的区分：**`unknown` 与 `missing` 不合并**——前者是「还没探测过」，
  下一步是去检查；后者是「探测过、确实没有」，下一步是去安装。混成一句会让用户
  去装一个本来就在的东西。`dependency_status` 为 `undefined`（老后端不提供该字段）时
  不显示任何提示，那与「不需要依赖」不是一回事，但都不该占用界面。

- **readiness 报出「静态构建与后端版本不一致」**（需求 14）：
  `app.py:71` 把 `$PWD/web` 作为静态目录，而 `webui/` 才是源码，两者版本各走各的：
  仓库根 `web/version.json` 是 `0.1.1-beta.3`，`webui/package.json` 是 `3.3.0-b14`，
  差了整整一个大版本。直接跑本地源码起服务时，浏览器看到的是那份旧界面——
  没有 skills.sh 来源选择器、没有依赖提示、没有本轮改的任何东西。
  失败形态是最难查的一类：**后端是新的，前端是旧的**。用户按新文档去点一个按钮，
  按钮不存在，他会以为文档写错了或功能没做，而 API 探针、健康检查、版本一致性检查
  全都通过——因为它们查的是后端。Docker 部署不受影响（`Dockerfile` 重新拷贝
  `webui/dist`），所以这个陷阱只在本地开发与源码部署时出现，恰好是最不容易被 CI
  覆盖到的路径。新增 `static_build_freshness()` 与 readiness 检查项 `static_build_current`。
  三条判定纪律：**不一致是 warn 不是 fail**（服务确实在正常响应，只是界面旧了；
  fail 会让健康检查把一个可用实例摘下线）；**读不到版本是 skip 不是 warn**
  （纯 API 部署没有静态目录，那是合法形态，报 warn 会让运维去修一个不存在的故障）；
  **修复建议给具体动作**——「在 webui/ 下重新构建并把 dist 拷到静态目录」，
  而不是复述「版本不一致」这个现象。

- **成本定价自动同步上游公开价目：`POST /llm/pricing/sync` + 调度器周期任务**（需求 9）：
  价格此前只能手工填或导入 JSON。`PriceCatalog.refresh()` 的语义是「重读本地文件
  以感知别的进程写入」，不拉远端，全文件没有任何 http 调用，也没有任何调度任务
  引用它做定时同步。用户新增一个上游后得自己去翻官网价目表，逐个模型敲四个数字，
  一旦记错单位（每千 vs 每百万），成本统计会整体偏一千倍且界面上没有任何提示。
  新增 `kirara_ai/llm/pricing_sync.py`，锁住四条容易悄悄错掉的行为：
  **不换算单位**（上游本身就是每百万，再乘一次就是前面说的千倍偏差）；
  **缺失字段按 0 而不是跳过整条**（真实响应里 openai 条目没有 `cache_write`，
  按「字段不全就丢弃」处理会让整个 OpenAI 系列没有价格）；
  **没有任何价格的条目要跳过**（否则落一堆 0 元模型，成本统计显示"免费"）；
  **网络失败不能污染既有价格**（同步是"读远端 + 写本地"两步，第一步失败时若已清空
  本地就等于一次网络抖动删掉了用户全部手工价）。
  手工价优先：已存在的手工版本不被自动同步覆盖。数字没变时不落盘，避免每轮同步都
  推高 revision 让乐观锁误判冲突。
  调度器复用模型自动检测那套状态与到期判定（`kirara_ai/scheduler/scheduler.py`），
  但**同步结果刻意不并入 `run_once` 的返回值**：那份映射的契约是「后端名 -> 检测
  是否成功」，调用方按后端名遍历它，塞一个 `__price_sync__` 进去会让界面上多出一行
  叫这个名字的"模型后端"。结果改由 `get_status()` 汇报，并把**「本进程还没同步过」
  与「同步过但失败了」分成 `null` 和 `false` 两种取值** —— 合成一个值会让界面把
  「刚启动」画成「上游挂了」。失败不打时间戳，否则要再等一个完整间隔才重试。
  前端 `webui/src/views/llm/PricingView.vue` 加手动同步按钮与间隔设置（0 天=关闭）。

- **WebUI 在线对话真流式：`POST /llm/chat/stream`（SSE）**（需求 4）：
  项目自己的 WebUI 在线对话此前是一次性 `POST /llm/chat`，后端没有任何聊天 SSE
  路由——四个入口里唯一连技术可行性都没有的一个。而这不是平台限制而是缺口：
  浏览器里一条 SSE 事件就是一次改写，它比 QQ 更有条件做到逐步显示。
  新增的路由与非流式路由共用同一条派发链路、同一个渠道身份、同一份 Agent 解析，
  事件为 `start` / `delta` / `reset` / `done` / `error`。
  `WebUIAdapter` 因此实现 `IncrementalDeliveryAdapter`（此前只有 Telegram 实现）。
  六条边界，每一条都对应一种「看起来成功其实坏了」：
  **推送的是新增那一段而不是全文**（协议规定 `update_incremental_reply` 收全文,
  那是为了让 Telegram 整条改写；向浏览器送全文会让每条事件随回复变长，
  一段 8 KB 的回复要传 O(n²) 字节，而 SSE 本就是追加式的）；
  **上游改写了已交付前缀时发 `reset` 而不是静默追加**（按尾巴追加会让浏览器拼出
  一段与服务端不同的文本，两边都认为自己是对的）；
  **校验错误留在流之外**（仍返回 400——塞进 SSE 会让「请求写错了」和「生成失败了」
  在客户端长得一样，而前者应当由表单立刻提示）；
  **运行期错误必须作为事件送达**（响应头在第一个字节之后就发出去了，此后无法再改
  状态码；这时抛异常，浏览器看到的是一个正常结束的空流——界面停在「正在生成」，
  只有后端日志里有错误）；
  **`done` 永远带完整文本**（`reply_stream_mode` 配成 `off` 或运行时一次增量都没推时
  流里没有任何 `delta`，不补就是空回复加一条成功日志）；
  **生产者结束必须放下队列哨兵**（不放的话消费端永远等在 `queue.get()` 上，
  表现与上一条相同但更难查，因为后端已经没有人在干活）。
  前端 `webui/src/views/llm/chat-stream.ts` 用 `fetch` + `ReadableStream` 而不是
  `EventSource`：后者只能 GET、不能带自定义头，而这条路要 POST 一个 JSON 体并带
  `Authorization`（把 token 放查询串会进日志与 referrer，鉴权中间件也明确拒绝）。
  解析按空行切事件而不是按 chunk（网络分片与事件边界无关：一个 `delta` 可能跨两个
  chunk，两个 `delta` 也可能挤在同一个 chunk 里；按 chunk 解析在本地开发几乎总是
  正常，上线后随机丢字），并用 `TextDecoder({ stream: true })` 流式解码
  （一个中文字符三字节，可能被切在 chunk 之间，逐块独立解码会在断点处产出 `�`）。
  界面上气泡在**首字节之前**就出现并显示进行光标——等第一个 delta 才插入的话，
  首字节之前界面上什么都没有，与非流式毫无区别，而首字节正是最慢的那一段。
  右上角保留「流式 / 非流式」开关：需求 4 的原文是「流式**和**非流式」，
  两条路径都必须存在；它也是唯一能在同一界面上核对「两条路径给出同一段回复」
  的地方，反向代理关掉分块传输时还能立刻切回去确认问题不在项目侧
  （响应已带 `X-Accel-Buffering: no`）。

- **模型目录自动检测计划有了界面：「模型 → 自动检测计划」**（需求 9）：
  后端一直提供三个接口（`GET /llm/auto-detect-schedule`、
  `PUT /llm/backends/<name>/auto-detect-schedule`、`POST /llm/auto-detect-schedule/run`），
  但前端**零调用点**，QUICKSTART 里明写着「这三个接口没有对应的 WebUI 界面，
  只能用 API 调用」。后果不是「少一个页面」：「模型目录会定期自动刷新」这件事在
  产品上完全不可见——运维无法回答「下一轮什么时候跑」「上一轮成功了吗」
  「这个后端到底开没开」，改一个间隔要手改 `data/config.yaml` 再重启整个进程
  （而重启会中断所有正在进行的对话）。
  新页面给出每个后端的间隔天数、上次成功时间、下一轮预计时刻与当前模型数，
  可直接改间隔（保存即生效，不需要重启）。三处刻意呈现：
  **`last_run` 为空显示「—」而不是编一个时间**（`null` 可能是从没到期、
  也可能是每次都失败，显示成真实时间会让人以为它跑过）；
  **调度循环没在运行时页首显著提示**（那种情况下所有间隔配置都不会触发，
  逐行显示「每 5 天」是一句谎话）；
  **「立即检测全部」带确认并说明影响范围**（它会访问每一个上游、消耗配额，
  并在目录有变化时改写 `data/config.yaml`，不是一次只读刷新）。
  另外不猜下一轮时刻：后台首轮延迟带 0–300 秒随机抖动，编一个「大约 X」
  会让人按那个时间去等。

- **跨渠道可比链路耗时：`GET /tracing/delivery/compare`**（需求 19.5）：
  19.5 的最后一句是硬要求「应给出 Telegram、WeCom 与 QQ 的**可比**链路耗时」。
  此前 `summarize()` 只接受一个 `channel`，界面上是一个下拉筛选器——要比较三个渠道
  得切三次下拉框，然后靠记忆对比六个阶段的数字。那不是可比，那是把对比推给人的
  短期记忆，而三个渠道 × 六个阶段的数字没有人能靠记忆比对。更要紧的是 19.5 要
  回答的问题本身就是对比式的：「QQ 慢，是 QQ 这条链路慢，还是模型本来就慢
  （三个渠道一样慢）」——单渠道视图永远给不出对照组。
  新接口把所有渠道的同一组阶段并排返回，口径与单渠道视图逐字一致
  （只对测到该阶段的行求平均、每项带样本数、一个都没测到时给 `null` 而非 0）。
  在对比视图里「`null` 不是 0」这条比单渠道时更严重：一个渠道显示 0 ms 首字节、
  另一个显示 2 s 时，看起来是前者快得多，而事实是前者根本没测。
  实现上是**一次分组聚合**而不是逐渠道循环：六个阶段 × N 个渠道 = 6N 次查询，
  而这张表在长期运行的部署上会长（默认保留 30 天），N+1 在这种表上是一个会随时间
  变慢的设计，而且慢下来的时候正好是最需要它的时候；
  `idx_im_delivery_channel_time` 覆盖 `(channel, recorded_at)`，分组聚合走它。
  「投递时间线」页新增对比表，高亮该阶段最慢的渠道，且只在**至少两个渠道都测到**
  这一阶段时标注——一个渠道独有的数字不构成对比，标成「最慢」会让读者以为已经
  比过了。

- **OneBot 冷启动宽限期：`initial_connect_grace_seconds`**（需求 1）：新配置项，
  默认 180 秒。适配器启动后这段时间内「还没有上游连进来」报 `initializing`
  而不是 `waiting`，readiness 给出「等待上游完成 QQ 冷启动与登录」而不是
  「去查心跳」。填 `0` 关闭。与既有的 `reconnect_grace_seconds` 是两个不同处境：
  那个的前提是曾经连上过。

- **OneBot 节流总额上界：`send_pacing_maximum_total_seconds`**（需求 5）：新配置项，
  默认 6 秒，约束一次投递里**按长度追加**的等待总额（页间最小等待不计入——
  它是「不连发」的硬保证）。此前只有单页上界，代价随页数线性累加。

- **二维码 `age_unknown` 状态与自走倒计时**（需求 3）：上游日志没有时间戳时如实
  报告「无从判断这张码是否还有效」，而不是编一个满额剩余时间；界面上的剩余秒数
  改为按 `expires_at` 每秒重算，归零后自动改口。

- **Agent 级 `reply_stream_mode` 的落盘与 REST 读写**（需求 4）：三层优先级的
  最上面一层此前跨重启不保留、接口也读不到写不了。

- **Tool Search：工具也走渐进披露，不再把几十个 schema 塞进每一次请求**（需求 8）：
  本项目此前只对 **Skill** 做了渐进披露（一行目录 + `skill_<id>` 工具），
  而 **MCP 工具**仍是全量注入。一台连了三个 MCP 服务器的部署很容易有几十个工具，
  每个带完整 JSON Schema，两处后果与当初 Skill 全量注入完全一样：
  成本随「工具数 × 请求数」线性增长（四十个工具各 300 token = 每轮固定多付
  12000 token），而且**模型在一堵墙前更容易选错**——几十个名字相近的工具
  （`read_file` / `read_text_file` / `read_multiple_files`）挤在一起时选择质量下降，
  这一点比成本更难发现，因为它表现为「AI 变笨了」。
  现工具数超过 `agent_runtime.tool_search_threshold`（默认 12）时，系统提示词里
  只放一行目录，完整定义由 `search_tools` 按关键词取回。四条边界：
  搜到的工具**立即进入本轮工具列表**（只给名字不放进列表，模型按目录做了正确的事
  却会撞「permission denied」，而那个错误无法自查）；搜索工具**搜完不收走**
  （第一次没搜准时才有第二次机会）；**白名单之外的工具既不进目录也搜不到**
  （搜索是取回路径，不是提权路径）；阈值 `0` 表示关闭，拿回逐字节一致的旧行为
  ——用阈值而不是布尔开关，是因为同一个开关在「三个工具」和「四十个工具」上
  一个是纯损失、一个是纯收益。

- **Telegram 真流式：生成中的内容边写边推给用户**（需求 4）：
  `aggregate` 只是把整个流在服务端吃完再发一条完整消息——用户端从来没收到过流式，
  等待期间界面上什么都没有。它买到的是首字节超时、静默超时与首字节前的故障转移，
  不是「用户看得见的流式」。新增 `incremental` 档：先发一条占位消息，
  再随生成用 `editMessageText` 不断改写同一条。
  这做成可选协议（`IncrementalDeliveryAdapter`）：QQ / OneBot 与企业微信没有
  等价能力，在那里逐步推送只会变成几十条碎片消息，比一条完整回复更糟，
  它们不实现该协议、运行时自动退回整段投递。
  （当时 Telegram 是唯一实现者；本轮 `WebUIAdapter` 也实现了它，见本节 Added。）
  五条边界：改写传的是**到目前为止的完整文本**而不是增量片段（传增量要调用方自己
  拼接，一旦与平台实际内容不一致，用户看到的就是错乱的文本）；改写有 1.2s 节流
  （逐 token 改写会撞频率限制，之后这条回复的所有更新全部丢失）；内容没变不发请求
  （平台会以「消息未修改」拒绝，那是一条会进日志的错误，看起来像故障）；
  **收尾不受节流约束**（被节流掉的收尾会让用户永远停在半句话上，而日志显示成功）；
  占位消息在**第一个非空片段之后**才建立（请求还可能因参数错误立刻失败，
  那时已发出的「正在生成回复…」就成了一条永远不会被改写的消息）。

- **回复取回方式可按 Agent 与按渠道配**（需求 4）：
  `reply_stream_mode` 原本只有一个进程级值，一个部署里所有 Agent、所有渠道共用同一档。
  而这两个维度本就该不同：一个接了慢上游的 Agent 需要流式带来的首字节超时保护，
  另一个走本地小模型、毫秒级返回的 Agent 打开它只是白付一次握手；
  而渠道之间对流式的承载能力也不同：Telegram 能编辑已发出的消息、WebUI 在线对话
  走 SSE，QQ / OneBot 与企业微信两者都没有。于是运维只能二选一，
  而两种选择都对一部分入口是错的。
  现优先级为 **Agent 显式声明 > 渠道默认（`channel_reply_stream_modes`）> 进程默认**。
  `AgentDefinition.reply_stream_mode` 缺省为 `inherit`——早于本特性的
  `registry.json` 没有这个键，缺省必须是跟随，否则升级会改掉所有既有 Agent 的
  取回方式。`inherit` 与「没设置」的区别是可读的意图：它能把一个曾被显式设成 `off`
  的 Agent 改回跟随，而不必先查上层是什么。
  **无法识别的取值一律当作「跟随上层」，绝不当作开启**：把拼写错误理解成开启
  会让一处笔误静默改变整条取回路径，而配置界面上它看起来是有效的。

- **按正文检索已安装资源：`GET /resources?query=`**（需求 10）：
  需求 10 点名的搜索能力是「支持按名称、描述或**内容**检索」。此前的搜索是纯前端
  过滤，匹配面只有元数据——因为 `GET /resources` **不返回正文**。
  而提示词这个类型的全部内容就是正文，名称与描述只是用户随手填的一行字：
  装了十几条之后，「哪一条里写了『先给结论』」只能靠逐条点开看，
  而那正是搜索框存在的理由。
  **不能靠让列表接口顺带返回正文解决**：`read_entry` 每次读取都重新校验摘要
  （读清单、读文件、算 SHA-256），对每条资源都做一遍等于把一次列表请求变成
  N 次全文件哈希；正文本身可能有几十 KB，几十条就是一次几 MB 的响应，
  其中绝大部分与当次搜索无关；更要紧的是提示词正文会包含用户写进去的规则，
  把它无条件塞进每一次列表响应，等于让一个只想看清单的请求把全部正文都取回浏览器。
  因此新增 `search_resources()` 在服务器侧读正文、**只返回元数据**。
  四条边界：只对不含可执行内容的类型读正文（prompt / memory / session——skill 与
  hook 的正文是行为声明，把它们并进关键词搜索会让一次搜索读遍所有 hook 命令行，
  它们仍可按 ID / 名称 / 描述命中）；读正文失败（文件被篡改、摘要不匹配）时
  **跳过那一条的正文面**而不是让整个列表 500（一条坏资源不该让「列出资源」
  这个动作不可用，那时用户既看不到清单，也无从知道是哪一条坏了）；
  关键词上限 200 字符（无界关键词会让每条资源都做一次超长子串匹配）；
  空关键词返回全部（「没在搜」不等于「搜不到」）。
  界面上前端仍按三个元数据面即时过滤，同时对服务器发一次节流 300ms 的正文查询，
  两者取并集：服务器返回的是前端结果的**超集**，所以请求在途时只会少显示几行、
  不会显示错的行；在途期间不报「没有匹配」，那时结论还没出来。
  乱序返回不覆盖新关键词（`token` 只让最后一次生效），换类型后重搜
  （正文命中属于上一个类型），搜索失败只丢正文这一面而不清空列表。

### Fixed

本轮修正的缺陷有一个共同形态：**功能看起来在工作，实际给出反向或空白的结果**。
每条都先有一个失败的回归测试，再改最小范围代码。

- **七个页面的核心判断此前只被「源码里有这个字符串」覆盖**（需求 14）：
  `scripts/audit_source_grep_tests.py` 的首次运行给出 21 个纯 grep 测试文件。
  逐个读完，其中七处的断言钉的是**写法**而不是行为，因此既挡不住真实错误、
  又会因无害重构而红。改法一致：把纯逻辑抽成模块，写按行为断言的测试，
  原文件只保留「必须写在组件里才成立」的部分（生命周期、可访问性属性、接线）。

  | 抽出的模块 | 原来的断言 | 它测不出什么 |
  | --- | --- | --- |
  | `views/llm/autoDetectSchedule.ts` | `toContain("if (!row.last_run) return '—'")` | `86_400_000` 写错、`<= 0` 写成 `< 0`——用户会按一个错的时刻去等 |
  | `views/im/qrLoginPresentation.ts` | `toMatch(/qrCountdownExpired\|remaining\w*\s*<=\s*0/)` | 那个 `\|` 让 `< 0` 也匹配，而它正是「归零后仍显示待扫码」的形态 |
  | `views/tracing/usageSource.ts` | 两个文件各 grep 自己那份文案表 | 两份表漂移时各自都「包含那个字符串」，测试全绿 |
  | `views/settings/viewmodels/agentRuntimeForm.ts` | `toContain('collectChannelModes')` | 空串没归一成 `null` → 创建者身份匹配不上任何消息，而界面说保存成功 |
  | `views/llm/failoverQueueOrder.ts` | `toMatch(/queue-move-up[\s\S]{0,300}index === 0/)` | 交换出来的次序对不对——而这个次序会写进 `config.yaml` |
  | `views/resources/entryDigest.ts` | `toContain('entryDigestMatches')` | 两边摘要都为空时 `'' === ''` 为真，等于把「都不知道摘要」说成校验通过 |
  | `views/llm/pricingForm.ts` | `toMatch(/display_name\s*=\s*label\s*\?\s*label\s*:\s*null/)` | 无法区分「改好了」与「改坏了」：重构成 `label \|\| null` 它红，把条件写反它也红 |

  顺带修掉两处真实的**重复**：用量来源文案在 `llm-tracing.vm.ts` 与
  `LLMStatistics.vue` 里各有一份（漂移后请求日志与统计图会说不同的词），
  队列按钮的禁用条件在模板里自己写了一份 index 比较（与提交侧的边界判断
  各自漂移会成为「按钮可点而提交被拒」）。现在各只有一份，并有一条测试
  钉住两侧边界一致。

  新增行为测试 141 条（前端 1031 → 1172，测试文件 132 → 141），删除 1 个被完整替代的 grep-only 文件。

- **后端升级在这个项目的运行环境里必定失败，而失败信息不指向原因**（需求 16）：
  `perform_update()` 的后端分支最后一步是
  `subprocess.run([sys.executable, "-m", "pip", "install", backend_file], check=True)`，
  而本项目的两个虚拟环境里**都没有 pip**：

      $ .venv/Scripts/python.exe -m pip --version
      No module named pip

  `uv venv` 默认不装 pip（不带 `--seed`），而依赖是用 uv 锁的。那时这条路径抛
  `CalledProcessError`，被 `except Exception` 抓住后返回 `str(e)`——
  也就是 `Command '[...]' returned non-zero exit status 1.`：既不提 pip，
  也不说该怎么办。用户看到「更新失败」加一串命令行，会去查网络与权限，
  而真实原因是这台机器上装什么都不会成功。更糟的是这一切发生在**下载完几十 MB
  并校验完摘要之后**。
  修法是两件事，都不是「加一层回滚」：
  pip 自己的 `UninstallPathSet.rollback()` 已经会在安装失败时把旧版本文件放回去，
  所以「装失败了」这一路旧版本仍然在，再包一层事务是重复它。真正缺的是
  (1) **前置检查**：`backend_installer_available()` 用 `importlib.util.find_spec("pip")`
  在下载之前问一次，失败时返回 409 并点名 pip、给出
  `uv pip install --upgrade kirara-ai` 这条替代做法；
  (2) **失败时报出当前版本**：`后端安装失败（退出码 N），当前仍在运行 3.3.0b14`——
  这是用户判断「重试」还是「手动装」的依据，而升级成功但运行时坏掉那种情况
  rollback-on-exception 本来也管不了，它需要的正是知道回到哪一版。
  探针用 `find_spec` 而不是起一个 `python -m pip --version` 子进程：
  安装命令里的 `sys.executable` 就是当前进程的解释器，因此这是一个进程内问题，
  起子进程会引入超时与子进程失败等额外失败模式，还会与任何替换 `subprocess.run`
  的测试互相干扰。
  前置检查排在**版本解析之后**：放在之前会让每个「已是最新」的 uv 部署都收到
  一句「请先安装 pip」，用户装完回来发现无事可做；而版本解析只读索引元数据、
  不下载 wheel，因此这个次序仍然满足「下载之前」。
  WebUI 升级不经过 pip，因此这条检查只挡后端分支。

- **搜索框承诺的三个匹配面里有两个从未命中过任何东西**（需求 10）：
  `resourceFilter.ts` 的关键词谓词读 `resource.name` 与 `resource.description`，
  输入框的占位符写着「搜索名称、ID 或描述」——而资源记录里**从来没有这两个字段**。
  `author_document()` 与 `install_skill()` 把它们写进 `source_metadata`，
  目录安装（`_install_builtin()`）**连写都没写**：目录条目自己带着
  `name`「Office and Research Assistant」和一整句中文描述，建 manifest 时被丢掉了。
  于是搜一条叫「办公助手」的提示词返回空，而它就在列表里。
  这个缺陷躲过了两道防线：`tsc` 发现不了（谓词入参类型把那两个字段声明成可选，
  传一个没有它们的对象完全合法），既有测试也发现不了（测试里手写的对象**带**这两个
  字段，那是一个真实响应里不存在的形状）。
  修法是**在读取处投影**而不是在安装处另存一份：`_snapshot()` 把
  `source_metadata` 里的显示名与描述提到记录顶层，所有返回资源记录的出口
  （列表、详情、安装、更新、启用、停用、回滚、备份恢复）统一走它。
  存两份就有两份可以各自漂移，而漂移之后没有症状——列表显示旧名字、
  更新检查用新名字，两边都「有值」。
  没有名字时是 `null` 而**不是缺字段**：缺字段在前端读到 `undefined`，
  就回到了这个缺陷的起点。
  同时补上目录安装丢掉的那两个键，并让每次 `install()`（含启动时的
  `ensure_builtins()`）给缺名称的已装资源补齐——修复前装好的资源不会因为代码更新
  自己长出名字，而在真实部署里那些恰恰是绝大多数。
  补齐走 `set_display_metadata()`：**不抬版本号、不改摘要、不触发备份**
  （显示名不参与 `content_sha256`；为补一行字给资源升一个版本会在版本列表里
  留下一条与内容无关的记录），且**只补空缺**（用户已经改成自己的叫法时，
  用目录里的名字盖掉它等于每次启动都撤销一次用户的重命名）。
  这个入口按构造只接受 `name` 与 `description` 两个键，不是一个能改整个
  `source_metadata` 的通用写口：`owner` / `repository` / `branch` / `directory` /
  `catalog_id` 决定「去哪里取下一版」，改它们等于把资源指向另一个上游。
  界面上列表第一列现在显示名称、ID 与描述三行（**ID 不因为有名字就消失**——
  每个确认框、每条审计记录都按 ID 称呼这条资源，只给名字会让
  「确认删除 prompt.office-research」对不上任何一行），详情面板加名称与描述两栏，
  「编辑正文」表单预填当前名称与描述。

- **改过的显示名会被下一次升级或回滚静默丢掉**（需求 10）：
  接上一条修好显示名之后，用改名 → 升级的顺序实测发现的：`source_metadata` 在
  `update_archive()` / `restore_version()` / `restore_backup()` 里是**整体替换**的。
  这对 `owner` / `repository` / `branch` / `directory` / `catalog_id` 是对的
  （它们说的是「下一版去哪里取」，换版本就该换），但显示名与描述跟着一起被换掉：
  新清单往往压根不声明名称（手工打包的 ZIP、`author_document_version(name=None)`
  都不写），于是升级后名字变成 `None`、列表回落到显示 ID——
  用户会以为是自己的重命名没保存上。这不是边缘情况，是升级路径的常态。
  新增 `_with_display_metadata()`：来源键仍按新清单整体替换，只有
  `name` / `description` 两个键按调用方指定的优先级取第一个非空值。
  **优先级在两种情形下相反**，因为「哪一份更新」不同：升级时新清单是上游这一版的
  说法（明确给了就用它，没给才沿用旧的）；回滚时那份存档记录的是**当时**的叫法，
  比用户之后的重命名更旧，所以现存记录优先——回滚的是内容，
  不该顺带把重命名也撤销掉。
  「清空名称」同样是一个用户动作：任何一份都没有这个键时把它从结果里去掉，
  而不是留下旧值，否则清空会在下一次升级时被悄悄撤销。
  六种处境逐一锁在测试里（升级清单沉默 / 升级清单命名 / 回滚版本 / 恢复备份 /
  清空后升级 / 来源坐标仍跟新清单走）。

- **「从纯文本创建提示词」在界面上完全不可用：两处正则丢了反斜杠**（需求 10）：
  `ResourceView.vue` 的版本号校验写成 `/^d+.d+.d+/`，版本建议写成
  `/^(d+).(d+).(d+)/`——那是正确写法被 heredoc 或编辑器吃掉反斜杠之后的样子。
  它们**是合法正则**：`tsc` 不报警、ESLint 不报警、运行时不抛异常，
  只是永远匹配不上任何东西（`/^d+.d+.d+/.test('1.0.0')` 是 `false`）。
  后果不是「校验松了」而是整条路不可用：`authoringError` 对任何输入都返回
  「版本号需形如 1.0.0」，保存按钮永远拦下；而 `suggestNextVersion` 永远回落到
  `1.0.1`，从 `2.3.4` 编辑时会建议一个比当前**更小**的版本号，
  用户会看到「版本必须递增」这个与他没做错任何事无关的错误。
  这个缺陷躲过了当时的测试，因为那些测试 `expect(viewSource).toContain('正文不能为空')`
  检查源码里有没有那行字符串——**字符串在，行为不在**。
  因此除了补回反斜杠，把校验与版本建议抽到
  `webui/src/views/resources/documentAuthoring.ts`，由
  `webui/tests/resource-authoring-validation.test.ts` **调用函数**验证行为
  （填齐就放行、建议值必然大于当前版本、两位数 patch 不被截断、
  编辑时不校验不可改的 ID）。
  另新增 `tests/test_regex_escape_residue.py` 作为按字符扫描的守卫，覆盖
  `webui/src` 与 `kirara_ai` / `scripts`：正则字面量或 `re.*()` 的模式串里出现
  前面没有反斜杠的 `d+` / `w+` / `s+` 及其大写形式即失败。
  这条守卫本身也有测试——用真实缺陷的原文作为样本证明它抓得住，
  并证明它不误报正确写法（`/\d+\.\d+/`、`/add+ress/`、除号）。

- **登记仓库要手抄三个字段，而用户手上拿到的一定是一个 URL**（需求 10）：
  参考界面只有一个输入框，占位符是 `owner/name 或 https://github.com/owner/name`。
  本项目此前是三个独立输入框（所有者 / 仓库 / 分支）——而用户从浏览器地址栏或
  `git clone` 命令里复制到的是一整个 URL，要求他拆成三段再分别填进去，
  是把一次粘贴变成三次手抄，而手抄正是拼错坐标的来源（此前拼错的坐标还删不掉）。
  新增 `webui/src/views/resources/repositoryCoordinate.ts`：接受 `owner/name`、
  仓库主页 URL（含省略协议与 `www.`）、`git clone` 的 `.git` 与 SSH 形态、
  以及带 `/tree/<branch>` 的深链（分支含斜杠时完整取出——只取第一段会登记成一个
  不存在的 `release`）。`/blob/...` 与子目录深链里的路径段一律忽略：
  它们描述的是仓库里的位置，不是坐标。
  **解析放在前端，后端校验一个字不放宽。** 后端那三个字段的正则是安全边界
  （它们会拼进 GitHub 归档 URL 与磁盘路径），放宽它等于把「URL 解析写错」升级成
  「一次路径穿越的机会」。前端解析完仍然提交三个干净字段，两层形成双重保险
  而不是互相替代——所以那些正则是刻意重复的。非 GitHub 主机直接拒绝
  （后端只会去 github.com 拉归档，接受别的主机等于给出一个必定以「仓库不存在」
  失败的登记，而那条错误指向的原因是错的）。
  坐标里带的分支优先于分支输入框：用户粘的是 `/tree/master`，他要的就是那个分支，
  让输入框的 `main` 覆盖它会登记出一份他没看过的内容。
  解析这一层不替用户写死 `main`，返回 `null` 让调用方决定——写死会覆盖掉
  用户已经填在分支框里的值。

- **登记过的技能仓库删不掉，一个拼错的坐标永久留在表上**（需求 10）：
  参考界面的仓库行右侧有「打开仓库」与「删除仓库」两个按钮，笔记写明
  「删除属于有影响的操作，应有确认与失败反馈」。本项目此前只有「登记」与「启停」——
  后端没方法、没路由，前端没入口。于是一个拼错的坐标（`anthropcis/skills`）
  会永久留在 `registry.json` 里：可以停用，但那条记录再也去不掉，
  仓库表上永远多一行说明不了任何事的死项，想清掉只能登服务器手改 JSON。
  「停用就够了」不成立：停用表达的是「这个来源暂时不用」，删除表达的是
  「这个来源是错的 / 不再存在」。两者都要能做到，否则用户会为了让列表干净
  而去停用一个本该删掉的条目，下一个人看到的是一个「疑似还能启用」的坐标。
  新增 `DELETE /resources/repositories/<owner>/<name>/<branch>`。四条边界锁在测试里：
  **只摘来源登记，不动已装资源**（那些已在服务器上独立成包，有自己的清单与摘要——
  一起删掉等于把「不再从这里拉新的」变成「把装过的都毁掉」）、
  **未登记的坐标返回 404**（静默成功会让一个拼错的删除请求看起来和真的删掉一样）、
  **创建者身份 + 显式确认**（写 `registry.json`，与启停同一边界；因为不可逆
  比启停多一道确认）、**只删指定的那一条**（同一个 owner/name 的不同分支是两条
  独立记录）。同时把这条路由加进 `test_creator_only_routes.py` 的写盘路由清单。
  界面：仓库行新增危险色「移除」按钮（与相邻的「停用」区分开——同色会让不可逆的
  那个看起来和可逆的一样），确认文案带上分支并明说「已经装好的资源不受影响」：
  「删除仓库」四个字读起来像会一起删掉装过的东西，而那正是按下按钮前最想知道的事。

- **仓库列表看不出「这个仓库里有多少技能」，配错与装着几百个技能长得一样**（需求 10）：
  参考界面的仓库管理页每一行带一个灰底徽章「识别到 N 个技能」。本项目的仓库记录
  只有四个字段（owner / name / branch / enabled），缺这个数的后果不是少一个装饰：
  注册之后界面上完全看不出它有没有用——一个 owner/name 拼错、分支写错、
  或者压根不含 `SKILL.md` 的仓库，与一个装着几百个技能的仓库长得一模一样，
  都只是「已启用」。用户要点进「发现」才知道，而那要出一次网、下载整个仓库归档。
  而 `discover_repository()` 本来就返回逐条清单，数量是它的自然副产品。
  新增 `discovered_skills` 字段与 `record_repository_discovery()`，四条边界锁在测试里：
  **`null` 与 `0` 严格分开**（`null` = 还没发现过，`0` = 发现过、里面一个都没有——
  后者才是「配错了」的信号，合成一个数会让每个刚注册的仓库看起来都是配错的）、
  **发现成功后自动记下**（不需要用户再点一次别的按钮）、
  **失败不写数**（一次网络错误不该把「有 864 个」改写成 0，那比不写更糟）、
  **记数不改启用状态**（顺带改它会让一次只读查询变成一次配置写入）。
  两处刻意保留既有能力：重新登记同一坐标时保留已记下的数（改一次启用状态不该把
  计数清空）；直查一个**未登记**的仓库仍然可用——`discover_repository` 的既有语义是
  「给一个坐标就能看里面有什么」，为了记一个数而拒绝这条路径，
  是用新特性削掉旧能力。
  界面：仓库表新增「技能数」列，`0` 用告警色、非 `0` 用成功色，
  「未发现过」单独一档；`null` 判断同时覆盖 `undefined`——只判 `null` 时
  一次字段改名会渲染出「识别到 undefined 个」。
  顺带把 `test_resource_sources.py` 里整体比对仓库字典的断言改为按字段断言：
  那条守的是「重复登记只留一条、状态改动落盘」，与记录有几个字段无关，
  钉住字典会让每次新增字段都变成一次红灯。

- **会话列表只有 64 位摘要，渠道身份后端给了但前端读不到**（需求 10）：
  需求 10 要求把每个入口统一映射到「渠道身份 → Agent → 上游模型/备用链 →
  Prompt/Skill/Memory/MCP」。会话是这条链上唯一带**具体人**的一环，而列表里的
  `session_id` 是一个 64 位 SHA-256 摘要——它对人没有任何含义。
  后端从渠道身份落盘那一版起，`GET /agents/sessions` 每一行都带
  `channel_identity`（五个字段），而前端 `SessionSummary` 里没有这一项，
  界面上也没有。于是运维看到一屏摘要，回答不了任何一个真实问题：
  「张三在 QQ 上那个卡住的会话是哪一行」「这批会话是 Telegram 还是企业微信来的」。
  更要紧的是**删除**：清空历史与删除会话都以那串摘要为唯一标识，
  分不清哪一行属于谁的时候，这两个动作只能靠猜。
  已补 `SessionChannelIdentity` 类型与会话表的「渠道 / 发送者」列。
  三处刻意为之：**渠道类型与发送者标识一起显示**（只有前者回答不了「是谁」，
  同一个渠道上有几十个会话；只有后者回答不了「同一个人在私聊和群里的两个会话」）；
  **完整五元组放 title**（排查时才需要适配器实例、账号与会话范围）；
  **`null` 显示成「未记录」**而不是空白或 `null` 字样——空白会被读成「渠道身份丢了」，
  而真正的含义是「这个会话建于渠道身份落盘之前」，它仍然可以被清空与删除。

- **MCP 页只有一个「Context7 模板」，而内置目录里有八个预设**（需求 10）：
  参考界面的「新增 MCP」第一步就是选类型：自定义 + 一组快捷标签，
  「类型快捷标签应能填充合理模板」。本项目的内置目录（`resource_catalog.py`
  的 `_BUILTINS`）里有八个 stdio MCP 预设，各自声明了 `command` / `args` 与
  `runtime_dependency`——而 MCP 页只有一个按钮。
  缺的不是七个按钮，是**这条链路的对称性**：同样八个预设，从「资源管理 →
  发现并安装」进去装得到，从「MCP → 添加服务器」进去只有一个。用户在 MCP 页
  找不到 `fetch`，会得出「这个项目不支持它」这个错误结论——而它就在另一个页面
  的目录里。
  已补一份表驱动的 `MCP_PRESETS`（八条，id 与目录逐一对应）加一个按 id 取表的
  `applyPreset()`。表驱动而不是八个 `openXxxTemplate`：后者是同一段逻辑抄八遍，
  每加一个预设要改函数、按钮、导出三处，漏掉任何一处都不会报错。
  四处刻意为之：**id 与目录一致**（两个入口装出两个不同 id 的同一个 MCP 之后，
  「为什么有两个 context7」无从解释，而 `refresh_managed_servers` 也按 id 对账）；
  **每条都标出靠 `npx` 还是 `uvx` 拉起**（两者都不是本项目的依赖，运行时镜像
  都没装，不说明的话用户点了启用只会看到「连接失败 / 工具数 0」）；
  **`filesystem` 不预填目录**（填任何具体路径都是替用户决定「哪些文件可以被读写」，
  而这条 MCP 的全部风险就在那个参数上，改为一句必须补参数的提示）；
  **args 拷贝而不是共享引用**（表单里删一个参数不该改掉那张常量表，
  否则下一次选同一个预设会填出被改过的参数）。
  「自定义」与预设并列且排在最前，用分隔线隔开——它是空白起点，不是第九个预设。

- **定价表只有模型标识，没有可读的显示名称**（需求 9）：
  参考界面的定价表有两列身份——模型标识（等宽、稳定的上游 ID）与显示名称，
  笔记里为此专门写了一条边界：「模型标识使用稳定的上游模型 ID，显示名称单独保存，
  **不能用显示名称代替路由匹配键**」。本项目此前只有 `model` 一个字段。
  几条价格时不是问题，几十条时是：`anthropic/claude-sonnet-5` 与
  `anthropic/claude-sonnet-5-20260514` 在表格里只差一个后缀，而单价可能不同——
  要在一屏里挑出「哪一行是我在用的那个」，唯一可读的抓手正是显示名称。
  同步进来的价格更是如此：上游目录**每个模型都带 `name`**，此前解析器把它丢掉了，
  明明有可读名字，界面上却只能看一串 ID。
  新增 `PriceVersion.display_name`（可选、非空白、上限 200），三条边界锁在测试里：
  **不参与任何匹配**（计价仍按 `(provider, model)`，一旦拿它当键，改一个标签就会让
  历史账单换一个价格）、**缺省不等于空串**（老价目文件没有这个字段，读进来照旧可用，
  显示时回落到 `model` 而不是留一个空白单元格）、**进摘要计算**（目录有 `integrity`
  自校验，新增字段不进摘要的话，一次手工改标签就会让文件与摘要不一致而无人发现）。
  同步侧：解析器保留上游 `name`，类型不对时当作没给（`str(123)` 会落一个假标签），
  **不回落到 provider 的 `name`**（那会让同一家所有模型都显示成「Anthropic」，
  比没有标签更容易读错）；只有标签变化不计入价格变化，否则每轮同步都重写会推高
  revision，让乐观锁误判成有人在并发改价。
  界面：表单里模型标识与显示名称是两个独立输入框，列表里可读名在前、等宽标识在后
  （计价真正用的键不能从界面上消失），空值在 `copyVersion` 里统一转 `null`——
  后端拒绝空白标签，原样提交空串等于把一个必然的 400 留给用户去撞。
  路由侧还有一处必须同步的白名单：`_PRICE_VERSION_FIELDS` 漏掉新字段的后果**不是**
  「字段被忽略」，而是整个请求以 400「version contains unknown fields」被拒——
  而那条错误对填表的人毫无指向性，他填的每一项看起来都合法。这是新增模型字段时
  最容易漏的一处：模型改了、界面改了、测试也可能只测模型层，而请求根本到不了模型。

- **Provider 与模型统计漏掉「缓存创建」，最贵的那种情形看不见**（需求 9）：
  后端逐组算齐了四类 Token（`_group_statistics` 里四个 SUM，缓存两项还各带一个
  `count` 用来区分「没上游报过」与「报了 0」），概览卡片有四类、趋势折线有四条，
  唯独 Provider 与模型两个分组的 tooltip 只列三类。
  丢的不是「一个数」：缓存写入的单价通常**高于**普通输入（Anthropic 是 1.25 倍），
  而缓存读取只有输入的十分之一。一家「缓存创建很高、缓存命中接近 0」的上游正在按
  溢价写一堆永远不会被读到的缓存——这是账单异常里最该先查的一种，
  而在只有「输入 / 输出 / 缓存读取」三项的 tooltip 里，它与一家正常上游长得一样。
  已补两处，并用 `formatNullableTokens` 保持三态（`null` = 这一组没有任何上游报过
  缓存，与「报了 0」不同）。新增
  `webui/tests/llm-statistics-group-token-breakdown.test.ts` 同时守住两个分组，
  并对照概览与趋势——三处口径不一致等于同一份账单在三个位置给出三种拆分。

- **请求日志只显示合计 Token，四类拆分取回来又不显示**（需求 9）：
  需求 9 点名「不同类型上游**真实消耗 Tokens**」，参考界面的请求日志表把输入与
  输出分成两列。数据链路是全的——库里四列都有、`to_dict()` 四个都出、
  前端 `LLMTrace` 类型四个都声明了、CSV 导出四列也都在，断的只是**列表列**。
  「只有合计」在这个页面上不是省略而是歧义：同样 100 万 Token，一家几乎全在读
  上下文、另一家几乎全在生成，成本能差 5~10 倍（输出单价通常是输入的数倍，
  缓存读取又比输入便宜一个量级）。要回答「这条为什么这么贵」，
  合计恰恰是唯一回答不了的那个数字。详情页早就有这份拆分，说明拆分本身有价值，
  只是要一条一条点进去看，而排查的第一步是横向比较一屏里的几十条。
  已补输入 / 输出 / 缓存命中 / 缓存创建四列，合计列保留。
  缓存两项用 `formatOptionalTokens` 区分「未上报」与「0」：`null` 是没有任何上游
  报过缓存维度，`0` 是报了、确实没命中——显示成同一个东西时，前者会被当成缓存
  失效去排查一个并不存在的问题。顺带给表加 `scroll-x`：加四列后总宽 2220px 超过
  常见视口，不给 scroll-x 时 naive-ui 会按容器宽度压缩每一列，
  数字被截断成「1,2…」，而这个页面的全部内容就是数字。

- **`memsearch` / `rtk` 技能被判成「不需要任何服务器依赖」**（需求 10）：
  需求 10 点名的五个工具都在依赖登记表里有条目、探测与安装都能跑，断的是中间那一层：
  `dependency_ids_for_resource()` 只认 `agent-browser` 与 `graphify` 两个名字，
  其余一律返回空列表——而空列表的含义是「这个技能不需要任何服务器依赖」。
  后果有两处，都不报错：技能广告里不会出现「服务器上没有这个命令」
  （`skill_readiness_note()` 拿到空列表就什么都不说，于是模型照着一份它执行不了的
  说明自信作答），安装界面也不显示这个技能缺什么。
  已补 `_SKILL_NAME_DEPENDENCY_IDS` 映射（`memsearch` / `rtk` / `tk` 三个键，
  需求原文写「tk」而本机命令名是 `rtk`，两种目录命名都收）。
  两条 `claude-plugin`（context-mode / caveman）刻意**不进**这张表：它们装在操作者
  本机的 Claude 配置里、`install_supported` 为假，加进来会让技能在任何服务器上都
  显示缺依赖，而那个「缺」无从修复。
  新增 `tests/plugin_manager/test_skill_dependency_mapping.py` 按**登记项驱动**
  而不是逐个写死名字：新增一条 CLI 登记项而忘了加映射时立刻红；
  同时锁住「已安装资源」与「目录项」两种字段形状得到同一批依赖——
  只认一种的后果是安装界面说缺、运行时说就绪，而这种不一致没有任何症状。

- **内置 AI 调试 Hook 只声明 8 个事件，另三个已派发事件没有可验证的样本**（需求 10）：
  上一轮补齐了 `SessionEnd` / `SubagentStart` / `SubagentStop` 三个派发点，
  契约（`HOOK_EVENTS`）与实现（executor）都是 11 个，而内置 `hook:ai-debug`
  的声明仍停在 8 个。它是需求 10 点名的「添加 hooks 进行 AI 功能调试」那个件，
  也是使用者验证「Hook 到底有没有在跑」的唯一现成样本。
  漏掉的后果不是少三条日志，而是这三类事件在产品上**没有任何可验证的入口**：
  想确认「会话结束时钩子跑了吗」「队友委派前后有没有被审计」，照内置件抄一份，
  抄到的声明里压根没有这些事件。而缺口完全静默——声明校验只查事件名在不在
  `HOOK_EVENTS` 里，少写几个永远不报错；`/agents/hooks` 返回的是声明里有什么，
  不是「还能挂什么」。已补齐 11 个事件，版本 1.1.0 → 1.2.0
  （`ResourceCatalogService.install()` 只在 bundled > installed 时推进已装资源，
  不抬版本号的后果是「新部署有、老部署没有」，而两边界面都显示已安装、已启用）。
  新增 `tests/plugin_manager/test_builtin_hook_event_coverage.py`：与派发点、
  与 `HOOK_EVENTS`、与 `audit_hook_command` 接受的事件名三向对齐，
  并检查每个事件把**自己的**名字传给命令（复制粘贴最容易漏改这一处，
  漏改后审计记录里每条都写着同一个事件——那比没有记录更糟）。
  顺带把 `test_resource_catalog.py` 里钉死 `1.1.0` 的断言改为按行为断言：
  钉版本号会让一次正当的扩充变成红灯，而改个数字让它变绿又什么都没验证。

- **两个页面用了没 import 的 naive-ui 组件，被当成原生元素渲染**（需求 12）：
  这个项目没有全局注册 naive-ui（`main.ts` 只 `use` 了 pinia 与 router），也没装自动
  导入解析器。`ResourceView.vue` 的 `<n-list>` / `<n-list-item>` 与
  `FrpServiceCard.vue` 的 `<n-icon>` 都没在 `script setup` 里 import，于是 Vue 把它们
  当成**原生元素**渲染：内容照样进 DOM，只是完全没有 naive-ui 的样式与行为——
  `bordered` 不画边框、列表项之间没有分隔线、图标不做尺寸与对齐。
  控制台里有一条 `Failed to resolve component`，而生产构建把它去掉了。
  这类缺陷的形态是「看起来像 CSS 没写好」：不报错、不白屏，只是排版不对，
  而真正的原因在几百行外的 import 清单里。
  新增 `webui/tests/naive-components-are-imported.test.ts` 从每份 `.vue` 的
  `<template>` 里收集 `n-*` 标签并逐个要求对应 PascalCase 名字出现在文件里——
  按行为断言，不钉具体文件，新增页面自动被覆盖。

- **「发现并安装资源」的结果是单列分隔行，无法横向比较候选**（需求 10）：
  发现这件事的本质是横向比较：同一个关键词往往返回十几个来源不同、名字相近的候选
  （`awesome-claude-skills` 与 `anthropics/skills` 下的同名技能只差仓库），
  而单列分隔行一屏只放得下两三个，逐行下拉看不出差别。
  另一半是三处发现结果**形态各不相同**：内置目录用 `article` 行、skills.sh 用
  `n-list`、仓库直查又是另一个 `n-list`。同一个面板里切换来源时布局整体换一次，
  读起来像换了一个页面，而三者的操作（查看、安装）完全相同。
  改成统一的响应式卡片网格：`repeat(auto-fill, minmax(280px, 1fr))` 决定列数而不是
  写死三列——写死会在窄屏把卡片挤成一条，先被压掉的正是操作区，而那是这个面板
  唯一的目的。描述限三行截断：网格里一张卡变高会把整行拉高，其余卡片下方留出
  大片空白。窄屏降到单列。

- **Gemini 流式路径丢掉推理强度：界面选了「最大」，只有非流式生效**（需求 8）：
  `build_gemini_thinking_config()` 只在 `chat()` 的 `generationConfig` 里被调用，
  `stream_chat()` 的请求体里没有 `thinkingConfig`。产品上的后果是同一个供应商在两种
  回复模式下推理强度不同，而**两边都成功返回**——没有任何地方会报出这个差别。
  流式恰恰是带首字节/静默超时保护的默认路径，也就是主流路径。
  同一个文件里 `systemInstruction` 已经为此写下「两条路径必须一致」的注释，
  `thinkingConfig` 正好漏了这一条。已补，并与非流式同一处理：未配置时该键整个消失
  而不是留 `null`——不支持思考的模型收到 `thinkingConfig: null` 会直接报错。

- **流式路径不整流：同一个请求换成流式就失去容错**（需求 8）：
  `rectify_request` 修的是「上游因参数约束拒绝、而这个约束不在用户能改的地方」这类
  硬失败——不支持的图片、上游不认识的 thinking 字段、超范围的 budget、
  不支持的 `reasoning_effort`。非流式路径（`chat`）已经在整流，
  但 `stream_chat` 里 `raise_for_status()` 失败后**直接抛**。
  产品上的后果：`reply_stream_mode` 配成 `aggregate` 或 `incremental` 之后
  （文档推荐这么配，因为流式超时与首字节前的故障转移才生效），
  供应商编辑页上的四个整流开关对这条路径**从未参与任何决策**。
  用户看到「请求失败」，而真正的原因是一张图或一个上游不认识的字段——
  两者都不是他能自己改的。而十个 OpenAI 兼容适配器全部继承
  `OpenAIAdapterChatBase`，所以这一个缺口覆盖绝大多数部署。
  已按非流式同一套语义接入：只对真实的上游拒绝生效、每类最多改一次、
  改完仍失败抛原始错误。流式路径额外关掉失败连接再重试——不关会让失败的响应体
  一直占着连接池，而流式请求本来就持有连接更久。
  新增 `tests/llm/test_rectifier_adapter_coverage.py` 锁住：四家自建请求体的适配器
  两条路径都不能悄悄退出整流。（本轮后续把 Gemini 与 Ollama 也接上了，
  按各家载荷形状分派规则，README 里那段「整流器不覆盖」的免责说明随之删除。）

- **`npm run type-check` 是一个恒绿的空壳门禁，掩盖了 37 条真实类型错误**（需求 12）：
  `tsconfig.json` 是 solution-style 配置（`"files": []` + `references`），而脚本写的是
  裸 `vue-tsc --noEmit`——不带 `-p` 也不带 `--build`。这种组合下 TS 只加载根配置，
  `files: []` 意味着**零个输入文件**，references 不会被跟随。命令秒退、退出码 0、
  什么都没检查。实测确认：在 `src/` 下写入 `const probe: number = 'not a number'`，
  `npm run type-check` 通过；加 `-p tsconfig.app.json` 立刻报 TS2322。
  严重性不在「少检查了一点」：`release-preflight.yml` 与 `quickstart-windows.yml`
  都把 `yarn type-check` 当作发布门禁。**一个永远不会失败的门禁比没有门禁更糟**——
  它让每次发布都带着「类型检查已通过」的记录，而实际从未执行。本仓库真实存在的
  37 条类型错误（含两个会在运行时抛 `ReferenceError` 的未定义名）就是这样长期通过
  发布检查的。改为显式指定两个真实项目并实测门禁能拒绝（exit=2），
  同时清零全部 37 条（详见下列各条）。

- **`ResourceView.vue` 用了两个没 import 的名字，「发现并安装」点下去直接抛异常**（需求 10）：
  远程安装的确认回调里调 `installRemoteSkill(...)`，但该函数从未从 `@/api/resource` 引入——
  点「安装」抛 `ReferenceError: installRemoteSkill is not defined`，**在线发现的资源
  一个都装不上**。`ImportableArchive` 同样用到未引入。两者在 API 层都存在且已导出，
  纯属漏了 import 清单。补 `tests/resource-view-imports.test.ts`：把 `@/api/resource`
  的导出名与本页实际 import 的名字对表，凡在 script 正文里作为独立标识符出现却没引入的
  一律报出（先抹掉 template、注释与字符串字面量再匹配，避免把中文文案里的词误判成标识符）。

- **MCP 列表的分页与筛选完全失效**（需求 10）：`mcp.vm.ts` 调用
  `http.get(path, { params })`，但第二个参数是 `Omit<RequestInit, 'method'>`——原生 fetch
  配置，**没有 `params` 这一项**。对象被原样展开给 fetch，fetch 忽略未知字段。
  后端 `page` 默认 1、`page_size` 默认 20，`type` / `status` / `query` 全为 None：
  翻到第 2 页看到的还是第 1 页，输入关键词搜索没有任何变化，而请求成功、控制台干净、
  后端日志干净。改为 `URLSearchParams` 拼进 URL，可选筛选项只在有值时才拼——
  `String(undefined)` 会得到字面量 `"undefined"`，后端会把它当成有效筛选值。

- **两处界面按钮放在 naive-ui 不存在的 `#action` 插槽里，从来没有渲染出来**（需求 9、8）：
  `LLMStatistics.vue` 统计加载失败时的「重试」、`LLMView.vue` 导入冲突时的
  「确认覆盖 / 取消」，都写在 `<template #action>` 里。但 naive-ui 的 `AlertSlots`
  只声明 `default | icon | header`（`Alert.d.ts:219`），运行时 `Alert.mjs` 也只消费这三个。
  实测确认：真实 `NAlert` 下 `#action` 的内容**完全不渲染**——正文出现，按钮不出现。
  后果是功能缺失而非样式偏差：统计加载失败后没有任何重试入口，只能刷新整页；
  导入冲突时看不到「确认覆盖」，整条供应商导入流程在这一步断掉。
  它长期没被发现的原因值得单记：`tests/llm-statistics.test.ts` 的 naive-ui mock 里
  手写了 `<slot name="action" />`——**stub 比被替代的真实组件更宽容**，于是测试点得到
  按钮、断言通过，真实界面上什么都没有。按钮已移到默认插槽（实测可渲染、
  `data-test` 可选中），并删掉 stub 里那个伪造的 slot。

- **`ConfigurationList.vue` 用数组下标索引按属性名的 Record，表单读写全程错位**（需求 12）：
  `v-for` 拿到的 `j` 是数组下标（number），而配置值 `editableConfigurationValue` 是
  `Record<string, any>`——键是属性名。九处 `editableConfigurationValue[j]` 读到的永远是
  `undefined`，用户填的内容写进 `"0"` / `"1"` / `"2"` 这种键，保存后与后端期望的属性名
  毫无关系。**打开能填、保存后配置全丢。** 七个对象操作函数的首参也都声明成
  `arr: number`。对照同仓已在服役的 `DynamicConfigForm.vue`：它用
  `for (const key in props.schema.properties)` 全程按属性名索引——那份是对的。
  已把 `ConfigurationGroup.properties` 改为 `Record<string, Configuration>` 与配置值同构，
  加 `groupProperties()` 单点兼容旧数组形态。顺带修 `createHash`：`CryptoJS[hashFunc]`
  是 `unknown`，收窄成 `resolveHashFn()` 并在算法名不合法时抛错，而不是把 vendor shim
  放宽成 `any`——那会让 CryptoJS 的其余误用一起失去检查。
  另修同文件三处：`saveToServer` 用字符串索引数组导致**所有密码字段以明文提交**
  （`form_type == 'password'` 判断永远不成立）、引用 `Configuration` 上不存在的
  `password` 字段、`$event.target.innerText` 未收窄事件目标。

- **引了一个既没声明也没安装的包，重新引用即构建失败**（需求 12）：
  `ConfigurationList.vue` 里 `import Markdown from 'vue3-markdown-it'`——该包既不在
  `package.json` 里，也不在 `node_modules` 里。它至今没炸只因为那个组件已经没人引用；
  一旦被重新接上，Vite 解析失败是**构建错误**，不是类型告警。改用仓库已声明的
  `markdown-it`（与 `IMAdapterDetail.vue` 渲染适配器说明走同一个库）。
  顺带补齐 5 个靠间接依赖碰巧能解析的包（`highlight.js` / `semver` / `date-fns` /
  `vscode-languageclient` / `@codingame/monaco-vscode-configuration-service-override`）
  的显式声明——今天能跑，上游哪天不再传递就断在构建期。
  新增 `tests/imports-are-declared-dependencies.test.ts` 锁住「所有裸包 import 都要在
  package.json 声明且真的装得上」。

- **撤销栈用了超出构建基线的 `Array.prototype.at`，基线浏览器上画布整体打不开**（需求 20）：
  `vite.config.ts` 没设 `build.target`，走 Vite 默认的 `'modules'` 基线
  （Chrome 87 / Safari 14），而 `.at` 要 Chrome 92 / Safari 15.4。关键在于
  **Vite/esbuild 只降级语法、不给内置方法注入 polyfill**：`undoStack.at(-1)` 会原样
  出现在产物里，在基线浏览器上抛 `TypeError: undoStack.at is not a function`。
  而 `pushHistoryState` 是画布每一次改动的必经路径，所以后果不是某个边角功能失效，
  是**工作流画布整体打不开**。把 `lib` 抬到 es2022 能让 4 条 TS2550 消失，
  但那是把真实的兼容性问题消音——产物不会因此多出 polyfill。改为加 `peek()` 工具函数，
  并加测试锁住「不要用它」，另附一条活文档断言：一旦有人显式设了 `build.target`，
  说明基线被重新声明过，该约束要重新评估而不是继续盲目禁用。

- **`vite.config.ts` 从未被类型检查，3 条错误里有 2 条会影响产物**（需求 16）：
  修好 type-check 门禁后 `tsconfig.node.json` 暴露 3 条。`resolveJsonModule` 缺失
  让 `import packageJson from './package.json'` 报错——而它是版本号注入的唯一来源
  （`VITE_APP_VERSION` 与 `version.json` 都从它取）；插件返回值未标 rollup `Plugin`
  类型让 `this.emitFile` 报 TS2339，缺它 `version.json` 不会产出。
  esbuild 双版本（顶层 0.25.x 被 @codingame 插件要求，vite 4 内嵌 0.18.x）的
  `Plugin` 类型冲突用收窄到单个值的转换处理，不用 `as any`——那会把将来真正的签名
  变化也一起吞掉；并加自检断言：两份 esbuild 对齐后应删掉该转换。
  实测 `vite build` 通过，`dist/version.json` 正确产出。

- **媒体日期筛选两处类型错误互相抵消，筛选「碰巧能工作」**（需求 12）：
  `n-date-picker` 的 `value` 是毫秒时间戳，而 `dateRange` 声明成 `[string, string]`。
  组件写回数字，被塞进声明为 `string` 的查询字段——后端 pydantic 恰好能把毫秒解析成
  datetime（已实测），所以筛选**碰巧能工作**。这比单纯的错更危险：一旦有人按声明
  把它当字符串处理（`.slice(0, 10)` 之类），就会拿到静默错值。已改为按真实类型声明 +
  显式 `toISOString()`。同文件另修 `n-switch` 直接绑 `boolean | null`——`null` 表示
  「配置还没加载回来」，语义要保留，但模板层必须落到 `false`。

- **状态栏内存兜底赋成标量，后端不可达时整条状态栏消失**（需求 12）：
  `memoryUsage` 在 store 里是 `{ percent, total, used, free }`，模板按
  `memoryUsage.used.toFixed(2)` 渲染。但 `onMounted` 的初始赋值与请求失败的 catch
  分支都写成 `memoryUsage: 0`——数字没有 `.used`，取到 `undefined` 再调 `.toFixed`
  直接抛 TypeError，**Vue 的渲染在这里中断，整条状态栏消失**。挂载即赋值，
  所以后端不可达时这是必然路径，不是边角情况。同处另修：`onMounted` 那次赋值漏了
  `platform` / `cpuInfo` / `pythonVersion` / `hasProxy` 四个字段（store 里变 undefined），
  以及 `fetchStatus` 拿 store 的 camelCase 内部类型去标注 snake_case HTTP 响应
  （12 条误报——运行时其实是对的，函数体做的正是 snake→camel 转换，错的是拿内部形状
  去描述外部载荷：后端加字段或改名时类型这层照不出来）。

- **`@click="createRule"` 把 MouseEvent 当参数传进去**（需求 2）：
  `createRule(workflowId = '')` 首参可选，但**默认值只在实参为 `undefined` 时才顶上**，
  而 MouseEvent 是个真对象。于是「创建规则」建出来的草稿 `workflow_id` 不是空串
  而是一个 MouseEvent，落到后端就是一条 workflow_id 不合法的规则，界面上看不出
  哪里错——用户只看到保存失败。补 `tests/click-handler-arity.test.ts` 拦这一类：
  只查**首参可选**的函数，零参函数裸绑是安全且惯用的，一起禁掉只会逼着到处加
  无意义的 `()`。

- **定价页手工同步成功后调用了一个不存在的函数，成功被显示成失败**（需求 9）：
  `syncFromUpstream()` 末尾调 `load()` 刷新列表，但组件里没有这个名字，只有
  `loadCatalog()`。这一行必抛 `ReferenceError`，又正好落在 `try` 里被 `catch` 成
  「定价同步失败」。实际后果是**同步已经写盘了，界面却报错并且继续显示旧价格**：
  用户看到失败会重试，每次重试都"再次失败"，而价格其实一直在正确更新。
  一条既有测试本该拦住它——「同步后要刷新列表」断言的却是 `/load\(\)/`，
  钉住了那个错名字，于是长期为一个运行时错误打绿灯。改为断言真实存在的
  `loadCatalog()`，并修正调用点。

- **`ResourceView.vue` 用了两个没 import 的名字，「发现并安装」点下去直接炸**
  （需求 10）：远程安装的确认回调里调 `installRemoteSkill(...)`，但该函数从未从
  `@/api/resource` 引入——点「安装」抛 `ReferenceError: installRemoteSkill is not
  defined`，在线发现的资源一个都装不上。`ImportableArchive` 同样用到未引入
  （类型缺失只影响编译期）。两者在 API 层都存在且已导出，纯属漏了 import 清单。
  补 `tests/resource-view-imports.test.ts`：把 `@/api/resource` 的导出名与本页
  实际 import 的名字对表，凡在 script 正文里作为独立标识符出现却没引入的一律报出。
  规则先抹掉 template、注释与字符串字面量再匹配，避免把中文文案里的词误判成标识符；
  另有两条自检确认两份文件真的读到了（否则空集合互比会永远绿）。

- **`@click="createRule"` 把 MouseEvent 当参数传进去，新建规则的 `workflow_id`
  是个事件对象**：`createRule(workflowId = '')` 首参可选，但默认值只在实参为
  `undefined` 时才顶上，而 MouseEvent 是个真对象。于是「创建规则」建出来的草稿
  `workflow_id` 不是空串而是一个 MouseEvent，落到后端就是一条 workflow_id 不合法的
  规则，界面上看不出哪里错——用户只看到保存失败。改为 `@click="createRule()"`。
  补 `tests/click-handler-arity.test.ts` 拦这一类：只查**首参可选**的函数，零参函数
  裸绑是安全且惯用的，一起禁掉只会逼着到处加无意义的 `()`。该规则扫全仓 `.vue`，
  当前精确命中且仅命中这一处。

- **`TopBar.vue` 的 `<script>` 漏了 `lang="ts"`，掩盖了全部 78 条既有类型错误**：
  缺少 `lang="ts"` 时 `vue-tsc` 把它当 JS 虚拟文件 `TopBar.vue.js`，撞上
  `allowJs: false` 报 TS6504。TS6504 是**致命错**，类型检查停在那一步就不再往下走，
  于是 `npx vue-tsc` 长期只报这一条、看着像"只有一个小问题"，实际后面还压着 77 条
  ——包括上面那两个会真正在运行时炸的未定义名。补上 `lang="ts"` 后错误全部显形，
  本轮修掉其中会导致运行时失败的部分（TS2304 已清零），其余 75 条为既有类型精度
  问题（`any` 隐式推导、naive-ui 组件 prop 联合类型收窄等），不改变运行行为，
  单独一轮处理。

- **`UsageRangePresetOption` 写成 `interface` 导致 `n-select :options` 报 TS2322**：
  naive-ui 的 `SelectMixedOption` 带索引签名 `[k: string]: unknown`，而 TS 只对
  type alias 做隐式索引签名匹配，`interface` 不做。改为 `type`。没有走"把 `value`
  放宽成 `string | number`"那条路——那样也能过检，但会丢掉「只有这六个预设键合法」
  的约束，预设键写错时就从编译期报错退化成运行时静默落进 `custom`。

- **调度器汇报的定价自动同步运行态前端读不到**（需求 9）：
  后端 `price_sync` 一直带 `interval_days` / `enabled` / `last_run` / `last_ok`，
  但前端类型只声明了 `running` 与 `backends`，定价页也只有一个手工同步按钮。
  界面上看不出缺口——手工点一次会成功，功能像是齐全的；丢掉的是**自动同步到底
  有没有在跑**：间隔设成 7 天后，用户无法从任何地方确认它生效了没有、上次何时跑、
  上次成功还是失败。价格静止不动时，「上游没调价」和「同步半个月前就失败了」
  在界面上完全同形。补 `PriceSyncState` 类型并在定价页渲染这份状态，其中
  `last_ok` 保持三态（`null` = 从未同步过），避免把「没跑过」显示成「失败」
  引来无意义排查。同一次加载顺带解决间隔输入框的问题：它此前是 `ref(7)` 硬编码
  初值、从不向后端读实际值，用户改成 30 天后刷新页面又看回 7 天。

- **定价同步的 API 测试会打真实网络，一条用例把整文件拖到超时**（需求 9）：
  `POST /llm/pricing/sync` 里 `UpstreamPriceSyncer()` 用的是默认 fetch，测试若不打
  monkeypatch 就真的向外发请求：整个测试文件从 7.5 秒变成 300 秒以上。危险的地方
  不是慢，而是**这条用例在有网时会"通过"**——它测的其实是上游可用性，不是本项目
  的路由行为，断网或上游改版时才突然变红，看起来像自己的代码坏了。改为在测试里注入
  固定文档，同步器的网络路径单独用假 fetch 覆盖。

- **超时默认值偏紧，且运行时兜底常数与配置默认值各写一份**（需求 8）：
  首字节 15s、流式静默 30s、非流式 60s 对开了最大强度思考的上游来说不够——一段长
  推理前缀就会被判成超时，用户看到的是"上游没响应"而不是"还在想"。放宽为
  60 / 120 / 600s。同时 `llm_manager.py` 与 `resilience.py` 里有三处硬编码兜底、
  熔断五参数另有一份独立常数：**放宽配置默认值不会改到它们**，于是同一个字段在
  "用户没填"和"配置对象没建起来"两条路径上取到不同的值。改为统一从
  `LLMBackendConfig` 的字段默认值派生，并加测试锁住「运行时兜底 == 配置默认值」。

- **前端手抄了一份超时默认值，从界面新建供应商会把旧值写回去**（需求 8）：
  `webui/src/api/llm.ts` 的 `resilienceDefaults()` 是照抄后端的字面量。后端放宽后
  它仍是 15/30/60，于是**从界面新建一个供应商，会把已经放宽的默认值盖回紧的那套**
  —— 而用户没有碰过任何超时输入框，界面上也不显示这是"默认值"还是"我填的"。
  改为解析后端模型，并加测试守住这 6 个字段的一致性。

- **「禁用自动检查」开关渲染了两遍，拨一个另一个跟着动**（需求 16）：
  `UpdateRegistryCard.vue` 里同一个 `disable_auto_check` 有两个 `n-form-item`、
  两个同名 `data-test`、两套不一致的标签文案，还共用一个 `v-model`。用户会读成
  "改了一个，另一个没保存住"。它同时制造假绿：按 `data-test` 取控件的测试拿到的是
  长度 2 的集合，断言"存在"永远成立。删掉重复块并修正随之失真的注释。

- **Claude 与 Gemini 收不到系统提示词——前者必然报错，后者静默降级**（需求 7）：
  第 7 条原文点名的 `Object of type LLMChatTextContent is not JSON serializable`
  出在 `claude_adapter.py`：`messages` 那一项经过
  `convert_llm_chat_message_to_claude_message()` 转成纯 dict，而 `system` 这一项
  直接取 `system_messages[0].content`——一个 `list[LLMChatTextContent]`。
  `requests` 对 `json=` 调用 `json.dumps`，于是每一次带系统提示词的调用都在发出
  之前就抛错。而本项目的 Agent 运行时**总是**带系统提示词（人格、技能目录、
  工具说明都在里面），所以这不是偶发，是 Claude 后端整体不可用；
  流式与非流式两条路径各有一份同样的代码。
  Gemini 是同一个缺口的另一种形态，但**不报错**因此更难发现：
  `convert_llm_chat_message_to_gemini_message` 把 `system` 和 `user` 一起交给
  `convert_non_tool_message`，后者把非 assistant 的一律映射成 `"role": "user"`。
  系统提示词于是变成对话里的第一条用户消息——请求成功、模型有回复、而人格与规则
  的权重完全不同（Gemini 有专门的 `systemInstruction` 字段，代码里一次都没出现过）。
  现新增两个转换函数，口径一致：**合并全部系统消息的全部文本部件**
  （只取第一段等于静默丢掉技能目录与工具说明，而模型仍会正常回答——那是最难发现
  的一类缺陷）；**非文本部件跳过而不抛错**（顶层字段只接受文本，一张误入的图片
  不该让整条请求失败，调用方的意图是设定人格）；**没有可用文本时返回 `None`**
  让上层剔除该键（`"system": []` 与「没有 system」在上游侧不是一回事）。
  Gemini 侧同时把系统消息从 `contents` 移除——留着会让同一段文字被算两遍 token，
  而第一条用户消息不再是用户真正说的话。两个适配器的**流式与非流式都改**：
  只修一条会让同一个 Agent 在两种模式下人格权重不同，而那个差别不会有任何报错。

- **界面上的「自动检测」把保存挂在前端多走一步上**（需求 7）：第 7 条原文前半句是
  「模型管理无法实现自动定期监测更新模型**并保存配置**」。后台调度器
  `TaskScheduler._detect_backend()` 那条链一直是完整的（指纹校验、写
  `backend_config.models`、`reload_backend`、`save_config_with_backup`，
  每步失败都回滚），而界面那个按钮打的 `GET .../auto-detect-models` 只把结果
  return——前端拿到之后再打一次 `PUT /backends/<name>` 间接落盘。
  问题不是「不能保存」，而是**保存这件事依赖前端多走一步**：异常、切页、请求被新
  一代取代——少走那一步就只刷新了界面而没落盘，而用户看到模型列表变了，
  以为已经存好，重启进程后全没。
  现新增 `POST .../auto-detect-models/apply` 把那条链搬到界面侧。
  **刻意不让 GET 顺手保存**：GET 不该有副作用（缓存、预取、重试都会变成静默改
  配置），而 21.1 要求保持公共 API 兼容——把既有 GET 改成 POST 会破坏现有调用点。
  五条边界：`confirmed` 必填（与熔断重置同口径，改写 `data/config.yaml` 不接受
  「顺手点一下」）；**空目录绝不写回**（一次网络抖动会让上游返回空列表，照它保存
  等于让该后端在工作流里彻底不可选，而界面显示成功）；**先重载再落盘**
  （反过来会留下「磁盘新、运行旧」的现场，下次重启静默切到从未验证过的目录）；
  任一步失败都回滚内存目录；目录没变时如实报 `changed=false`（报成功会让运维以为
  刚才那次操作改了什么，从而去别处找原因）。
  实现时踩过一个坑值得记：函数体内又取了一次 `CONFIG_UPDATE_LOCK`，而
  `@serialize_config_update` 已经持有它——`asyncio.Lock` 不可重入，请求永久挂住，
  表现是**测试超时而不是报错**。

- **十八条测试在「镜像内测试」里以 error 收场，因为三处 skip 判断都漏了同一条
  路径**（需求 24.3）：`run-tests.yml` 的 Docker image validation 把仓库挂进
  **运行时**镜像再跑整个 `./tests`。那个镜像只装 Python + ffmpeg + libmagic1——
  没有 `git`、没有 Node/`npx`，而产品本身不需要它们。
  失败分两类，原因都与被测行为无关：
  `git` 相关 16 条（私有路径门禁 7、WebUI 契约 4、版本管理 5）——这三份文件
  **各自都写过**一句「git 不可用就 skip」，但都只看 `returncode != 0`，
  而可执行文件不存在时 `subprocess.run` 直接抛 `FileNotFoundError`，
  那句判断一次都没执行到；`npx` 相关 2 条是 context7 MCP 用例。
  现新增 `tests/utils/external_tools.py`，把「这个工具在不在」收成一处
  `shutil.which` 判断，六个站点统一调用（三份 git helper + `_staged_deletions`
  + 两条 MCP 用例）。用 `which` 而不是「跑一次看它报什么错」：后者要为每个工具
  各写一遍异常处理，而缺失这件事与工具无关，是同一个判断——散在三处各写一遍的
  后果就是上面那个「三处都漏同一条路径」。
  **刻意不用 `-m "not integration"` 排除**：那会让本机也不再验这条链路，
  而它证明的是需求 10 的核心证据（真实下载的 Skill 进运行时、正文经
  `skill_<id>` 到达模型）。skip 与 pass 的区别在报告里可见，exclude 之后连这个
  都看不到。本机有 Node 与那份下载产物时照常运行。
  验证方式同步改掉：不再拿本机 venv 结果当发布依据，而是本机 `docker build`
  后用 CI 的同一条命令在容器里跑（已确认镜像内 `git ABSENT` / `npx ABSENT`）。

- **三处测试把本机环境当成了普遍环境，Linux CI 上必然失败**（需求 24.3）：
  本机 2935 passed 而 CI 四个后端 job 全红，`docker` job 因此被跳过——门禁拦住了
  镜像发布，但这三处本该在推送之前就被发现。共同形态是**测试断言了一个只在
  这台机器上成立的事实**：
  两条 integration 用例硬断言 `.qa-real-agent-browser-20260827/` 存在，
  而该目录按 `.gitignore` 的 `/.qa-*/` **刻意不进仓库**（第三方下载内容，
  体积与许可都不该由本仓库承担）——硬断言等于要求每台 runner 与每个 clone
  都先手工下载一遍。现改为缺失时 `pytest.skip` 并说清原因；本机有产物时照常
  运行，因此那条证据链（真实下载的 Skill 能进运行时、正文能到达模型）没有消失,
  只是不再要求它无条件在场。刻意**不**用 `-m "not integration"` 排除：
  那会让本机也不再验它。
  `test_version_management.py` 写入 LF 却断言读回 CRLF，那个等式只在 Windows 的
  文本模式换行转换下成立；改为写入时 `newline=""` 关掉转换，期望值两个平台一致——
  这条用例验的是「回滚不动无关的新文件」，让它的期望值依赖运行平台等于给它加了
  一个与主题无关的变量。

- **同名文件占用持久化目录时，Windows 上给出的是一句指向不存在问题的建议**
  （需求 18.2）：这一处由上面那条 CI 失败**暴露出的产品缺陷**，不是测试问题。
  `ensure_data_directories` 只为 `ENOTDIR` / `EEXIST` 准备了「路径中的某一级已被
  同名文件占用」，而两个平台给的错误码不同：Linux 报 `ENOTDIR`，
  Windows 报 **`ENOENT`**（它先解析整条路径，父级不是目录时报「找不到路径」）。
  于是同一个部署错误在 Windows 上落到兜底文案「请为该路径授予当前用户的写权限」——
  那句建议指向一个不存在的权限问题，操作者会去改 ACL，而要做的是移走那个同名文件。
  现 `ENOENT` 归入同一分支。回归测试改为断言**处置内容**（而非某个平台的错误码
  分支），并额外断言它**不会**落到权限建议上——反向断言才是真正防回归的那一半。

- **发送限流与「上游真的慢」被混成同一个数字**（需求 19.5）：19.5 原文点名五种
  原因不能混成一个「QQ 慢」，其中一项是**发送限流**。而 `send_seconds` 是
  `send_started → send_succeeded` 的整段墙钟时间，里面同时含着两件性质相反的事：
  我们为防刷屏**主动等**的时间（`pacing.wait_before_page`，设计行为），
  和上游**真的慢**的时间（网络、QQ 服务端、限流拒绝后的重试）。两者处置正好相反——
  前者调 `send_pacing` 配置，后者查上游。混成一个数字时，一条十页回复因节流等了
  20 秒会显示成「平台发送 20 秒」，运维去查 QQ 而 QQ 什么问题都没有；反过来上游
  真的慢时也会被归到「我们自己配的节流」上。现场报障那句「系统显示成功到收到回复
  中间隔了很久」正是这个形态：Kirara 侧已经 `send_succeeded`，用户手机上还没收到，
  而那段时间的大头是节流。
  现新增 `send_pacing_waited` 阶段与 `send_pacing_seconds` / `send_upstream_seconds`
  两列（迁移 `f4a2c8e1b573`）。三处刻意的设计：
  **秒数走 details 而不是两个时间戳相减**——节流是一段一段发生的（每页之前等一次），
  墙钟差只给出「第一次等待开始到最后一次等待结束」，中间还夹着真正的发送，
  累加值才是「我们一共主动等了多久」；
  **失败路径同样记录**——「等了 18 秒然后失败」与「上游 18 秒后拒了」是两个不同的
  故障，而它们的 `send_seconds` 相同；
  **这两列上 `0` 与 `NULL` 含义相反**——`0` 是「测了，这次没等」（单页回复不触发
  节流），`NULL` 是「这条链路没有测量节流」（没有节流概念的 Telegram / WeCom、
  不上报的第三方适配器）。因此它们用 `_zero_aware_or_none` 而不是
  `_positive_or_none`：后者会把 `0.0` 当成没测到，那正好抹掉这两列存在的理由。
  `send_seconds` 保留不动——它回答「用户等了多久」，是一个独立且必要的问题。
  历史行不回填：回填需要一个「当时节流等了多久」的值，而那个值从来没被记录过，
  按配置重算是编数据（`send_pacing` 可能改过，抖动本身也是随机的）。

- **QQ 自身的热更新在代码里一个诊断都没有**（需求 18.4、19.5）：18.4 点名七件事
  各自要有诊断信息与可测试的状态转换，「QQ 热更新」是其中一件；19.5 更明确要求它
  不能与「LLM 慢」「发送限流」混成一个「QQ 慢」。而 `qr_login.py` 里 `hotUpdate`
  出现次数是 **0**——日志里有，代码里没有。文档写了「怎么关掉它」，
  但没有任何接口回答「它现在是不是正在跑」。
  这不是一个理论问题：现场日志里热更新与一次真实对话完全重叠——
  `07:56:20` 开始下载、`07:56:56` 下载完（36 秒窗口），而
  `[收-私] 写一个回火算法` 恰好落在 `07:56:56` 这一刻。运维事后问「那条为什么慢」
  时，面板上没有热更新这回事，唯一能得到的结论就是「QQ 慢」，
  而真正的原因是上游正在后台拉一个几十 MB 的包。
  现 `QRLoginSnapshot` 新增 `hot_update`（六态 + 目标版本 + 起止时刻 + 窗口长度 +
  处置建议）。四条边界：
  **它是另一条线，不进 `state`**——折进同一个字段会让「正在下载更新」顶掉
  「等待扫码」，而操作者此刻真正需要看到的是后者；
  **还在下载时 `duration_seconds` 为 `null` 而不是 0**——0 会被读成「瞬间完成」，
  正好与「它正在占着带宽」相反；
  **整个对象为 `null` 与 `up_to_date` 是两件事**——后者是「检查过、无需更新」，
  前者可能只是日志没挂全；
  **这些日志行只有 `HH:MM:SS.mmm`、没有日期**，因此起止时刻只承诺「时分秒可比」，
  界面只显示区间长度、不格式化绝对时刻——拿一个编出来的日期去显示
  「07:56 开始」是在给出没有依据的精确（与扫码那边「没有时间戳就报 `age_unknown`」
  同一条纪律）。
  WebUI 在「机器人」页显示成一枚**独立标签**，且只在 `downloading` 与 `failed` 时
  出现：`ready` / `up_to_date` / `checking` 此刻不影响任何事，常驻一枚「已就绪」
  只会挤占状态区，让真正需要注意的标签更难被看见。

- **模型直接贴在正文里的代码被正文规则改坏（QQ / 企业微信 / Telegram 三处）**
  （需求 6、19.1、19.3）：现场报障那段 QQ 回复贴了整整一百行 Python，
  **一个反引号都没有**——模型在对话里直接把代码写进正文，这是常态而不是例外。
  此前解析器只认围栏，于是这些行整段走正文规则：顶格的
  `# ------------------- TSP 应用示例 -------------------` 被 ATX 标题规则吃成
  `■ ------------------- TSP 应用示例 -------------------`（企业微信是 `━━━ … ━━━`），
  一行 Python 注释变成了一个标题；`_private_` 掉下划线、`*b*` 掉星号、
  `` `SELECT 1` `` 变 `「SELECT 1」`、`[a](b)` 变 `a（b）`、
  `mask = a | b | c` 被画成框线表格。整段还进不了
  `split_for_copyable_code` 的可复制路径，因此 QQ 上没有代码框、没有语言标识、
  没有复制指引，分页时也不被当成一个原子块。19.3 明文要求「代码必须保持原始缩进和
  换行，使用明确的语言标识和代码边界」，这些都不成立。
  Telegram 的后果更重：它不走块渲染，管线是
  `markdownify(convert_markdown_tables(degrade_math(text)))`，
  markdownify 会把行首空格当排版空白吃掉——一段没有围栏的缩进代码被压成全部顶格的
  一堆行，而 Python 的块结构就是缩进。QQ 侧至少还留着缩进。
  现新增无围栏代码段探测（`_detect_code_spans`）并在三个平台共用同一份判断
  （新增 `fence_unfenced_code()` 供不走块渲染的 Telegram 使用）——19.1 要求
  「平台差异只放在渲染/发送层」，「哪些行是代码」这个判断因此收在一处。
  判据刻意保守，因为**反向误判更严重**：把一段中文说明当成代码，会给用户一段带围栏
  的说明文字，还会把它送进「长按可整段复制」的路径。因此一个 run 必须以一个
  **不含 CJK 的强代码行**开头、包含至少两个强行、中途不出现任何自然语言行；
  中文注释与中文字符串落在中性档由已成立的 run 吸收，从不自己开一个 run。
  另有两条专门的防误判：赋值语句右侧是「三个以上纯字母词且无任何代码标点」时
  按正文处理（`Cost = benefit minus risk.` 的形状与 `x = y` 无异）；
  SQL 续行关键字（`FROM` / `ON` / `AND` …）只认全大写，否则以 `On` / `Set` 开头的
  英文句子会变成可被代码段吞掉的中性行。实测那段回复：QQ 侧从 2 条纯文本变成
  3 条消息加一条复制指引，代码成为**一条**带 `python` 标识的可复制消息，逐行原样。

- **容器收不到 SIGTERM，所有优雅关闭逻辑在 `docker compose down` 下一次都不执行**
  （需求 18.3）：`entry.py` 注册了 `SIGTERM` 处理器，`finally` 块里做了完整收尾
  （停调度器、flush 记忆的异步写队列、关追踪与数据库、停 Web 服务器、停所有适配器、
  断开 MCP）。但 `docker/start.sh` 最后一行是 `python -m kirara_ai`——**没有 `exec`**，
  于是容器里 PID 1 是 bash，而 bash 在等待子进程期间不转发信号：SIGTERM 被它吞掉，
  10 秒宽限期后整个容器被 SIGKILL。三个可观察后果：记忆的异步写队列没 flush
  （最后几条对话记忆丢失）；`sending` 状态的投递不会被隔离成 `ambiguous`，
  下次启动的 `recover_on_startup()` 面对一份不完整的现场；适配器不走 `stop()`，
  反向 WebSocket 被硬切。而需求 1 与 18 的整个「重启恢复」都建立在
  「上一次是干净停下的」这个前提上。现改为 `exec python -m kirara_ai`
  （Python 直接成为 PID 1），并在两份 compose 里声明 `stop_grace_period: 60s`——
  Docker 默认只给 10 秒，超时仍会 SIGKILL，那样 `exec` 等于白加：信号到了，
  但没时间用完。文档补上验证方法（`ps` 看 PID 1、`stop` 后看收尾日志）。

- **带工具的 Agent 永远拿不到流式，而那是本项目最常见的形态**（需求 4、21.3）：
  `_execute_model` 的流式分支条件含 `and not candidate_request.tools`，而 `tools`
  在**第 0 轮**就非空——`run()` 先拼好 MCP 工具、队友委派工具与技能工具再进循环。
  于是一个绑了任何工具的 Agent，它绝大多数只需一次回复的对话从头到尾没有一次流式
  请求，也就拿不到 `stream_first_byte_timeout_seconds`、`stream_idle_timeout_seconds`
  与首字节前的安全故障转移——而 21.3 把这三项列为必须集中配置并生效的参数。
  那条限制的原始理由是「聚合文本会丢掉 `tool_calls`」，它只对**真的产生了工具调用**
  的那一轮成立，真正的缺口在适配器：流式解析只读 `delta.content`。
  现补齐三层：① OpenAI 兼容适配器新增 `accumulate_stream_tool_calls` /
  `resolve_stream_tool_calls`，按 `index` 归属、`arguments` 分片拼接、`id` 取首帧
  （单个坏帧跳过而不抛，与文本增量同一约定）；② 聚合器把工具调用与文本一起交出
  （只拼文本时工具调用会在这一步静默消失，上层把「模型想调工具」当成「模型答完了」
  ——那比一刀切成非流式更糟）；③ 新增 `LLMStreamToolCallProtocol` 声明式标记与
  `LLMManager.stream_supports_tool_calls()`，按**该模型的全部候选供应商取最弱的一个**
  判定（故障转移会在候选之间切换，只有第一家支持的话一次转移后工具调用就消失了）。
  Claude / Gemini / Ollama 目前只解析文本增量，因此带工具时仍走非流式——
  工具调用完整，只是少了那三项保护。

- **分页退化成「一个块一条消息」，8.7 KB 的回复就被截断**（需求 19.4）：
  `_split_structured_body` 在每个代码围栏和每个框线表处 flush 之后直接 `extend`，
  **从不回填**。于是一段「标题 + 正文 + 代码 + 表格」× N 的技术回答每个块各占一页。
  实测：20 小节（5.8 KB）→ **80 页、均页 96 字节、利用率 2.5%**；
  30 小节（8.7 KB）→ 撞满 100 页上限**被截断**，而它离「单页上限 × 页数上限」
  （380 KB）差两个数量级。19.4 的两条硬性要求各违反一条：每页 96 字节不是
  「按平台安全长度拆分」的意思，而截断就是丢内容——这是「回复内容可能不够全」
  比页码更靠根本的成因（页码那条让人**以为**不全，这条是真的没了）。
  现新增 `_backfill_chunks`：块边界从**强制**切点降为**优先**切点，相邻的小块合起来
  还装得下就留在同一页。合并的是「已经完整的块」（围栏成对、表格带边框），
  不是把两个块的内部拼在一起；只合并相邻项，不重排。
  修正后 20 小节 → 2 页（利用率 79%），100 小节不再截断。三家渠道共享这个实现。

- **企业微信被动回复只发第 1 页，却记成完全成功**（需求 19.4）：
  未开通主动回复能力时上游返回 `48001`，退回被动回复 API——它**只能回一条消息**。
  此前把第 1 页交出去就记 `send_succeeded`。于是一条 4 页的回复变成：用户收到
  「第 1 页 / 共 4 页」然后什么都没有，而投递耗时看板上这一轮是成功。
  19.4 要求「保证顺序稳定、**全部发送**、失败可记录」，这一条三项全丢。
  现新增 `truncated_passive_reply()`：在那唯一一条消息末尾说明还有几页未发出
  并给出用户自己能做的事，`send_succeeded` 带上 `dropped_pages`，
  日志说清这次丢了几页。刻意不把剩余页塞回那一条——被动回复的长度上限与主动发送
  一致，硬拼回去会让整条被上游拒收，那时连第 1 页都收不到。

- **`webui` 渠道的配置说明拿一个不成立的事实当立论依据**（需求 4）：
  `channel_reply_stream_modes` 的描述写「WebUI 能逐步渲染而 QQ 不能」，
  而 WebUI 的在线测试对话是一次性 POST `/llm/chat`，后端没有任何聊天 SSE 路由，
  `WebUIAdapter` 也不实现增量协议——它比 QQ 更不能逐步渲染。运维照这句话把
  `webui` 配成 `incremental` 会得到一个静默无效的开关。当时的处理是把三处文案
  （配置描述、`resolve_reply_stream_mode` 与 `__init__` 的注释）改为按真实能力表述。
  **后续复检推翻了那次处理的前提**：「WebUI 不能逐步渲染」本身是缺口而不是限制——
  浏览器里一条 SSE 事件就是一次改写。因此本轮补上了 `POST /llm/chat/stream`
  与 `WebUIAdapter` 的增量协议实现（见本节 Added），并把这十一处文案
  （代码注释、配置描述、前端提示、QUICKSTART、PRACTICAL_PLAN）再次按现在的
  真实能力更正：能兑现 `incremental` 的是 Telegram 与 WebUI 在线对话，
  QQ / OneBot 与企业微信仍然退回 `aggregate`。
  记在这里而不是删掉：一次「用错误事实论证的正确修正」与一次真正的能力补齐
  是两件事，前者会让人以为这条路已经走到头。

- **QQ 回复里的裸 Markdown 标记原样发给用户，而企业微信早就有一整套符号表**
  （需求 6(a)(b)(e)）：第 6 条原文要求「参照 telegram、wecom 等其他 APP 的格式，
  让 QQ 回复更美观」，而实测方向恰好相反——同一段回复在 QQ 上是
  `## 二、结论` / `**重点**` / `` `T` `` / `- 第一条` / `> 引用`，在企业微信上是
  `━━ 二、结论 ━━` / `「重点」` / `『T』` / `• 第一条` / `┃ 引用`。**六种标记在 QQ 上
  全部原样保留。** 根因在 `render_plain_text`：它接收一个 `TextDocument`
  （`parse_text_document` 已经把块结构解析好了）却只取 `document.source`，
  把 `blocks` 全部丢掉，随后只做数学降级与表格转换——解析了，然后扔了。
  现新增共享的 `render_rich_text(document, inline_rules, block_renderers)`，
  平台只提供**符号表**，解析与结构处理仍由共享实现完成（项目自己的约定是
  「不允许各平台各写一套 Markdown 解析」，这个函数是那条约定在块级上的落点）。
  QQ 的符号与企业微信刻意不完全相同（`■`/`▎`/`·` 标题、`【】` 强调、`「」` 行内代码、
  `（）` 链接——QQ 气泡更窄，`━━━ 标题 ━━━` 会把标题挤到折行），
  但「有没有渲染」不再不同。官方 QQ 机器人渠道共用同一张表：
  两条接入方式面对的是同一个 QQ 客户端，给出两种排版会让用户以为是两个机器人。
  链接的 URL 保留——删掉等于给出一个点不开的词。

- **未闭合的围栏被补上闭合，于是截断回复的剩余正文变成「代码」**（本轮引入并当场修掉）：
  给 QQ 接块渲染时，`_render_code` 顺手给代码块补了闭合围栏。解析器把未闭合围栏也
  收成代码块（否则后面的内容会散成正文），因此这个改动让一条被上游截断的回复里
  剩下的正文变成了合法代码块——`split_for_copyable_code` 随后判它是代码，
  发出一条「长按可整段复制」，而用户复制走的是半句话。既有测试
  `test_an_unclosed_fence_does_not_swallow_the_following_prose` 当场变红。
  现 `TextBlock` 新增 `closed` 字段（默认 `True`，既有构造点行为不变），
  两个渲染器都据它决定是否补闭合。企业微信侧有同一个潜在缺陷（补 `［/代码］`
  等于宣称「代码到这里结束」，而事实是上游被截断了），一并修掉。

- **被隔离的出站投递会让某个会话彻底收不到消息，而面板上一切正常**（需求 1、5）：
  `recover_on_startup()` 把上次进程被杀时留在 `sending` 的投递改成 `ambiguous`，
  这个决定是对的（那些动作可能已经到了对方，重发会造成重复消息）。但
  `_deliver_recipient_through` 有一条更强的规则：同一收件人序列里存在更早的
  `ambiguous` 或 `dead_letter` 时，后续投递**直接返回、不再发送**。于是
  `docker compose down` 之后可能出现某个群从此收不到任何回复，而适配器状态是
  `connected`、日志里没有错误、投递接口每次都「成功返回」——用户看到「机器人不理我
  了」，运维看到一切正常。队列计数本来就在采集（健康快照的 `outbox`，同时充当存储
  写入探针），此前**没有任何消费方**：readiness 不看它，WebUI 除类型声明外零渲染。
  一个采集了却无人消费的指标与没有采集没有区别。现 readiness 把这两个数纳入判定并
  给出处置——去 QQ 客户端确认是否已送达，**不要重试**（重试正是它被刻意隔离要避免的
  事）；evidence 里加 `outbox_ambiguous_count` / `outbox_dead_letter_count` 供外部监控
  直接告警。它排在扫码与重连之前（那两类是「等就行」，这一类已经在丢消息），
  但仍让位于凭据被拒与存储不可写（那两类要求先改配置）。`retry_wait` 不算卡住，
  队列读不到（没配数据库）也不算。

- **成本趋势测试在 UTC 午夜后的头 5 分钟必然失败**（测试缺陷，非产品缺陷）：
  `add_trace` 用 `now() - 5 分钟`写行、`_today()` 用 `now()` 算日期——各取一遍时钟。
  在 00:00–00:05 UTC 之间这两个 `now()` 落在不同日期上：行进了昨天的分桶，
  而断言去找今天的分桶。这不是理论风险，本轮实测撞过一次：一次完整跑跨过
  00:00–00:07 UTC，`TestDailyCostTrend` 的**前 4 个用例失败、后 3 个通过**，
  边界恰好在 00:05。已改为整个模块只取一次时钟，写入与断言共用它；
  用固定在 00:02 UTC 的时钟验证过修复前后的行为差异。
  同目录其余统计测试用 `daily[-1]` 取分桶，不受影响。

- **适配器面板不会自己刷新，于是「等就行」变成「盯着一个静止的画面等」**（需求 1、3）：
  冷启动宽限期 180 秒、二维码有效期 120 秒——两个都是会自己变化的状态，而这一页
  只在 `onMounted` 拉一次。用户重启容器后看到「正在启动」，然后一直是「正在启动」；
  上游其实两分钟前就连上了，他要手动刷新整页才知道。二维码那栏更糟：倒计时会自己
  走到 0 显示「已过期」，而上游早就生成了新码，路径与刷新次数还是旧的。
  现每 10 秒静默拉一次（与容错面板同一间隔），可在面板上关掉；轮询失败只进控制台
  （每 10 秒弹一次「获取列表失败」会把界面糊满），后台轮询不动 loading 骨架屏。

- **框线表的宽度上限按错了一个量级，48–57 列的常见表格全部画成框线**（需求 6(c)）：
  `MAX_TABLE_DISPLAY_WIDTH = 60` 的依据写成「30 个汉字的两倍显示宽度」，而 30 个汉字
  偏大——375pt 手机上 QQ 气泡正文区约 280pt，默认字号一行放得下 17–18 个汉字，
  即 35–37 显示列。实测三张真实表格：4 列中文参数表 48 列、3 列长键名配置表 57 列、
  2 列长值状态表 52 列，在 60 之下**全部画框线**，而它们都放不进手机一行。折行之后
  竖线错位，而 `render_field_table` 的判据正是「错位的框线连『哪个值属于哪一列』
  都保证不了」。现改为 38。另有一层无法靠计算修正的风险一并记入注释：
  制表符 U+2500–257F 的 East_Asian_Width 是 **Ambiguous**（西文字体 1 列、
  中日韩字体 2 列，而 `display_width` 按 1 计），边框行全是制表符、数据行是混排，
  于是在把 Ambiguous 当全角的客户端上两者膨胀幅度不同——实测边框行 48→96、
  数据行 48→53，对齐彻底失效。这让「窄表才画框线」从美观取舍变成正确性要求。
  2 列短表与 3 列中等表（最常见的形态）观感不变。

- **超过 256 字符的代码在 Telegram 上没有任何复制提示**（需求 6(d)）：
  `CopyTextButton` 的载荷上限是 256 字符，`copyable_button_text` 超限返回 `None`，
  于是那条代码消息一个按钮都没有——而 256 字符只够十来行代码，这不是边缘情况。
  不挂按钮的决定是对的（挂上去会让整条 sendMessage 被平台拒收），但结论下得太早：
  Telegram 客户端在 Markdown 代码块右上角**自带**复制图标，缺的不是复制途径，
  是用户不知道有。一条 300 字符的代码什么提示都没有，而它旁边 200 字符的那条带着
  显眼的「复制代码」按钮——两条看起来能力不同，实际都能复制。现超限时追加一句
  独立的指引（不进代码消息，那条整体是可复制的代码；每个代码块只发一句），
  文案刻意不含任何 MarkdownV2 保留字符——一个未转义的 `_` 会让整条消息被拒收，
  于是一句「提示」把本来能发出去的回复变成发不出去。

- **群成员的角色与头衔在融入时被丢掉**（需求 7、12）：被融入项目的
  `_convert_group_member_info` 返回 `extra_info={'role','title','join_time','last_sent_time'}`，
  本项目把两个转换函数合并成 `_profile_from_info` 时这四个字段没有跟过来。
  `UserProfile.extra_info` 字段一直存在但全仓零写入点。`role` 尤其不能少：
  它区分群主 / 管理员 / 普通成员，而那正是「这条指令要不要执行」的判据——
  缺了它，「只让管理员触发某个动作」的工作流只能硬编码 QQ 号，而那份名单换个群
  就失效。需求 12 明确要求不得降低原有功能细节品质。现补回，且只收上游真的报了的键
  （填 `None` 会让消费方分不清「没报」与「空值」；`get_stranger_info` 压根与群无关，
  给它一个全 `None` 的字典等于回答一个没被问的问题），空串按缺失处理，
  全缺时给 `None` 而不是空字典。

- **QQ 冷启动那几分钟被报成「等待连接」，readiness 随之建议去查心跳**（需求 1）：
  反向 WebSocket 由 OneBot 实现主动拨入，而它要先冷启动 QQ 再完成登录——现场日志
  里这一段跨了 19 分钟（`05:37:19` 容器启动、`05:56` 才 `QQ 登录成功`），最快也在
  90 秒以上。这段时间里 Kirara 侧不可能有连接：它是服务端，只能等。此前这段时间
  返回 `waiting`，readiness 落到兜底分支给出「检查 IM 适配器运行状态、登录状态和
  连接心跳」——那是这个窗口里**最不该给的建议**：心跳、令牌、地址三项都没有问题，
  照着查一遍全部正常，然后开始怀疑配置，而配置从一开始就是对的。现新增启动基线
  （`_start_monotonic`，`start()` 置位、`stop()` 清空）与
  `initial_connect_grace_seconds`（默认 180 秒），窗口内报 `initializing`
  并给出「等待上游完成 QQ 冷启动与登录」，超过它才转 `waiting`。
  与 `reconnecting` 的区别是**有没有连上过**：后者的前提是本进程内至少连过一次，
  因此在冷启动路径上不可达。手动停掉的适配器仍显示 `disconnected`——
  运维需要知道它是被停的，而不是「正在启动」。填 `0` 关闭，拿回旧行为。

- **QQ 的发送节流把一次正常回复拖成分钟级，而 Telegram / WeCom 没有这个现象**
  （需求 5）：`pacing.py` 自己写下的判据是「风控看的是**频率**，不是等得够不够久」，
  可它的 `0.1 秒/字符` 是从被融入的 OneBot 适配器项目照搬的，而那个项目按**消息段**
  计费（一个文本段通常几十个字符）。本项目先分页再发送，一页 3800 字节
  （约 1300–1900 字符）。同一个系数换了计费单位之后：长度项在 **80 字符**就撞上
  8 秒的单页上界，于是「按长度递增」对任何真实页面都失效（每页算出同一个数），
  抖动被上界一起裁掉（页间间隔变成恒定的 8.000 秒——最可识别的机器特征，
  恰好违反引入抖动的初衷），代价随页数线性累加。实测：现场那条 4578 字符的回复
  分成 3 条要纯等 13.7 秒，带 3 个代码块的回复分成 10 条要纯等 54.6 秒。
  而「Telegram 与 WeCom 没有这种现象」成立的原因很直接——节流全仓只有 OneBot
  一家在用（另两家一行都没有）。现把等待拆成两部分：下界每个间隙都付（它是
  「不连发」的硬保证，也正是风控针对的行为），按长度追加的部分受新增的
  `send_pacing_maximum_total_seconds`（默认 6 秒）约束并按间隙数摊开；
  抖动改为围绕确定值双向摆动，因此在任何页面尺寸下都仍然存在。
  两条投递路径都把总页数传给节流器——漏传会静默回到旧行为。
  修正后 3 页回复纯等待降到 6–10 秒，10 页从 72 秒降到 24 秒以内。

- **上游日志没有时间戳时，二维码永远显示「还剩 120 秒」**（需求 3）：
  `_parse_timestamp` 认两种时间戳形状，而现场那几行
  （`[PMHQ login] listener.onQRCodeGetPicture expireTime= 120 urlLen= 68`、
  `[I] qq-protocol 二维码文件已保存: …`）一个都不匹配。`_apply` 于是回落到
  「读日志的时刻」，`generated_at` 变成现在、`remaining_seconds` 恒为满额 120、
  状态恒为 `waiting_scan`——**永远不会翻成 `expired`**，无论那张码是十分钟前生成的。
  模块 docstring 声称「过期由本地时钟判定而非等上游日志行」，时钟判定确实在，
  但它拿到的生成时刻是编的，于是判定永远为真。这正是「面板说有效、手机说过期」
  的成因。现新增 `age_unknown` 状态：生成时刻不可信时 `generated_at` /
  `expires_at` / `remaining_seconds` 一律返回 `null`（`0.0` 会被读成「刚好过期」，
  同样是没有依据的论断），`validity_seconds` 仍然给出——`expireTime= 120`
  是真的，它回答「这种码能撑多久」，与「这张还剩多久」是两个问题。
  `is_scannable` 不含该状态。已扫码 / 已成功 / 已失败不受影响。

- **界面上的「剩 N 秒」是一个不会动的静态数字**（需求 3）：二维码有效期 120 秒，
  而「看一眼面板 → 走去拿手机 → 解锁 → 打开扫一扫」轻易花掉一半。一次性渲染的
  数字在这个尺度上必然说谎，且它说的谎恰好是「还来得及」。现按 `expires_at`
  每秒重算（`onUnmounted` 清理定时器），归零后标签自动从「待扫码」改成
  「二维码已过期」并提示刷新，而不是继续显示「待扫码（剩 0 秒）」；
  tooltip 里补上「有效期 N 秒」——此前 120 秒这个事实只活在后端常量与文档里。

- **`incremental` 档在 Telegram 上把同一条回复发两遍**（需求 4）：增量投递把占位
  消息改写成完整文本之后，`_deliver_runtime_result` 仍然**无条件**再调一次
  `send_message()`。`IncrementalReplyDelivery.active` 只被测试读过，生产代码里
  没有任何抑制分支。用户看到机器人把同一段几千字的内容说了两次，
  开着 `incremental` 反而比关掉更糟。现 sink 新增 `delivered`
  （只在收尾**确实成功**后为真），`RuntimeResult.delivered_incrementally` 携带它，
  dispatcher 据此跳过整段投递并补记 `send_started` / `send_succeeded`
  （跳过时没人记这两个阶段，投递耗时看板上这一轮会凭空消失）。
  判据刻意不是「本轮尝试过增量」：占位失败、改写被限流、渠道不支持编辑时
  用户屏幕上没有完整回复，整段投递必须照常兜底。

- **Agent 级 `reply_stream_mode` 存不下来、也调不到**（需求 4）：三层优先级
  （Agent 声明 > 渠道默认 > 进程默认）的最上面一层实际只能靠在进程内手工构造
  `AgentDefinition` 触达——`_agent_to_dict` 不含这个字段，显式设过的值在
  `registry.json` 落盘后消失，重载时 dataclass 默认值把它变回 `inherit`；
  `_agent_payload` / `_agent_from_payload` 也不含它，REST 既读不到也写不了。
  一个只在内存里生效、重启即丢的配置项比没有这个配置项更糟：运维设过它、
  看到生效了，重启之后行为悄悄变回去，而界面上没有任何痕迹。现三处补齐，
  旧注册表缺键按 `inherit` 处理（缺省成 `off` 会让已配渠道级流式的部署
  升级后悄悄退回非流式），取值校验仍由 `AgentDefinition.__post_init__` 唯一持有。

- **同一条回复里出现两次「第 1 页 / 共 2 页」**（需求 6 的「内容不够全 / 数据丢失」）：
  代码要单独成条，因此 `_text_pages` 把正文与代码拆成若干片段分别分页，
  而页码是**每一段各自**算的。一条「正文 + 代码 + 正文」的回复于是发出 5 条内容
  消息，却告诉用户「共 2 页」，且「第 1 页」出现两次——他唯一能得出的结论就是
  内容不全，而内容一条都没少：页码在说谎。现页码跨全部片段重新编号成一个序列，
  分段时按 `MAX_PAGE_LABEL_BYTES` 预留固定空间（按单段长度预留会在总页数进位时
  让某一页刚好超出 QQ 上限而被上游拒收）。同源的两处一并修：**代码消息不再带页码**
  （长按复制会把它一起复制走，粘进编辑器就是坏代码，而代码单独成条的全部目的
  正是让它可以整段复制），它在序列里仍占一位所以页码会跳号——缺的那个就是代码；
  **复制指引一个代码块只发一条**（此前每页一条），跨多条时写明「这段代码共 N 条」。
  只因长度被切开时才加页码：一条两句话的回复因「代码单独成条」变成 3 条消息不标页码。

- **`\dfrac` 渲染成 `dfracΔ Ek_B T`，而 `\frac` 好的**（需求 6 的「$\to 等乱码」）：
  未收录命令的兜底是「去掉反斜杠、保留命令名」，对真正未知的 `\foo` 合理，
  但 `\dfrac` / `\tfrac` 只是 `\frac` 的显示尺寸变体、语义完全相同——
  处理结果不该取决于作者选了哪个同义写法。同类：`\hat` / `\vec` / `\bar` /
  `\tilde` / `\dot` / `\ddot`（`\overline` 早已处理，它们没有）。
  行列环境更彻底：`\begin{cases}` 的环境名被删掉了，但列分隔符 `&` 没人管，
  于是每一行都拖着一个孤零零的符号——现场贴出的
  `1, & \Delta E \le 0 \\` 正是这个形态。现三者全部补上；`&` 只在数学片段内部
  替换（正文里的 `Tom & Jerry` 是内容），且两侧只吃空格与制表符不吃换行，
  否则多行 cases / matrix 会被压成一行。

- **一个金额让它后面每个公式都留下 `$`**（需求 6）：`_looks_like_currency` 判断的是
  单个 `$...$` 片段，但 `$` 的配对是从左到右贪心的。正文里出现一个落单的 `$`
  （金额、货币符号，或模型漏写一个定界符）时，它与后面第一个公式的开定界符配成
  一对；`re.sub` 即使原样返回这一段，扫描位置也已经越过那个闭定界符，
  于是从这里开始每个公式的配对都错位一格。实测
  `成本 $200 起。温度 $T$ 控制接受概率 $P(\Delta E)$，当 $T \to 0$ 时收敛。`
  渲染出 3 个残留的 `$`。现改为手写扫描：拒绝一段时只跳过**开**定界符，
  让闭定界符有机会与后面的内容重新配对；同时新增「中文句读出现即判为正文」
  这一条（公式不含句读，捕获到它说明中间夹着一整句话）。
  纯货币配对（`price $5 and $7 total`）行为不变。

- **画布尺寸估算偏小，「已排好」的布局渲染出来仍然重叠**（需求 2）：
  `useLayout.ts` 自己的判据是「估算值宁可略大也不能偏小：一旦小于真实渲染高度，
  节点就会重新压在一起」，而三处偏小都在高度上。① `.custom-node-body` 的
  `padding: 12px` 是**无条件**渲染的，估算只在有配置项时才算它，于是没有配置项的
  节点（`GetIMMessage` / `SendIMMessage` / `IMMessageToText`，每个工作流的头尾）
  真实高度比估算多 24px。② 代码节点主体按定值 132px 计，而预览区高度随行数变化
  （取前 5 行 + 一行 `# ...`，且 `white-space: pre-wrap` 让长行继续折行）。
  ③ 零端口代码节点渲染的是一段三行中文空态提示（约 70px），而 `portRows === 0`
  时端口区贡献 0——刚拖进画布的自定义脚本节点一定是这个形态。分隔是 60px，
  能吸收单侧误差；两侧叠加就会突破它，于是布局算出「不重叠」而渲染出来重叠，
  用户只看到角标提示「建议点击自动排布」，而他刚刚点过。现三处补齐，
  预览行数封顶 6 行（不封顶会让一个 600 行脚本把画布拉散），
  并把脚本内容一路传到三个估算入口。

- **脚本节点的端口增删无法撤销**（需求 2）：四个处理函数里只有 `addInputPort`
  复制了 config，另三个直接在 `props.selectedNode.data.config` 上原地赋值。
  而 `updateSelectedNodeData` 是先 `emit('before-node-mutation')` 让画布拍历史
  快照、再 `updateNode`——原地写入发生在拍快照**之前**，快照里已经是新值。
  撤销拿回旧的 `inputs` / `outputs` 数组，而 `config.inputs` / `config.outputs`
  停在新值上；后端 `CodeBlock` 的端口正是从 config 读的，结果是画布显示旧端口、
  保存下去是新端口。自定义脚本的端口全靠手工添加，加错一个能不能 Ctrl+Z
  直接决定这个交互好不好用。现四处写法一致。

- **整流器此前只对 Claude 生效，十个 OpenAI 兼容适配器上那四个开关从未参与决策**
  （需求 8、21.2）：供应商编辑页有整流开关、`LLMBackendConfig.build_rectifier_config()`
  也真的把配置写进了 `LLMChatRequest.rectifier`，而 `rectify_request` 的唯一调用点在
  `claude_adapter.py`。本项目里最常见的形态恰恰是 OpenAI 兼容供应商——OpenAI 官方、
  DeepSeek、Moonshot、OpenRouter、SiliconFlow、火山、腾讯云、阿里云、Mistral、
  MiniMax 全部继承 `OpenAIAdapterChatBase`。于是「发图片给不支持图片的模型 →
  换成可见占位文本后重试」这条已经写进操作文档的产品行为，在这十家上从来没有
  发生过：用户看到一次硬失败（「请求失败」），而真正的原因是一张图，
  且那不是他能自己改的。现基类的失败分支接入同一套整流循环，语义与 Claude
  路径逐字一致（只在上游**真的拒绝**后动、只改命中白名单的那一处、每类只改一次、
  改完仍失败抛原始错误、总开关或单项开关关掉时一个字节都不改）。
- **新增第四类整流：上游不认识 `reasoning_effort` 时删掉该字段再重试一次**
  （需求 8、21.2）：大量兼容网关只实现了 chat/completions 的核心字段，
  收到这个键直接 400。这类失败**换供应商也没用**——同一个不合法请求发给备用上游
  同样会被拒，故障转移只会把队列打满然后返回同一个错误。判据要求错误里
  **同时**出现字段名与「不支持 / 不认识」类措辞：只匹配字段名会把「取值非法」
  （上游只认 low/medium/high 而我们发了 max）也判成「字段不支持」并把整个字段删掉，
  那会让一个只需降档的请求彻底失去思考能力。开关为
  `rectify_reasoning_effort_unsupported`，默认开启，供应商编辑页可单独关闭。
- **推理强度在 Ollama 上被静默丢弃**（需求 8）：`reasoning_effort` 此前只有三家
  真的翻译它（OpenAI 系的 `reasoning_effort`、Claude 的 `thinking.budget_tokens`、
  Gemini 的 `thinkingConfig.thinkingBudget`）。Ollama 是第四个**有自己的思考开关**
  的适配器，却完全不读这个字段——供应商编辑页允许给它选「最大强度」，
  `llm_manager` 也把值写进了请求，然后适配器把整个字段丢掉。没有报错、没有警告，
  用户唯一能观察到的现象是「开了最大强度但答案质量没变」，而那无法自查。
  现译成 Ollama 的**顶层** `think` 字段（不是 `options` 里的一项——塞进 `options`
  会被当成未知采样参数忽略，于是又变成一次静默失效）。Ollama 没有 `max` 这一档，
  因此 `max` 映射到 `high`：透传一个上游不认识的字面量会被拒，而降一档仍然是
  「最高可用强度」这个语义。流式与非流式同一口径。
- **用量来源无法区分「四维齐全」与「上游只报了一部分」**（需求 22.1）：
  多数 OpenAI 兼容端点只回报 `prompt_tokens` / `completion_tokens`，不报缓存两维，
  而这类响应此前与「四维齐全」一样标成 `provider`。两者的可信度完全不同：
  后者的总额就是上游认定的消耗，前者的缺失维度按 0 计价、总额是**补出来的**。
  缓存读取的单价通常只有输入 Token 的 1/5 到 1/10、缓存写入往往更贵，
  因此一份「缺失维度按 0」的账单在缓存密集的部署上会系统性偏低，
  而页面上没有任何迹象表明它被补过。现新增 `UsageSource.PROVIDER_PARTIAL`，
  四个成员各有不同处置。判据是**维度是否齐全**而不是「值是否为 0」：
  上游明确报 0 是一个事实，没报是一个空缺，把前者也标成 partial 会让绝大多数
  请求挂上一个没有意义的标记。历史记录不回填——历史账单不能被后来的口径改写。
- **趋势分桶把区间内每一行物化到 Python**（需求 22.2 结尾「注意大数据量下的
  分页/索引性能」）：统计页其余聚合（概览、延迟、Provider/模型/错误分组、
  请求日志分页）都已在 SQL 侧完成，唯独日/时趋势仍是「把区间内所有行 SELECT
  回来，再用 defaultdict 累加」。默认视图看不出问题（只取近 30 天与近 24 小时），
  但调用方一旦传入显式时间范围，那两个兜底过滤器就被跳过——「导出全年趋势」
  等于把全年每一行的十列读进进程内存，行数与内存、与响应时间线性相关。
  现改为 SQL 侧按 **15 分钟槽 × 状态 × 币种** 聚合：15 分钟是所有 IANA 时区偏移的
  公约数（含 +05:30、+05:45），因此一个槽只会落进一个本地小时；日界仍在 Python 里
  用真正的 `astimezone` 换算（只有它认得 DST 与半小时偏移）。取回行数由时间跨度
  决定而不是请求数，响应形状与字段语义逐字段不变。
- **`reconnecting`：上游刚掉线与上游没了此前是同一个词**（需求 18.1）：
  `docker compose down && pull && up -d` 之后面板显示「未连接」，这个词对两种
  处境里的一种是错的——OneBot 实现掉了反向 WebSocket 并会在几秒内自己回连时，
  **什么都不用做**，而读到「未连接」的人会去重查地址与令牌，那两项从来没错。
  现新增 `reconnecting` 状态：它是全部非连接状态里唯一一个「等一下就好」的状态。
  窗口有上限（`reconnect_grace_seconds`，默认 45 秒）——连着十分钟「正在重连」
  的链路就是断了，继续显示等待状态只是换个措辞掩盖故障；凭据被拒与握手被拒
  不会被它盖掉（那两类要求操作者动手改配置）；填 0 关闭该状态，拿回旧行为。
  自检里单列 `reconnecting_count`，界面上用警告色 + 轻微脉动而非错误色。
- **扫码状态缺少「刷新」动作**（需求 18.4）：该条逐项点名有效期、生成时间、
  当前状态、**刷新动作**、失败原因、最新二维码路径，前五项里只有刷新缺失——
  快照此前只随整份适配器信息返回。二维码有效期实测 120 秒，远短于「看一眼、
  去拿手机、回来扫」这个动作序列，于是用户总在扫一张屏幕上还在、上游其实
  已经换掉的码，这正是「二维码总是过期，无法登录」的形态。现新增
  `POST /im/adapters/<id>/qr-login` 与面板上的按钮。**它只重读上游日志，
  不让上游重新生成**——生成方是 LLOneBot / PMHQ 自己的容器，把「重新生成」
  写进按钮文案是对所有权的谎报：点了没反应时操作者会去排查 Kirara。
  三种「拿不到」严格分开：适配器不存在 → 404；没有扫码环节（Telegram / WeCom）
  → `supported: false`；支持但没配日志路径 → 明确告知要填哪个配置项。
- **「重置熔断」在文档里存在、在界面上不存在**（需求 21.3）：
  `POST /llm/backends/<name>/circuit/reset` 已经落地（创建者身份 + 显式确认 +
  同时撤销持久化隔离），操作文档第 4.0 节也把「容错面板每一行都有『重置熔断』」
  写成了产品行为，而前端没有任何代码调用它——面板显示「已熔断」，用户看得到
  状态却没有任何动作能改变它。文档承诺一个不存在的按钮比没有文档更糟：
  用户会去面板上找，找不到之后怀疑自己看错了版本。现补上调用点与按钮，
  只在 `open` / `half-open` 时出现，需二次确认（它把一个刚被判定不健康的上游
  放回真实流量），只重置那一家，成功后立刻重取状态而不是等 10 秒轮询。
- **「导入已有」只能再上传一次，覆盖不到「包已经在服务器上」**（需求 10）：
  该条把五项能力并列，而「从ZIP安装」与「导入已有」此前都是浏览器上传，
  机制上是同一件事、只有审计口不同。真正缺的那半边是：运维用 scp 把一批包放进了
  服务器，手里没有可上传的文件；或者包有几十 MB，走浏览器既慢又容易断。
  现新增 `GET /resources/imports`（只读列举 `resources/imports` 这一层）与
  `POST /resources/imports/install`（按**文件名**安装，不接受路径——允许路径
  就等于把一个只读列举接口变成任意文件安装接口）。已装过的包标出「已安装」
  或「可更新（已装 x.y.z）」而不是从列表里消失（消失会让人以为文件没放对，
  于是反复重传同一个包）；坏包单独标错，不让整份列表打不开。
- **Agent 运行时的四个参数只能改 `config.yaml`**（需求 21.3 点名「集中配置并
  校验边界」，含**请求总截止时间**与取消传播）：`turn_deadline_seconds` 早就真的
  把 deadline 与取消信号下传给模型调用，`reply_stream_mode`、
  `channel_reply_stream_modes`、`tool_search_threshold` 也都有真实消费点，
  但四项都没有任何 HTTP 写入路径。一个只能靠登服务器改 YAML 的「集中配置」
  不满足该条——它恰恰是需求要消除的状态。现新增
  `POST /system/config/agent-runtime` 与系统设置页的「Agent 运行时」卡片。
  三条边界：没提交的键保留原值（与 `PUT /llm/backends/<name>` 的 `exclude_unset`
  同一语义，「改一个字段把其余重置回出厂值」是这批字段真实发生过的缺陷）；
  边界校验在路由层给出 400 而不是让 pydantic 在落盘阶段抛 500（用户需要知道
  是哪一项越界）；`0` 是值而不是缺省（`turn_deadline_seconds=0` 表示不设总预算，
  `tool_search_threshold=0` 表示关闭渐进披露）。`inherit` 不被接受为进程默认——
  它只在 Agent 层有意义，接受它会让整条解析链没有终点。

- **资源正文在界面上看不到，「提示词管理」回答不了它唯一要回答的问题**
  （需求 10）：prompt / skill / hook 此前只能走通用的安装 / 启用 / 停用 / 版本 /
  备份生命周期，而 prompt 这个类型的**全部内容就是正文**——一个看不到正文的
  「提示词管理」回答不了「现在生效的提示词到底写了什么」。
  `ResourceLifecycleService.read_entry_metadata()` 早就存在且返回的正是这些
  （entry 路径、正文、已校验摘要、来源、权限），但**零调用点**，
  与 `UsageSource.ESTIMATED` 当初完全同一形态：有定义、有测试、主链路上没人用。
  现新增 `GET /resources/<id>/content`（`resources.read` 即可——它不写盘、
  不执行任何东西，要求创建者身份会让「看一眼提示词写了什么」变成需要提权的动作）
  与资源详情弹窗里的正文区块。**只读且刻意没有写入路由**：`content_sha256`
  把清单与文件绑在一起，运行时每次载入都重新校验摘要，就地编辑的后果不是
  「改了没生效」而是那个资源彻底不可用；改正文的受支持路径是装一个新版本。
  摘要与版本记录比对后一起显示（「你看到的」与「运行时载入的」是同一份必须
  可自证，而不是靠信任），多版本时可切换查看旧版正文——回退前想先看看旧版
  写了什么，是这个入口最实际的用途之一。`version` 必须已注册，
  否则一个拼错的版本号会变成任意路径读取。

- **发布计划把「本地已打但没推」与「远端已发布」显示成同一个词**（需求 23.2
  「不得把离线候选当作正式发布版本」）：`occupied_git_versions()` 把本地与远端
  Tag 合成一个无区分的集合。碰撞判定本身没错——两种情况下那个号都不能重用——
  但输出里丢掉了「它为什么被占用」，而那恰恰是这句要求的对象。远端已有意味着
  已经发布过，接着往下找号；仅本地有则是一次没推成功的打标（网络断了、
  门禁没过、或者被人手工 `git tag` 试了一下），它占住了一个号而**没有任何发布
  产物与它对应**，删掉那个本地 Tag 再重试往往才是对的。实际后果是版本号被
  无谓地跳过：一次失败的打标之后每次重跑计划都会跳过那个号，几次之后版本号
  里出现空洞，而没有任何地方记录那些号去哪了。现新增 `OccupancyReport` 与
  `occupied_release_versions()`，`plan` 的文本与 JSON 输出都多出 `released` 与
  `reserved_locally` 两项。**碰撞判定逐字节不变**（两类都算占用），
  `occupied` 仍是全集，既有调用方与 JSON 消费者不受影响。

- **移除源码、文档与测试里对某个外部参考产品的具名描述**（需求 11）：
  该需求明确要求项目中不得以文字说明或其他方式暗示与那个产品的关系。此前有 40 个
  被跟踪文件命中它的名字，其中 13 个在 `kirara_ai/`（随 wheel 与 Docker 镜像分发）、
  2 个在 `webui/src/`（随 JS bundle 分发），最直接的三处是**公开模块 docstring**
  （`mcp_module/compat.py`、`plugin_manager/resource_catalog.py`、
  `plugin_manager/resource_sources.py`）——`help()` 就能打出来。
  现全部改写为「参考实现」「主流 Agent 客户端」等机制性表述：**只改叙述，
  不动任何函数名、变量名、字段名与行为**，因此对既有部署零影响。
  另有五份纯调研素材（该产品的界面逐条记录、历史交接原文）停止跟踪并加入
  `.gitignore`：它们整篇在描述另一个产品，改称呼不改变性质；
  历史交接文档还含操作者原话与本机绝对路径，改写即失真，因此不改写而是不再分发。
  文件保留在本地。

- **流式响应的用量被后一个分片整体覆盖，输入 Token 凭空消失**（需求 9、22.1）：
  `agent_runtime/executor.py` 聚合流式响应时写的是 `usage = chunk.usage`——
  整体替换。而 OpenAI 兼容端点常把用量拆在多片里回报：`prompt_tokens` 只在第一个
  带 usage 的分片出现（提示词在请求时就已确定），`completion_tokens` /
  `total_tokens` 要等生成结束。于是最后那片抹掉先前那份，**账单里的输入 Token
  变成「未上报」**，而这条请求明明报过。更麻烦的是 `usage_source` 仍是 `provider`，
  界面上没有任何「数据不完整」的迹象——它看起来是一条正常的、便宜的请求。
  新增 `merge_stream_usage()` 做字段级合并：后到的非 `None` 值优先，后到的 `None`
  不覆盖已有值（`None` 是「这片没提」，`0` 是「报了，确实没有」），
  `source` 只升不降（不让一个没写 source 的收尾分片把请求打进「不明」）。

- **手动重置熔断器当场失效，不用等到重启**（需求 8、21.3）：
  `reset_provider_circuit` 先 `pop` 内存里的熔断器，再调
  `_initialize_resilience_state()` 重建。而重建把「字典里没有这个名字」当成**新建**，
  紧接着从 `data/llm/circuit-state.json` 把停机前的 open / half-open **原地读回来**。
  调用返回成功、界面显示已重置，下一个请求仍然跳过这个 Provider，
  日志里既没有错误也没有重置痕迹。现新增 `CircuitBreakerStore.forget()`
  只删指定条目（顺手清空整个文件等于把「重置一家」变成「取消所有隔离」，
  而其余上游可能正因真实故障被隔离着），并保留原 `saved_at`
  （重写时间戳会把其余 Provider 的「已经开了多久」清零，让本该很快进半开的熔断器
  重新等满整个恢复窗口）；重置改为**先删盘再重建**。

- **熔断重置此前没有任何接口，只能靠重启放回一个被误隔离的上游**（需求 8、21.3）：
  `reset_provider_circuit` 早就存在，但仓库里没有路由调用它——它是一个只能从
  Python 交互式会话里碰到的方法。一次上游抖动打开的熔断只能等满配置里的恢复窗口，
  或者重启整个进程，而重启会一并中断所有正在进行的对话。
  新增 `POST /backend-api/api/llm/backends/<name>/circuit/reset`：
  创建者身份（它把一个刚被判定不健康的上游放回真实流量）、需显式确认、
  拒绝夹带其他字段（顺带改配置会变成一次没有审计记录的写入）、
  未知后端返回 404 而不是 200（`reset_provider_circuit` 对未知名字是静默通过的，
  否则一个拼错的名字看起来和成功一模一样），响应直接带回刷新后的健康快照。

- **WebUI 对话页没有代码框，也没有任何复制入口**（需求 6、19.3）：
  `ChatView.vue` 把整条回复塞进一个 `<p>`：代码与正文同字体、无边框，
  `navigator.clipboard` 零调用。这是四个渠道里唯一**没有平台限制可讲**的一个。
  现按 CommonMark 围栏切分（口径与 `im/text_render.py` 一致，否则同一段回复会在
  QQ 上被认成代码、在 WebUI 上不是），代码单独成框、带语言标识与**真的复制按钮**，
  复制的是**代码原文**而不是渲染结果。剪贴板不可用（非 HTTPS、权限被关、旧浏览器）
  时给一句「请手动选中代码复制」——代码仍在框里可选中，而不是一个用户无法处置的错误。

- **Telegram 有原生复制按钮能力却零调用**（需求 6、19.3）：
  Bot API 的 `InlineKeyboardButton` 有 `copy_text` 字段，点一下即把文本放进用户剪贴板，
  不走回调、机器人也不必再发消息。适配器此前从不使用它，用户只能长按选中——
  而 Telegram 客户端里选中一段带缩进的代码最容易连着前后正文一起选上。
  现代码块单元携带原生复制按钮，三条边界：载荷取代码原文（MarkdownV2 转义会把
  `_` 变成 `\_`，复制走那份粘进编辑器就是坏代码）；超过平台 256 字符上限时
  **退回没有按钮**（挂上去整条 sendMessage 会被拒，等于「加个按钮」把一条本来能发出去的
  回复变成发不出去）；一个代码块被拆成多片时按钮只挂第一片（每片都挂等于给出几个
  内容不同却看不出区别的「复制」）。

- **资源装上之后永远删不掉**（需求 10、22.3）：
  `ResourceLifecycleService.remove` 实现完整（备份当前版本、写注册表、留审计、
  要求确认），但没有任何路由调用它。一个装错的 Skill、不再用的 MCP 条目、
  写坏的 Prompt 只能被「停用」而永久留在列表里：停用不释放磁盘、不清注册表，
  也不让那个 ID 重新可用——重装同名资源会撞「重复 ID」。运维唯一的办法是登服务器
  手改 `registry.json`。现新增 `DELETE /backend-api/api/resources/<resource_id>`：
  创建者身份 + 显式确认（卸载不可逆，没有确认就执行等于把一次误点变成一次删除），
  删前自动备份，删 mcp 资源后刷新受管服务器（否则界面上资源已不存在、服务器上进程还在）。

- **版本回退对五类资源永远不可用**（需求 10「从备份中恢复」）：
  `restore_version` 有一道与恢复本身无关的前置检查：未绑定工作流就拒绝。
  而 `workflow_id` 只有绑定了工作流的资源才有——skill、prompt、hook、mcp、memory
  从设计上就没有，于是这个接口对它们**永远返回 409**：一个被升级搞坏的 Skill
  明明还留着上一版目录和备份，却没有任何办法回退，而报出的理由
  （「没绑定工作流」）与用户正在做的事毫无关系，看起来像 bug 而不是限制。
  已移除该检查。回退真正需要的三条前置条件不变且都在检查：显式确认、
  目标版本在注册表里、那个版本的目录还在磁盘上。

- **四个 IM 适配器的渠道身份靠类名推导，一次重命名就静默失效**（需求 10）：
  `ChannelContext.from_message` 在适配器没有 `channel_type` 时用类名去掉 `Adapter`
  后缀再小写。OneBot / QQ 官方 / Telegram / 企业微信四个适配器都依赖这条回落，
  今天恰好命中枚举值——那是巧合，不是契约。任何一次类名重构都会让该渠道的所有
  Agent 绑定**静默失效**（绑定表存旧值、运行时算新值，两边对不上，请求退回全局默认
  Agent），会话键也跟着漂移使历史上下文断开，两者都不报错。`http` 渠道曾经就是这样。
  四个适配器现显式声明 `channel_type`，并新增源码级契约测试：
  枚举里每个渠道都必须有且只有一个适配器声明它，且改类名不影响声明值。

- **拖入节点的落点在候选耗尽时可能压在既有节点上**（需求 2、20.1）：
  `findFreeNodePosition` 的循环先检查当前坐标、命中就返回，否则算出**下一步**坐标
  再进入下一轮。于是最后一轮算出的那对坐标从未被碰撞检查过，却在循环结束后直接返回。
  表现是画布上只看到一个节点、另一个被完全盖住，用户以为拖放没生效，再拖一次，叠三层。
  现改为循环内先检查再推进，并把总试探次数设上界（无界搜索会在极端图上把一次拖放
  变成长时间无响应）；连硬上界都找不到空位时把落点推到所有障碍的右下角之外——
  那里必然是空的，节点仍然可见，用户可以自己拖回来。

- **拖放坐标换算丢掉画布元素在视口里的偏移**（需求 20.1）：
  `onDrop` 把 `event.clientX/clientY`（视口坐标）直接喂给 `project()`，
  而 `project` 假设画布原点就是视口的 `(0, 0)`。今天画布恰好是
  `position: fixed; top: 0; left: 0`，偏移为 0，缺陷被掩盖着；一旦画布被放进带侧栏
  或顶栏的容器，拖入的节点就整体偏移，偏移量正好等于画布左上角的位置。而它不报错——
  节点确实生成了，只是位置不对，用户会以为自己没拖准。改用 `screenToFlowCoordinate`
  （它先减掉 `getBoundingClientRect()` 偏移），并加源码级断言防止下一个人顺手用回
  `project`（两个函数的名字看不出这个差别）。

- **空工作流是一张只有点阵的白板**（需求 14「首次上手」、20）：
  零节点时画布不给任何提示。左侧节点面板可能是收起的，拖放这个交互本身也没有视觉暗示，
  而新建工作流是每个用户的第一屏。现零节点时渲染空状态，给出拖放、连线、自动排布与
  撤销粒度四条可执行动作；提示用 `pointer-events: none`，不拦拖放——
  一个自己变成障碍的提示比没有提示更糟（用户照着它去拖，却拖不进去）。

本轮此前修正的八处缺陷（同一形态）继续保留在下方。

- **WebUI 版本比较在序号进入两位数后完全反向**（需求 16、23.2）：
  `webui/src/utils/version.ts` 把 PEP 440 的预发布序号规范化成 semver 的
  `-b<N>`，而 semver 只在标识符「全是数字」时按数值比较；`b<两位数>` 是字母数字
  标识符，按字典序排在 `b<一位数>` 之前。后果是装着新版本的用户被提示「升级」到
  一个更旧的版本，而真正的新版本反而不提示。后端 `packaging.Version` 的排序一直
  是对的，两侧因此对同一对版本给出相反结论。
  修法是只在比较时把序号变成独立数字标识符（`-b.<N>`），**不改变**
  `normalizeAppVersion` 的输出——那个值要显示在版本卡片上、也要和 npm 包版本对得上。
  展示形态与排序形态混在一起，才会出现「为了排序而改标签」。
  覆盖：`webui/tests/version.test.ts`（新增 9 项，含 stage 顺序与等值用例）。

- **成本对除 Claude 之外的每个供应商永远为空**（需求 9、22.1、22.2）：
  `calculate_cost_snapshot` 要求四个成本维度**全部**非 `None` 才给 `total_cost`，
  而 `cache_write_tokens` 只有 Claude 适配器会填。OpenAI、Gemini、Ollama、
  Volcengine 等形态的 usage 因此永远缺一维，`total_cost` 恒为 `None`，
  `apply_cost_projection` 随之把 `total_cost` / `cost_currency` 两列留空——
  统计页的成本汇总对这些供应商恒为空白，而 `input_cost`、`output_cost` 明明算出来了。
  判据改成「至少一维已知就汇总已知项」：供应商没上报某一维不是「花了钱但不知道多少」，
  而是「这一维不产生费用」。四维全未知仍然是 `None`，因为那才是真的没有定价证据。
  覆盖：`tests/llm/test_cost_dimension_coverage.py`（7 项，含全未知仍为 `None`
  与「上报 0 要按 0 计价、不能当缺失」）。

- **复合画布编辑毁掉上一次编辑且无法重做**（需求 20.3）：
  `runCanvasBatch` 里 `setNodes()` 只改 Vue Flow 自己的 store，而 `updateBlocks()`
  是 500ms 防抖的，于是批次关闭那一刻工作流 store 里什么都还没变，
  `performBatchAction` 比对结果是「无变化」、检查点不入栈；批次期间逐次记录那条
  路又被抑制。两条路都不写历史，改动却在防抖到期后落进 store——撤销栈栈顶仍是
  **上一次**编辑的快照，一次 Ctrl+Z 直接回退掉那次编辑，且重做栈里没有这次批量
  改动可以恢复。一键整理、批量复制、粘贴三条路径都受影响。
  新增 `webui/src/components/workflow/workflow-canvas-batch.ts`：批次关闭前同步
  写回一次，消掉这个相位错配。覆盖：`webui/tests/workflow-canvas-batch.test.ts`
  （8 项，含一条变异守卫，证明去掉 flush 后历史确实丢失）。

- **一次连续拖拽产生六七个撤销步骤**（需求 20.1、20.3）：
  历史合并此前依赖一个布尔值加 500ms 防抖窗口——窗口一过就重新开始记录，
  而拖一个节点走两秒会跨过好几个窗口。用户要连按七八次 Ctrl+Z 才能退回拖动前。
  新增 `webui/src/components/workflow/workflow-canvas-history-gesture.ts`：
  改为按**手势**边界合并，拖拽起止、配置面板编辑各自成为一个可撤销单元。
  覆盖：`webui/tests/workflow-canvas-history-gesture.test.ts`（8 项）。

- **HTTP 入口无法绑定 Agent**（需求 10）：`SUPPORTED_CHANNEL_TYPES` 漏了 HTTP，
  而 `HttpLegacyAdapter` 也没声明 `channel_type`，推导出的类型是 `httplegacy`。
  两处不一致的结果是：`bind_channel("http", ...)` 直接被拒，
  而运行期上下文写的是 `httplegacy`——即使绕过校验绑上也永远匹配不到。
  需求要求「WeCom、QQ、Telegram 等入口统一映射到渠道身份 → Agent」，HTTP 入口
  被排除在这个模型之外。适配器改为显式声明 `channel_type = "http"`，
  并把 `http` 加入支持集合。覆盖：`tests/agent_runtime/test_http_channel_identity.py`
  （11 项，含每个受支持渠道都能绑定的枚举用例）。

- **四种围栏写法逃过全部结构保护**（需求 19.1、19.3、19.4）：
  共享渲染层用 `startswith("```")` 判围栏，于是 CommonMark 的波浪号围栏
  （`~~~`）完全不被识别，四反引号围栏里的三反引号又被当成闭合。后果分四类：
  波浪号代码块里的 LaTeX 被当正文降级、Markdown 表格被当表格转换、代码不再单独
  成条（复制路径失效）、分页在代码块内部切断且不补围栏。四反引号块则被内层围栏
  切成三段。改为按 CommonMark 识别围栏（字符 + 长度，闭合必须同字符且不更短），
  三处硬编码判断收敛到一个识别器。

- **同一段回复在企业微信与 QQ 上得到不同结构**（需求 19.1）：
  WeCom 侧维持着一条独立的正则替换链，与共享结构化渲染并存。同一段模型回复因此
  在两个平台上结构不同——需求明确要求平台差异只放在渲染层。改为走共享的结构化
  块渲染，`［代码］` 围栏这一个真正的平台差异保留。
  上两条覆盖：`tests/test_fence_and_platform_parity.py`（28 项）。

- **纯符号公式的定界符留在正文里**（需求 19.2）：判据是「内容带 LaTeX 特征才当公式」，
  于是 `$x = 5$`、`$a + b = c$` 这类不含反斜杠命令的公式两个 `$` 原样送到 QQ。
  改成按「货币写法」排除而非按「LaTeX 特征」收纳：`$5`、`$1,200` 继续原样保留，
  其余配对定界符按公式处理。

- **远程安装接口把请求体直接展开成关键字参数**（需求 22.3）：
  `install_skill(**payload)` 让客户端能覆盖服务器自己生成的参数；
  同时 `_validate_directory(".")` 在所有形态检查**之前**返回，`"."` 因此绕过
  `_DIRECTORY_PART`、`..`、`//`、`\\` 全部判据——直接请求它会把整个仓库当成一个
  Skill 装进来。改为白名单字段校验，并把「整仓即 Skill」的内部结果值与用户可请求
  的输入分成两条路径。覆盖：`tests/plugin_manager/test_remote_install_validation.py`（21 项）。

- **同名工具被判为就绪**（需求 12）：`rtk-cli` 的说明写着「以 `rtk gain` 是否可用
  为准」，探针却只跑 `rtk --version`——另一个同名工具（Rust Type Kit）同样能答，
  于是被标成就绪，直到实际调用才失败。补上说明里那条判别命令。
  覆盖：`tests/plugin_manager/test_dependency_probe_discriminator.py`（4 项）。

- **供应商凭据只有一半被脱敏**（需求 21.1）：`volcengine_adapter` 声明的
  `access_key_secret` 命中 `_secret` 后缀，成对的 `access_key_id` 两处判据都不命中。
  后果是 `GET /llm/backends` 明文返回它、导出文件把它写进那份「已脱敏、可转发」的
  内容里、编辑时不享受「留空即不修改」语义、追踪落库同样漏掉。这类缺陷的形态是
  「一半正确」——看到另一半被打了码，会让人相信整条路径是安全的。
  新增 `kirara_ai/credential_keys.py` 作为唯一的凭据识别词表，API 响应/导出与追踪
  落库两处共用，避免两份表各自漂移；`max_tokens` 这类带 token 的非凭据字段继续原样返回。
  覆盖：`tests/llm/test_credential_redaction_coverage.py`（32 项）。

### Added

- **发送节流：避免触发 QQ 风控**（需求 11）：被融入的 OneBot 适配器项目在每次
  flush 之前主动等待（`max(text_length * 0.1, 1) + random.uniform(0.5, 1.5)`），
  融入时漏掉了这一条。它**不是**重试退避，两者方向相反：
  - 失败重试退避（本项目已有）：这一页发失败了，等一会儿再试；
  - 发送节流（此前缺失）：这一页发成功了，下一页也要等，否则被判定为刷屏。

  漏掉它的后果是一类**表现与「发送失败」完全不同**的故障：QQ 对短时间内连发
  多条消息有风控，命中之后账号被限制发言——所有接口都返回成功、日志里一切正常，
  消息却到不了对方，且要等很久才恢复。排查时最容易走的弯路是去查投递队列，
  因为队列显示「全部已投递」。一个从旧项目迁过来的用户会直接撞上旧项目专门
  规避掉的这个风控。

  新增 `kirara_ai/plugins/im_onebot_adapter/pacing.py` 与五个配置项
  （`send_pacing_enabled` 等，默认开启）。三条边界：按文本长度算（短文本连发
  才是风控最敏感的形态）、带随机抖动（固定间隔本身就是可识别的机器特征）、
  有上界（风控看频率而非「等得够久」，超过某点再等只是惩罚用户）。
  第一页不等——首字延迟是用户唯一能直接感知的耗时，而第一条不构成连发。

  **直发与 outbox 两条路径都接上了**：走哪条取决于部署有没有配数据库，
  而风控与这个无关；只修一条等于同一个账号换个部署形态又会被限制发言。
  覆盖：`tests/plugins/im_onebot_adapter/test_send_pacing.py`（16 项，
  含变异验证两条路径的调用点都是必需的）。

- **上游限额余量：把「离上限还有多远」变成看得见的**（需求 9）：
  桌面端参考实现的额度面板回答「这个上游还剩多少可用」，读的是各家订阅计划的专有
  接口。本项目不照搬那些接口，而是取同一用户意图在本项目里的落点——**上游在每个
  响应里就带着限额余量**（`x-ratelimit-*` / `anthropic-ratelimit-*` / `retry-after`）。
  此前这些响应头被完整丢弃：适配器里 `response.headers` 零次读取。

  丢掉它们的后果不是少一个图表，而是**限流只能事后发现**：请求开始报 429 才知道
  撞了上限，而那时排队与重试已经在发生。余量是唯一能在撞上之前给出信号的东西。

  新增 `kirara_ai/llm/rate_limit.py`。采集点放在 `CancellableRequestMixin.
  _track_response`——四家适配器唯一都会经过的收口，一处覆盖全部；且采集发生在
  `yield` **之前**，因为 429 那次的响应头恰恰是最有价值的一次（它带 `retry-after`）。
  余量随 `GET /llm/resilience/status` 返回，与熔断状态同行：两者回答同一问题的
  两面——熔断说「它已经坏了」，余量说「它还剩多少」。

  三条边界：缺头是 `null` 不是 0（0 表示余量用尽，是最该报警的状态，把「没上报」
  显示成 0 会造出一个不存在的紧急情况）；只有 remaining 没有 limit 时不反推百分比
  （编一个分母会得到看起来精确的错数字）；采集失败绝不影响请求（限额头是上游给的，
  一个解析异常会让整条本已成功的请求失败）。
  覆盖：`tests/llm/test_rate_limit.py`（12 项）、`test_rate_limit_integration.py`
  （适配器真的采集 + status 真的暴露）、`webui/tests/llm-rate-limit-headroom.test.ts`。

- **每模型 / 每供应商的单次请求成本**（需求 9）：分组聚合一直返回 `count` 与
  `cost`，界面也显示了合计成本，缺的是两者之商。单次成本是回答「该不该换模型」
  的那个数，而合计成本回答不了：请求量最大的模型往往不是最贵的，合计高也可能
  只是调用多。两个模型合计相同、单次差十倍时，只看合计完全看不出差别。
  分母是**已定价**请求数——把未定价的算进分母会得到一个偏低且看起来正常的数字。

- **请求整流器：上游因参数约束拒绝时，改一处再重试一次**（需求 8 末句）：
  需求 8 点名「如果整流器能够一起融入进来到本项目最优」。参考实现里它是两个
  专用模块（thinking 整流与 thinking 预算整流）加一组四个
  开关，修的是**同一个 API 在不同模型上约束不同**这一类必然失败：
  - 思考预算有下限且必须小于最大输出长度，关系不对时整个请求被拒，正文一个字
    都出不来；
  - 多轮对话回传上一轮思考块时带签名，换模型或换供应商后该签名失效；
  - 不支持图片的模型收到图片块会拒绝整个请求。

  三者的共同点是改一处就能成功、不改就必然失败，而原因既不在错误里说清，也不是
  用户能自己改的。此前的表现是一次硬失败：用户看到「请求失败」，无从判断是模型
  不行、网络不行，还是一个参数关系不对。

  新增 `kirara_ai/llm/rectifier.py`，三条边界写进实现而不是留给调用方自觉：
  **事实驱动**（只有上游真的返回拒绝才动，不做发送前预判）、**白名单匹配**
  （要求多个错误特征同时出现——只看 `signature` 会把鉴权签名错误也当成思考签名
  问题，去删一堆与失败无关的字段，而真正的原因反而被掩盖）、**每类只改一次**
  （改完仍失败就抛原始错误；反复整流会把「参数错」变成「一直在转」，后者更难查）。

  开关是每供应商的，随 `LLMChatRequest.rectifier` 下发，理由与 `reasoning_effort`
  相同：队列里 P1 是自建 Anthropic 网关、P2 是不支持思考的兼容接口时，两者必须
  各按自己的配置走。`图片降级`是唯一会改变模型看到的内容的一项，可单独关闭；
  图片换成可见占位文本而不是静默删除——否则模型会对着空内容编一个答案，
  而用户以为它真的看过那张图。

  非流式与流式两条路径都接上了。只修一条是半个修复：参数约束错误在两条路径上
  完全一样，`reply_stream_mode` 换个取值就又会硬失败。流式只在建连阶段整流——
  流已经开始产出内容之后再重试会让用户看到两段回复拼在一起，那比一次失败更难
  解释。

  覆盖：`tests/llm/test_rectifier.py`（28 项判定与改写）、
  `test_rectifier_config.py`（配置到运行时映射、per-provider 下发）、
  `test_rectifier_integration.py`（适配器层真的重试，两条路径）。
  另修 `tests/llm_adapters/test_provider_streaming.py` 的响应替身缺 `close()`：
  建连失败后不释放连接，每次整流重试就漏一条，而漏连接没有任何症状。

### Security

- **升级包安装前校验 registry 声明的哈希**（需求 16）：
  `download_file` 一直在算下载内容的 SHA-256 并把它返回，却**没有任何调用点比对
  它**。算了不比对是最坏的一种形态——代码看起来做了校验（有 `hashlib`、有摘要
  返回值），审阅时容易一眼扫过去认为已经校验过了。

  实际后果是**镜像源成了任意代码执行的入口**：镜像地址是用户可配的
  （`config.update.pypi_registry` / `npm_registry`），一个被投毒或被中间人替换的
  镜像可以返回任意 wheel，而升级流程会直接 `pip install` 它。此前唯一的保护是
  TLS——它只能证明「确实来自这个镜像」，证明不了「这个镜像给的东西没被换过」。

  两侧的期望值其实一直拿得到，只是没被带出来：PyPI 的 PEP 691 在每个文件条目里
  给 `hashes.sha256`，npm 在 `dist.shasum` / `dist.integrity` 里给。新增
  `ArtifactDigest` 与 `verify_artifact_digest`，并把解析层拆成
  `resolve_pypi_release` / `resolve_npm_release`（带摘要，供安装路径）与原有的
  `get_latest_pypi_version` / `get_latest_npm_version`（只读检查，不下载东西）——
  拆函数而不是改 arity，避免把一次安全加固变成一次连带破坏。

  **摘要缺失时拒绝安装**：「没人告诉我该是什么」不等于「它是对的」，把缺失当通过
  等于留一个「只要别声明哈希」的绕过口，而投毒者正好可以自己决定不声明。
  校验发生在 `pip install` 与解包**之前**（装完再校验没有意义），不匹配时删掉
  下载文件（留着它，下一次「重试升级」可能直接拿它）。
  覆盖：`tests/web/api/system/test_update_integrity.py`（19 项，含一条不 mock
  校验、端到端断言被篡改的包不会被安装的用例）。

- **发布镜像记录源提交**（需求 16）：`verify-tag` 只检查**当下**的自洽（本地 Tag、
  远端 Tag 与 HEAD 指向同一提交），没有任何历史记录。于是这条时间线全程通过校验：
  打某个版本 Tag 发布镜像 → 事后把同一个 Tag 移到另一个提交 → 手动重跑
  `docker-tag.yml` 指定同一个 `image_tag`。结果是 Docker Hub 上同名标签的内容被
  换成了另一份，而**没有任何地方记录这件事发生过**——拉到旧镜像和新镜像的人都
  认为自己跑的是同一个版本。
  修法不是禁止重建（重建有正当理由，例如基础镜像补安全更新），而是让镜像自己带上
  `org.opencontainers.image.revision` / `.version` / `.source`：两份镜像从此可区分，
  「这个版本标签是哪个提交构建的」从无从查证变成一条 `docker inspect`。
  `revision` 取已与 preflight 提交比对过的那个值，不用 `github.sha`——后者没经过
  那次比对，记下一个未校验的值会让这条标签失去意义。

- **「禁用自动升级」从只能改 YAML 变成界面可配**（需求 8）：
  `update.disable_auto_check` 一直有真实消费点，但通往它的路只有手改
  `config.yaml`——`GET /system/config` 不返回它，`POST /system/config/update`
  收到也丢掉，前端 `UpdateForm` 没有这个键。离线与内网部署恰恰是最需要它、
  也最不方便登服务器改文件的场景。
  该路由另有一处：镜像源用 `data["..."]` 直接下标，只提交开关的请求会
  KeyError → 500；同时若把缺失的键补成默认值，老前端改镜像源时会静默把开关
  关掉。改为只写请求里真的出现过的键，与 `PUT /llm/backends/{name}` 的
  `exclude_unset` 语义一致；镜像源留空则 400 拒绝——空 URL 存下去，
  错误会出现在几天后的启动日志里，和这次保存对不上。

- **Skill 变成可被模型调用的能力，而不只是塞进上下文的一段文字**（需求 10）：
  主流 Agent 客户端的 Skill 机制是**渐进披露**——前置元数据（name +
  description）常驻上下文作为一句廉价广告，正文只在模型决定用它的那一轮才载入。
  需求 10 要求采用同一原理，而本项目此前只有一半：
  `_build_messages` 把每个已绑定 Skill 的**全文**拼进每一次请求的 system 消息。
  - 成本随「技能数 × 请求数」线性增长，其中绝大部分与当轮问题无关：
    十个 Skill 各 2000 token，就是每轮 20000 token 的固定开销。
  - 更要紧的是**模型无法「选用」一个技能**，因为没有可调用的东西。
    「装了技能之后 AI 会话真的因此改变行为」只能靠把全文硬塞进上下文来实现，
    那不是同一个机制，也拿不到同一种效果——模型面对一堵墙时反而更容易
    忽略其中的具体指令。
  - 新增 `skills.py`：可广告的 Skill 在系统提示词里只留一行目录，正文由
    `skill_<resource_id>` 工具按需取回。工具与 MCP 工具、队友委派同一形态，
    模型侧不需要区分。
  - **判据是「能不能广告」，不是一个新开关**：没有前置元数据的 Skill
    （纯文本、旧资源）无法广告，仍然整篇注入——行为与此前逐字节一致，
    不会因为升级让既有部署的技能突然「消失」。`allow_tools` 关闭时同理：
    没有工具可调时，一行目录就是一句模型无法兑现的空头承诺。
  - 正文版本取自**本轮快照里的绑定**而不是「当前版本」：一次对话中途被更新的
    技能不该让前后两轮遵循不同的说明，那种不一致无法从对话记录里看出来。
  - 恢复路径（人工确认之后那一轮）会重建技能与委派工具。不重建的话，
    模型会看到确认前构建的那份广告、却调不到被广告的工具，
    拿到一句我们自己制造的 "permission denied"。
- **技能依赖没装时，模型会被告知，而不是自信地假装执行过**（需求 10）：
  需求 10 要求「下载安装在 VPS 里边的各种插件**起作用**」并点名 agent-browser。
  它的 SKILL.md 通篇是 `agent-browser ...` 命令，而这个 CLI 装没装记录在
  `SystemDependencyService` 里——此前那份状态**只投影给安装界面**，
  Agent 运行时完全不读。于是没装 CLI 时会发生这样一轮：模型读到技能、
  照着写出命令、命令在服务器上不存在、模型无从得知，只能把「我已经打开了浏览器」
  当成事实继续往下答。**这是最坏的一类失败：没有报错，只有一个自信的假答案**，
  而用户看不出与真的执行成功有什么区别。
  - 技能广告与工具描述里都会带一句就绪状态，并明确要求模型不要假装执行过。
    警告必须进**工具描述**——那是模型决定是否调用时唯一会读的地方。
  - **三态严格区分**：就绪 → 一个字都不加（每句多余的话都是每轮都要付费的噪音）；
    确认缺失（`ready is False`）→ 点名那个组件；未知（服务未接线、探测抛错、
    依赖表里没登记、`ready` 为 `None`）→ 同样什么都不说。把「不知道」说成
    「缺失」会劝退一个本来能用的技能，那是凭猜测造成的损失。
  - 依赖 id 的映射提到 `system_dependencies.dependency_ids_for_resource`，
    安装界面与运行时**共用同一条规则**。各写一份的后果不是重复代码，
    而是两份会各自漂移的判断，且不一致的那一刻没有任何症状。
  - `entry.py` 把容器里的 `SystemDependencyService` 传给运行时。
    容器里没有它时（嵌入式用法）行为退回「不提示」，而不是装配失败。
- **`docs/PRACTICAL_PLAN_AND_TUTORIAL.md`：一份从零到可用的操作教程**（需求 14）：
  此前九份文档各自都对，但都是**专题文档**——没有一份回答「我该按什么顺序做」。
  新部署的人面对九个入口，最常见的失败不是某一步做错，而是不知道先做哪一步。
  - 六个阶段按依赖顺序排列，每个阶段给出「做什么 → 怎么验证 → 失败时看哪里」。
  - 逐条写明**哪些能力在本机验证不了**（真实 Docker 重启、真实 QQ 扫码、
    真实多 Provider 故障转移、真实客户端渲染），以及为什么不能把
    「本机跑过测试」当成「线上可用」。
  - 命令全部与实际脚本核对过：Windows 后端用 `.venv-win/Scripts/python.exe`，
    WebUI 用实际存在的 `test:unit`（需求 23.4）。
- **使用统计有了时间范围预设，且日界按所选时区算**（需求 9）：本页此前只有一个
  `datetimerange`——想看「最近 7 天」得自己算两个时刻再点两次日历，
  而按天回看是这个页面最常做的第一步动作。
  - 新增今天 / 近 24 小时 / 近 7 / 14 / 30 天 / 自定义。「今天」与「近 24 小时」
    都保留：上午九点时前者只覆盖 9 小时、后者跨到昨天下午，它们回答的不是
    同一个问题，合并成一个就得替用户决定他问的是哪一个。
  - **日界按用户选的时区算，不是浏览器时区。** 本页时区可选（跨时区对账要看到
    对方眼里的「今天」），若预设按本地时间切日界，一个 UTC+8 的查看者选了 `UTC`
    之后「今天」会横跨上游眼里的两天——这类错位不报错，只让两边数字差一截，
    且差多少取决于当前几点。改时区会按新时区重算。
  - 多天预设从**当天零点**起算（`7d` = 含今天的 7 个日历日），不是「now 减
    7×24 小时」：后者首尾各半天，日趋势图第一根柱子永远偏低，会被读成
    「那天用量下降」。逐日回退而非减固定 24 小时，跨夏令时那天只有 23 或 25 小时。
  - 日历选择器保留（预设是快捷方式不是替代品）；用户直接改日历时预设自动切回
    「自定义」——留着「近 7 天」的标签而区间已经不是近 7 天，那个标签就是错的。
- **成本有了趋势曲线**（需求 9、22.2）：22.2 要求「统计页面要支持趋势」，
  而此前只有请求数与 Token 有日 / 时分桶，成本只在 `overview` 里给一个 30 天合计。
  于是**「这个月贵了三倍，是哪天开始的」这个问题没有出口**——只能手工二分时间
  范围反复改筛选条件重查，而账单异常恰恰最需要快速定位到某一天（换了模型、
  上了新流量、缓存失效）。
  - 日 / 时分桶各自新增 `cost`、`cost_currency`、`cost_by_currency` 与
    `unpriced_requests`；取的是写入时冻结的快照投影列，历史账单不受后来改价影响。
  - **不同货币不画在同一条线上**：界面按币种各画一条，币种集合从数据里推导
    （写死 USD 会让人民币结算的部署看到一张空图）。把两种货币加进同一条曲线
    得到的是一串没有单位的数字，而那不会报错。
  - **未定价请求单列并在 tooltip 里标出**：按 0 元并入当天合计会把「有请求没
    匹配到价格版本」显示成「这天便宜」。没有任何定价证据的那天 `cost_currency`
    是 `null`，不编一个币种出来。
  - 成本单独一张图而不是并进 Token 趋势：金额与 Token 数差好几个数量级，
    同框时其中一条必然被压成一条平线。
- **统计给出输入 / 输出 / 缓存四类 Token 与缓存命中率**（需求 9、22.1）：
  22.1 逐项点名了「输入/输出/缓存 Token」，而这四个数字**每一行本来就记着**
  （`prompt_tokens`、`completion_tokens`、`cached_tokens`、`cache_write_tokens`），
  请求详情页也四个都显示。但 `get_statistics` 的 `overview` 只 `SUM(total_tokens)`，
  分组统计只给一个 `tokens`，日 / 时分桶同样只有 `tokens`——**聚合这一跳**
  把四类合成了一个数。
  - 后果不是「少一个装饰性数字」：缓存命中率算不出来。输入 Token 单价通常是
    缓存读取的 5~10 倍，一份「总 Token 完全没变」的账单在命中率从 80% 掉到 0%
    时成本会翻几倍，而只显示总量的页面在这两种情况下给出的数字**一模一样**。
  - 另一半是处置方向：「输出涨了」查 prompt 与 `max_tokens`，「输入涨了」
    查上下文与历史长度，合成一个总数就把该查什么留给读者猜。
  - `overview` 新增四项合计与 `cache_hit_rate`（口径 = 缓存读取 /
    （输入 + 缓存写入 + 缓存读取），与上游计价一致）；分组统计与日 / 时分桶
    各自带四项。`total_tokens` 含义不变，历史看板上的数字不会前后不一致。
  - **`null` 与 `0` 一路保留到界面**：`total_cached_tokens: null` 是「没有上游
    报过缓存」，`0` 是「报了、确实没命中」，两者的排查方向完全不同（查上游是否
    返回 usage / 查提示词前缀是否稳定）。界面在未知时显示「未上报」并附一句说明；
    显示 0% 会让人去排查一个并不存在的缓存失效问题。
  - 趋势图从一条总量线改为四类堆叠 + 总量虚线；模型与 Provider 的 tooltip
    补上输入 / 输出 / 缓存读取。趋势分桶里缓存两项按 0 累加而非 `null`——
    折线中间出现 `null` 会断开，而在趋势图上「这小时没上游报缓存」与「报了 0」
    没有不同处置。
- **分组统计给出成功率：能看到「谁在失败」而不只是「在失败什么」**（需求 9、22.1）：
  `error_categories` 按错误类型分组，一个 `timeout` 分组里可能混着三家供应商，
  因此回答不了「该把哪家在故障转移队列里排后面」——而那正是调队列的依据，
  此前只能翻请求日志人工计数。
  - `providers` / `models` / `backends` 等分组各自新增 `success_requests`、
    `failed_requests`、`pending_requests` 与 `success_rate`。
  - 它同时区分「一家慢」与「一家坏」：`avg_duration` 偏低也可能是大量快速失败
    把均值拉下来，而慢请求超时被计入了失败。
  - **`pending` 不进分母**：还在跑的请求既不是成功也不是失败，算作失败会让
    正在进行的长请求把成功率压下去。一条都还没有结论时 `success_rate` 是
    `null`，界面显示「未知」——报 0% 会让一家刚配好、只有一条在途请求的供应商
    看起来是最差的那一个。
- **「禁用自动升级」从只能改 YAML 变成界面可配，且真的不再自动外呼**（需求 8）：
  `update.disable_auto_check` 有真实消费点（`entry.py::check_update` 打开时
  完全不发请求），但通往它的路只有手改 `config.yaml`：`GET /system/config` 的
  `update` 段不返回它，`POST /system/config/update` 收到也丢掉，前端
  `UpdateForm` 更没有这个键。最需要它的恰恰是离线 / 内网部署，
  而那种环境最不方便登服务器改文件。
  - 补齐读写两端与「系统设置 → 下载源」的开关，默认关闭——静默停掉版本检查
    会让部署长期停在旧版本而无人知道。
  - **更关键的一处：只挡启动那一次是不够的。** `StatusBar.vue` 在 `onMounted`
    里无条件调 `GET /system/check-update`，于是离线部署每打开一次页面仍然要等
    PyPI 与 npm 两次超时。现在自动检查在开关打开时直接返回、不外呼；
    `?manual=1`（用户点的）照常外呼——若手动也一起挡掉，「禁用自动检查」
    就悄悄变成了「禁用检查」，而说明里并没有这么写。
  - 响应体新增 `checked`，区分**没查**与**查了没更新**：两者都让
    `backend_update_available` 为 `false`，但只有后者可以对用户说「已是最新版本」。
  - 「立即检查更新」按钮此前只存在于**零挂载点**的 `VersionCard.vue`
    （`AboutView.vue` 还是「施工中」占位页），也就是说文档里承诺的那颗按钮
    在界面上并不存在。现在它挂在开关旁边，复用 `UpdateChecker` 的
    「跳过 / 稍后 / 立即更新」流程，不另写一套。
- **「隐藏 AI 署名」补上界面入口**（需求 8）：后端一直有 `hide_ai_attribution`
  且有真实消费点（`llm_manager` 在非流式与流式聚合后各清理一次），但
  `webui/src` 下 grep 这个字段零命中——只能改 `config.yaml`。现补类型、
  供应商面板开关与说明文案（讲清「只删署名句、不动答案本身」，
  否则没人敢开一个会改写模型输出的开关）。它**不进** `resilienceDefaults()`：
  进表意味着每次 payload 都补一个值，会覆盖用户在 YAML 里开的那次。

- **逐次尝试明细上界面：能看到「哪一家在失败」而不只是失败了几次**（需求 22.1）：
  `attempts` 一直在 `to_dict()` 里返回，每条都带 provider、retry_index、
  success、error_category、时间戳与 partial_output。但全仓库没有任何界面消费它
  （`webui/` 里 grep `attempts` 零命中），于是那份证据只存在于数据库列里。
  只给「重试 2 次、转移 1 次」两个数字回答不了运维真正要问的问题：
  **哪一家在失败、失败类型是什么、换到哪一家之后成功了**——而这三件事对应完全
  不同的动作（调超时 / 查那家的配额 / 把它从故障转移池里摘掉）。
  - 新增 `trace-attempts.ts` 与详情页「逐次尝试」表：一行一次尝试，按发生顺序
    排列（乱序会让「换到哪一家之后成功了」不可回答）。
  - **同一家再试与换一家分成两种 `kind`**：这是这张表存在的理由，两者的处置相反。
    provider 缺失时标 `unknown` 而不是猜一个——「不知道是否换了家」与
    「确定换了家」是两件事。
  - **首字节缺失时是 `null` 而不是 0**：非流式请求本来就没有首字节时刻，
    0 会被读成「零延迟」，那是一个没有依据的论断。
  - 产出过部分内容的失败单独标注：用户可能已经看到半句话，重发会造成重复。
  - 没有 `attempts` 的记录（旧行、第三方调用方、从未走故障转移的请求）
    显示为空表而不是「尝试过 0 次」。少字段的 attempt 也不让整块面板打不开。
- **首次上手：新部署第一次打开就能看到「还缺什么、下一步做什么」**（需求 14）：
  后端 `GET /system/readiness` 一直提供 7 项检查，每项都带 `summary` 与
  **可执行的 `remediation`**。但 `webui/src/api/system.ts` 是个 0 字节空文件，
  全仓库 `grep readiness` 在 `webui/src/` 零命中——那份诊断只能靠 `curl` 看到，
  文档里也确实是教用户手敲 curl。最需要它的恰恰是刚部署完、还没配好任何东西的人，
  而那种人不会去读 curl 示例。
  - 快速开始页新增「还需要处理」面板，逐条列出 `fail` / `warn` 的检查，
    并**原样呈现后端的 remediation**，不另写一套说法——两处说法一旦不一致，
    用户就得先判断该信哪个。`fail` 排在 `warn` 前面：前者阻塞可用性。
  - 全绿时不逐项列「无需处理」：那是噪声，不是信息，只说一句「都通过了」。
  - **读不到就绪状态不等于「没就绪」**。接口未响应时单独说明，并且**不断言任何
    一步已完成**——把「不知道」显示成「未就绪」会让人去修一个不存在的问题，
    而真正的问题只是这一个诊断接口没响应。
- **引导步骤的完成状态改由真实就绪状态推导**（需求 14）：此前完全是**前端点击
  痕迹**——点一下就写 `localStorage`，不校验是否真配了 IM/LLM。于是换个浏览器
  全部归零、配好了也不打勾。一个勾选状态与事实无关的清单比没有清单更糟：
  它会让人以为自己配完了。
  - `im` / `llm` / `workflow` / `dispatch` 四步改看对应的就绪检查，配好了就打勾
    （哪怕从没点过那一步），没配好就不打勾（哪怕点过十次），并额外标「已核实」
    以区分「点过」与「真的配好了」。
  - 「浏览插件市场」没有对应的服务端事实，仍用点击痕迹——这是它唯一可得的依据。
- **「已启用」不再等于「生效」：资源列表标出未被任何 Agent 绑定的资源**（需求 22.3）：
  装好并启用一个 Skill 后界面显示「已启用」，但它只有在被**绑定到某个 Agent**
  之后才会进入 LLM 请求（`_build_messages` 遍历的是 `agent.*_bindings`，
  不是「所有已启用的资源」）。没有绑定时状态是「已启用」而实际效果是零。
  这不是功能缺失，是**状态显示与实际效果不一致**——最难自查的一类，因为界面上
  没有任何地方在说「它还差一步」，于是用户去怀疑模型或提示词。
  - 资源响应新增 `bound_agent_ids`（即使绑定被停用也列出——它解释了「为什么改
    这个 Agent 会影响这个资源」）与 `in_effect`（资源已启用**且**有启用的 Agent
    用启用的绑定引用它）。
  - 界面在「已启用但未生效」时额外标一个**未生效**标签并给出下一步。
  - **字段缺失表示「不知道」**，与 `false`（确定未生效）严格区分：读不到 Agent
    注册表的部署里两个字段都不出现。把「不知道」显示成「未生效」等于给出一个
    没有依据的论断，会让人去解决一个不存在的问题。
- **合并转发可以展开成真实内容**（需求 11）：`forward` 段此前只产出
  `[合并转发：<id>]`。这在「不静默丢消息」这一层是对的，但它把内容也一起丢了——
  用户转发一段对话过来问「这里说的对吗」，模型收到的只有一个 ID。
  参考实现同样没有调用过 `get_forward_msg`，这是共同空白。
  - 新增 `expand_forward_messages`（**默认关闭**，行为与升级前逐字节一致），
    打开后渲染成带发言人的缩进文本。
  - 三道边界各有测试：`forward_max_depth`（嵌套转发可以再包转发，无界递归会把
    一次消息转换变成一串上游调用）、`forward_max_nodes`（一段可能几百条，
    全部展开会让提示词爆掉且随后被切成几十页）、**自引用只请求一次**。
  - **失败退回占位**：展开是增强而不是前提，`get_forward_msg` 权限不足、
    ID 过期或上游未实现时退回占位并记一条日志，绝不让整条消息失败。
  - 超出条数明确标注「已省略 N 条」——静默截断会让人以为只有那么几条。
  - 转发里的媒体**不下载**，只给可读标记：下载会把一次消息转换变成一串下载。
- **好友申请与入群邀请有了处置出口**（需求 11）：`_handle_request` 已经在记录
  这两类事件，日志写着「请在 QQ 客户端或上游 WebUI 处理」。可 OneBot 协议本来
  就有 `set_friend_add_request` / `set_group_add_request`——本项目此前没有任何
  调用点，于是一个部署好的机器人只能干看着申请堆积，处置得回到手机上做。
  - **框架依然不自动同意**：自动接受入群邀请是一个安全决定，不该由框架代替
    部署者做。补的是「部署者可以决定」的能力，不是「框架替你决定」。
  - 日志现在带上 `flag`：处置动作用它标识具体哪一条申请，而运维唯一能看到
    事件的地方就是日志。只记「有一条好友申请」等于把处置能力锁死在日志里。
  - 两处**在发出前**就拒绝，因为它们的失败形态是「返回成功但什么都没做」：
    群申请必须指明 `sub_type`（`add` 与 `invite` 是两件不同的事，刻意不设默认值）；
    多账号部署必须指明 `self_id`（用错账号同意等于让另一个机器人进了群）。
- **`send_message` 返回上游 `message_id`，发出去的消息可以撤回**（需求 11）：
  `recall_message` → `delete_msg` 一直可用，但 `send_message` 返回 `None`，
  调用方拿不到刚发出那条消息的 ID。于是「发一条提示、30 秒后撤回」这种再普通
  不过的用法做不到——**能撤，但不知道撤谁**。上游在响应里明明给了 `message_id`，
  投递队列也一直把它落进 `response_json`，只是没有出口。
  - 新增 `MessageSendResult`：`message_ids` 是**每一页**的 ID
    （只回第一页等于「后面几页撤不掉」，用户看到撤回一半的回复，比不撤更糟），
    `message_id` 取第一页作为「这条回复」的代表，另带 `delivery_id` 与 `page_count`。
  - **上游没回 ID 时给空元组与 `None`，绝不编一个**：0 或空串会让调用方拿它去
    撤回，然后撤到别人的消息上，或者静默失败。
  - 顶层与 `data` 两处都读 `message_id`——OneBot 各实现放的位置不一致。
  - `IMAdapter.send_message` 的返回值**仍然允许是 `None`**：既有适配器与第三方
    实现无需改动，调用方按「拿不到 ID」处理。
- **OneBot V11 标准消息段补齐入站覆盖**（需求 11）：`poke`、`location`、
  `contact`、`share`、`music`、`xml`、`anonymous`、`markdown` 此前都没有分支，
  到达时被静默丢弃。丢一段是安全的，**丢整条不是**——一条只含 `poke` 的消息
  所有段都被丢掉后元素列表为空，用户看到的是「机器人毫无反应」，
  而那和「机器人挂了」在观感上一模一样。参考实现同样缺这些段，
  所以这不是照抄漏了，是两边共同的空白。
  - 每段给出可读的纯文本占位；`location` 有标题优先用标题，
    没有标题时给出经纬度（那时它是唯一的信息）；`contact` 区分群与好友。
  - **`markdown` 例外**：它的 `content` 就是正文，必须原样保留。
    换成 `[Markdown]` 是丢内容，比丢一个交互动作严重得多。
  - **占位不伪装成富媒体**：`location` 不是图片、`contact` 不是文件，
    硬映射成那些类型会让下游按错误的方式处理它们。
  - 未知段（含上游私有扩展）保持忽略：给每个未知类型造占位会让任何私有扩展
    都在回复里留下噪声。`docs/QQ_ONEBOT_OPERATIONS.md` 新增第九节逐段对照表。
- **创建者可以从 IM 渠道使用受保护的插件能力**（需求 10「只有创建者能通过插件
  修改 VPS 内容」）：`principal_can_control_agent` 是唯一门禁，而身份此前只由
  HTTP Bearer 中间件注入——OneBot / QQ / Telegram / WeCom 的入站链路全程没有它。
  结果**不是**「非创建者不行」，而是「所有人都不行」，包括创建者本人：
  这些渠道上 MCP 工具列表恒为空、command 型 Hook 恒被拒（含内置 `hook:ai-debug`
  的八个事件）、需确认的宿主操作走不到确认那一步。设计是前者，实现成了后者。
  - 新增 `agent_runtime.creator_channel_identities`：显式声明哪些渠道身份属于
    创建者。**默认空表**，不声明时行为与升级前逐字节一致。
  - **渠道与发送者一起比**：QQ 号和 Telegram 用户 ID 可能撞号，只比一个等于把
    另一个渠道的同号用户也放进来。`*` 不是通配，只是一个匹配不到人的字符串。
  - **群聊默认不生效**：群里所有人都看得到创建者发的指令并照抄。照抄的人
    `sender_scope` 不同因而拿不到身份，但把宿主操作暴露在多人可见的会话里是
    另一回事，要开必须显式写 `allow_group_chat`。
  - **已有身份绝不被替换**：无条件套一层 `runtime_principal_context` 会在未声明
    任何渠道身份时用 `None` 把 HTTP 中间件设好的身份清掉——那不是「IM 侧多一条
    路」，而是「WebUI 侧少一条路」，且症状与本改动毫无表面关联。提权路径只有一条。
  - 主体取自 `AuthService.creator_subject` 本身，与 Agent 的 `owner_subject`
    是同一个值；取不到时返回「无身份」而不是编一个——一个匹配不上任何 owner 的
    subject 只会让门禁静默失败。
  - 未声明的发送者仍然得到**正常的 AI 回复**（工具列表清空而非请求被拒），
    与需求「其他使用者收到修改 VPS 的命令一律忽视但仍正常回复」一致。
- **熔断的触发与恢复证据可回溯**（需求 21.3「记录触发与恢复证据」）：三态与
  当前快照一直都有，缺的是「**什么时候**、**因为什么**变成这个状态」——
  `resilience.py` 与 `circuit_store.py` 内零 logger、不写审计，
  `resilience/status` 只给当前值，于是轮询间隔内发生的
  open → half-open → closed 全部不可见，「昨天下午 P1 被隔离过吗、隔了多久」
  只能靠恰好抓到那一次轮询。
  - `CircuitBreaker.transitions()` 记录每次迁移，五种原因彼此可分：
    `failure_threshold`（刚开始出问题）与 `error_rate`（持续不稳定）
    的处置完全不同，混成一句「已熔断」等于没说；`recovery_timeout` /
    `recovery_success` / `half_open_probe_failed` 覆盖整段恢复过程。
  - **open → half-open 由时间驱动**，没有任何调用方会「知道」那一刻发生了迁移，
    因此记录点在状态刷新里而不是在某个 `record_*` 上。
  - 六个固定字段、全为数字或枚举串（面板要读，不能带上游报文与凭据）；
    按 Provider 保留最近 64 条覆盖写——历史活在内存里，
    一个持续抖动的上游不该把内存吃掉。
  - `resilience/status` 增加 `recent_transitions`（最近 10 条），
    容错面板新增「状态变化」折叠区。`at` 是单调时钟，只算「多久以前」，
    不当墙上时间格式化（那会显示 1970 年）。
- **`storage_unavailable`：持久化目录在运行期不可写是一个独立状态**（需求 18.1
  「数据目录挂载错误」）：这一类此前只存在于两个够不到的位置——启动期检查
  （只读挂载时进程直接退出，那时 HTTP 接口读不到）与 readiness 的
  `data_directories_writable`（只探测 `DATA_PATH` 本身）。真正会漏掉的是
  **运行期**：卷在启动之后被重新挂成只读或写满时，WebSocket 还连着，
  适配器照旧报 `connected`，而每一条要落库的投递都在失败——面板上一切正常，
  消息在丢。
  - 投递队列每次被读取都当作一次写入探针（OneBot 与 QQBot 同一口径），
    读不出来即报 `storage_unavailable` + 原因码 `data_directory_unwritable`。
  - **不盖住可修的原因**：凭据被拒、握手被拒、心跳超时都是用户能直接动手改的，
    被存储故障盖掉会让人去查磁盘而真正要改的是 Token。只覆盖
    `connected` / `waiting` 这两个「看起来没问题」的状态。
  - 存储恢复后自动回到真实链路状态，不需要重启适配器；readiness 的
    `im_available` 增加 `storage_unavailable_count` 并把处置指向数据卷与磁盘。
- **供应商配置的写操作全部留痕，并可从备份恢复**（需求 21.1「备份、恢复、审计
  记录」）：审计此前只覆盖 `POST /backends/import`，而单条后端的
  创建 / 编辑 / 删除是更敏感的动作——改错一个后端会让整个渠道停摆，
  删掉一个会让所有指向它的 Agent 立刻失去上游。
  - 三条写路径都写入统一审计流，记录操作、后端名与主体摘要；
    **凭据与主体明文永不入库**。后端名是审计对象，缺了它一条
    `llm_backend / update` 只能证明「有人改过某个上游」，答不出改的是哪一个。
  - 新增 `POST /backends/restore`：把供应商清单回滚到上一次写入前的
    `config.yaml.bak`。**只回滚 `llms.api_backends` 一段**——整份写回会把用户
    同一时间改过的 Web 端口、IM 适配器、工作流一起退回，那是「回滚全部设置」
    而不是「恢复供应商配置」。要求 `confirmed: true`；没有备份时返回 404
    （「没有可恢复的东西」是正常状态，不是服务器故障）。
- **`hide_ai_attribution`：移除回复里的 AI 自我署名**（需求 8「隐藏 AI 署名」）：
  参考实现改的是编码客户端写 git commit 时的 `Co-Authored-By` 尾注
  （`attribution.commit` / `attribution.pr`）。本项目不代写 commit，但同一个用户
  意图在聊天场景
  里有确切落点：模型经常在回复里自报身份（「作为一个 AI 助手，我……」
  「本回复由 AI 生成」），QQ / 企业微信这类面向真人的渠道里这类句子既占篇幅
  又暴露实现细节。现为供应商级开关，默认关闭。
  - **只删署名不删答案**：「作为一个 AI 助手，我建议你先备份」里
    「我建议你先备份」是答案，分界在那个逗号上；整句删掉就是丢答案。
  - **不进围栏代码块**：代码里的 `AI` 字样是内容，改它等于改坏用户要执行的东西。
  - **不动工具参数与用量**：工具参数是给程序读的；署名是上游已经生成并计费的
    token，把它从展示里去掉不等于没花那笔钱，改写 usage 会让账单对不上。
  - **流式在聚合之后清理，不逐分片**：一句署名很可能被切成 `本回复由 ` /
    `AI 生成。` 两片，逐片判断两片都不像署名，整句就原样漏出去——结果取决于上游
    怎么切分片，这是最难复现的一类缺陷。按真正成交的那家供应商的配置执行一次。
- **Teammates 模式：把其他 Agent 作为工具委派**（需求 8「Teammates 模式启用」）：
  参考实现打开的是编码 CLI 的多 agent 协作。本项目的等价物在
  **Agent 层**而不是供应商层——供应商是「上游模型」，不是「协作单元」。
  `AgentDefinition.teammate_agent_ids` 非空时模型获得 `delegate_to_<id>` 工具，
  队友用自己的模型链、提示词、技能与工具白名单执行子任务。
  - **防无限递归**是这条特性的核心风险：A 委派 B、B 委派 A，每层都是一次真实的
    模型调用。深度上限 2 且每次委派递减；耗尽时**不再暴露**委派工具——
    不是暴露了再拒绝，那会让模型反复撞墙白花 token。自委派在定义期就被拒。
  - **只为存在且启用的队友生成工具**：放一个必定失败的工具等于让模型撞一次墙。
  - **队友看不到主对话历史**，因此工具描述要求 `task` 自带完整背景；
    空任务作为工具错误返回，而不是发起一次注定无效的委派。
  - **不是绕过授权的旁路**：委派本身不动服务器所以无需确认，但队友自身的高危
    工具仍走原有 `PermissionRequest` 与创建者校验；队友集合也进入
    `_agent_policy_signature`，集合变化会让待确认操作失效。
  - 配置在 WebUI「模型与 Agent → Agent」的「队友」区块；持久化到
    `data/agents/registry.json`，旧注册表缺该键时缺省为「不启用」，升级不宕机。
- **供应商级推理强度 `reasoning_effort`**（需求 8「Tool Search 最大强度思考」）：
  各家 API 都已提供推理强度控制，而 `LLMChatRequest` 此前**一个都没有**——
  配了推理模型也只能跑默认强度。现新增四个与厂商无关的档位，由各适配器翻译成
  自家字段：OpenAI 系 `reasoning_effort`、Claude `thinking.budget_tokens`、
  Gemini `generationConfig.thinkingConfig.thinkingBudget`（`max` 映射到 `-1`
  即「动态思考」，把预算交给模型而不是我们猜一个上限）。
  - **留空是一个必须可表达的状态**：不支持扩展思考的模型收到
    `thinking` / `thinkingConfig` 会直接报错，因此该字段不进
    `resilienceDefaults()`，前端下拉可清空，「恢复默认」也会把它清掉。
  - **Claude 的预算被夹在 `[1024, max_tokens - 1024]`**：它要具体 token 数而非
    档位名，预算吃满 `max_tokens` 会让整个请求被拒、正文一个字都出不来；
    区间为空时干脆不开启，而不是硬塞一个会被拒的值。
  - **逐供应商生效且不改写调用方的请求对象**：队列里 P1 是高强度推理网关、
    P2 是不支持思考的兼容接口时，就地改写会让 P1 的设置泄漏到 P2，
    而 P2 收到未知字段可能 400——一次本可成功的故障转移变成两连败。
    流式与非流式两条路径同一处翻译，口径一致。
- **`update.disable_auto_check`：禁用启动时的自动版本检查**（需求 8
  「禁用自动升级」）：此前启动无条件去 PyPI 查版本，离线或内网部署既查不到又要
  等超时，且没有任何开关。关闭后**完全不发起请求**——「禁用」如果只是不打印
  结果，那条超时等待依然存在。WebUI 的「检查更新」按钮不受影响，那是用户主动
  发起的。
  - 另外两个参考实现的开关（隐藏 AI 署名、Teammates 模式）见上方两条：
    它们**按语义落地**在对应的层上（回复文本层与 Agent 层），
    而不是把编码 CLI 的字段名照抄成供应商配置。理由与翻译表在
    `docs/EXTENDING.md` 第九节。
- **供应商配置的导入 / 导出在前端没有入口**（需求 8「编辑导入供应商」）：
  `GET /llm/backends/export` 与 `POST /llm/backends/import` 早已实现并鉴权
  （整份校验、空凭据保留现有值、同名冲突 409），但 `webui/src/api/llm.ts` 里
  既无封装也无调用方——从产品角度看这条能力只能 curl，而同类的定价目录
  一直有按钮。现补上客户端封装与「模型与 Agent」页的工具条：
  - 导出走 `http.fetch` 而非 `http.get`，否则拿不到 `Content-Disposition`
    也就无法按后端给的文件名下载；文案明确写出「不含凭据」，
    避免被当成完整备份。
  - **同名冲突由用户拍板**：409 时列出冲突名单并要求确认后才带
    `overwrite: true` 重发。静默覆盖会把目标机器上已填好的 Key
    与容错参数冲掉。
- **统一的「使用统计」页面**（需求 9）：此前趋势 / Provider 统计 / 模型统计的
  图表只挂在**引导页**上，请求日志在 `/tracing/llm`，成本定价在 `/llm/pricing`，
  而 `/tracing` 侧栏里没有统计入口——能力都在，但对一次账单要在三个页面之间
  来回跳。现新增 `/tracing/statistics` 作为 `/tracing` 的默认落地页：
  提供 Provider / 模型 / 时间范围筛选，把筛选下发给既有的图表组件，
  并用链接通往请求日志与成本定价。
  - **复用而不重做**：图表仍由 `LLMStatistics.vue` 渲染，日志与定价不在本页
    重新实现——重做会立刻产生两套口径，而口径不一致比少一个入口糟得多。
  - 筛选项来自统计接口自身的分组结果；`null` 的 Provider 显示为「未标注」
    而不是被丢弃，否则各分组之和会小于总请求数，读起来像数据缺失。
- **引导页图表此前发的是裸请求**：同一个统计接口在 `/tracing/llm` 上带筛选与
  浏览器时区调用，在引导页却两者都不带。跨时区用户在两处看到的「今天」不一致，
  而两处显示的是同一批数据。现在时区由图表组件**无条件**附加，筛选由宿主页面
  下发，两条都不依赖调用方记得。
- **扫码登录生命周期快照**（需求 18.4）：二维码由 LLOneBot / PMHQ 在自己的容器里
  生成，Kirara 不参与生成也不代理，但只要那份日志挂到本容器可读的位置，就能把
  「这张码还能扫吗」变成一个可回答的问题。新增 `kirara_ai/im/qr_login.py`：
  9 种状态、4 个稳定失败原因码，并给出需求要求的六项——有效期、生成时间、
  当前状态、刷新次数、失败原因与最新二维码路径。
  - **过期由时钟判定，不等上游日志**。等上游打出「已过期」才改状态，就会在这段
    等待里一直把死码显示成有效——这正是「二维码总是过期」这个报障的根因：
    操作者扫的是终端缓冲区里往上翻出来的旧图。
  - **`unavailable` 与 `failed` 严格分开**。`QR code unavailable` 在启动期是正常
    噪声，混成一个「出错」会让人在正常启动过程里白等或白重启。
  - **绝不携带账号标识**。日志里有 uin、uid、昵称与头像 URL，快照里一个都没有；
    有专门的测试断言这一点，且夹具本身用合成标识而非真实值。
  - 通过 `qr_login_log_path` 显式开启（默认不读任何文件），只读日志尾部
    （默认 256 KB），读取失败只丢这一项、不影响健康快照——观测不能成为新的失败点。
  - readiness 的 `im_available` 增加 `qr_*` 证据字段与「去扫码」这一条独立处置：
    「上游没接进来」要查地址与 Token，「上游接进来了但 QQ 没登录」要去扫码，
    这是「重启后显示未连接」这类报障里最常见的误诊方向。
  - WebUI 在适配器卡片上把扫码状态显示为**独立**标签，并直接给出下一步动作，
    不与连接状态合并。
- **OneBot 通知事件不再静默丢弃**：`_handle_notice` 此前是 `return None`。
  被踢出群、被禁言这类会直接导致「机器人不回话」的事件完全无声，排查时只能看到
  发送失败、看不到原因。现按类型记录可读说明；影响本账号可用性的
  `group_decrease` / `group_ban` 升为 warning。仍然不派发进工作流——
  它们不是消息，没有回复语义，硬塞进去会让每次群成员变动都跑一遍模型。
- **OneBot 请求事件（好友申请、入群邀请）此前根本没有订阅**：管理员在 QQ 里看到
  一个悬而未决的申请，服务端日志里一个字都没有。现补 `on_request` 并记录，
  但**不自动同意**——自动接受入群邀请是安全决定，不该由框架代替部署者做。
- **Claude、Gemini、Ollama 三个适配器补齐流式实现**（需求 4「必须要实现流式」）：
  此前只有 OpenAI 兼容适配器实现了 `LLMChatStreamProtocol`，配置这三家的部署即使
  把 `reply_stream_mode` 打开也会**静默**走非流式——流式首字节超时、静默超时与
  「首字节之前的故障转移」这三条容错路径对它们全部无效，而用户看不到任何提示。
  三家帧格式互不相同，照搬 OpenAI 的解析会一个分片都读不到，因此各自实现：
  - Claude：SSE 按 `event` 分类型，文本在 `content_block_delta.delta.text`；
    用量分散在 `message_start`（input）与 `message_delta`（output），
    只在拿到 output 时产出一次带用量的分片，避免把中途累计值当成最终值。
  - Gemini：`:streamGenerateContent?alt=sse`，文本在
    `candidates[0].content.parts[*].text`；每帧都可能带累计 `usageMetadata`，
    因此只在带 `finishReason` 的末帧产出用量。
  - Ollama：**不是 SSE**，是按行 JSON（NDJSON），末行 `done=true` 带统计。
  三者一致遵守：上游没给用量就保持 `None`，交由上层估算器标记为 `estimated`，
  绝不在适配器里补 0；单个坏帧只跳过不终止整条流；HTTP 层错误照常抛出，
  让上层故障转移看得到。新增 16 项测试逐家覆盖帧解析、用量归集、坏帧容错与错误传播。

### Changed

- **三处前端判断从 `.vue` 里抽成可调用的纯函数**（需求 14）：
  `autoDetectSchedule.ts`（下一轮时刻、上次时刻、本轮结果标签、脏值判断、
  间隔校验与提示语）、`pricingForm.ts`（提交前把空白显示名转成 `null`、
  表格标签回落）、以及既有的 `stagedArchives.ts`（此前是纯函数但**从未被调用过**）。
  抽出的动机不是「代码更整洁」，而是这些判断此前只能靠 grep 源码「验证」：
  自动检测那一页的 40 条断言里有 `toContain("if (!row.last_run) return '—'")`
  这种把整行代码当字符串钉住的写法。改一个比较运算符、把 `86_400_000` 写成
  `86_400`、把 `<= 0` 写成 `< 0`——字符串全都还在，测试全绿，
  而用户会按一个错的时刻去等。
  三份新的行为测试共 54 条，都调用函数：其中
  `resource-staged-decisions.test.ts` 遍历八种 `(installed, is_upgrade, error)`
  组合锁住一条不变式（状态为 installed 或 error 时按钮必须禁用，其余必须可点），
  这能区分 `&&` 与 `||`——而被替换掉的那条 grep 断言不能。
  相应地删掉了钉写法的断言，换成「确认组件真的接到了那个函数上」：
  原来 `toMatch(/display_name\s*=\s*label\s*\?\s*label\s*:\s*null/)`
  无法区分「改好了」与「改坏了」——重构成 `label || null` 它红，
  把条件写反成 `label ? null : label` 它也红，两种情况同一个信号。

- **重试次数与故障转移次数拆成两项**（需求 22.1 把它们并列列出）：此前只有一个
  聚合的 `attempt_count`，于是「同一家重试 3 次」与「切换 3 家各试 1 次」
  在统计上完全一样——都是 3。可这两件事的处置**完全相反**：前者是这家上游慢或抖，
  该调超时与退避；后者是这家上游不可用，该查供应商健康与熔断。给出一个分不开的
  数字，等于把「该查什么」留给读者猜，而他手上没有能猜对的信息。
  - 新增 `retry_count` / `failover_count` 两列（迁移 `d8b4e6f2c917`，
    用 `attempts_json` 里已有的 provider 序列回填历史行）。
  - 按**相邻**两次尝试的 provider 是否相同来分，而不是去重计数：
    `A → B → A` 是两次转移，去重后（2 家 − 1）只会算成 1 次，
    但实际发生了两次切换，每次都付了一遍连接与首字节成本。
  - `null` 表示「没有 attempt 数据」（旧记录、第三方调用方、未走故障转移路径），
    与 `0`（确实一次成功）严格区分。
  - 统计新增 `latency.avg_retry_count` / `avg_failover_count`；
    CSV 导出与请求详情页也分列显示，`null` 显示 `---` 而不是 0。
- **投递汇总补上分段数量与重试次数**（需求 19.5 九项里的后两项）：前七项是时间戳，
  折算成阶段耗时后落库并在 `/tracing/delivery/summary` 按阶段给出平均值与样本数。
  后两项被四个适配器写进 timeline details、被 dispatcher 取出来存进两个列——
  **但汇总接口不聚合它们**。于是「上周二那批慢投递是不是因为分了很多页」回答不了：
  单条记录里有 `segment_count`，汇总里只有阶段耗时。落库了却不出现在汇总里的字段，
  实际等于只能逐条翻。
  - 汇总新增 `counts.segment_count` / `counts.retry_count`，口径与阶段耗时一致：
    只对**测到该值**的行求平均并给出样本数。
  - 一个都没测到时 `avg` / `max` 给 `null` 而不是 `0`——`retry_count: 0` 是一个
    论断（「都没重试过」），会让人以为链路一切正常，而实际只是没有数据。
  - 投递时间线页面新增「分段与重试」区块，与阶段耗时表并列；没有样本时显示
    「未测到」并说明「该范围内没有记录这项的投递」，而不是让读者把空白读成 0。
- **数据目录清单补齐三列，QQ 侧与 Kirara 侧口径分开**（需求 18.2）：清单原本缺
  「Compose 挂载」列，读者只能从正文一句话推断；QQ 侧还缺「备份方式」与
  「升级兼容策略」两列，而那一侧恰恰**不能照抄** Kirara 侧的口径——
  `./QQ` 是可直接登录该账号的凭据，备份它等于多一份可登录副本；
  `./QQ/versions/config.json` 在换镜像后可能被重写并重新打开热更新，
  升级后必须复查。两张表现在各自完整。
  - 「Compose 挂载」列统一写「随 `DATA_PATH` 一并挂载」并说明**为什么不该单独挂
    子目录**：漏掉一个就等于那部分状态只存在容器里，`down` 之后消失。
  - 消息队列单独说明它是数据库里的表、没有独立目录——去找一个「队列目录」
    会找不到，而清单不写清楚正是这种误解的来源。
  - 二维码那节补一条：`refresh_count` 是观测值而**不是可点的按钮**。
    刷新由 LLOneBot 自己做，Kirara 只读日志。给一个假的刷新入口比不给更糟，
    点了没反应会让人以为上游挂了。
- **成本汇总回到 SQL**（需求 22.2「大数据量下的分页/索引性能」）：成本存在
  `cost_snapshot_json` 这个 Text 列里（历史账单必须沿用请求当时的定价，
  拿现价重算是错的），代价是 `SUM` 无从下手——`get_statistics` 与每一个分组都要
  把**筛选后的每一行**取回 Python 逐条 `json.loads`。已有的六个复合索引在这条
  路径上完全没用，而请求日志有分页保护、统计页没有：一年几十万条追踪时，
  打开统计页就是一次全表物化。
  - 新增 `total_cost` / `cost_currency` 两列作为快照的**投影**（迁移
    `c7f1b3a9d204`，含历史行回填）。快照仍是权威来源，两列写入时算一次、
    之后不参与任何计算；投影挂在 `cost_snapshot_json` 的赋值钩子上，
    而不是「记得写完调一次」——后者漏掉的表现是一条有快照、成本列为 NULL 的
    记录，会被汇总当成「没有定价证据」，账单静默变小且没有任何报错。
  - `NULL` 与 `0` 严格区分，回填同样只填有快照的行。
  - **不同货币不再相加**：汇总按币种分组，`overview.total_cost` 只是金额最大的
    那个币种的合计，其余在新增的 `overview.cost_by_currency` 里逐一列出，
    界面在出现第二种货币时明确提示。把两种钱加进同一个数字不会报错，
    得到的却是一个没有单位的数——混币部署里那是最难发现的一类错误。

### Fixed

- **`POST /system/config/update` 的三处下标写法**（需求 8）：`data["pypi_registry"]`
  让「只想关掉自动检查」的请求 KeyError → 500；反过来把缺失的键补成默认值，
  会让老前端（只发两个镜像源）在用户改镜像源时静默把 `disable_auto_check` 关掉。
  现在只写请求里真的出现过的键，与 `PUT /llm/backends/{name}` 的
  `exclude_unset` 语义一致；镜像源留空则返回 400，因为空 URL 存下去之后
  错误会出现在几天后的启动日志里，与这次保存对不上。
- **`test_readiness_im_states.py` 在 Python 3.12+ 上九个用例集体失败**（门禁自身）：
  裸 `MagicMock()` 不再满足 `runtime_checkable` Protocol 的 `isinstance()`——
  3.12 起改用 `inspect.getattr_static`，而 MagicMock 的方法是被访问时由
  `__getattr__` 现造的，静态取不到。于是 `_im_availability` 走进「没有健康快照
  能力」的分支、把适配器算成 `connected`，**失败原因与被测行为毫无关系**。
  改用真实实现该方法的替身类。生产代码不受影响（四个适配器都在类上定义了
  该方法，已逐一验证），但 CI 矩阵含 3.13，所以这不是本机特有现象。

- **定价「生效时间」只能靠猜才填得对**（需求 22.2）：后端 `effective_from` 是
  `datetime` 且有校验器**强制要求带时区**，前端却是一个裸 `<input v-model>`——
  没有 type、没有校验、没有格式提示。用户按最自然的写法填 `2026-01-01 00:00`，
  点保存，拿到一个来自 pydantic 的英文 4xx。而定价填错的后果不是「报错了事」：
  生效时刻落错的版本会让之后所有请求按错误价格计费，**没有任何症状**，
  直到有人去核对账单。
  - 抽出 `pricing-effective-from.ts` 做本地校验并归一化到 UTC。归一化的理由是
    后端按 UTC 存储与比较——不归一化时「界面上显示的时刻」与「用于计费判定的
    时刻」是两个值。
  - **`2026-02-30` 这类不存在的日期必须拒绝**：`Date` 对它不报错，而是静默滚成
    `2026-03-02`。对一个决定「从哪一刻起按新价计费」的字段，静默改掉月份是最坏的
    失败形态——保存成功、界面显示一个用户从未填过的日期、两天按错版本计费。
    校验方式是把年月日读回来与输入比对，并按输入自身的偏移量还原到「用户写的那个
    时区里的日历日」再比（`+08:00` 的输入在 UTC 下合法地差一天，不能误判）。
  - 留空不再替用户填「现在」：那等于悄悄决定了计费起点。
  - 格式提示**常驻在字段旁**，而不是只在出错后出现——后者等于让每个人都先错一次。
- **非 GitHub 来源的 Skill 被告知「重试」一个永远不会成功的动作**（需求 22.3）：
  后端早已返回 `update_channel_supported: false` 与一句可执行说明
  （「该来源暂不支持自动检查更新；请从来源页面重新安装以获取新版本」），
  但前端类型里没有这个字段，任何带 `error` 的响应都渲染成红色的
  「检查更新失败，请重试。」——把「这条通道不存在」显示成「这次调用失败了」，
  于是用户反复点一个结果永远相同的按钮。比空白更误导：空白至少不会给出错误的下一步。
  - 现在按 `update_channel_supported` 区分：不支持的来源显示原样的后端说明并标为
    提示而非错误，「更新」按钮不出现（它本来也不可能成功）。
  - 真正的传输失败仍然显示为错误并保留「重新检查」——那一种重试是有意义的。
- **连线被拒绝时全部是静默的**（需求 20.4）：`validateWorkflowConnection` 一直会
  区分六种拒绝原因，但画布只取 `.valid`，并在 `handleConnect` 里提示一句
  「类型不兼容，无法连接」。真正的问题是那句提示**从未执行过**——两个节点组件
  把 `isValidConnection` 传给了 Handle，而 vue-flow 在 Handle 判定 invalid 时
  不会触发 `onConnect`。于是端口不存在、端点缺失、类型不兼容、输入已被占用
  四类拒绝全都只表现为「线拉过去又弹回来」，与「画布卡了」无法区分。
  - 提示改挂在 `connect-end` 上（那是唯一还知道原因的时机），每种原因一句
    独立文案并指出下一步。松手在空白处不提示：那是改主意，不是错误。
  - 「一个输入只允许一条边」从节点组件收回到画布统一判定。它原先在节点里
    `return false` 了事，因此**最容易被误读成类型问题的那一种拒绝**恰恰是
    唯一连原因都传不出来的——而这两者需要的动作完全相反（删掉已有连线 vs 换端口）。
  - 已占用的输入不进校验缓存：删掉那条已有的线之后同一端口必须重新可连，
    而缓存会让它一直显示为不可连。
  - `buildEdge` 返回 `null` 的路径此前完全静默（端点缺失或端口解析失败），
    现在归入「端口不存在」并说出来。
- **打开旧工作流会静默改坐标并置为未保存**（需求 20.2）：没有保存过布局的节点
  会在打开时被自动补位——这本身是对的，但改动没有任何说明，工作流随即变成
  「未保存」，用户什么都没动、离开时却被拦下来问是否放弃修改。那个确认框在不解释
  原因时看起来像一个 bug：改动是我们做的，不是他做的。现在明确提示补了几个节点、
  为什么补、保存后生效。
- **自动布局从未真的启用打破环**（需求 20.2 的循环图降级）：`computeWorkflowLayout`
  的注释写着「dagre 的 greedy-FAS（打破环）依赖插入顺序」，但 `setGraph` 里
  从未设过 `acyclicer`——dagre 默认不破环，`ranker` 只决定分层算法。
  环图之所以没出事，靠的是 `resolveNodeOverlaps` 在错误版式之后补救，
  而不是分层本身正确。现已显式 `acyclicer: 'greedy'`，并补 5 条环图用例
  （双节点环、自环、长环、输入顺序无关、环内节点不重叠）。
- **工具轮 Hook 返回的上下文被丢掉**（需求 10）：`_inject_hook_context` 只在
  `SessionStart` + `UserPromptSubmit` 之后调用一次，于是 `PreToolUse` /
  `PostToolUse` 返回的 `systemMessage` 与 `hookSpecificOutput.additionalContext`
  被解析、被审计记为 `status: ok`，然后丢掉。一个想告诉模型「这个结果的单位是分
  而不是元」的 Hook 写得完全正确、看起来也成功了，而模型永远看不到那句话——
  协议里有、解析通过、审计说成功，唯独不起作用，Hook 作者只会怀疑自己的业务逻辑。
  - 现在工具轮之后注入到下一次请求。**只注入一次**：内容进入消息序列后每轮都
    带着，重复注入会让同一段文本在长对话里出现十几次，白花 token 还会被模型
    当成被反复强调的重点。有测试钉住「不重复」。
  - `Stop` / `SessionEnd` 仍然不注入——之后已经没有模型调用，无处可去。
    扩展指南补了一张「哪些事件的上下文会进模型」对照表。
- **Telegram 的两个重试配置从未生效**（需求 18.3）：`TelegramConfig` 有
  `outbox_max_attempts`（默认 3，文档写「Telegram 明确拒绝请求时的最大投递尝试
  次数」）与 `outbox_retry_delay_seconds`，两者被传进 `TelegramOutboxService`、
  赋值给实例属性，**然后再没有任何地方读它们**。真实行为是 `RetryAfter`
  （Telegram 明确说「稍后再试」）直接进 dead letter，一次都不重试。
  字段在、文档在、界面能填，改它却什么都不会发生——比「没有这个配置」更糟，
  后者至少不会让人以为已经配好了。
  - 现在 `RetryAfter` 按配置次数重试，间隔走共享的 `retry_backoff_seconds`
    （指数 + 5 分钟上限 + 只提前的抖动），与 OneBot / QQBot 同一口径。
  - 三条边界分别有测试钉住：`max_attempts=1` 真的只试一次（它是「关掉重试」的
    表达方式）；普通异常不重试（重试只是把同一个错误重复犯几遍）；
    **结果未知绝不重试**——可能已经发出去了，重发就是重复消息。
    `ambiguous` 与 `dead_letter` 在「都失败了」这一层看起来像，但一个是
    「不知道有没有发」、另一个是「确定没发」，处置完全相反。
- **排版残留两类「转义残片」**（需求 19.2 明确禁止的四类中的后两类）：
  - **未收录的 LaTeX 命令带着反斜杠原样送达**：`_COMMAND_PATTERN` 是白名单，
    命中不到就保留 `match.group(0)`，于是 `\foo` 在 QQ 里显示为一个反斜杠加
    一串字母——那正是「转义残片」的定义，还会让人以为回复被截断或编码坏了。
    现在去掉反斜杠、保留命令名：不一定准确表达原意，但它是一个可读的单词。
    刻意不猜未知命令的语义。`\$`、`\_`、`\{` 这类字面量转义也一并还原。
  - **落单的行内代码反引号没有任何清理**：已有的两处防守（未闭合围栏不当代码、
    分页不劈开行内代码）解决的都是「我们不要弄坏它」，不解决「模型输出本身就
    少一个」。一个落单的反引号是可见的垃圾字符，更糟的是它会让后面一大段正文
    呈现为「行内代码待闭合」的观感。现在只删**最后一段落单的单个**反引号：
    前面配对的一个都不动（删多了是丢格式而不是清噪声）；长度 ≥2 的反引号串
    不处理（Markdown 里有合法用途，猜错就是改内容）；围栏代码里的反引号是内容，
    完全不参与。配对判断按整段而非逐片进行——一段行内代码可能跨越分段切点，
    逐片判断会把配对的两半各自当成落单的。
- **取消从未真的中止上游请求**（需求 21.3「取消传播」）：`llm_manager` 在超时、
  取消信号与 deadline 三处调用 `adapter.cancel_pending_request(request)`，
  但**没有任何适配器实现过它**——全仓库对它的引用只有 `getattr` 那一处。
  这不是「取消没做」，而是「取消看起来做了」：日志写着已取消、等待循环也确实
  松手了，可 HTTP 连接还在，上游继续生成、继续计费，承载请求的 daemon 线程
  跑到自然结束。最坏的形态——日志里写着已取消，账单上照旧扣钱。
  - 新增 `kirara_ai/llm/cancellation.py`，四家预置适配器（OpenAI 兼容、Claude、
    Gemini、Ollama）的流式与非流式路径全部登记在途响应。
    `close()` 是唯一真正断开连接的动作，只设标记等下次循环去看对上游毫无作用。
  - **非流式同样可取消**：只支持流式等于让默认配置（`reply_stream_mode: off`）
    完全没有取消能力。
  - **按 `id(request)` 登记**：`LLMChatRequest` 未声明 frozen 因而不可哈希，
    且两条内容相同的并发请求必须能分别取消——按内容做键会打到错误的那一条上。
  - **登记表自己清空**：只登记不移除等于一个按请求数增长的 map，长期运行会变成
    内存泄漏，而症状（内存缓慢上涨）与取消功能毫无表面关联。有测试专门钉住。
  - 卸载后端时中止它所有在途请求：那个后端已从模型表摘掉、结果无人接收，
    留着只会继续计费。
- **artifact index 把 IP 字面量当成发布版本号**（需求 23.1）：token 正则会把
  `127.0.0.1` 截成 `127.0.0`，于是 `docs/EXTENDING.md` 里每一处 curl 示例都往
  `.version-artifacts.json` 塞一个假 token（单文件 12 个 token 里 10 个是噪声）。
  后果不是误报——`check` 照旧通过——而是**掩盖**：真正的版本漂移混在一堆固定
  噪声里，在人眼和 diff 里都不再显眼。四段数字（IP、`1.2.3.4`）永远不是发布
  版本号，正则不再在它们中间截断，索引已重新生成。
- **QA 截图会进入 Docker 构建上下文**（需求 23.3「排除测试临时产物」）：
  根目录三张 `resource-*.png`（约 225 KB）没有任何 `.dockerignore` 规则匹配，
  `COPY . /source` 会逐张上传。已排除并加入构建上下文契约测试的必排清单。
- **发布链路没有任何门禁禁止把离线候选当正式版本**（需求 23.2）：
  `--local-only` 跳过远端 Tag 核验（断网时本地推候选用），实现与单元测试都在，
  但**没有一条契约断言禁止发布 workflow 传它**——「哪天顺手加上去省一次网络
  调用」不会被任何门禁拦住，而那正是把离线候选发成正式版本的路径。现补上。
- **WebUI 资源动作断言写死了模板形态**：`discover-repository` 标记在仓库表格的
  `h()` 渲染函数里是对象属性而不是模板属性，断言只认后者。功能一直在，
  断言现在同时接受两种写法。
- **查询串形式的 `access_token` 分类正确但连不上**（需求 11 的 C/D 互操作）：
  `_classify_access_token` 会读 `?access_token=...` 并在正确时返回「凭据没问题」，
  但被包装的 aiocqhttp **只读 `Authorization` 头**，匹配不上直接 401。于是用查询串
  认证的 LLOneBot / NapCat 会被 401 拒掉，而适配器认为一切正常、不记录任何原因码
  ——面板上既不是「已连接」也没有失败原因，比不给原因更糟。现在在令牌**已校验
  通过**且请求头**确实缺失**时，把它补成标准 `Authorization` 头再交给 aiocqhttp。
  已有头时绝不覆盖（「头里错、查询串对」是矛盾配置，以头为准而被拒绝，否则配错的
  部署会意外可用、换个客户端又突然不可用）；令牌错误时不补（补了等于把 403 变 200）。
- **MCP 的增删改与启动只查 scope，与资源侧自相矛盾**（需求 10）：启用一个 **mcp
  资源**要创建者身份，而直接 `POST /mcp/servers` + `start` 达到同样效果却只要
  `mcp.manage`；`start` 更是真的在服务器上拉起一个 stdio 子进程。默认 token 带
  `["*"]`，等于任何登录用户都能在 VPS 上起进程。现 create / update / delete /
  start 四条限创建者；只读与 `stop` 保持不变——停止只让扩展不再生效，
  与资源侧 `disable` 同一判断。
- **依赖「探测」也在服务器上执行命令，却只查 scope**：`probe` 会跑
  `agent-browser doctor`、`rtk --version` 这类登记的 argv。「不安装」不等于
  「不执行」，需求 10 管的是「在 VPS 中执行指令」。现与 install 同一边界。
- **新增 / 启停外部仓库会写 `registry.json` 却只查 scope**：它改变「哪些来源可被
  安装」，属于修改服务器内容。现限创建者。
- **日志目录不在 `DATA_PATH` 下，运维文档却承诺「只挂一个目录就不丢状态」**
  （需求 18.2）：`logger.py` 用的是裸相对路径 `logs`，跟着进程工作目录走，
  没有任何 compose 卷挂它。后果正好落在最需要日志的时刻——`docker compose down`
  之后运维按第八节验收矩阵去翻「日志证据」，而那批日志刚随容器消失。文档在这一点
  上给出与事实相反的承诺，比不写更糟。现默认 `<DATA_PATH>/logs/`，复用启动期的
  目录校验（只读挂载/磁盘满/被同名文件占用都给可执行说明），并新增
  `KIRARA_LOG_DIR` 覆盖出口；数据目录清单补上这一行，`.dockerignore` 排除
  `data/logs`，OBSERVABILITY / QUICKSTART / EXTENDING 三处路径同步订正。
- **CI 的 Python 版本契约只检查「字符串存在」**：`project_check.yml` 用
  Python 3.10 跑 `version.py check`（靠显式安装的 `tomli` 回退包才能工作），
  而旧断言只查 `uses: actions/setup-python` 这个字符串，既抓不到「版本被降到
  3.8」也抓不到「3.10 但忘了装 tomli」。现按 **job** 解析每个调用 `version.py`
  的作业：≥3.11 直接通过，≤3.10 必须在同一 job 里安装 `tomli`。
  已用「临时删掉 tomli 安装步骤」验证该断言真的会失败。
- **「未标注」维度筛选是个空操作**（需求 22.2）：统计接口按 provider / model /
  usage_source 分组，`null` 那一组在界面上显示为「未标注」，但筛选参数层把空串
  当成「没填」丢掉——用户选了「未标注」却拿到**全量数据**。这比没有这个选项更糟：
  它给出一个错误的答案而不是拒绝回答。现用一组独立的 `*_unset` 参数表达
  「该列为 NULL」，统计接口与请求日志接口共用同一语义（否则同一筛选条件在两个
  页面会得到不同的结果集）；同时要求「等于某值」和「为空」时直接 400 而不是
  静默丢掉一个。
- **统计页没有导出**（需求 22.2 把导出列在统计页能力里）：后端端点与 CSV 都在，
  唯一入口却在请求日志页。现统计页也可导出，复用同一端点与**当前页面上的
  同一份筛选条件**，保证「看到的」与「导出的」是同一批数据。
- **时区只能自动检测**：后端接受任意 IANA 名，界面却只发浏览器时区，
  跨时区对账时看不到对方眼里的「今天」。现时区可选（默认本地，可自由输入）。
- **嵌入与重排仍把「未知」写成 0**（需求 22.1 的同一缺陷类别）：聊天适配器刚
  修掉「缺失字段兜底成 0」，但 OpenAI / Ollama / Voyage 的 embedding、
  多模态 embedding 与 rerank 三条路径仍在用 `.get("...", 0)`；Voyage 的 rerank
  更是缺 `usage` 键就直接 `KeyError`。嵌入常用于记忆检索，一次调用可能处理上千条
  文本，把它记成 0 token 会让「记忆功能不花钱」这个错误结论看起来有数据支撑。
  `LLMReRankResponse.usage` 相应改为可选。
- **OneBot `retcode` 1200 被当成「稍后可以」而重试，可能重复发送**：
  证据来自 LLOneBot / LuckyLilliaBot 的 `BaseAction.websocketHandle`——
  payload 校验失败返回 **1400**（还没开始做），`_handle` 抛错返回 **1200**
  （已经在做了）。此前两者都在可重试集合里：1200 的重试等于在一条
  **可能已经发出去**的消息上再发一次，直接违反需求 19.4 的「不会重复发送」；
  1400 的重试则是拿同一份永久错误的 payload 反复打扰上游直到次数用尽。
  现在三类分开——429/503（HTTP 语义透传，一定在处理器开始前）才重试，
  1200 记为 `ambiguous`（不重发、不假装成功），1400 与其他码一次判死。
  风险不对称时选择保守：丢一页有记录可查，重复发送直接呈现给用户。
- **QQBot / Telegram / WeCom 的超长回复会整条丢失**（需求 19.4「全部发送、
  内容不得丢失」）：只有 OneBot 走 `paginate_with_truncation_notice`，其余三家
  直接调用会抛 `ValueError` 的 `split_structured_text`。超过 100 页或 1 MB 时
  异常从渲染函数一路穿出 `send_message`，用户**什么都收不到**——比截断更糟，
  因为连「还有更多」都不知道。现四家统一走截断路径：收到前 N 页 + 明确的
  「已截断」提示。QQBot 侧同时记一条 warning，便于运维发现回复过长。
- **四类 usage 里的「供应商返回」被伪造**（需求 22.1）：OpenAI / Claude 适配器
  把缺失的 usage 字段兜底成 `0` 再构造 `Usage` 对象，于是
  `mark_provider_usage` 把它标成 `provider`、`attach_estimated_usage` 因
  `usage is not None` 而跳过估算。结果是一条永久记为「0 Token、0 成本、
  供应商亲口所说」的请求——需求明令禁止的「把断言冒充观测」。现三家在上游
  未回报 usage 时一律交出 `None`，由估算器如实标记为 `estimated`。
- **Gemini 非流式的输出 Token 恒为 0**：读的是顶层 `promptTokensDetails`
  （一个并不存在的键），而同一适配器的**流式**分支读的是正确的
  `usageMetadata.candidatesTokenCount`。同一家上游两套口径，成本统计只在
  其中一条路径上正确。测试夹具此前也没有该字段，两个错误互相掩盖，
  于是「输出为 0」还被断言固化了下来；现夹具与断言一并订正。
- **`cache_write_tokens` 是有价格却无生产者的死列**：定价表按缓存写入计费
  （`cache_write_per_million`），但没有任何适配器填这个字段，缓存写入成本
  恒为 0。现 Claude 读 `cache_creation_input_tokens`（写）与
  `cache_read_input_tokens`（读）并分别落库，OpenAI 读
  `prompt_tokens_details.cached_tokens`；流式与非流式两条路径口径一致，
  否则同一模型的缓存成本会取决于用户是否开了流式。
- **Ollama 缺统计字段时整条请求以 `KeyError` 失败**：用
  `response_data['prompt_eval_count']` 直接下标。上游没给统计本该只是
  「用量未知」，不该让这次对话失败。
- **OneBot 反向 WebSocket 拒绝 `x-client-role: Universal`**：预检只
  casefold 了头**名**、没有 casefold 头**值**，而 LLOneBot / LuckyLilliaBot
  发送的正是首字母大写的 `'Universal'`，被包装的 aiocqhttp 自己则是
  `.lower()` 后再比。于是我们加的这道预检**比被包装的库更严格**，把最常见的
  OneBot 实现以 4400 拒掉；对方每 3 秒重连一次，形成死循环。现值也 casefold，
  并新增大小写参数化用例。
- **画布拖动落点与算法落点用了两套网格**（需求 20.1「拖动后位置跳变 /
  视觉错位」）：模板只写了 `:snap-to-grid="true"` 而没有传 `:snap-grid`，
  于是吃到 vue-flow 的默认 `[15, 15]`，而 `useLayout` 的落点计算、拖放新增、
  自动排布全部按 `LAYOUT_GRID_SIZE = 20` 对齐，背景点阵也是 20。手工拖过的
  节点既不落在点阵上、也不落在算法认可的格点上，下一次自动排布又把它挪走。
  现 `snap-grid` 与背景 `gap` 都由 `LAYOUT_GRID_SIZE` 驱动，单一真值。
- **`computeWorkflowLayout` 不是顺序无关的**（需求 20.2「自动布局必须是
  确定性的」）：dagre 的 greedy-FAS 与层内重心排序都依赖插入顺序，而节点和
  连线是按输入数组原样喂进去的。同一张图在「撤销后恢复」「删掉一个节点再
  排布」「导入」这些数组顺序变化的场合会排出不同版式。现按 id 排序后再插入
  （`layoutMissingNodes` 早已如此，这里补齐）。「自动排布」按钮真正调用的
  入口此前在测试里零引用，现补 7 项：块序无关、边序无关、循环图、
  混合尺寸无重叠、孤立节点无重叠，以及去重叠扫描的收敛性与顺序无关性。
- **脚本节点的零端口警告不显示在节点上**（需求 2 与 20.4）：
  `code_node_without_ports` 警告只出现在工具栏问题列表里，
  `CodeNode.vue` 从未 inject `workflowNodeIssues`，于是一个零端口的脚本节点
  在画布上只是「一个连不上线的坏框」——这正是「自定义脚本不同框是断开的吗？
  是故意这样设计的吗？」这个提问的来源。现补上与 `CustomNode` 同一套问题角标，
  并在两侧端口都为空时给出可操作的空态提示（指向配置面板），
  把「有意的动态端口边界」在节点自身说清楚。
- **写入 VPS 的资源路由只有 scope 校验**（需求 10「只有创建者才能修改服务器
  内容或执行文件操作」）：此前只有依赖安装 / 重试 / 取消三条用了
  `require_creator`，而上传 ZIP 安装、导入、目录安装、远程安装、升级版本、
  启用、回滚、从备份恢复、删除备份这一整批**都会在服务器磁盘上落文件或
  删文件**，却只检查 scope。默认 token 带 `["*"]`，等于任何登录用户都能改
  VPS 内容，与依赖安装那三条自相矛盾。现十条全部限创建者；只读端点与
  「停用」保持不变——停用只让扩展不再生效，不引入新的服务器副作用。
- **`.dockerignore` 的排除规则是根锚定的**（需求 23.3「镜像必须排除缓存、
  私有数据、测试临时产物」）：dockerignore 的 pattern 匹配**完整相对路径**，
  裸 `__pycache__` 只挡根目录那一个，挡不住 `kirara_ai/**/__pycache__/`；
  `*.log`、`*.tsbuildinfo`、`node_modules` 同理。而 `Dockerfile` 第一段
  `COPY . /source` 会上传整个上下文，实测 1300+ 个 `.pyc`（10 MB）、
  1 MB 级 `tsbuildinfo`、`docs/superpowers/`、整个 `web/`（含写着过期版本号的
  `version.json`）都在其中。现补 `**/` 形式并新增
  `tests/test_docker_build_context.py`：它**模拟 Docker 的匹配算法逐路径判定**，
  而不是断言「这行字符串存在」——后者正是漏掉这个语义差的原因。
- **发布洁净度门禁查的是索引，不是提交**：
  `git update-index --force-remove` 只清索引，HEAD 里的文件依旧在，而
  `git archive`、GitHub Release 源码包与 `git checkout <tag>` 读的都是**提交**。
  实测 HEAD 1542 文件 / 索引 810 文件，差额 730 个即那批本地审计产物：
  在这种状态下打 Tag，发出去的源码包依旧携带它们，而门禁全绿。现新增两条以
  HEAD 为对象的断言，并接受「已在暂存区标记删除」作为通过条件。
- **`--kind stable` 无法为别人开的预发布线收尾**（需求 23.2）：
  `3.4.0b1` 已发布时请求 stable 得到的是 `3.4.1`，而 `3.4.0` 明明更高
  （预发布小于同号正式版）且未被占用。同一处判断下 `--kind minor` 会给出
  `3.4.0`，stable 却跳到 `3.4.1`，自相矛盾。
- **同渠道的在飞预发布线被整条跳过**：别人在 `3.3.1b4` 上开了 beta 线而本机
  还停在 `3.3.0b10` 时，结果是 `3.3.2b1`——`3.3.1b5` 空着且高于全部已发布
  tag，却把整条 3.3.1 线作废，导致该线永远发不出正式版。需求要求的「自动
  跳过冲突版本」指的是跳过被占用的**号**，不是跳过一整条**线**。
- **四个 job 调用 `version.py` 却不装 Python**：该脚本用 `tomllib`
  （3.11 才进标准库）读 `pyproject.toml`，不装 Python 时落到 runner 镜像预装的
  解释器上。今天恰好够用，镜像一换成 3.10 这些门禁就会以
  `ModuleNotFoundError` 失败，而失败信息看起来像「版本不同步」，
  排查方向完全被带偏。同时把 `python3` 统一为 `python`
  （`actions/setup-python` 只保证 `python` 指向所选版本），并新增一条契约测试
  遍历全部 workflow 钉住这两点。
- **借款合同 DOCX 工具脚本处在「一次 `git add .` 就进仓库」的状态**：
  三个与本项目无关的本机脚本既未被跟踪也未被忽略，而它们生成的文档含个人
  身份字段。现纳入 `.gitignore` 与 `.dockerignore`。
- **示例 compose 只有两服务，`.env.example` 却声明了第三个服务的变量**：
  需求给出的拓扑是三服务（两个 QQ 实例 + Kirara），`.env.example` 也已声明
  `LLONEBOT2_AUTH_TOKEN` / `LLONEBOT2_QQ`，但示例 compose 里没有 `llonebot2`。
  变量指向一个不存在的服务，读起来像「已配好」而实际连不上——比不提供第二实例更糟。
  现补齐 `llonebot2`，并把两个实例的**数据卷与端口整组错开**：共用 `./QQ`
  会互相覆盖登录态与设备标识，现象是「登录一个就把另一个挤下线」，
  排查时看起来像 QQ 侧随机掉线。契约测试从 4 项扩到 9 项，新增
  「每实例独占卷」「端口不冲突」「示例不含形似真实 QQ 号的字面值」，
  以及一条**双向**漂移检查：compose 的 `:?` 变量必须都在 `.env.example` 里，
  而 `.env.example` 声明的 `LLONEBOT*` 变量也必须真的被 compose 使用。
- **Telegram 缺数学降级**：需求 19.1 点名 QQ、Telegram、WeCom 三个平台，
  要求差异只体现在渲染层。QQ 与 WeCom 已降级，Telegram 没有，于是同一段模型回复
  在 QQ 上是 `T → 0`、在 Telegram 上是原始 `$T \to 0$`。现接入共享 `degrade_math`。
  管线顺序本身就是约定（先数学降级→再表格→最后 MarkdownV2 转义；颠倒会让转义
  产生的反斜杠被当成 LaTeX 命令重新处理），因此抽出公开的 `TelegramAdapter.render_text`
  让这个约定可被直接断言。新增一条三渠道一致性用例：同一段公式在三处必须得到
  相同的可读符号——它的价值不在单渠道，而在「不因渠道而异」。
- **Telegram 与 WeCom 在就绪检查里被当成「已连接」**（需求 18.3 明确禁止的假连接
  状态）：`readiness.py` 对不实现 `AdapterHealthProvider` 的适配器走「按 connected
  计数」的兜底分支，而这两个适配器从未实现该协议。于是一个 Token 失效的 Telegram、
  或凭据换不出 `access_token` 的企业微信，在就绪检查里显示为健康——面板给出错误的
  安心，比不给状态更糟。现两者都自报状态：
  Telegram 依据长轮询是否在跑 + `get_me()` 是否成功过；
  WeCom 是回调模型、没有可持续观测的「链路已连通」信号，因此只在
  「路由已挂载且 API 代理就绪」时报 `connected`，凭据不可用时报 `waiting`，
  而不是假装它等价于 OneBot 的 `connected`。
  新增 `tests/im/test_adapter_health_coverage.py`，其中一条测试遍历全部四个
  内置适配器断言都实现了该协议——将来新增适配器忘了实现会立刻失败。
- **QQ 运维文档的验收矩阵引用了不存在的日志行**：「QQ 先启动」一行把
  `正在等待 QQ 启动进行重连` 列为日志证据，而这个字符串在整个仓库里零命中——
  它是上游 LLOneBot 的行为，不是 Kirara 会打的日志。照表验收的人会去找一条
  永远不会出现的记录。现改为指明证据在**上游侧**，并把其余各行的证据字段
  统一写成可在健康快照或日志里真正查到的键名。
  同时补一段说明「重连由谁负责」：Kirara 是反向 WebSocket 的**服务端**，
  不主动拨号，因此仓库内没有客户端重连循环、也就没有本侧的重连退避；
  OneBot 11 只规定固定间隔 `reconnect_interval`（默认 3000 ms，无指数退避）。
  本侧有上限退避的是**出站投递重试**（`outbox_backoff.py`），
  与「连接何时重建」是两件事，此前文档措辞容易让人把二者混为一谈。
- **取消传播与请求总截止时间从未生效**（需求 21.3）：`LLMManager` 完整实现了
  `cancellation_event` 与 `deadline_seconds`——超时放弃等待、退避期间也能被取消、
  取消不污染熔断统计——但**没有任何生产调用方**传这两个参数（`grep` 只能在
  manager 自己和测试里找到）。于是真实部署里一个卡住的上游会占着线程与连接
  直到进程退出，而「请求总截止时间」这条要求形同虚设。
  现新增 `agent_runtime.turn_deadline_seconds`（默认 0 = 不设预算，保持既有行为），
  设置后整轮共享**一个**取消信号与**一个递减**的预算：多轮工具调用不会每轮
  重新给满，预算耗尽时置位取消信号让仍在等待的上游松手。
  参数按签名探测再传，第三方 `LLMManager` 没有这两个形参时不会 TypeError。
- **管理动作丢弃了目标账号解析结果**：`recall_message`、`mute_user`、`kick_user`
  三处写的是 `self._action_self_id()` 而不赋值，返回值被丢掉，`self_id` 仍是
  `None`。今天等价（无 recipient 时该函数只会抛错或返回 `None`），但那行读起来
  像「已解析目标账号」而实际没有——一旦该函数学会解析单账号，丢弃就变成静默
  路由到错误账号。现接住返回值，并补两条用例把「结果被使用」钉住，而不是只钉
  当前取值。同时把 `mute_user` 那个前置逗号的畸形签名格式化回正常形态。
- **`set_chat_editing_state` 谎称「OneBot 不支持输入状态」**：LLOneBot 与 NapCat
  都实现了 `set_input_status`，对这两个最常用的实现来说那句话是错的，还白白丢掉了
  一个能让长回复期间界面不显得卡死的提示。现尝试调用并容错降级
  （不支持只让提示消失，绝不影响发送），群聊无对应语义直接跳过。
- **仓库索引里混入了 727 个本地审计产物（14.0 MB）**：`.qa-*`（22 个目录）、
  `.playwright-mcp/`、`.superpowers/`、`.memsearch/`、`work/*.zip` 与
  `PATHFINDER-2026-08-21/` 都被 `git add` 进了 HEAD。`.gitignore` 早已写了
  对应规则，但它只能阻止**新增**文件入索引，对已提交的文件完全无效——
  于是 `git archive HEAD` 膨胀到 46.6 MB，GitHub Release 源码包会携带
  原始计划明确禁止发布的 `PATHFINDER-2026-08-21/`。现已从索引移除
  （磁盘文件保留），并新增 `test_git_index_carries_no_local_audit_artifacts`
  防复发：规则写了不等于生效，必须断言索引本身干净。
  经扫描，这些文件中**没有**真实凭据值，仅有第三方 skill 文档里的占位符，
  因此是体积与整洁问题，不是泄密。
- **`.dockerignore` 未排除 `.env` 与密码哈希**：服务器上 `.env` 与 compose 同目录，
  `docker build .` 会把承载 `AUTH_TOKEN` 的它一并上传到构建上下文。现补
  `.env`、`.env.*`（保留 `!.env.example`）、`**/*password.hash`，并把
  `.memsearch/` 与本轮分析笔记一并排除。
- **运行期 SQLite 数据库被提交**：`data/mcp/audit.db` 带着 724 行本机 MCP
  工具调用审计记录、`data/mcp/confirmations.db` 带着一条待确认令牌摘要进了
  版本库。`.dockerignore:31-33` 早已排除 `*.db`，Git 侧却没有对应规则。
  运行态数据每台机器都不同，随仓库分发既无意义又会把本地操作记录发出去。
  现已移出索引并补 `.gitignore` 规则，新增
  `test_git_index_carries_no_runtime_databases` 防复发。
- **`.env.example` 缺 compose 强制要求的变量**：QQ 运维文档用
  `${LLONEBOT1_AUTH_TOKEN:?...}`（缺失即失败），而示例文件只有
  `DOCKERHUB_IMAGE`，照文档部署的人第一次 `up -d` 必然报错。现补全占位符与说明。
- **唯一可运行的 compose 连不上 QQ**：`docker-compose.yml.example` 只有
  `kirara-agent` 一个服务，双容器拓扑仅存在于文档里；且缺 `DATA_PATH`
  （而 `docker-compose.yml` 有它、契约测试还断言它），两份文件自相矛盾。
  现补齐 `llonebot` 服务、显式共享网络（默认 bridge 下容器名不可解析，
  这是「配置看起来对但连不上」最常见的原因）与反向 WebSocket 地址说明，
  契约测试同时覆盖两份文件。
- **熔断器错误率分支在 `circuit_min_requests > 20` 时永久失效**：样本窗口是
  `deque(maxlen=history_size)`，`history_size` 默认 20 且三处构造点都不传它，
  而配置只校验 `ge=1`。`_should_open` 要求 `len(outcomes) >= min_requests`，
  窗口装不满就永假——配 30 的用户拿到的是「错误率熔断被静默关掉」。
  实测 200 次连续失败、`min_requests=50`：`state: closed, error_rate: 1.0`。
  现窗口取 `max(history_size, min_requests)`，不缩小用户可配置范围。
- **`UsageSource.ESTIMATED` 在主链路上没有生产者**：`attach_estimated_usage`
  只挂在 `trace_llm_chat` 装饰器上，而 `LLMManager` 用
  `suppress_llm_chat_tracing()` 让装饰器整体短路，主路径只调
  `mark_provider_usage`。于是供应商不返回 usage 的请求以 `usage=None` 落库，
  统计页显示成 0 token、0 成本的「免费请求」——「0」是一个断言，「未知」不是，
  前者更糟。现同步与流式聚合两处都补上估算，并明确标记来源。
- **`llm_first_byte` 从未在生产代码里记录**：阶段名、`llm_first_byte_seconds`
  列、alembic 迁移与文档四处都在，唯一写入者却是测试自己。真实部署里
  「模型首字节」与「生成耗时」永远是 NULL，而 NULL 和 0 在排查时含义相反。
  现由流式聚合路径测出首字节（非流式没有可测的中间事件，保持留空而不是
  拿响应到达时刻冒充），经 `RuntimeResult.llm_first_byte_at` 进入投递时间线。
- **遗留工作流路径不落投递耗时**：`workflow_started` 与落库都只在 Agent 分支，
  未迁移到 Agent 的部署投递耗时表始终为空，「QQ 慢」在这些部署上无法拆开定位。
  现遗留分支也记录 `workflow_started`，并由 `SendIMMessage` 把入站阶段拼到
  真正发出的回复上、走同一个 `DeliveryTimingStore` 落库（发送失败也落库：
  `send_failed` 同样是需要回查的证据）。
- **货币金额被当成数学公式吃掉**：`_MATH_PATTERN` 只看 `$` 是否配对，而
  `price $5 and $7` 里两个货币符号恰好配对，中间内容被按 LaTeX 剥离——
  用户读到「价格 5 和 7」，数字还在、单位没了。现要求 `$...$` 内出现
  反斜杠命令、上下标或分式才按公式处理。
- **定界符外的裸 LaTeX 命令完全不处理**：模型经常直接写 `\to`、`\times`，
  旧实现只在 `$...$` 内替换，于是这些命令原样进入 QQ——正是需求明确禁止的
  「成片的 `\to`」。现非围栏正文也扫一遍；同时扩充命令表
  （`\int \nabla \partial \theta \forall \in` 等）、剥离 `\begin{}/\end{}`
  环境包裹、`\left/\right` 只去命令留符号、嵌套 `\frac` 反复求解。
- **WeCom 路径完全跳过数学降级**：同一段模型回复在 QQ 上是 `T → 0`、
  在企业微信上是原始的 `$T \to 0$`。「有的平台处理了、有的没有」不是平台差异，
  是漏了一步。现把降级逻辑导出为 `degrade_math` 供 WeCom 复用（它自带一套
  标题/强调/列表规则，需要的只是这一步），代码块占位符先行摘出因此不受影响。
  WeCom 的表格也改走共享 `render_table`，同样获得宽表降级。
- **宽表无阈值降级**：8 列中文表实测每行 97 显示列，在没有等宽字体、也不能
  横向滚动的 QQ 上按窗口宽度随机折行，框线错位后读者反而分不清值属于哪列。
  现超过 `MAX_TABLE_DISPLAY_WIDTH`（当时取 60，本轮已按手机气泡实际容量改为 38，
  见本文件「未发布」一节）时改为逐行「字段：值」分组布局；
  窄表仍走框线表，既有观感不变。
- **分页会切坏 Markdown 标记**：`*aaa…*` 在 40 字节上限下被劈成四页，
  首页尾部挂着未闭合的 `*`、末页开头凭空多出一个 `*`。现把成对强调、
  行内代码与链接一并视为不可切分片段，并把标题、列表项、引用、表格行的
  行首作为优先切点。
- **超长回复整条丢失**：超出页数或总字节预算时 `ValueError` 一路穿出
  `send_message`，用户什么都收不到。现新增
  `paginate_with_truncation_notice`：截断到预算内并追加明确的「已截断」提示；
  上限本身非法仍然抛出（那是配置错误，不该静默降级）。
- **OneBot 缺自身消息回声过滤**：上游开启 `reportSelfMessage` 时机器人会回复
  自己，且入站去重收据挡不住——回声的 `message_id` 与入站消息不同，去重表
  看来是全新事件。现按 `post_type == "message_sent"` 与 `user_id == self_id`
  在去重之前丢弃。
- **`mface` / `forward` 段被丢弃成空消息**：市场表情与合并转发到达时元素列表
  为空，整条消息被当成空内容，用户看到机器人毫无反应。现 `mface` 有图按图片、
  无图回落到 summary 占位，`forward` 给出可见占位，并补 `dice`/`rps`/`shake`。
- **`access_token` 查询参数形式被误判**：`_classify_access_token` 的 docstring
  声称支持 `?access_token=`，实现只读请求头。LLOneBot 与 NapCat 都允许查询串
  认证，这类连接会被记成 `access_token_missing` 而 aiocqhttp 实际放行了——
  健康面板给出的原因码与真实情况相反。现两种形式都读，请求头优先。
- **限流类 `ActionFailed` 被直接判死**：该异常混装了两类失败，参数错误重试
  一万次也不会变，限流、上游忙等一会儿就好。此前全部走 dead_letter，
  一次群内限流就永久丢掉一页回复。现按 retcode 区分（1200/1400/429/503
  视为瞬态），仍受 `max_attempts` 与退避上限约束。
- **`CodeNode.vue` 宽度双份真值**：CSS 写死 200/300px 而 `useLayout.ts` 另有
  同名常量，两边漂移不会报错，只表现为节点间距忽大忽小。`CustomNode` 早已
  收敛到常量绑定，代码节点此前遗漏。现同样内联绑定，并新增测试断言
  CSS 回退值与常量始终相等。
- **QQ 运维文档三处与实现不符**：readiness 检查名写作 `im_adapters_connected`
  （实际是 `im_available`，照文档做外部监控取不到值）；目录清单缺需求要求的
  宿主机路径与权限两列；compose 示例钉死厂商账号与版本号，且把 5900 标注为
  noVNC（5900 是原始 VNC，noVNC 是 6080，而参考镜像根本不暴露任何 VNC 端口）。
  现全部订正，并补齐升级兼容策略与反向 WebSocket 地址填法。
- **依赖探测遇到缺失的可执行文件不再可能抛出**：探测一个不存在的命令是
  「未安装」，不是接口错误。默认 runner 已把 `OSError` 转成 exit 127，
  但注入的自定义 runner 可能直接抛出；现统一按 `missing` 处理，
  探测接口不会因此 500。
- **WebUI 保存后端会重置全部容错参数**：`webui/src/api/llm.ts` 的 `LLMBackend`
  从未声明 `priority`、`participate_in_failover`、重试、超时与熔断字段，
  而后端 `LLMBackendUpdateRequest` 直接继承 `LLMBackendConfig` 会接收它们。
  于是从界面改一个开关，提交的 payload 缺字段，pydantic 用默认值补齐——
  一次无关编辑就把调好的整套容错预算恢复出厂。现补齐类型、补齐新建默认值，
  并在模型管理页提供「重试与队列 / 超时配置 / 熔断器设置」三组可编辑项。
- **流式请求读错超时键**：同步路径已按 `non_stream_timeout_seconds` 计算总截止时间，
  流式路径仍直接读遗留键 `request_timeout_seconds`，只配了新键的后端在流式下
  仍按 60 秒旧默认值执行。新增 `stream_total_timeout_seconds` 并让流式路径优先采用它。
- **`get_llm` 绕过优先级队列**：该入口一直是 `random.choice`（源码带 `TODO`），
  与 `get_provider_candidates` 建立的确定性排序互相矛盾，同一模型两次调用可能
  落到不同 Provider，配置里的 `priority` 对它完全无效。现复用同一套排序，
  队列为空时才回退到活跃后端列表，保证「只配一个不参与故障转移的后端」仍可用。
- **投递重试退避无上限**：OneBot 与 QQBot 的 `retry_delay * 2**(n-1)` 没有封顶，
  在配置自身允许的最大值下（`outbox_max_attempts=10`、`outbox_retry_delay_seconds=60`）
  最后一次等待约 8.5 小时，与队列卡死无法区分；Telegram 则完全没有指数项。
  现统一到 `kirara_ai/im/outbox_backoff.py`：指数增长、5 分钟上限、抖动只提前不推迟。
- **超时预算缺跨字段校验**：单字段 `gt=0` 从不检查「首字节 + 静默」是否超过流式总超时、
  重试退避总量是否超过非流式总超时，配了永远达不到的值也照样接受。
  现对用户显式写入的总超时做校验；沿用遗留键的既有配置不会因此变得无法加载。
- **WeCom 媒体临时目录绕过 `DATA_PATH`**：此前用 `os.getcwd()` 拼接，
  容器工作目录与数据卷挂载点不同时，临时文件落在卷外，既进不了备份也会在重建时丢失。
- **启动期目录创建没有可执行的错误信息**：裸 `os.makedirs` 在只读挂载、磁盘写满或
  路径被同名文件占用时抛出原始异常，不指明路径也不给处置建议，而现成的诊断
  只存在于 HTTP readiness 接口里——那时进程已经起不来。现给出「路径 + 原因 + 处置」，
  并实际探测一次写入（目录已存在但整卷只读是容器里最常见的情况）。
- **画布两套回退节点尺寸**：`useLayout` 按端口数、配置项与标签宽度估算，
  `WorkflowCanvas` 另有写死的 240×140。空位搜索、重叠告警与跳转居中都用了较小的那套，
  首次测量前的几何判断与布局结果互相矛盾。现统一走同一估算函数。
- **从节点列表添加节点会压在既有节点上**：该路径直接用 `project()` 原始坐标，
  跳过网格吸附与空位搜索；拖拽路径有防护，点击路径没有。
- **卸载前 500ms 的画布编辑被丢弃**：`onBeforeUnmount` 直接 `cancel()` 两个防抖，
  且父组件的未保存标记也来不及置位，于是「拖一下就切页」既无提示也未保存。
  现先把待写入状态刷入 store 并向上通知，再取消定时器。
- **自定义脚本节点无法连线且不给原因**：`internal:code` 的类级 `inputs/outputs` 为空
  （端口在 `__init__` 里按配置构建），`/block/types` 因此返回零端口，
  新建的脚本节点没有任何 handle，校验器也不报此情况。现补一条可操作的
  `code_node_without_ports` 提示。
- **前端拒绝运行时能接受的连线**：配置面板让用户为脚本端口选一个类型（默认 `str`）
  并据此校验，而后端端口实际是 `Any`，类型系统视其与任何类型兼容。
  现把脚本端口按 `Any` 处理，普通节点之间的类型校验保持不变。
- **画布不随可用面积变化重新适配**：内边距感知的 fit 只在恢复图、一键整理与显式点击时执行，
  窗口缩放、侧栏折叠与面板开合都会把内容压到面板底下。现用 `ResizeObserver` 观察，
  并在用户手动平移或缩放后停止自动接管视角。
- **`creator.subject` 存在两个位置**：`resolve_password_file_path` 把
  `data/web/password.hash` 改写到 `<DATA_PATH>/web/`，派生的身份文件随之下移一级；
  旧位置的文件不再被读取，一旦被「恢复」回去就会让全部已签发令牌静默失效。
  现在没有生效文件时自动继承旧文件（保证升级不掉线），两者都存在且不同时以生效位置为准并记一次日志。
- **远端 Skill 版本是合成的**：安装恒为 `1.0.0`、更新自动进位 patch，
  上游 `SKILL.md` 声明的 `version` 从不生效，`ResourceLifecycleService` 的
  降级保护对远端 Skill 形同虚设。现读取上游版本，仅在高于已装版本时采用，
  否则仍做本地递增（覆盖「内容变了但版本没动」的仓库）。
- **非 GitHub 来源查不到更新**：`check_updates` 对 `provider != "github"` 直接跳过，
  catalog 与 skills.sh 安装的 Skill 完全不出现在结果里，界面因此显示「无更新」。
  「没有这一行」和「没有更新」是两件事；现返回带 `update_channel_supported=false`
  的行并说明该来源应如何获取新版本。
- **Hook 无按工具过滤与按事件启停**：所有已绑定 Hook 在每个已声明事件上都会执行，
  一个只为某个危险工具写的 `PreToolUse` Hook 会在每次无关工具调用上被拉起，
  连同它的阻断能力一起；关停单个事件只能改文件重装。现支持 `matcher`
  （正则或工具名列表，整名匹配）与 `enabled`，未声明 matcher 时行为与此前一致。
- **readiness 会因未知适配器状态返回 500**：`counts[snapshot.status] += 1` 遇到
  新增状态直接 KeyError，把整个就绪接口拖下线。现降级计入未连接。
- **发布物审计把浏览器留痕当作版本载体**：`.playwright-cli/` 与 `.playwright-mcp/`
  被扫描为版本载体导致 `version.py check` 失败；三处忽略规则（git / docker / 版本脚本）
  现保持一致，规划留痕与授权说明草稿也不再进入镜像构建上下文。

### Added

- **QQ / OneBot 连接状态可区分**：`AdapterHealthSnapshot.status` 增加  `initializing`、`credential_rejected`、`upstream_refused`，并新增固定取值的
  `last_disconnect_reason`（`access_token_missing`、`access_token_mismatch`、
  `invalid_client_role`、`missing_self_id`、`heartbeat_timeout`、
  `upstream_lifecycle_disconnect`、`adapter_stopped`）。原有四种状态语义不变，
  只知道旧状态的消费方继续可用。readiness 分别统计新状态，凭据被拒时给出
  「核对访问令牌」而不是「检查心跳」。适配器详情页显示状态与一行可读原因。
- **QQ 代码可复制路径**：QQ 的 OneBot 消息模型没有交互按钮，画一个点不动的
  「复制」按钮比不画更糟。改为让代码块单独成为一条消息（整条即代码本体，
  长按全选即可复制），随后附一句复制指引；可用 `isolate_code_messages` 关闭，
  关闭后与正文混排，观感与此前完全一致。
- **端到端投递时间线**：阶段扩展为 `received_event`、`workflow_started`、
  `llm_first_byte`、`llm_completed`、`formatting_started`、`formatting_completed`、
  `send_started`、`send_succeeded` / `send_failed`，并纳入 `to_dict()` 序列化
  （此前只存在于内存、被排除在序列化之外，事后无法回答「为什么慢」）。
  `delivery_durations()` 给出各阶段耗时；**没测到的阶段不会输出 0**。
- **成本与失败维度统计**：概览新增总成本、计价货币与未定价请求数，
  按 Provider / 模型 / 失败类型聚合各自成本，另给出首字节与尝试次数摘要。
  成本一律取请求当时的价格快照，不会因后来改价而改写历史账单。
  CSV 导出补上 `cost_snapshot`。
- **统计前端补齐维度与导出**：筛选条件与时区现在会真正送达统计接口
  （此前统计卡片完全不受筛选影响，且按服务器时区分桶导致跨时区用户的「今天」是错的），
  新增 provider / 失败类型 / 用量来源 / 时间范围筛选，表格补 provider、
  失败类型、首字节、尝试次数、用量来源与成本列，并提供 CSV 导出入口。
- **会话与待确认可管理**：新增 `GET /agents/sessions`、
  `DELETE /agents/sessions/<id>`、`DELETE /agents/sessions/<id>/history`
  与 `GET /agents/confirmations`，Agent 页面提供只读列表与清空/删除动作。
  接口只返回条数与时间戳，**不返回任何对话正文或工具参数**；
  会话 ID 只接受 64 位摘要，杜绝路径穿越。
- **统一页码格式**：`PAGE_LABEL_PATTERN` 收敛页码字面量，WeCom 自有的
  `[i/N]` 前缀与并行 markdown 分段实现移除，四个渠道统一为「第 N 页 / 共 M 页」。
- **QQ / OneBot 运维文档**：新增 [`docs/QQ_ONEBOT_OPERATIONS.md`](docs/QQ_ONEBOT_OPERATIONS.md)，
  覆盖连接方向、七种状态与原因码对照、Kirara 与 QQ 两侧的数据目录清单、
  Compose 参考（VNC/PMHQ 只绑本机、Token 走 `.env`）、
  `down && pull && up -d` 的预期状态序列与恢复时间、二维码有效期与快速登录、
  回复慢的分段定位方法，以及 11 项 Compose 验收矩阵。
- **入站去重收据**：新增 `im_inbound_receipts` 表（四渠道共用）与迁移 `a4d1f8c30e57`，
  OneBot 与 QQBot 补齐入站去重。反向 WebSocket 在投递中途断开时上游无法知道
  我们是否已处理，重投是它唯一安全的选择——去重必须由本侧完成。
  事件身份取 `self_id` + `message_id`，缺失时退回 `self_id` + `user_id` + `time`；
  两者都拿不到时照常处理但不去重（丢一条消息比偶尔重复一次更糟）。
  处理失败会释放收据，让上游重投得到一次真正需要的重跑；进程中断时留在
  「处理中」的事件在下次启动重新开放认领。
- **本地 Token 估算**：新增 `kirara_ai/llm/token_estimator.py`，
  `UsageSource.ESTIMATED` 第一次有了生产者。供应商不返回 usage 时给出脚本感知的
  估算值（CJK 按字符计、拉丁按约 4 字符 1 token、标点各计 1）并明确标记为估算；
  供应商返回过任何 usage（**包括 0**，那是实测值）一律不覆盖；
  完全没有可测内容时仍保持 `unknown`，不硬造数字。
- **熔断状态跨重启保留**：新增 `kirara_ai/llm/circuit_store.py` 与
  `data/llm/circuit-state.json`。只持久化「已打开 / 半开」的熔断器及其打开时刻，
  恢复等待时间在停机期间继续流逝，因此重启不会让等待从头开始。
  **结果环形缓冲区不持久化**：错误率描述最近的真实流量，
  把重启前的窗口搬回来会让现在健康的上游被过期样本重新熔断。
  状态文件超过 24 小时或损坏时直接忽略。
- **流式回复模式**：OpenAI 兼容适配器实现 `stream_chat`（SSE 解析，单个坏帧不中断整条流），
  并新增 `agent_runtime.reply_stream_mode`。`aggregate` 以流式向上游取回内容再整段投递——
  **不是逐字推送**，因为 QQ、Telegram、WeCom 都不支持对已发出消息逐字编辑，
  逐字推送只会变成几十条碎片消息。真正的收益是流式首字节超时、静默超时与
  「首字节之前可安全切换 Provider」这三条容错路径开始生效。
  工具调用轮次始终走非流式：工具轮需要结构化 `tool_calls`，聚合文本会丢掉它。
  未实现流式的适配器自动回退，不报错。
- **投递耗时落库与回查**：新增 `im_delivery_timings` 表与迁移 `b5e2c94a17d8`，
  以及 `GET /tracing/delivery/summary`、`GET /tracing/delivery/recent`。
  日志只能回答「刚才那条为什么慢」，按时间范围回查「上周二 QQ 慢是模型还是发送」需要行。
  三条约束：**不存任何消息正文**（会话键存 SHA-256 摘要）；
  **没测到的阶段存 NULL 不存 0**，平均值只对测到该阶段的行求平均并给出样本数；
  保留期默认 30 天，启动时清理。
- **Hook 上线前预演**：新增 `GET /agents/hooks` 与
  `POST /agents/hooks/<id>/preview`，回答「这个 Hook 声明了什么」与
  「它会不会因为某个工具而触发」。两者都不执行 handler、不启动进程；
  声明解析失败返回错误说明而不是抛出，一个坏声明不会让整份列表打不开。
  WebUI 在「Agent 管理 → Hook 声明与预演」提供对应界面。
- **批量撤销接入**：`performBatchAction` 此前无调用方，复合编辑（复制多个节点、
  一键整理）靠 500ms 防抖窗口合并，跨窗口就会被拆成多个撤销步骤。
  现由画布批次统一写历史，一次 Ctrl+Z 即可完整退回。
- **依赖目录补齐 1.txt 指名的工具链**：新增 `rtk-cli`、`memsearch-cli`、
  `context-mode-plugin`、`caveman-plugin` 四个条目（此前只有 `graphify-cli`），
  因此这些工具的就绪状态第一次能在前端看到。
  有真实非交互安装器的（rtk、memsearch）可受控安装；
  Claude Code 插件（context-mode、caveman）**装在操作者自己的 Claude 配置里、
  不是服务器运行时组件**，因此 `install_supported` 为 false 并给出运维指引——
  为它们编一条安装命令只会把命令跑到错误的目标上。
  `rtk` 的描述明确与同名 Rust Type Kit 区分。
- **REST 管理接口在 README 成文**：新增一节列出 readiness、熔断状态、
  LLM 统计与导出、投递耗时、会话与待确认、Hook 声明与预演的全部接口，
  并注明返回值均不含凭据、对话正文与工具参数。

### Security

- MCP 工具确认判定从私有方法提升为公开 `tool_requires_confirmation`
  （保留原私有名别名，行为不变），让「HTTP 路由签发确认令牌」与
  「运行时拒绝未确认调用」使用同一个显式边界。
- 会话与确认接口的返回值经过裁剪：对话正文、工具参数不跨越该边界。
- 断开原因码为固定枚举，不含令牌、账号或上游原始报文。

### Tests

- 新增后端用例：容错配置与优先级（`tests/llm/test_resilience_config.py`）、
  投递退避（`tests/im/test_outbox_backoff.py`）、投递时间线
  （`tests/im/test_delivery_timeline.py`）、代码复制路径
  （`tests/im/test_code_copy.py`、`tests/plugins/im_onebot_adapter/test_code_delivery.py`）、
  连接状态（`tests/plugins/im_onebot_adapter/test_connection_states.py`）、
  readiness 新状态（`tests/web/api/system/test_readiness_im_states.py`）、
  数据目录契约（`tests/test_data_paths.py`）、页码统一
  （`tests/plugins/im_wecom_adapter/test_page_markers.py`）、成本统计
  （`tests/tracing/test_statistics_cost.py`）、创建者身份
  （`tests/web/auth/test_creator_identity.py`）、Skill 版本与更新渠道
  （`tests/plugin_manager/test_skill_versions.py`、`test_update_channels.py`）、
  Hook matcher（`tests/agent_runtime/test_hook_matchers.py`、
  `test_hook_dispatch_matching.py`）、会话管理
  （`tests/agent_runtime/test_session_management.py`、`tests/web/api/agent/test_sessions.py`）、
  链路时间线（`tests/workflow_executor/test_dispatch_timeline.py`）。
- 新增 WebUI 用例：后端容错字段往返（`llm-backend-resilience`）、
  脚本节点端口（`workflow-code-node-ports`）、节点尺寸估算（`workflow-node-size`）、
  统计请求契约（`tracing-statistics-request`）、会话面板（`agent-view-sessions`）、
  批量撤销（`workflow-batch-history`）、Hook 声明与预演（`agent-view-hooks`）。
- 本轮补充的后端用例：入站去重（`tests/im/test_inbound_receipts.py`、
  `tests/plugins/im_onebot_adapter/test_inbound_dedup.py`）、Token 估算
  （`tests/llm/test_token_estimator.py`、`tests/tracing/test_estimated_usage.py`）、
  熔断持久化（`tests/llm/test_circuit_store.py`）、流式回复模式
  （`tests/llm_adapters/test_openai_streaming.py`、
  `tests/agent_runtime/test_stream_reply_mode.py`）、Hook 预演
  （`tests/agent_runtime/test_hook_introspection.py`）、投递耗时落库
  （`tests/im/test_delivery_timing_store.py`、
  `tests/web/api/tracing/test_delivery_timings.py`、
  `tests/workflow_executor/test_delivery_timing_persistence.py`）、
  依赖目录覆盖（`tests/plugin_manager/test_dependency_catalog_coverage.py`）。
- 修正一条断言方向错误的既有用例：`test_im_text_render.py` 原本断言
  投递时间线**不**被序列化，而那正是缺陷本身；现断言其可序列化，
  同时保留「事件不可篡改」「时间戳带时区」两项要求。
- 本轮（无围栏代码 / WebUI 流式 / 自动检测界面 / 跨渠道对比）新增的用例：
  无围栏代码识别与三平台渲染（`tests/test_unfenced_code_blocks.py`，
  含一批「形状像代码但其实是正文」的反向样本——中文技术散文、英文等式句、
  日志行、URL 列表都不得被判成代码）、WebUI SSE 契约
  （`tests/web/api/llm/test_webui_chat_stream.py`）、WebUI 增量协议
  （`tests/web/api/llm/test_webui_incremental_adapter.py`）、跨渠道可比耗时
  （`tests/im/test_delivery_channel_comparison.py`，含一条钉住「分组聚合而非
  N+1 查询」的用例）、对比接口（`tests/web/api/tracing/test_delivery_timings.py`
  追加六条）；以及 WebUI 侧的 SSE 解析（`chat-stream`，含「事件被网络分片切开」
  与「中文字符被切在两个 chunk 之间不产生替换字符」两条）、
  流式气泡行为（`llm-chat-view` 追加一个 `describe`）、
  自动检测计划页（`llm-auto-detect-schedule`）、
  跨渠道对比表（`delivery-timeline-view` 追加七条）。
  `resolve_reply_stream_mode` 的优先级用例补齐入口声明那一层
  （`tests/agent_runtime/test_reply_stream_mode_scope.py`）。
- 需求 7 的两半各有一组契约用例：系统提示词投递
  （`tests/llm_adapters/test_system_prompt_delivery.py`，14 条，覆盖两个适配器 ×
  流式/非流式 + 五种边界；只断言**请求体形状**，不打真实上游——要钉住的是
  「我们发出去的 JSON 长什么样」，那与网络无关）；自动检测保存链路
  （`tests/web/api/llm/test_auto_detect_apply.py` 10 条 +
  `webui/tests/llm-auto-detect-apply.test.ts` 9 条，含「GET 仍然只读」这条
  反向断言——那是这次修法的前提，丢了它下一个人会顺手给 GET 加副作用）。
- 节流归因与热更新诊断的用例：`tests/im/test_pacing_attribution.py`
  （含「没有节流记录时两列缺失而不是 0」与「失败路径同样分开归因」两条边界）、
  `tests/im/test_hot_update_diagnostics.py`（用现场日志原文，含「热更新不覆盖扫码
  状态」「还在下载时 duration 为 null 而不是 0」「不泄漏实例标识」三条）、
  以及前端类型与呈现契约 `webui/tests/im-hot-update-tag.test.ts`。
  `tests/plugins/im_onebot_adapter/test_adapter_outbox.py` 里两条钉住阶段序列的
  既有断言随之更新——新增阶段是一次真实的契约变化，不是可以绕过的实现细节。

### 未验证项（不计入完成）

- 真实 `docker compose down && pull && up -d` 后的连接恢复与免扫码登录。
- 真实 QQ 扫码、PMHQ 注入与 QQ 热更新时序。
- 真实多 Provider 上游的故障转移与熔断触发。
- 真实 QQ / Telegram / WeCom 客户端上的渲染观感。
- 真实反向代理（nginx / Cloudflare）后 SSE 的分块传输行为。响应已带
  `X-Accel-Buffering: no`，但代理若仍缓冲，表现是「等很久然后整段出现」——
  与非流式无法区分。本机 `app.test_client()` 不经过代理，测不出这一层。

以上五项依赖外部环境（真实 QQ 账号、真实 Docker 主机、真实上游、真实反向代理），
本机无法产出证据。状态机、去重、退避、排版与耗时记录均有自动化测试覆盖，
但**不能据此声称实机行为已验证**；请按 `docs/QQ_ONEBOT_OPERATIONS.md`
第八节的 11 项验收矩阵在自己的部署上核对。

### 仍未实现（明确记录，不含糊）

- **多 worker 共享熔断状态**：熔断状态按进程持久化，多 worker 部署下各进程
  各写各的状态文件，不互相可见。
- **工作流逐节点执行历史**：`WorkflowExecutionBegin` / `WorkflowExecutionEnd`
  有事件但没有内置消费者，节点级耗时与中间值不落库。
- **匿名指标端点**：没有 Prometheus `/metrics`，readiness 需鉴权。
- **主动告警**：日志与追踪都是被动记录，框架不会主动通知。

## [3.3.0b11] - 2026-08-22

### Added

- **智能版本升级**：新增基于 `pyproject.toml` 唯一版本源的候选版本解析，默认沿用当前 alpha、beta 或 rc 通道，自动检查本地与指定远端 Tag 并跳过已占用版本；正式版、patch、minor 和 major 变更必须显式选择。
- **发布同步审计**：自动发现 Python、npm、锁文件、源码、Docker、CI 和活动文档中的版本载体，以 `.version-artifacts.json` 记录审计结果，并由 `check` 拒绝遗漏、漂移、旧版本残留和 Tag 不一致。

### Fixed

- **版本同步可恢复性**：跨文件写入前建立事务快照，锁文件或校验失败时只恢复本次版本同步涉及的目标，不删除无关的新文件，也不触碰数据、配置、密钥和私有 Logo。
- **发布工作流一致性**：Docker latest 发布跨 Release 串行，手动镜像发布通过环境变量传递输入，避免旧 Release 覆盖最新镜像或因 shell 插值造成版本解析错误。

### Changed

- **发布版本保持一致**：Python 元数据、`uv.lock`、WebUI 包、活动文档和版本索引统一为 `3.3.0b11`；历史升级指南、CHANGELOG、测试夹具和规划资料继续作为排除项保留，不被批量改写。

### Tests

- 版本管理、发布工作流、发行物契约、readiness 及后端全量测试在版本同步后重新执行；WebUI type-check、单元测试、生产构建和发行物检查作为发布前门禁继续执行。

## [3.3.0b10] - 2026-08-22

### Added

- **OneBot V11/QQ 适配器**：内置反向 WebSocket、event/api/universal 角色校验、多账号 `self_id` 路由、连接健康状态、HTTP 动作调用、群成员管理，以及 `url`、`path`、`file`、`data` 和 `base64://` 媒体入站支持。
- **QQ 长回复与排版**：长文本按平台限制分段发送并标注“第 N 页 / 共 M 页”；Markdown 表格转换为规整等宽表格，代码围栏保持完整，常见 LaTeX 符号转换为可读文本，避免 `\\to` 等源码标记直接显示。
- **OneBot 部署与诊断契约**：增加媒体大小、超时和公网地址 SSRF 防护；适配器健康状态与 readiness 检查可用于排查 QQ 连接和部署问题。

### Fixed

- **工作流画布重叠**：保留有效节点坐标，仅对缺失或无效坐标增量布局，并以空间索引处理碰撞查询，减少打开既有工作流时的堆叠和不必要的全图重排。
- **画布历史操作**：保留 `undo`、`redo` 和 `performActionWithoutHistory` 兼容入口，历史记录有界且嵌套配置快照相互隔离。
- **IM API 稳定性**：多文件配置更新、敏感配置脱敏、空值保留和 WebUI 请求错误处理补齐边界测试，避免失败后状态不一致或把密钥回显给客户端。

### Changed

- **发布版本保持一致**：Python 元数据、`uv.lock`、WebUI 包版本、操作文档、Docker 构建身份和版本索引统一为 `3.3.0b10`；历史 `3.3.0b9` 记录保留不改写。
- **分发内容完整**：OneBot 插件及图标纳入 wheel/sdist 与 fresh-install smoke 检查，运行数据、虚拟环境、缓存、私有 Logo 和本地探索产物继续排除在发布物之外。

### Tests

- 后端全量测试：`647 passed, 1 skipped`。
- WebUI type-check、unit tests 与 production build 均纳入发布前复核；OneBot/媒体专项测试覆盖连接角色、分页、排版、媒体安全和多账号路由。

## [3.3.0b9] - 2026-08-21

### Added

- **多模态模板恢复角色扮演人设**：`chat/normal_multimodal.yaml` 的 `system_prompt` 此前只剩 `# Information` / `# Memories` 骨架，模型因此不再扮演角色。现补回与 `factories/persona.py` 的 `DEFAULT_PERSONA_SYSTEM_PROMPT` 一致的人设正文（1044 字符逐字节核对）。随包预设与 `data/workflows` 两份副本同步修改，`model_name` 继续留空由用户在下拉框选择。`chat/dsr_thinking.yaml` 不在此列：它针对思维链模型专门重写为 `# Rules` 指令集（控制标记不可见、代词记忆关联、拒绝客套、专家视角），是有意的差异，保持不变。

### Fixed

- **正文对比度达到 WCAG AA**：12 处正文说明文字读的是 `--text-color-tertiary`（浅色 `#909399`，对比度仅 2.87:1）或 `--n-text-color-3`（同色值），均低于 AA 要求的 4.5:1。现按 `main.css` 既有规则「background / border 用原键，color 用 `-text` 键」改读 `--text-color-tertiary-text`（浅色 4.62:1、深色 5.66:1）。该达标令牌此前定义了却无人使用。作为填充与描边使用的 3 处（状态标签底色、滚动条滑块）保持原键不变。
- **body 行高读排版令牌**：`base.css` 的 `body` 写死 `line-height: 1.6`，与 `--line-height-normal: 1.5` 长期不一致，使「用了令牌的文字」与「继承来的文字」行距对不齐。现改读令牌并保留原字面量作回退。`font-size` 保持 15px 不动——令牌 `--font-size-base` 是 14px，改读令牌会让全站继承文本整体缩小一档。
- **本地审计目录不进入版本库或镜像**：根目录 `work/` 用于保存 CI 调试与构建留痕，现由 `.gitignore` 和 `.dockerignore` 同步排除；`scripts/version.py` 已跳过该目录，三处规则保持一致，避免本地证据文件进入提交或 Docker 构建上下文。

### Changed

- **发布版本保持一致**：远端已有不可移动的 `v3.3.0b8` tag，当前代码顺延为 `3.3.0b9`，让 GitHub tag、源码版本与 Docker 镜像继续指向同一份代码。
- **仓库自身链接指向本仓库**：`pyproject.toml` 的 `Homepage`、`Bug Tracker`，以及 README 的 star / license / CI / codecov 徽章、问题列表与 star-history 均改指 `HyskoaMorroh/kirara-ai`。发布身份与外部资源保持上游不变：PyPI 包名 `kirara-ai`（`entry.py`、`system/routes.py`、`system/utils.py` 依赖它做自更新与版本读取）、npm 包名 `kirara-ai-webui`、文档站 `kirara-docs.app.lss233.com`、插件市场 API、Docker Hub 拉取徽章、作者署名与贡献者名单。
- **社区入口统一为 Telegram**：README 的 6 个 QQ 交流群、机器人调试群与开发者交流群链接（多数已标注「已满」）替换为单一入口 <https://t.me/kirara_ai>。

### Tests

- **Python 3.10 与运行时镜像回归**：测试在 Python 3.10 下通过 `tomli` 兼容路径；版本管理测试在不安装开发工具 `uv` 的运行时镜像中通过，避免把 `uv` 引入生产镜像。
- **`FunctionCalling` 区块补测试**：该区块在 `blocks/llm/chat.py` 有实现却无任何测试引用（`ChatCompletionWithTools` 是另一个区块，其测试不覆盖它）。新增 3 个用例覆盖「模型请求工具走 `tool_call`、不请求走 `resp`」的二选一输出契约、未选模型时的报错须指名节点、主模型不可用时降级到备用模型。
- **人设防回归守卫**：新增用例断言默认工作流的提示词确实取自 `persona.py`，以及 `normal.yaml` / `normal_multimodal.yaml` / `talk_break.yaml` 三个角色扮演预设的两份副本都仍带人设主体、互动规则与记忆占位符。时间信息允许由 `{current_date_time}` 占位符或 `internal:current_time_block` 节点任一提供（`talk_break.yaml` 走后者）。

## [3.3.0b8] - 2026-08-19

### Fixed

- 修复 WebUI 传递依赖中的 6 个高危安全告警；约束 `lodash`/`lodash-es`、`postcss` 和 `nanoid` 使用已修复版本，并由锁文件固定可复现依赖树。
- 修复更新弹窗把落后镜像源中的 `3.2.0` 显示成“最新版本”的问题；检查接口会把不高于当前安装版本的候选归一为当前版本并清空下载地址，执行接口继续拒绝相同版本与降级。
- 修复 PyPI 预发布发现、npm `beta` 标签选择、WebUI 未知构建版本和在线更新信任边界；后端只接受服务端可信 registry 解析出的下载地址，WebUI 归档采用受限解包、暂存和原子替换。
- 修复 PR 审查工作流在 `pull_request_target` 可写令牌上下文中安装并执行外部 PR 代码的高危链路；类型检查改用只读 `pull_request` 权限。
- 修复工作流与调度多文件写入中断后可能留下半完成状态的问题；启动恢复现在保证旧逻辑状态或新逻辑状态之一，并保留已编辑预设和删除 tombstone。
- 修复异步工作流/模型请求乱序覆盖新状态，以及模型目录检测结果在后端适配器或配置已变化后仍被应用的竞态；目录刷新仍不修改工作流主模型和备用槽位。
- 修复工作流画布卸载或切换时遗留的防抖写入，避免旧工作流状态回写到新工作流。
- 修复模型目录刷新和后端配置更新失败后的运行时回滚路径。
- 修复规则持久化失败时未受锁保护的内存回滚，以及 MCP 超时测试产生的未等待协程警告。
- 修复窄屏节点配置面板和画布弹窗的横向溢出，并为关闭、输入端口删除、输出端口删除按钮补充可访问名称。
- 修正文档中默认调度规则数量与优先级表格。

### Added

- **动态版本唯一源**：以 `pyproject.toml` 的项目版本为唯一源，`scripts/version.py set/check/discover` 自动发现并同步 Python、`uv.lock`、npm、Docker、CI 与 Windows 发布载体；发布工作流按 tag 反向校验，遗漏或漂移立即失败。
- **发布门禁**：Docker、GitHub tag、WebUI 构建元数据、wheel/sdist 和 Windows 快速启动包统一使用动态版本，并增加版本契约、产物元数据、全量测试和镜像 smoke test。
- **升级与回滚手册**：新增 `docs/UPGRADING.md`，覆盖独立 A 数据副本、备份检查、鉴权、readiness、工作流/调度/模型验证、停止放量条件和恢复后重启。
- **历史升级清单**：补充 `docs/UPGRADING_TO_3.3.0a7.md`，冻结记录 `3.3.0a7` 发布周期的备份、readiness、工作流/调度、模型选择、周期刷新观察和回滚核对项；当前升级以 `docs/UPGRADING.md` 为准。
- **受控扩展实用指南**：新增 `docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`，明确 Agent/Skill 是现有工作流与目录元数据的组合、Hook 不是 Python sandbox、MCP 没有通用人工审批中心，并给出真实 manifest、lifecycle、审计和移除边界。
- **本地 readiness 诊断**：新增鉴权接口 `GET /backend-api/api/system/readiness`，以稳定检查 ID 汇总数据目录、配置、工作流、调度目标、IM、LLM 与可选 MCP 状态；检查有超时上限且不返回密钥。
- **预设目录与受控 extension manifest**：随包工作流增加独立 catalog 元数据；插件可声明 capability 和 lifecycle allowlist，框架 host facade 拒绝并审计未声明访问，MCP 操作记录脱敏结果与耗时元数据。
- **大图画布与可访问主题**：增加空间索引、只为缺失坐标节点布局、100 步有界历史、语义色令牌、键盘工具栏和窄屏检查器；已有坐标、工作流语义和色板 ID 保持兼容。
- **工作流操作与部署指南**：新增 `docs/WORKFLOW_OPERATIONS_GUIDE.md`，把首次部署、模板选型、模型手动选择、自动探测边界、默认规则、画布操作、排错顺序和扩展边界串成可执行流程；README 文档导航同步更新。
- **精湛部署与扩展指南**：新增 `docs/EXCELLENCE_DEPLOYMENT_GUIDE.md`，明确现有能力的部署验收顺序，并把 Agents、Skills、Hooks、MCP 与可观测性拆为可回滚、可验证的后续阶段，避免把规划误当成交付。
- **缓存与发布回归契约**：补充 Block 静态元数据缓存、Docker 锁文件依赖导出、Yarn 版本声明和非改写式 lint 命令的回归检查；新增画布重叠检测的前端单元测试。
- **触发规则试运行**：规则页可用示例消息按真实的“优先级降序 + 规则 ID 升序”预演命中顺序，逐条显示将执行、被前序规则截断、未命中、已禁用或无法确定；试运行不执行工作流、不发送消息、不保存草稿。随机概率和需要真实 IM 实例的条件会明确标为无法确定，避免把演示结果误当成实际运行结果。
- **工作流结构预检**：工作流画布的“检查”现在会调用无副作用的服务端预检，统一检查重复节点、未知区块或端口、重复输入连线、类型不兼容、必需输入、入口、不可达节点与非受控环；预检只报告问题，不保存、执行或自动改图，搭建中的草稿仍可按原方式保存。
- **完整备份与恢复服务**：新增 `kirara_ai.backup.BackupService`，可导出便携式 `.kirara-backup.zip`，覆盖系统与 Web 设置、模型与机器人配置、工作流、触发规则、数据库、记忆、媒体、插件、字体及自动探测状态。
- **安全恢复机制**：导入前校验归档清单、文件哈希、路径、容量、压缩比和符号链接；写入前自动创建回滚包，验证或恢复失败时保留原数据，避免半恢复状态。
- **备份管理 API**：在系统 API 中提供备份创建、列表、下载、导入、删除和恢复状态接口；恢复成功后明确要求重启服务以重新加载配置与运行对象。
- **备份测试与设计文档**：新增备份服务/API 测试，以及完整备份恢复的设计和实施文档，便于后续维护与审计。
- **可配置镜像示例**：新增 `.env.example`，使用 `DOCKERHUB_IMAGE` 显式指定部署镜像，避免新部署默认依赖第三方固定镜像名。
- **MCP 资源读取与提示词采样 API**：新增 `GET /mcp/servers/<id>/resources/<resource_id>` 与 `POST /mcp/servers/<id>/prompts/sample`，补齐 WebUI MCP 详情页「查看资源」「采样提示」两个按钮所需的后端接口。
- **备份与恢复图形界面**：新增后端自带页面 `GET /backup`（源文件 `kirara_ai/web/static/backup.html`），提供导出、导入前检查、恢复和回滚包下载四组操作。该页面不属于 `kirara-ai-webui` 前端项目，因此不受 WebUI 版本影响，部署后立即可用；会自动复用浏览器中 WebUI 的登录令牌，也支持在页面内单独登录。页面本身不含任何凭据，所有接口调用仍需 Bearer 令牌。
- **WebUI 内置备份入口**：将 `kirara-ai-webui` 0.1.1-beta.3 源码受版本控制地纳入 `webui/`，在「系统设置」增加与原有界面协调的「备份与恢复」标签页。页面直接使用既有备份 API，支持导出、导入前检查、二次确认恢复和不含令牌 URL 的回滚包下载；`/backup` 旧入口继续保留。
- **首次部署的内置工作流与规则**：将进阶聊天工作流作为包内预设，并在没有用户规则文件的全新安装中注册系统、游戏及聊天规则；已有 `data/` 中的工作流、规则和明确删除记录仍优先，升级不会覆盖用户配置或复活已删除的预设。
- **分发完整性契约**：为 wheel 包内预设工作流、Docker 的默认数据初始化以及排除测试工作流夹具增加回归检查，避免源码运行正常而 pip/Docker 首次部署缺失模板或携带测试数据。
- **打包声明**：`MANIFEST.in` 与 `pyproject.toml` 增加 `kirara_ai/web/static` 和 `kirara_ai.workflow.presets` 的分发声明，确保 wheel 与 Docker 镜像内同时包含后端自带页面和进阶工作流模板。

- **主题与外观系统**：新增 `webui/src/theme/palettes.ts` 与 `webui/src/stores/theme.ts`，提供 6 套配色方案——`classic`（经典蓝，主色沿用项目原始的 `#007AFF`）、`graphite`（石墨灰）、`midnight`（午夜蓝）、`forest`（松林绿）、`contrast`（高对比，全部语义色满足 WCAG AA）、`oled`（纯黑，深色底为 `#000000`），每套都有独立的浅色/深色取值；明暗模式支持「跟随系统 / 浅色 / 深色」三档，跟随系统时监听 `prefers-color-scheme` 实时切换。选择通过 `localStorage` 的 `themeMode` 与 `themePalette` 两个键持久化，刷新后保留。
- **外观设置界面**：新增 `webui/src/views/settings/components/AppearanceCard.vue`，挂载在「系统设置 → 外观」标签页，可切换明暗模式与配色方案，每个色板卡片用自身色值绘制浅色/深色预览并带 `aria-label`。状态栏另加一个明暗快速切换按钮，方便在工作流画布这类全屏页面直接切换。
- **首屏防白屏（FOUC）**：`webui/index.html` 增加内联启动脚本与骨架加载动画，在主应用加载前就按 `localStorage` 读到的模式/色板写入 `data-theme`、`.dark` 与背景色。启动脚本内的色板表由 `/* THEME_BOOT_TABLE */` 标记框定，并由新增测试 `webui/tests/theme-boot-table.test.ts` 校验它与 `palettes.ts` 不发生漂移；`localStorage` 不可用（隐私模式等）时脚本静默跳过，交由 CSS 兜底。
- **设计令牌体系**：`webui/src/assets/main.css` 增加字号阶梯（`--font-size-xs` … `--font-size-3xl`）、行高（`--line-height-tight/normal/relaxed`）、字距（`--letter-spacing-*`）、间距节奏（`--space-1` … `--space-8`）、阴影层级（`--box-shadow-sm/lg/overlay`）与动效时长（`--transition-duration-fast/slow`）；新增 `--border-radius-large`、`--border-radius-pill`；为语义色补充满足 AA 的文字变体 `--primary-color-text`、`--success-color-text`、`--warning-color-text`、`--error-color-text`、`--info-color-text`。全局响应 `prefers-reduced-motion: reduce`（把过渡与动画压到 0.01ms），并为 `:root` / `body` / `#app` 的背景与文字色加上主题切换过渡。
- **色板级形状差异**：新增 `ThemeShape`（圆角、基础字号、控件高度、字重），让各套色板在颜色之外还能有密度与圆角差异；`DEFAULT_THEME_SHAPE` 逐项等于改动前 `App.vue` 里的取值，未声明 `shape` 的色板行为不变。
- **画布防重叠与布局工具**：`webui/src/components/workflow/useLayout.ts` 导出 `computeWorkflowLayout`、`resolveNodeOverlaps`、`findFreeNodePosition`、`findOverlappingNodes`、`snapToGrid`、`measureTextWidth`，节点宽度改由按字符类别累加的真实文本测量决定（替换原先 `label.length * 14` 的估算：该系数按纯中文标签调出，西文标签被高估、中英混排被低估），宽度区间由 200 / 300 提升为 `NODE_MIN_WIDTH = 220` / `NODE_MAX_WIDTH = 360`（代码节点单独保留 200 / 300）。
- **预设坐标重算脚本**：新增只读脚本 `webui/scripts/relayout-presets.mjs`，复用编辑器同一套 `computeWorkflowLayout()` 计算随包预设的无重叠坐标并打印到标准输出，不修改任何文件。
- **画布可用性增强**：节点级校验角标与问题清单弹窗，可逐条跳转到出问题的节点；缩放控件里新增可见的「自动排布」按钮（原先只有 Ctrl+L 与顶部工具栏图标）；新增节点查找面板，支持 `Ctrl / ⌘ + F` 按名称或 ID 跳转。
- **8 个新增内置工作流模板**（`kirara_ai/workflow/presets/chat/`，并同步到 `data/workflows/chat/`），把此前没有任何模板覆盖的区块变成开箱可用：
  - `mcp_tools.yaml`「聊天 - 工具调用 (MCP)」：让模型在回答前自动调用 MCP 工具，使用 `mcp:mcp_tool_provider` 与 `internal:chat_completion_with_tools`。
  - `function_calling.yaml`「聊天 - 函数调用」：用「基础：代码」节点把对话记录与 `mcp:mcp_tool_provider` 给出的工具列表打包成一次函数调用请求，由 `internal:chat_function_calling` 返回结果；工具的实际执行需自行接节点，只想开箱可用请改用「聊天 - 工具调用 (MCP)」。
  - `time_aware.yaml`「聊天 - 时间感知」：用 `internal:current_time_block` 在系统提示词里注入实时日期时间。
  - `plain_text.yaml`「聊天 - 纯文本输出」：用 `internal:text_strip_markdown_block` 去掉回复里的 Markdown 标记，适配语音播报与不支持 Markdown 的平台。
  - `sensitive_word_filter.yaml`「聊天 - 敏感词替换」：用 `internal:text_replace_block` 在发送前把指定词替换成安全表述。
  - `long_reply_split.yaml`「聊天 - 长回复分条」：用 `internal:code` 按 `<break>` 把长回复拆成多条消息，并用 `internal:append_im_message` 在末尾追加一条固定提示语。
  - `custom_script.yaml`「聊天 - 自定义脚本」：仅用 `internal:code` 节点处理消息，不接大模型也能回复。
  - `group_mention.yaml`「群聊 - 提及触发」：配合「@机器人」条件，先用 `internal:im_message_to_text` 转纯文本、再用 `internal:text_replace_block` 去掉 @ 符号后交给模型。
- **人设提示词单一来源**：新增 `kirara_ai/workflow/implementations/factories/persona.py`，把此前在多处重复的人设正文收敛为 `DEFAULT_PERSONA_SYSTEM_PROMPT` 与 `DEFAULT_USER_PROMPT_FORMAT`，代码侧工作流工厂统一引用。
- **采样温度可配置且真正生效**：`kirara_ai/workflow/implementations/blocks/llm/chat.py` 新增 `resolve_temperature()`，`ChatCompletion` 与 `ChatCompletionWithTools` 增加「采样温度」区块配置。取值优先级为 节点配置 → 命中规则的 `metadata.temperature` → 不携带（交由模型默认值），合法区间 `0.0~2.0`，超出范围或非数字会被忽略并记录告警。此前 `data/dispatch_rules/rules.yaml` 里的 `metadata.temperature` 只是注释性的字段，从未进入请求。
- **模型回退开关**：`ChatCompletion` 新增 `use_deployment_default_model`（默认 `False`）。关闭时只使用节点上配置的主模型与备用模型，不再在全部失败后静默换成部署默认模型；一个模型都没配置时仍会使用默认模型，否则工作流无法运行。
- **工作流结构预检模块**：新增 `kirara_ai/workflow/core/workflow/validation.py`，改用 `BlockRegistry.get_type_name` 这一新增公开方法（不再触碰私有 `_type_system`），环检测改为带 `MAX_CYCLE_SCAN_DEPTH = 10000` 上限的迭代式 DFS（不再受 Python 递归上限约束），并支持动态端口。
- **内置默认规则模块**：新增 `kirara_ai/workflow/implementations/rules/default_rules.py`，以常量声明优先级档位 `PRIORITY_SYSTEM = 100`、`PRIORITY_COMMAND = 60`、`PRIORITY_CHAT = 30`、`PRIORITY_FALLBACK = 0`，并提供 `build_default_rules()`、`register_system_dispatch_rules()`、`validate_rule_workflows()`；`kirara_ai/entry.py` 在 `load_rules()` 之后注册内置默认规则（同 ID 的用户规则优先保留），随后校验规则引用的工作流。
- **模型目录规范化模块**：新增 `kirara_ai/scheduler/model_catalog.py`，导出 `normalize_detected_models` 与 `model_catalogs_equal`，供定时自动检测与手动检测复用同一套去重、排序与比较逻辑。
- **前端本地存储与前端单元测试**：新增 `webui/src/utils/safe-storage.ts`（`localStorage` 不可用时安全降级）、`webui/vitest.config.ts` 与 `webui/tests/` 下 8 个测试文件。
- **可测试的前端纯函数模块**：从体量过大的画布与规则页里抽出可单测的纯逻辑——`webui/src/components/workflow/workflow-data.ts`、`workflow-node-utils.ts`（节点稳定唯一命名）、`workflow-model-options.ts`（模型槽位识别）、`webui/src/views/workflow/dispatch-preview-utils.ts`（试运行结论文案）、`dispatch-rule-utils.ts`（规则编辑深拷贝草稿，避免「取消」仍污染列表原规则）。

- **统一的深拷贝实现**：新增 `webui/src/utils/deep-clone.ts`，导出唯一的 `deepClone`。它支持嵌套对象与数组、保持 `Date` / `RegExp` / `Map` / `Set` 的类型、保留原型（class 实例不会被降级成裸对象）、处理循环引用，并在**每一层**都先 `toRaw`、对 `ref` 按 `reactive` 的读取语义解包，因此 Vue 响应式代理不会进到 `structuredClone` 里触发 `DataCloneError`。原先规则编辑器用 `JSON.parse(JSON.stringify())`、工作流历史用 `structuredClone` 加 JSON 回退，两份实现语义不同。现在 `dispatch-rule-utils.ts` 的 `cloneDispatchRule` 与 `store/workflow-editor.ts` 的 `cloneHistoryValue` 都只是转调 `deepClone`；`cloneDispatchRule` 的导出名原样保留，任何导入路径都没有变化。`WorkflowCanvas.vue` 复制节点时也改用它拷贝 `data.config`。
- **规则可达性的后端单一实现**：新增 `kirara_ai/workflow/core/dispatch/reachability.py`，导出 `dispatch_order_key`、`sort_rules_in_dispatch_order`、`is_unconditional_group`、`is_catch_all_rule`、`analyze_dispatch_reachability`、`DispatchRuleReachability` 与 `FALLBACK_RULE_TYPE`，并从 `kirara_ai.workflow.core.dispatch` 一并导出。「优先级降序 + 规则 ID 升序」的调度顺序、无条件（兜底）规则判定和遮蔽关系至此只有一处定义：`DispatchRuleRegistry.get_active_rules()` 与调度 API 都改为读取本模块。分析是纯静态的，不需要示例消息，也不会创建条件实例、取样随机概率或访问 IM 实例。
- **可达性接口（纯增量）**：`GET /dispatch/rules` 的响应新增 `reachability` 数组；`DispatchPreviewRuleResult` 新增 `order`、`catch_all`、`unreachable`、`shadowed_by_rule_id` 四个字段（都带默认值，旧客户端不受影响）；新增 `POST /dispatch/reachability`，只做静态可达性分析，可选携带 `draft_rule`——草稿的 `rule_id` 已存在时替换同 ID 规则，否则作为新规则参与排序，因此保存前就能预判遮蔽关系。
- **规则页的遮蔽提示来自服务端**：`DispatchRules.vue` 不再本地推导遮蔽关系，改为渲染 `/dispatch/rules` 返回的 `reachability`（匹配次序、无条件标记、被哪条规则遮蔽），并在编辑弹窗里对草稿发起 300ms 防抖的 `/dispatch/reachability` 请求，只采纳最后一次响应，保持「边改边看」的即时反馈。请求失败只清空提示，不打断编辑。
- **试运行判定文案归位**：判定标签与颜色的唯一实现移到 `webui/src/api/dispatch.ts`，与同类的 `getRuleTypeLabel`（后端枚举 → 中文标签）放在一起，并新增可枚举的 `DISPATCH_PREVIEW_DECISIONS` 常量供测试校验映射完整性；`webui/src/views/workflow/dispatch-preview-utils.ts` 保留为纯再导出的兼容层，原有导入路径不变。
- **首次部署上手指南**：新增 `docs/QUICKSTART.md`，按「启动与首次登录 → 认识内置模板 → 接入聊天平台 → 配置 LLM 后端与模型 → 确认调度规则 → 发测试消息 → 外观设置」的顺序走通第一次部署，包含首次登录即设定密码的机制、随包预设与规则的自动释放、11 个随包 YAML 模板表、模型「先检测目录、再在下拉框里手动选」的机制与 `TaskScheduler` 的周期刷新、四档优先级，以及 6 套配色 × 3 档明暗的说明。
- **可观测性说明**：新增 `docs/OBSERVABILITY.md`，说明日志去向与标签、`@trace_llm_chat` → `LLMTracer` 的落库路径与 `tracing.llm_tracing_content` 开关、`webui/src/views/tracing/` 下的 5 个文件、`POST /workflow/validate` 的请求/响应结构与全部 14 个 issue code、`POST /dispatch/preview` 的判定与新增的 `POST /dispatch/reachability`、画布上的问题角标，并明确列出**不存在**的观测能力（无 `/metrics`、无工作流执行历史、无分布式追踪、无告警、WebUI 控制台看不到 `DEBUG`）。
- **扩展开发指南**：新增 `docs/EXTENDING.md`，分八节讲自定义 Block（含 `ParamMeta` / `options_provider`——它正是「模型手动选择」下拉框的实现机制）、插件（3 个 `@abstractmethod` 生命周期钩子、entry point group 必须逐字写 `chatgpt_mirai.plugins`）、MCP 接入、预设 YAML 结构与坐标、调度规则与 `metadata.temperature`、事件总线、定时任务、全量校验。同时诚实记录当前**没有**的能力：没有消息拦截/中间件插入点、事件监听器不能是 `async def`、`BlockRegistry` 没有 `unregister`、`data/plugins/` 当前不会被自动扫描（只扫包内的 `kirara_ai/plugins/`）、`TaskScheduler` 不是通用任务注册中心、MCP 的 prompts/resources 没有对应 Block。

### Fixed

- **打开工作流节点的重复反射**：Block 类型接口现在缓存输入、输出和配置的静态反射结果，并向每次请求返回独立副本；模型候选等 `options_provider` 仍按请求实时执行，因此手动下拉选模型、定期探测后刷新候选的既有行为不变。
- **画布热路径与小屏遮挡**：节点与边的持久化改由 Vue Flow 的变更事件驱动，不再在每次交互中全量序列化图数据；重叠角标检测改为按 x 轴扫描候选节点，节点列表面板在窄屏限制宽度，避免覆盖画布。
- **配置、模型目录与规则并发写入**：配置保存、LLM 后端更新和调度规则变更分别进行串行化；定时模型探测在重新加载成功后才提交新模型目录，重载失败会恢复旧目录，避免出现保存成功但运行对象未刷新的一致性裂缝。
- **内部文档死链**：修复 WebUI、Web API 与各 API 模块说明中的过期 `framework/`、适配器、监控和开发指南链接，移除不存在的截图占位资源。
- **自动检测与五档模型链兼容性**：定时自动检测现在仅刷新后端当前可发现的模型目录，并识别同 ID 的能力元数据更新；它不会改写任何工作流的主模型或 4 个备用模型。若已配置模型不再被检测到，工作流编辑器对应下拉槽位显示为空，原始配置会保留到用户主动选择替代模型，避免无关保存操作静默删除降级链。
- **手动检测与定时检测一致性**：模型页的“自动检测”接口现在复用相同的目录规范化逻辑，兼容仍返回字符串 ID 的旧适配器、去除重复项并保留提供商返回顺序；它同样只更新当前后端模型目录，不会触及工作流的五档模型配置。
- **模型页无关请求**：编辑模型名称、能力或密钥时不再重复请求“是否支持自动检测”；只有切换适配器类型才会检查，并忽略较慢旧请求的返回，避免状态被覆盖。
- **工作流编辑器状态与撤销完整性**：在节点配置和代码端口变更前记录历史，避免撤销快照已经包含新值；从已有工作流切换到“新建工作流”或其他工作流时会清空并重新初始化画布状态，不会带入上一张图的节点、连线或执行配置。
- **工作流副本完整性**：管理页复制工作流现在保留原有 `config`、`metadata`、节点坐标、区块和连线，副本不再静默丢失执行时限或模板分类信息。
- **统计页自动刷新清理**：离开引导页时释放 LLM 统计的五分钟刷新计时器，保留自动刷新能力并避免重复请求与后台内存泄漏。
- **工作流重命名文件保护**：重命名时除检查内存注册表外，也会检查目标 YAML 路径；未被加载的残留、手工恢复或暂时无效的目标文件不会再被 `os.replace` 静默覆盖，接口返回 409，原工作流保持可用。
- **工作流预设 wheel 构建告警**：排除本地测试产生的 `__pycache__` 命名空间候选，避免打包时将缓存目录误判为 Python 包；预设 YAML 与运行源码仍会完整进入 wheel。
- **模型列表在 WebUI 中空白**：Docker 不再从 npm 的 `latest` 或可变 `beta` 标签拉取前端，而是构建仓库内固定的 `kirara-ai-webui` 0.1.1-beta.3 源码。该版本按 `model.id` / `model.type` / `model.ability` 渲染，与 3.3 后端的 `ModelConfig` 对象数组兼容，因此模型卡片会正常显示名称和能力。
- **MCP 提示词与资源列表接口返回 500**：`/mcp/servers/<id>/prompts` 与 `/mcp/servers/<id>/resources` 此前直接 `jsonify` MCP 原始对象，`Resource.uri` 是 `AnyUrl` 类型无法被 JSON 序列化。现统一转换为 `MCPPromptInfo` / `MCPResourceInfo`，并补上 WebUI 需要的 `id` 字段。
- **HTTPS 实时连接与工作流白屏**：WebSocket 客户端在 HTTPS 页面自动使用 `wss://`，避免浏览器拦截不安全连接；前端构建产物使用内容哈希文件名，避免代理缓存把旧入口文件与新懒加载模块混用。
- **非编辑页面首屏负担与配置日志泄露**：Monaco/VSC 编辑器运行时不再随控制台、设置、模型、插件等路由预加载，仅在编辑器页面按需下载；保存配置时不再向浏览器控制台输出密码相关设置。
- **主题回归修正**：品牌主色恢复为 `#007AFF`（`palettes.ts` 的 `classic` 与 `main.css` 一致）；深色语义色恢复为 `#63e2b7` / `#f3a769` / `#e88080`；`base.css` 重新补上 `prefers-color-scheme: dark` 兜底，与 `.dark` 类并存，`localStorage` 不可用时仍能得到深色底；`App.vue` 里未加 scope 的 `:root` 规则补齐了对应的 `.dark` 版本；`--border-radius` / `--border-radius-small` 修正为 `10px` / `8px`，与 naive-ui 主题覆盖里的取值一致；`forest` 浅色的文字对比度提高。
- **登录页对比度**：`LoginView.vue` 原先只有一层 0.7→0.45 的主色半透明遮罩，白字对比度仅约 2.5:1（松林色板低至 1.8:1）；改为「主色实底渐变 + 固定深色蒙版」两层后，明暗两态下白字对比度均 ≥8:1，稳过 AA。
- **硬编码颜色收敛**：12 个此前遗漏的组件改用设计令牌——`Console.vue`、`nodes/CodeNode.vue`、`MCPDetail.vue`、`ModelListForm.vue`、`ConfigurationList.vue`、`MediaList.vue`、`LLMAdapterConfig.vue`、`FrpServiceCard.vue`、`StatusBar.vue`、`LoginView.vue`、`LLMStatistics.vue`、`TopBar.vue`；图表与节点的分类色补上深色模式取值（`utils/node-colors.ts` 按 `<html>` 上的 `dark` 类选取深色适配值并按明暗分别缓存）；8 个文件补上 `:focus-visible` 键盘焦点样式。
- **必需端口角标被截断**：`CustomNode.vue` 把必填标记 `*` 移出会被 ellipsis 截断的 `.port-label`，改为其兄弟节点并禁止收缩，端口名过长时不会先把 `*` 吃掉。
- **画布性能**：移除 `{ deep: true, flush: 'sync' }` 里直接 `JSON.stringify` 全部节点的监听，改为按 Vue Flow 节点/边变更事件分类保存；选择态和尺寸变更不再触发持久化，节点配置通过显式的变更前快照保存。Monaco 配置写回改为防抖后统一 flush；`isValidConnection` 结果记忆化，避免 vue-flow 对每个端口反复调用时重复计算；打断 props → emit → `initGraphData` 的回环；`NodeListPanel` 的分组结果记忆化。
- **撤销/重做与画布健壮性**：切换工作流时清空撤销/重做栈（原先会把上一张图的区块恢复到当前工作流），并以 `MAX_HISTORY_DEPTH = 50` 限制栈深；`handleRedo` 与 `handleUndo` 行为对称；`/block/types/compatibility` 请求失败时进入降级模式（只提示一次并安排重试），不再锁死画布；导出时延后 `URL.revokeObjectURL`，保证下载完成；导入补上不派发 `cancel` 事件浏览器的兜底，并对端口对不上的连线逐条容错、用结果弹窗列出被丢弃的连线明细。
- **首次部署即可用**：`data/dispatch_rules/rules.yaml` 的私聊规则 `chat_creative` 由 DeepSeek 专用的 `chat:dsr_thinking` 改为通用的 `chat:normal`，全新部署换普通模型时不再输出奇怪内容；`talk_break.yaml` 提示词里写死的 `2025-02-23` 改由 `internal:current_time_block` 提供实时时间；抽卡规则改为整条消息匹配的正则 `^\s*(?:[/.。])?(?:抽卡|十连|单抽)\s*$`，并在说明里写明指令必须单独发送；`normal.yaml`、`dsr_thinking.yaml`、`normal_multimodal.yaml` 里写死的模型 ID（多数用户并未配置）清空为 `''`，改由下拉框手动选择。
- **预设节点坐标重叠**：随包预设与 `data/workflows/chat/` 副本里写死的坐标全部重算并对齐到规则网格，节点框不再互相压叠（自动排版只对没有保存过位置的节点生效，所以 YAML 里的坐标会原样进入画布）；`data/workflows/chat/` 下的副本按设计保留，并由 `tests/test_workflow_presets.py` 校验两处文件内容逐字一致、且任意两个节点框不重叠。
- **失效规则引用导致每条消息报错**：`validate_rule_workflows()` 在启动时把指向已删除工作流的规则降级（改指 `chat:normal` 或禁用），不再在每条私聊消息上抛 `WorkflowNotFoundException`。
- **阻塞式 I/O 与并发安全**：工作流与规则注册表的加载、保存改为 `asyncio.to_thread` 执行，不再在事件循环里做 fsync/复制；两个注册表各自加上 `threading.RLock`，并新增共享的配置写锁 `CONFIG_WRITE_LOCK`，避免后台自动检测改写 `config.llms.api_backends` 与 Web 路由读取交错；`create_rule` 的错误路径不再无条件 `pop`。
- **启动探测抖动**：`kirara_ai/scheduler/scheduler.py` 的启动首轮模型探测在固定 `STARTUP_DELAY_SECONDS = 60` 之外增加 `STARTUP_JITTER_SECONDS = 300` 的随机抖动，避免全新安装在启动 60 秒后同时探测所有启用的后端。
- **锁文件不可移植**：`webui/yarn.lock` 中 44 个 `registry.npmmirror.com` 下载地址改为 `registry.npmjs.org`（integrity 哈希与线上注册表一致），中国大陆以外的环境可以正常执行 `yarn install --frozen-lockfile`。
- **构建缓存入库**：清理并忽略残留的 `.build-tmp/`、`.pytest-tmp/`、`*.tsbuildinfo`；`.gitignore` 补上 `*.py[cod]`，避免落在 `__pycache__/` 之外的 `.pyc` 被误提交；`.dockerignore` 同步排除这些缓存。
- **未实现路由不再误导**：`/im/platforms`、`/llm/backends`、`/llm/models`、`/llm/chat`、`/memory`、`/memory/search` 这 6 个尚未实现的路由此前一律渲染工作流模板页（点进去会看到与菜单名毫不相干的内容），改为新增的 `webui/src/views/ComingSoon.vue` 占位页；路由守卫读取 `token` 时加上 `try/catch`，浏览器禁用本地存储时按未登录处理而不是抛异常。
- **规则遮蔽误判为「永不触发」**：规则页原先的本地 `isCatchAll` 只看条件组里是否含 `fallback`，因此把「`and` 组内除 `fallback` 外还有其他条件」的规则也当成无条件规则，给排在它之后的规则打上错误的「永远不会被触发」结论。可达性收敛到后端后，`is_unconditional_group()` 严格对应 `CombinedDispatchRule.match()` 的组内逻辑——`and` 组必须**全部**是兜底条件才恒成立，`or` 组含一个即可——`tests/test_dispatch_reachability.py` 为这种混合 `and` 组补了回归测试。已禁用的规则也不再遮蔽后续规则或被标记为不可达。

### Changed

- **Docker 运行依赖可复现性**：镜像构建读取已提交的 `uv.lock`，导出无开发依赖的带哈希 requirements，再以无依赖模式安装项目 wheel；前端声明 Yarn `1.22.22`，并新增不会自动修改源码的 `yarn lint:check`。
- **发布构建一致性**：Windows 快速启动包改为构建仓库内与 Docker 镜像相同的固定 WebUI 源码，不再下载独立仓库的最新前端产物；发布附件工作流明确申请 `contents: write`，并将前端 TypeScript 编译器升级至与 Vue 3.5 类型声明兼容的 5.2 系列。

- **默认聊天工作流与实际使用配置对齐**：`data/workflows/chat/` 下 5 个工作流按当前线上配置更新，部署后无需在 WebUI 里手动调整。`normal.yaml` 换为「刘思思（全能专家版）」人设并配置 `grok-4.5` 主模型加 4 个备用模型；`dsr_thinking.yaml` 精简为专家视角提示词并配置 `claude-opus-4-8` 主模型加 4 个备用模型；`normal_multimodal.yaml` 精简提示词并指定 `gemini-3-pro-preview`；三个文件同时清理了重复的 `connected_to` 连线（同一对端口被声明两次）。`memory_store.yaml` 与 `talk_break.yaml` 内容与线上一致，未改动。所有文件区块数量保持不变，无功能块增减。

- **Docker Hub 自动发布流程**：`.github/workflows/docker-latest.yml` 会为每个非草稿 GitHub Release 构建并推送 `<Release 标签>` 镜像；只有 GitHub 标记为当前 Latest 的正式 Release 才额外更新 `latest`。预发布和非 Latest Release 仍可获得自己的版本镜像，不会覆盖稳定版。`.github/workflows/docker-tag.yml` 不再监听 Tag 推送，仅作为需要单独重建版本标签时的手动应急入口。工作流增加并发控制及 Docker Hub 账号、令牌、镜像名的前置校验。
- **Compose 部署来源**：`docker-compose.yml` 和示例文件改用环境变量解析镜像；示例 Compose 移除源码热挂载，生产部署以镜像内容为准，降低“仓库已更新、容器仍运行旧代码”的风险。
- **前端构建来源**：Docker 新增固定 WebUI 构建阶段，使用项目内 `webui/` 源码和锁文件生成静态资源。锁文件统一改为 npm 官方源，避免把本机不可用的镜像地址带进 Docker 构建。
- **发布构建版本标识**：Docker 与 Windows 快速启动包在构建时注入 GitHub Release 标签，镜像内不再依赖 `.git` 获取前端版本；状态栏会显示发布版本，前端更新比较兼容 `3.3.0a5` 这类预发布编号。
- **Windows 发布资格**：预发布与非 Latest 正式 Release 不再触发 Windows 快速启动包构建；手动触发仅保留临时 Actions Artifact，只有 GitHub 当前 Latest 的正式 Release 才会生成并上传 Windows Release 附件。
- **发布前快速门禁**：新增 `Release Preflight`，在 `main`、`master` 的推送和 Pull Request 上并行执行发布契约检查与 WebUI 类型检查、生产构建；该检查不构建镜像、不发布 Windows 包、不使用部署密钥。
- **部署说明**：README 增加 Docker Hub、环境变量、默认工作流初始化和完整备份恢复说明，强调已有 `data/` 卷不会被新镜像自动覆盖。
- **忽略规则**：`.gitignore` 忽略本机 `.env`，防止部署参数和敏感配置被误提交。
- **采样参数类型放宽**：`LLMChatRequest` 的 `temperature`、`top_p`、`frequency_penalty`、`presence_penalty` 由 `Optional[int]` 改为 `Optional[float]`，`0.7` 这类取值终于可以表达；`max_tokens` 是 token 个数，仍保持 `int`。pydantic 会把 `int` 自动转成 `float`，旧配置里写 `1` 仍然通过校验，向后兼容。
- **默认触发规则重排优先级**：`data/dispatch_rules/rules.yaml` 的优先级由原先普遍的 5/10 改为按档位区分——系统指令（`/help`、`/清空记忆`）100、游戏指令（骰子、抽卡）60、聊天（群聊、私聊）30、兜底（记录聊天内容）0，与 `default_rules.py` 里的常量一致；同时补齐每条规则的 `metadata.category` / `metadata.permission` 并把描述改写为可直接照做的说明。
- **前端 CI 门禁扩展**：`Release Preflight` 的 WebUI 作业改名为「WebUI type check, unit tests and production build」，在类型检查与生产构建之间加入 `yarn test:unit`；`webui/vitest.config.ts` 补上与 `vite.config.ts` 一致的 `@` → `src` 别名（此前非纯类型的 `@/` 导入会解析失败）。
- **锁文件下载源检查改为白名单**：`tests/test_webui_build_contract.py` 里的注册表守卫从黑名单改为白名单（只允许 `registry.npmjs.org`），黑名单只能挡住已知镜像，`npmmirror.com` 正是这样漏进锁文件的。
- **wheel 打包排除字节码**：`pyproject.toml` 增加 `exclude = ["*.__pycache__", "*.__pycache__.*"]` 与 `[tool.setuptools.exclude-package-data]`，避免本机字节码进入 wheel 并消除包发现告警。

- **圆角阶梯统一**：`webui/src/assets/main.css` 用一套权威阶梯取代此前 8 种互相竞争的字面量（4 / 6 / 8 / 10 / 12 / 16 / 20 / 24px）：`--radius-xs` 4px（内联小件、圆角容器的内层元素）、`--radius-sm` 8px（交互控件与紧凑表面，含画布节点）、`--radius-md` 12px（卡片、面板、列表项，默认档）、`--radius-lg` 16px（模态、抽屉、页面级主卡片）、`--radius-xl` 24px（登录页外框这类整屏级容器）、`--radius-pill` 999px（胶囊标签、状态徽标）。各档的角色分工与嵌套原则（内层 ≤ 外层，一般降一档）以中文注释块写在令牌旁边。四个历史令牌名全部保留为别名（`--border-radius`→md、`--border-radius-small`→sm、`--border-radius-large`→lg、`--border-radius-pill`→pill），没有重命名或删除任何令牌。
- **色板圆角由系数派生**：`ThemeShape` 新增 `radiusScale`，`getRadii(scale)` 按 `RADIUS_BASE`（4/8/12/16/24）缩放整套阶梯并设 2px 下限，`pill` 档不参与缩放；`radiusShape(scale)` 顺带填好三个历史字段（`borderRadius`/`borderRadiusSmall`/`borderRadiusLarge` = md/sm/lg），因此单个色板不可能只改一档而让梯度断裂。各色板系数：`classic` 1、`graphite` 0.5、`midnight` 0.75、`forest` 1.25、`contrast` 0.375（最方正——弱视用户依赖清晰的矩形边界定位控件）、`oled` 1.125（纯黑底上 1px 描边几乎不可见，层级只能靠形状表达）。基准梯把通用档由 10px 提到 12px、大档由 12px 提到 16px，小档仍是 8px。
- **naive-ui 与 CSS 共用同一份圆角**：`App.vue` 的 `themeShape` 与 `stores/theme.ts` 都消费 `getRadiiForSeed`，运行时写入 `--radius-*` 与四个历史别名，两侧不会再各说各话；并补上 `Tag: { borderRadius: pill }` 覆盖，与 `main.css` 的 `.n-tag` 一致。
- **全站改用圆角令牌**：114 处圆角字面量替换为令牌引用（`webui/src/` 下现有 156 处 `var(--radius-*)`），并对 19 处内层元素按嵌套原则降档并就地写明理由（`.statistic-icon`、`.status-icon`、`.stat-icon`、`.code-preview`、`.tool-response pre`、`.n-descriptions`、`.trace-table`、`.rule-item`、`.adapter-info`、`.config-form` 等）。仅 `LLMView` 的 `.custom-modal .n-card` 变化超过 4px（10px → 16px），原因是模态内的卡片就是模态自身的表面，不该与全局 `.n-modal` 渲染出不同圆角。保留 10 处就地注明的例外：3 处 `border-radius: 0`（贴边满铺的面板与窄屏登录容器，任何圆角都会露出底色）、4 处 2~3px 的发丝级预览装饰、1 处 `50%` 正圆，以及 2 处 ECharts 的数值型 `borderRadius`（canvas 绘制，无法读取 CSS 变量）。
- **色板预览体现形状性格**：`AppearanceCard` 的色板缩略图圆角取 `getRadii(scale * 0.5)`，让每套色板的方正/圆润差异在选择时就能看出来。

- **后端全量用例成为每个 PR 的门禁**：`.github/workflows/run-tests.yml` 此前**只有** `workflow_dispatch`，全量后端用例从不在 PR 上自动运行；现在触发条件为 `pull_request` / `push` / `merge_group`（均限定 `main`、`master`）+ `workflow_dispatch`。工作流拆成四个作业：`static`（`compileall` 语法门禁为阻塞项，pyflakes 与 isort 以 `continue-on-error` 输出到 Step Summary）、`backend`（ubuntu + Python 3.11，与 Dockerfile 运行时一致，`uv sync --frozen` 装锁定依赖后跑全量用例，上传 Codecov 覆盖率/测试结果与 JUnit artifact；`CODECOV_TOKEN` 缺失时自动跳过上传，fork PR 不会红）、`backend-matrix`（ubuntu×3.10、ubuntu×3.13、windows×3.13，`PYTHONUTF8` 保证中文断言在 Windows 上不炸）、`docker-image`（`needs: backend`，构建镜像并断言 wheel 的 package-data、wheel 可独立导入、镜像内 `/app/web` 有前端产物，再在镜像里跑一遍用例）。后两个重活默认只在推送默认分支 / 合并队列 / 手动触发时运行，PR 上打 `ci:full` 标签可强制拉起。并发组按 `github.ref`，仅 `pull_request` 事件取消进行中的运行。
- **发布前门禁扩容**：`release-preflight.yml` 补上 `merge_group` 触发，并新增非阻塞的 `webui-lint` 作业（`npx eslint src/`，输出报告与 artifact——此前 CI 从未跑过任何前端 lint）；`webui` 作业把类型检查、单元测试、生产构建拆成三个独立 step，Node 20 + yarn 缓存。`project_check.yml` 触发为 `push` / `merge_group` / `workflow_dispatch`（mypy 报告 + 自动开 issue，并发组不取消以免留下半截报告）；`pr_review.yml` 仍为 `pull_request_target`（文件内已就地记录它的 "pwn request" 暴露面与为何把 `actions/checkout` 停在 v6）；`docker-latest.yml` 与 `quickstart-windows.yml` 为 `workflow_dispatch` + `release: published`；`docker-tag.yml` 仅 `workflow_dispatch`（必填 `image_tag`）；`stale.yml` 为 `workflow_dispatch` + 每日定时。
- **发布镜像前必须跑全量用例**：`docker-latest.yml` 与 `docker-tag.yml` 各新增 `verify` 作业（`uv sync --frozen` + `pytest ./tests -q`），构建/推送作业 `needs: verify`。理由写在工作流里：镜像一旦推到 Docker Hub 无法收回，而 release 是从 tag 构建的，tag 上的内容未必等于当初通过 CI 的 commit。草稿 release 下 `verify` 直接跳过，`docker` 作业随之跳过，与原有「草稿不发布」行为一致。镜像构建使用 registry buildcache（`type=registry,ref=<镜像>:buildcache`），CI 的镜像校验作业使用 GitHub Actions 缓存（`type=gha`）。
- **Windows 快速启动包与 PR 门禁跑同一组前端检查**：`quickstart-windows.yml` 的前端构建步骤由只跑 `yarn build` 改为 `yarn type-check` → `yarn test:unit` → `yarn build`，类型错误与单测回归不会再被直接打进用户下载的压缩包。
- **发布契约测试同步到新架构**：`tests/test_release_workflow_contract.py` 新增两条契约——`run-tests.yml` 必须同时具备 `pull_request` / `push` / `merge_group` 触发、必须用 `uv sync --frozen` 安装并执行 `pytest ./tests -q`、并发取消策略必须是「仅 PR 取消」；`docker-latest.yml` 与 `docker-tag.yml` 都必须有跑全量用例的验证步骤且发布作业 `needs: verify`。同时把 Windows 快速启动包的 `actions/setup-node@v4` 断言放宽为 `actions/setup-node@v`（这条契约要守的是「与镜像用同一套受版本控制的前端源码构建」，钉死 action 大版本只会让例行升级把测试变成噪音），并要求快速启动包与 `release-preflight.yml` 都包含 `yarn test:unit`。

### Tests

- 新增 `tests/utils/test_block_registry.py` 的静态元数据缓存与副本隔离回归、`webui/tests/workflow-layout.test.ts` 的画布重叠识别回归，并扩充 `tests/test_webui_build_contract.py` 的 Docker/Yarn 发布契约。
- 后端测试现为 425 个，`.venv/Scripts/python.exe -m pytest ./tests -q` 全部通过（系统 `python` 未安装 pytest，必须使用虚拟环境解释器）。`tests/` 顶层测试文件由 12 个增至 16 个。
- 新增 `tests/test_workflow_presets.py`（预设可发现、名称/说明/节点齐全、区块类型与端口可解析、坐标不重叠、随包预设与 `data/workflows` 副本一致、不写死模型 ID、不写死日期）、`tests/test_default_dispatch_rules.py`、`tests/test_model_catalog.py`、`tests/test_workflow_preset_deletions.py`、`tests/web/api/dispatch/test_dispatch.py`。
- 扩充 `tests/system_blocks/llm/test_chat.py`（采样温度解析与模型回退开关）、`tests/test_webui_build_contract.py`（锁文件白名单、wheel 预设声明、Docker 默认数据不含测试夹具）、`tests/web/api/workflow/test_workflow.py`。
- 新增前端单元测试：`webui/tests/` 下 10 个文件共 40 个用例全部通过（`theme-boot-table.test.ts` 校验 `index.html` 启动色板表与 `palettes.ts` 不漂移，另有 `safe-storage`、`workflow-data`、`workflow-editor`、`workflow-model-options`、`workflow-node-utils`、`dispatch-preview-utils`、`dispatch-rule-utils`、`deep-clone`、`workflow-layout`）。

**当前实测数字**：

- 后端 `.venv/Scripts/python.exe -m pytest ./tests -q` → **425 passed**；`tests/` 顶层测试文件 16 个。
- 前端 `cd webui && npx vitest run --config vitest.config.ts` → **10 个文件 / 40 个用例全部通过**（新增 `workflow-layout.test.ts`）。
- 前端类型检查 `cd webui && npx vue-tsc --noEmit` → 退出码 0。
- 发布契约 `pytest tests/test_release_workflow_contract.py tests/test_webui_build_contract.py -q` → **29 passed**。
- 新增 `tests/test_dispatch_reachability.py`：覆盖调度排序键、`or` / `and` 组的无条件判定、已禁用的无条件规则不遮蔽后续规则，以及「`and` 组混合 `fallback` 与其他条件时不得被判为兜底」这条前端旧实现的回归。
- 新增 `webui/tests/deep-clone.test.ts`：嵌套结构、`Date` / `RegExp` / `Map` / `Set`、原始值与 `undefined`、循环引用，以及「逐层解包 Vue 响应式代理」这条关键回归（`structuredClone` 只在顶层 `toRaw` 时嵌套层仍是 Proxy）。
- `webui/tests/dispatch-preview-utils.test.ts` 改为直接解析 `kirara_ai/web/api/dispatch/models.py` 里 `decision: Literal[...]` 的取值集合，断言前端标签映射与后端枚举完全一致且没有缺项——写死字符串相等的断言发现不了「后端新增判定、前端渲染出 undefined」。

### Security

- 备份包可能包含模型密钥、机器人令牌和 Web 凭据相关设置；README 与备份说明明确要求仅保存到可信位置，禁止提交到 GitHub、Docker Hub 或分享给他人。
- `/backup` 页面所有接口调用均带 Bearer 令牌，页面文件本身不含密码或密钥；回滚包下载沿用后端已有的 `auth_token` 查询参数鉴权，未新增任何免鉴权入口。
- 恢复流程只接受通过结构与清单校验的备份包，并拒绝越界路径、异常压缩包和不受支持的内容。

## [3.3.0a2] - 相对 3.2.0 的产品升级

### Breaking Changes

- **运行环境升级**：最低 Python 版本从 `3.9` 提升至 `3.10`，以支持 MCP 与代码诊断依赖。旧版 Python 环境必须先升级再部署。
- **新增运行依赖**：加入 `mcp>=1.6,<2`、`pygls>=1.3,<2`、`jedi`、`pyflakes`。其中 MCP 与 pygls 锁定在 1.x 兼容范围，避免上游 2.x 移除既有接口造成运行失败。
- **私聊默认路径调整**：默认“私聊 AI 对话”规则由 `chat:normal` 改为 `chat:dsr_thinking`；旧实例若保留已有 `data/` 卷，仍会继续使用自己的历史规则，需通过导入、管理界面或新数据目录完成迁移。

### Added

- **MCP 服务器接入与管理**
  - 新增 MCP 配置模型、服务器生命周期管理和连接状态管理，支持从配置加载、连接、断开、重连、统计和工具缓存。
  - 新增 MCP Web API，可管理服务器、查看状态/工具/提示词/资源、检测 ID、启动/停止并调用工具。
  - 新增工作流 `MCPToolProvider` 区块，使工作流能够把 MCP 工具作为可调用能力提供给模型与后续区块。
  - 在应用初始化与退出阶段接入 MCP 管理器，确保服务启动时加载、连接，停止时有序断开。

- **模型能力与模型类型体系**
  - 新增统一的模型类型与能力声明：文本对话、嵌入、重排序、图片、音频和工具调用能力可被配置、注册表、API 与工作流共同识别。
  - 新增 Embedding、Rerank、Tool 三类格式契约，支持模型适配器以统一方式暴露向量化、重排序及工具调用能力。
  - 新增 Voyage 适配器与适配器公共工具模块，并扩充 OpenAI、Gemini、Ollama 等适配器的测试桩和契约测试。

- **工作流编辑诊断能力**
  - 新增 Python 语言服务及诊断组件，覆盖导入检查、Jedi 语法/补全检查、Pyflakes 静态检查和必需函数检查。
  - 工作流区块 API 可返回更准确的 Python 编辑诊断，降低自定义区块在保存后才发现语法或依赖错误的概率。

- **模型自动探测调度**
  - 调度器新增按每个 LLM 后端 `auto_detect_interval_days` 定期刷新模型列表的机制。
  - 自动探测状态写入 `data/auto_detect_state.json`，刷新后重载后端并保存配置；Web LLM API 同步支持相关配置与状态。

- **默认工作流补齐**
  - 新增 `data/workflows/chat/normal.yaml`，补齐标准文本聊天工作流定义，避免规则引用存在而默认工作流文件缺失。
  - 深度思考工作流移除固定的单一模型名，改由模型配置/区块参数决定，便于不同部署环境复用。

- **记忆组合策略**
  - 新增记忆 Composer、Decomposer 策略抽象和 XML 辅助处理，改善复合记忆的组装、拆分与结构化解析能力。
  - 配套增加记忆策略、记忆管理器和兼容性回归测试。

### Changed

- **聊天与工具调用的容错能力**
  - `ChatCompletion` 与函数调用区块支持主模型优先级和最多四个备用模型；主模型请求失败时按顺序切换备用模型并记录结果。
  - 工具调用区块、系统区块注册和工作流构建流程同步适配模型能力选择，减少“模型存在但不具备所需能力”的执行错误。

- **LLM 注册、配置与适配器实现**
  - 重构 LLM 适配器基类、请求/响应/消息格式、管理器和注册表，使模型能力、工具调用和多模态内容可在各适配器之间一致传递。
  - 更新阿里云、Claude、DeepSeek、Gemini、MiniMax、Moonshot、Ollama、OpenAI、OpenRouter、火山引擎等预置适配器及初始化逻辑，增强接口兼容性与模型发现一致性。
  - 更新 LLM Web API 模型与路由，配合能力字段、后端配置和自动探测状态提供更完整的前后端接口。

- **工作流引擎与分发规则**
  - 更新区块注册表、类型系统、工作流基类与构建器、执行器及异常模型，提升区块参数解析、执行链路和错误处理的一致性。
  - 更新分发器、分发注册表及消息/发送者/系统规则实现，使规则匹配、优先级和兜底工作流与新的默认工作流保持一致。
  - 更新工作流 Web API 的请求/响应模型和路由，支持新版工作流与区块能力。

- **记忆、媒体与多模态消息链路**
  - 更新记忆内置组合、记忆管理器和聊天记忆区块，使记忆结构与新的组合策略兼容。
  - 更新媒体对象、元数据、媒体管理器和媒体 API，改进媒体资源在聊天/模型消息中的表达和管理。
  - 聊天消息可向模型传递图片等多模态内容；媒体清理与调度逻辑同步调整。

- **聊天平台适配体验**
  - QQ 适配器增加文本渲染辅助，改善 Markdown/表格等消息呈现。
  - Telegram 适配器改进长消息分段，减少超出平台单条消息长度导致的发送失败。
  - 企业微信回调与消息委托逻辑调整，改善重复回调、超时和纯文本分段场景的稳定性。

- **应用与系统 API**
  - 应用启动、配置加载和全局配置模型更新，以装配 MCP、调度、模型能力和新版工作流组件。
  - 系统、区块、聊天平台、媒体、模型和工作流 API 均随配置与能力模型更新；系统 API 在本地增强中额外提供备份恢复功能。

- **Windows 快速启动与协作流程**
  - 更新 Windows 快速启动脚本以及 PR 审查工作流，使本地启动与协作检查适配新版运行条件。

### Fixed

- 修正默认工作流与分发规则之间的不完整对应关系：标准聊天工作流文件已补齐，私聊规则不再依赖写死的特定本地模型配置。
- 修正模型能力、请求格式和适配器实现之间的兼容性边界，并以新增的回归测试覆盖常见模型协议场景。
- 增强工作流执行、规则匹配、媒体处理和平台消息发送的错误处理与容错路径，降低单一模型、长消息或不兼容输入使整条链路中断的概率。
- 修正 Windows Quickstart 对已下线 FFmpeg 固定版本路径的依赖，改用官方 release essentials 稳定下载别名、SHA-256 校验和动态目录定位，避免上游版本更新后出现 404 或解压目录名不匹配。

### Tests

- 测试文件由 39 个增至50 个。
- 新增 LLM 适配器模拟服务和 OpenAI、Gemini、Ollama、Voyage 适配器测试。
- 新增 MCP 服务器、模型/记忆兼容性、记忆 Composer/Decomposer、媒体 API、备份服务和备份 API 测试。
- 更新聊天区块、LLM API、系统 API、追踪和记忆测试，覆盖新版行为与回归场景。

## Migration Guide

1. **先升级运行环境**：使用 Python `3.10+` 重建虚拟环境并按 `pyproject.toml` 安装依赖；不要将旧环境中的依赖目录直接复制到新版本。
2. **部署镜像前配置来源**：服务器的 `.env` 必须设置 `DOCKERHUB_IMAGE`，Compose 才会拉取你自己发布到 Docker Hub 的镜像。不要把真实令牌或密钥写入仓库。
3. **理解数据卷优先级**：容器挂载的已有 `data/` 目录优先于镜像内默认数据，因此升级镜像不会自动补齐或覆盖旧工作流、触发规则。需要默认模板时，请使用新空数据目录，或先导出再通过备份恢复功能导入；不要直接删除生产数据。
4. **检查私聊规则**：如需使用新版深度思考私聊路径，请确认 `data/dispatch_rules/rules.yaml` 的私聊规则指向 `chat:dsr_thinking`，并确保目标工作流已成功加载。
5. **恢复备份后重启**：导入 `.kirara-backup.zip` 成功后必须重启服务，才能使配置、工作流、MCP 连接与缓存全部重新加载。
6. **审核 MCP 配置**：MCP 服务可能执行外部命令或访问外部服务。导入配置前应核对服务器命令、环境变量与网络地址，只启用可信来源。

## File Scope

下列范围覆盖本次相对所有可复现功能差异；同一目录内列出的文件均为实现、配置或测试的一部分。

- **构建与部署**：`.env.example`、`.gitignore`、`pyproject.toml`、`docker-compose.yml`、`docker-compose.yml.example`、`.github/workflows/docker-latest.yml`、`.github/workflows/pr_review.yml`、`.github/quickstarts/windows/scripts/启动.cmd`、`README.md`。
- **默认数据**：`data/dispatch_rules/rules.yaml`、`data/workflows/chat/dsr_thinking.yaml`、`data/workflows/chat/normal.yaml`、`data/workflows/chat/normal_multimodal.yaml`、`kirara_ai/workflow/presets/chat/normal_multimodal.yaml`；测试工作流位于 `data/workflows/test-group/`。
- **应用、配置与调度**：`kirara_ai/entry.py`、`kirara_ai/config/`、`kirara_ai/scheduler/scheduler.py`、`kirara_ai/web/app.py`。
- **LLM 与预置适配器**：`kirara_ai/llm/`、`kirara_ai/plugins/llm_preset_adapters/`，包括新增的模型类型、Embedding/Rerank/Tool 格式与 Voyage 适配器。
- **MCP 与工作流能力**：`kirara_ai/mcp_module/`、`kirara_ai/web/api/mcp/`、`kirara_ai/workflow/core/`、`kirara_ai/workflow/implementations/blocks/mcp/`、`kirara_ai/workflow/implementations/blocks/llm/chat.py`、`kirara_ai/workflow/implementations/blocks/system_blocks.py`。
- **记忆、媒体与聊天平台**：`kirara_ai/memory/`、`kirara_ai/media/`、`kirara_ai/plugins/im_qqbot_adapter/`、`kirara_ai/plugins/im_telegram_adapter/`、`kirara_ai/plugins/im_wecom_adapter/`、`kirara_ai/im/text_render.py`。
- **Web API 与编辑诊断**：`kirara_ai/web/api/block/`、`kirara_ai/web/api/im/`、`kirara_ai/web/api/llm/`、`kirara_ai/web/api/media/`、`kirara_ai/web/api/system/`、`kirara_ai/web/api/workflow/`。
- **备份恢复与质量保证**：`kirara_ai/backup/`、`tests/backup/`、`tests/llm_adapters/`、`tests/memory/`、`tests/web/api/`、`tests/test_mcp_server.py`、`tests/test_compatibility_regressions.py`，以及 `docs/superpowers/` 下的备份设计与实施文档。
- **可达性与前端去重**：`kirara_ai/workflow/core/dispatch/reachability.py`、`kirara_ai/workflow/core/dispatch/registry.py`、`kirara_ai/web/api/dispatch/`、`webui/src/utils/deep-clone.ts`、`webui/src/api/dispatch.ts`、`webui/src/views/workflow/DispatchRules.vue`、`webui/src/views/workflow/dispatch-rule-utils.ts`、`webui/src/views/workflow/dispatch-preview-utils.ts`、`webui/src/store/workflow-editor.ts`、`tests/test_dispatch_reachability.py`、`webui/tests/deep-clone.test.ts`。
- **圆角与主题形状**：`webui/src/assets/main.css`、`webui/src/theme/palettes.ts`、`webui/src/stores/theme.ts`、`webui/src/App.vue`、`webui/src/views/settings/components/AppearanceCard.vue`。
- **CI 门禁**：`.github/workflows/run-tests.yml`、`release-preflight.yml`、`project_check.yml`、`docker-latest.yml`、`docker-tag.yml`、`quickstart-windows.yml`、`stale.yml`、`pr_review.yml`、`tests/test_release_workflow_contract.py`。
- **文档**：`docs/QUICKSTART.md`、`docs/OBSERVABILITY.md`、`docs/EXTENDING.md`。
- **人设单一来源与可访问色**：`kirara_ai/workflow/implementations/factories/persona.py`、`kirara_ai/workflow/implementations/factories/default_factory.py`、`tests/test_workflow_factories.py`、`tests/system_blocks/llm/test_chat.py`、`webui/src/assets/base.css`、`webui/src/components/workflow/NodeConfigPanel.vue`、`webui/src/components/form/DynamicConfigForm.vue`、`webui/src/views/im/IMView.vue`、`webui/src/views/im/IMAdapterDetail.vue`、`webui/src/views/mcp/MCPList.vue`、`webui/src/views/plugins/PluginList.vue`、`webui/src/views/plugins/PluginMarket.vue`、`webui/src/views/workflow/WorkflowTemplates.vue`。
