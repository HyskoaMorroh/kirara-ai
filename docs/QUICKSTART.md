# 首次部署上手指南

本文面向刚把 Kirara AI 跑起来、还没配过任何东西的部署者。按顺序走完六步，就能收到第一条机器人回复。

所有页面路径都指 WebUI（默认 `http://127.0.0.1:8080/`，端口取自 `data/config.yaml` 的 `web.port`，见 `kirara_ai/web/app.py`）。后端 API 的统一前缀是 `/backend-api/api`（`kirara_ai/web/app.py:285`）。

---

## 第 0 步：启动与首次登录

```bash
python -m kirara_ai
```

启动日志里会打印本地访问地址（`kirara_ai/entry.py` 的 `run_application`）。

首次打开 WebUI 时没有密码：在登录页直接输入你想用的密码即可完成设置——`POST /backend-api/api/auth/login` 在 `auth_service.is_first_time()` 为真时会把这次输入的密码保存下来（`kirara_ai/web/auth/routes.py:16`）。`data/config.yaml` 里的 `web.secret_key` 若为空或仍是示例占位值，启动时会自动生成随机密钥并落盘（`kirara_ai/entry.py`），无需手工处理。

首次启动还会自动做三件事：

| 动作 | 代码位置 |
| --- | --- |
| 把随包预设工作流释放到 `data/workflows/` | `WorkflowRegistry._extract_bundled_presets` |
| 在没有规则文件时注册内置默认调度规则 | `register_system_dispatch_rules`（`kirara_ai/workflow/implementations/rules/default_rules.py`） |
| 校验规则引用的工作流是否存在，失效的降级或禁用 | `validate_rule_workflows` |

因此**全新部署开箱就能对话**，你只需要补上一个 LLM 后端。

---

## 第 1 步：认识内置工作流模板

打开「工作流 → 工作流模板」。除了代码内置的 `chat:normal`、`chat:memory_store`、`system:help`、`system:clear_memory`、`game:dice`、`game:gacha` 之外，还有 11 个随包分发的 YAML 模板，源文件在 `kirara_ai/workflow/presets/chat/`：

| 文件 | 名称 | 说明 |
| --- | --- | --- |
| `mcp_tools.yaml` | 聊天 - 工具调用 (MCP) | 让模型在回答前自动调用 MCP 工具（联网搜索、读写文件等）。请先在「MCP」页面添加服务器，再在「MCP: 提供工具」节点里勾选要开放的工具，并为「LLM: 执行对话并调用工具」选择一个支持函数调用的模型。 |
| `function_calling.yaml` | 聊天 - 函数调用 | 手工搭建函数调用流程的模板：用「基础：代码」节点把对话记录与工具列表打包成一次函数调用请求，再由「LLM: 函数调用」返回结果。模型请求调用工具时会走 tool_call 分支，工具的实际执行需要你自己接节点；只想开箱可用请改用「聊天 - 工具调用 (MCP)」。 |
| `time_aware.yaml` | 聊天 - 时间感知 | 在系统提示词里注入实时日期时间，让模型知道「今天是几号、现在几点」。适合需要回答时间、排期、节日相关问题的场景。 |
| `plain_text.yaml` | 聊天 - 纯文本输出 | 去掉模型回复里的 Markdown 标记（#、**、列表、表格等），输出适合语音播报与不支持 Markdown 的 IM 平台阅读的纯文本。 |
| `sensitive_word_filter.yaml` | 聊天 - 敏感词替换 | 在把模型回复发出去之前，把指定的词替换成安全表述，适合公开群聊的合规需求。请在「基础：替换文本」节点里填写要替换的词，在旁边的文本节点里填写替换后的内容，可按需串接多个替换节点。 |
| `long_reply_split.yaml` | 聊天 - 长回复分条 | 把模型的长回复按 `<break>` 拆成多条消息发送，并在末尾追加一条固定的提示语，避免大段文字在群里刷屏。 |
| `custom_script.yaml` | 聊天 - 自定义脚本 | 用「基础：代码」节点写一小段 Python 处理消息，不接大模型也能回复。默认脚本统计消息字数并原样回显，改写 code 参数即可做关键词回复、格式转换、调用自家接口等。 |
| `group_mention.yaml` | 群聊 - 提及触发 | 只在群里被 @ 时才回答。配合调度规则里的「@机器人」条件使用：先把消息转成纯文本并去掉 @ 符号，再交给模型，避免把「@机器人」本身当成提问内容。 |
| `normal_multimodal.yaml` | 聊天 - 原生多模态对话 | 基于原生多模态能力的图文对话，适用于本身支持图片输入/回答的模型，在读取记忆时会恢复原来的媒体资源 |
| `talk_break.yaml` | 聊天 - 自定义分段 | 使用 `<break>` 作为关键词，让 AI 分段回复的工作流 |
| `dsr_thinking.yaml` | 聊天 - 深度思考 | DeepSeek 思考模型聊天，隐藏 `<think>` 标签内容 |

第一次部署建议先用代码内置的 `chat:normal`（「聊天 - 角色扮演」），它不需要任何额外配置。

模板页的「以此为模板」会把模板完整复制一份到你自己的分组，改副本不影响原模板，升级也不会覆盖你的副本。

> 想删掉某个不用的预设？直接在 WebUI 删除即可。删除会写入 `data/workflows/.preset_tombstones.json`，下次启动不会再把它释放出来（`WorkflowRegistry.mark_preset_deleted`）。

---

## 第 2 步：接入聊天平台

打开「IM 平台」，选一个适配器（`telegram`、`http_legacy`、`onebot`、`qqbot`、`wecom` 等由 `kirara_ai/plugins/` 下的内置插件注册），填入平台令牌，保存并启动。

只想先本地验证、不想注册任何平台账号时，用 `http_legacy` 适配器最快：它把 `POST /v1/chat` 挂在主 Web 服务上（`kirara_ai/plugins/im_http_legacy_adapter/adapter.py` 的 `setup_routes`），可以用 curl 直接发消息。

### 接 QQ（OneBot）

QQ 走 OneBot V11 反向 WebSocket，且**方向与其他平台相反**：Kirara 是服务端，
OneBot 实现（LLOneBot / NapCat 等）主动连过来。因此保存适配器后不会立刻「已连接」，
而是先显示「等待连接」，直到对方接入。

三件事按顺序做：

1. 在适配器详情页复制 `websocket_url`（形如 `/im/websocket/onebot/<随机段>/ws`），
   拼上你的公网地址填进 OneBot 实现的反向 WebSocket 配置。
2. 两侧的访问 Token 必须一致。填错时状态会显示**「凭据被拒」**并给出原因，
   这种情况再等也不会自己好，必须改配置。
3. QQ 侧容器从启动到登录完成通常要 30–90 秒，这段时间显示「等待连接」是正常的。

两个后来才会遇到的状态，先知道比事后查更省事：

- **「正在重连」不是故障。** `docker compose down && pull && up -d` 之后
  OneBot 实现会自己回连，这段时间什么都不用做。它与「已断开」是两回事——
  后者才需要你动手。超过重连宽限期（默认 45 秒）仍没连上才转为「已断开」。
- **二维码总是过期时，先点那一行的「刷新扫码状态」。** 有效期实测 120 秒，
  比「看一眼、去拿手机、回来扫」这个动作序列短，屏幕上那张常常已经不是最新的。
  该按钮只重读上游日志（二维码由 OneBot 实现自己生成），看不到扫码信息时是
  没配 `qr_login_log_path`，不是故障。

状态含义、断开原因码对照、数据目录清单与 Compose 验收矩阵见
[`QQ_ONEBOT_OPERATIONS.md`](QQ_ONEBOT_OPERATIONS.md)。

---

## 第 3 步：配置 LLM 后端与模型

打开「模型 → 供应商」，新建一个后端：

- **名称**：后端标识，如 `deepseek-official`
- **适配器类型**：`openai` / `deepseek` / `claude` / `gemini` / `ollama` 等（由 `kirara_ai/plugins/llm_preset_adapters/` 注册）
- **配置**：`api_key`、`api_base` 等字段由适配器的配置类决定，界面根据 `GET /backend-api/api/llm/types/<adapter_type>/config-schema` 动态渲染

**模型不会自动出现在工作流里，这一点必须理解清楚：**

1. 填好密钥后点「自动检测」。它调用 `GET /backend-api/api/llm/backends/<backend_name>/auto-detect-models`，向供应商拉取当前可用的模型目录，经 `normalize_detected_models()`（`kirara_ai/scheduler/model_catalog.py`）去重、补全能力位后写入这个后端的 `models` 列表。适配器不支持自动检测时，界面会提示你手动添加模型。
2. 自动检测**只刷新后端的模型目录**，它不会改写任何工作流里的模型选择。要让某个模型真正被调用，还得进工作流编辑器，在「LLM: 执行对话」节点的「模型 ID1」下拉框里**手动选**一个。
3. 这个下拉框的候选项来自 `model_name_options_provider()`（`kirara_ai/workflow/implementations/blocks/llm/chat.py:24`），只列出类型为 LLM 且具备 `TextChat` 能力的模型，并按 ID 升序排列，所以顺序稳定，不会因为一次检测就抖动。
4. 「LLM: 执行对话」最多可配 5 档模型（主模型 + 4 个备用），前一个失败时依次降级。若某个已配置的模型在新一轮检测后不再存在，编辑器里的对应槽位会显示为空，原配置保留，直到你主动改选——不会被静默替换。

### 模型目录的定期自动刷新

后台调度器 `TaskScheduler`（`kirara_ai/scheduler/scheduler.py`）会按每个后端的 `auto_detect_interval_days`（`LLMBackendConfig` 字段，默认 5 天，填 0 表示关闭）周期性重跑自动检测：

| 行为 | 取值 |
| --- | --- |
| 后台检查周期 | `CHECK_INTERVAL_SECONDS = 86400`（24 小时检查一次谁到期） |
| 启动后首轮延迟 | `STARTUP_DELAY_SECONDS`（60 秒）+ `random.uniform(0, STARTUP_JITTER_SECONDS)`（0–300 秒随机抖动，避免全新安装时所有后端同时探测） |
| 上次检测时间记录 | `data/auto_detect_state.json` |
| 目录有变化时 | 写回 `data/config.yaml`（带备份），并重载该后端 |

界面在**「模型 → 自动检测计划」**（`/llm/auto-detect`）。这一页给出每个后端的间隔天数、上次成功时间、下一轮预计时刻与当前模型数，可以直接改间隔（保存即生效，不需要重启），也可以「立即检测全部」跑一轮。

三处刻意的呈现，都是为了不给出错误的安心：

- **上次成功为「—」表示从未成功检测过**，与「很久以前检测过」是两件不同的事。启动后首轮有 60 秒基础延迟加 0–300 秒随机抖动；长期为「—」要去查该后端的凭据与网络。
- **后台调度循环没在运行时页首会显著提示**。那种情况下所有间隔配置都不会触发，逐行显示「每 5 天」是一句谎话。
- **「立即检测全部」带确认**：它会访问每一个上游、消耗配额，并在目录有变化时改写 `data/config.yaml`（后端先备份），不是一次只读刷新。

对应接口：`GET /backend-api/api/llm/auto-detect-schedule` 查看各后端计划与上次执行时间，`PUT /backend-api/api/llm/backends/<backend_name>/auto-detect-schedule` 改间隔天数，`POST /backend-api/api/llm/auto-detect-schedule/run` 立刻强制跑一轮。后两项会写配置/状态，强制检测还会访问远端；不要把它们放进默认只读 smoke（下面第 6 步给了可直接复制的命令）。间隔天数也可以直接改 `data/config.yaml` 里后端的 `auto_detect_interval_days` 后重启。

### 回复生成模式：非流式（默认）、流式聚合与逐步推送

`data/config.yaml` 的 `agent_runtime.reply_stream_mode` 有三个取值：

| 取值 | 行为 | 何时用 |
|---|---|---|
| `off`（默认） | 非流式请求，一次拿到完整回复 | 默认；与既有部署行为完全一致 |
| `aggregate` | 以**流式**方式向上游取回内容，再整段投递 | 想让流式超时与首字节前的故障转移生效时 |
| `incremental` | 在 `aggregate` 之上，边生成边把内容推给用户 | 渠道能改写已交付内容时（Telegram 编辑已发消息、WebUI 在线对话走 SSE） |

`aggregate` **不是**逐字推送。它的实际收益是三条容错路径开始生效：

- `stream_first_byte_timeout_seconds`（默认 60 秒）：等首个数据块的上限，超时可切换
  下一个供应商。默认给得宽，是因为开了最大强度思考的上游会先做一段长推理再吐第一个
  字节——按十几秒判定的话，用户看到的是「上游没响应」，而实际上它还在想；
- `stream_idle_timeout_seconds`（默认 120 秒）：识别中途卡住的流。同理，推理块之间
  的停顿是正常的，不是卡死；
- **首字节之前**的故障转移是安全的（还没有任何内容发给用户）；一旦已经产出内容，
  系统不会切换并拼接，避免用户看到两段重复回复。

`incremental` 需要渠道能**改写已经交付出去的内容**。两个渠道具备：

- **Telegram**（`editMessageText`）：先发一条「正在生成回复…」占位消息，随生成不断
  改写它，改写间隔 1.2 秒以避开限流。
- **WebUI 的在线对话**（SSE）：`POST /backend-api/api/llm/chat/stream` 把一轮回复送成
  `start` / `delta` / `done` 事件，一条事件就是一次追加，界面上文字逐段出现。
  这条路**默认开启**，不需要改配置；界面右上角有「流式 / 非流式」开关可以现场对比。

QQ / OneBot 与企业微信没有等价接口，在那些渠道上这一档**自动退化成 `aggregate`**
（仍走流式请求，仍有超时保护，用户仍只看到一条完整回复），不会报错也不会变成几十条
碎片消息。

> 反向代理关掉分块传输时 SSE 会被攒成一整块再发出，表现是「等很久然后整段出现」。
> 后端已经带上 `X-Accel-Buffering: no`（nginx 的约定）；仍有问题时把
> `channel_reply_stream_modes.webui` 显式配成 `off`，可以确认问题在代理而不在项目。

增量投递成功之后**不会**再整段发一次——否则同一段回复会出现两遍。反之，占位消息
发不出去、改写被限流、或渠道不支持编辑时，整段投递照常兜底，用户不会什么都收不到。

按渠道覆盖用 `agent_runtime.channel_reply_stream_modes`，键是渠道类型
（`telegram` / `onebot` / `wecom` / `qqbot` / `webui` / `http`）：

```yaml
agent_runtime:
  reply_stream_mode: aggregate          # 进程默认
  channel_reply_stream_modes:
    telegram: incremental               # 编辑已发出的那条消息
    onebot: aggregate                   # QQ 没有等价能力，写 incremental 也会退回这一档
```

`webui` 不写也会走 SSE：那条路由本身就是按流式协议接住回复的，所以「客户端已经打开
了一条流」这件事本身就是一次 `incremental` 声明（优先级在 Agent 与渠道**之后**、
进程默认之前）。要关掉它就把 `webui` 显式写成 `off`。

单个 Agent 还可以再覆盖一层（`reply_stream_mode`，多一个取值 `inherit` 表示跟随
上层，也是默认值）。优先级是 **Agent 声明 > 渠道默认 > 进程默认**；无法识别的取值
一律当「跟随上层」，绝不当开启。入口在「Agent 管理 → 回复取回方式」。

前提是所用适配器实现了流式接口。**OpenAI 兼容、Claude、Gemini、Ollama 均已实现**
（OpenAI 兼容那一族包含 DeepSeek、Moonshot、Mistral、OpenRouter、SiliconFlow、
硅基/火山/阿里云等预设）；未实现的适配器会自动回退到非流式路径，不会报错。

**带工具的请求也能走流式**，前提是适配器的流式解析会累积 `delta.tool_calls`——
OpenAI 兼容那一族已经会，Claude / Gemini / Ollama 目前只解析文本增量，
带工具时仍走非流式。这个区分很重要：绑了 MCP 的 Agent 在第 0 轮就带着工具，
如果一律按非流式处理，它**永远**拿不到上面那三项保护，而那是本项目最常见的形态。

### 单轮总时间预算：`turn_deadline_seconds`

`agent_runtime.turn_deadline_seconds` 默认 `0`（不设总预算，与既有行为一致）。
设成正数后，这一轮对话共享**一个**递减的时间预算与**一个**取消信号：

- 预算作为 `deadline_seconds` 下传给模型调用，超时不再等待；
- 预算耗尽会置位取消信号，并**真的断开在途的上游 HTTP 连接**（四家预置适配器的
  流式与非流式路径都已接入）。这一点很重要：只让本进程停止等待、不断开连接的话，
  上游会把整段内容生成完并照旧计费——日志里写着已取消，账单上一分不少；
- 多轮工具调用**共享**同一份预算，不会每轮重新给满——否则「总预算」名不副实。

它约束的是整轮。单次请求的超时仍由各 Provider 自己的
`non_stream_timeout_seconds`（默认 600 秒）/ `stream_*_timeout_seconds` 决定，
两者是不同层级：
Provider 超时决定「这一次上游调用等多久」，`turn_deadline_seconds` 决定
「这一整轮（含工具往返）最多花多久」。

建议值：交互式聊天 60–120 秒。设得太小会在工具轮较多的 Agent 上提前截断，
因此默认不开启，由部署者按自己的 Agent 复杂度决定。

### 想在 QQ 里用 MCP 工具或 command Hook：声明你自己的渠道身份

受保护的插件能力（MCP 工具、command 型 Hook、需要确认的宿主操作）由
「调用者是不是这个 Agent 的创建者」把关，而这个身份此前只由 WebUI 的登录态提供。
IM 入站链路没有它，因此**聊天侧所有人都拿不到工具，包括你本人**。

**在界面上声明**（推荐）：「系统设置 → Agent 运行时 → 创建者渠道身份」，
点「添加创建者身份」，选渠道类型、填你自己的用户标识（QQ 号 / Telegram 用户 ID），
保存后**重启服务**。渠道类型是下拉而不是自由文本：后端只接受六个渠道名，
写错会静默匹配不上任何消息。

也可以直接改 `data/config.yaml`，两者是同一份配置：

```yaml
agent_runtime:
  creator_channel_identities:
    - channel_type: onebot        # webui / http / onebot / qqbot / telegram / wecom
      sender_scope: "10001"       # 你自己的 QQ 号，不是机器人的
      # account_scope: "20002"    # 可选：限定只经由这个机器人账号才算
      # adapter_instance: bot-a   # 可选：限定适配器实例
      # allow_group_chat: false   # 群聊里是否生效，默认关闭
```

默认为空表，不声明就是升级前的行为。两点值得留意：

- **群聊默认不生效。** 群里所有人都看得到你发的指令并照抄；照抄的人身份不同
  因而拿不到工具，但把宿主操作暴露在多人可见的会话里是另一回事。
- **渠道与发送者标识一起比对。** QQ 号和 Telegram 用户 ID 可能撞号，
  只比一个等于把另一个渠道的同号用户也放进来。
- **未声明的人照常得到正常回复**，只是工具列表为空——不是报错，也不是拒绝服务。

完整边界见 `docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md` 第 8 节。

---

## 第 4 步：确认调度规则

打开「工作流 → 调度规则」。全新部署会看到 8 条内置规则（`build_default_rules()`），优先级分五档，数字越大越先匹配：

| 优先级 | 档位 | 内置规则 |
| --- | --- | --- |
| 100 | 系统命令 | `system_help`（`/help`）、`system_clear_memory`（`/清空记忆`） |
| 60 | 精确指令 | `game_dice`（`.roll 1d100`）、`game_gacha`（「抽卡」「十连」「单抽」） |
| 30 | 对话 | `chat_normal`（群聊 `/chat` 或 @机器人）、`chat_creative`（私聊直接对话） |
| 15 | 宽松提及 | `game_gacha_mention`（群聊未用 `/chat` 也未 @机器人时，句中提到「抽卡」即触发） |
| 0 | 兜底 | `fallback`（静默记录聊天内容，不回复） |

`chat_normal` 与 `chat_creative` 都指向 `chat:normal`。想换成第 1 步里的某个模板，编辑规则把 `workflow_id` 改过去即可；你在 WebUI 里改过的规则会写入 `data/dispatch_rules/rules.yaml`，之后启动都以你的版本为准（`register_system_dispatch_rules` 在检测到已有规则文件时直接返回，不再注入默认值）。

改完先别急着发消息——用「试运行消息」按钮验证匹配结果，它调用 `POST /backend-api/api/dispatch/preview`，按真实调度顺序解释每条规则会不会命中，但**不执行任何工作流**。详见 `docs/OBSERVABILITY.md`。

---

## 第 5 步：发一条测试消息

在你接入的平台上：

- 私聊：直接发「你好」，命中 `chat_creative` → `chat:normal`
- 群聊：发 `/chat 你好`，或 @机器人 后说话，命中 `chat_normal`
- 任意场景：发 `/help`，命中 `system_help`，机器人会根据当前已启用的规则自动生成一份帮助（`GenerateHelp` 区块）

用 `http_legacy` 适配器时可以直接用 curl（`api_key` 在适配器配置里填过才需要 `Authorization`）：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"friend-test","username":"tester","message":"你好"}'
```

没有回复时按这个顺序查：

1. 「控制台」页看实时日志，或直接看 `<DATA_PATH>/logs/log_YYYY-MM-DD.log`（默认 `data/logs/`）
2. 「LLM 追踪」页看这次请求有没有发出去、报了什么错
3. 用 `POST /backend-api/api/dispatch/preview` 确认消息到底命中了哪条规则

---

## 第 6 步（可选）：外观设置

「设置 → 外观」提供明暗模式与配色方案两组开关（`webui/src/views/settings/components/AppearanceCard.vue`）。

**明暗模式**三档：跟随系统 / 浅色 / 深色。选「跟随系统」时会监听 `prefers-color-scheme` 实时切换。

**配色方案**共 6 套（`webui/src/theme/palettes.ts` 的 `palettes` 数组，顺序即界面展示顺序）：

| key | 名称 | 说明 |
| --- | --- | --- |
| `classic` | 经典蓝 | 项目原生蓝白配色，主色与旧版 `#007AFF` 一致 |
| `graphite` | 石墨灰 | GitHub 风格中性灰蓝，弱饱和、久看不累 |
| `midnight` | 午夜蓝 | One Dark 风格深蓝紫，青色主调，节点辨识度高 |
| `forest` | 松林绿 | Solarized 风格暖调低蓝光，夜间护眼 |
| `contrast` | 高对比 | 为低视力与强光环境准备，全部语义色满足 WCAG AA |
| `oled` | 纯黑 | OLED 真黑省电配色，深色底为 `#000000` |

前四套是原有色板，`contrast` 与 `oled` 是本轮新增。每套都自带浅色/深色两份取值，并各有自己的圆角、字号与控件高度（`ThemeShape`），所以换色板不只是换颜色。选择通过 `localStorage` 的 `themeMode` 与 `themePalette` 两个键持久化，刷新后保留；状态栏另有一个明暗快速切换按钮，方便在工作流画布这类全屏页面直接切。

工作流画布的底色、网格点与节点配色也走同一套 CSS 变量，因此换色板后画布会跟着变。

---

## 常用校验命令

```bash
# 本地 readiness（配置、工作流、规则目标、IM/LLM/MCP；不会回显密钥）
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/system/readiness

# 系统状态（运行时长、活跃适配器/后端数、已加载插件数、工作流数）
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/system/status

# 各后端的模型自动检测计划与上次执行时间
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule

# 有副作用：访问远端并可能更新模型目录/状态，人工确认后才运行
curl -X POST -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule/run
```

`<token>` 是 `POST /backend-api/api/auth/login` 返回的 `access_token`，有效期 1 天。

---

## 下一步

- QQ / OneBot 接入后的状态判读、目录挂载与重启恢复：`docs/QQ_ONEBOT_OPERATIONS.md`
- 想加自己的节点、插件、MCP 服务器、工作流模板或调度规则：`docs/EXTENDING.md`
- 想搞清楚系统在干什么、出错怎么定位：`docs/OBSERVABILITY.md`
- 从旧实例升级或回滚：`docs/UPGRADING.md`
- 组合 Agent/Skill、声明 Hook 或接入 MCP：`docs/AGENTS_SKILLS_HOOKS_MCP_GUIDE.md`
