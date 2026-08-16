# 首次部署上手指南

本文面向刚把 Kirara AI 跑起来、还没配过任何东西的部署者。按顺序走完六步，就能收到第一条机器人回复。

所有页面路径都指 WebUI（默认 `http://127.0.0.1:8080/`，端口取自 `data/config.yaml` 的 `web.port`，见 `kirara_ai/web/app.py`）。后端 API 的统一前缀是 `/backend-api/api`（`kirara_ai/web/app.py:285`）。

---

## 第 0 步：启动与首次登录

```bash
python -m kirara_ai
```

启动日志里会打印本地访问地址（`kirara_ai/entry.py` 的 `run_application`）。

首次打开 WebUI 时没有密码：在登录页直接输入你想用的密码即可完成设置——`POST /auth/login` 在 `auth_service.is_first_time()` 为真时会把这次输入的密码保存下来（`kirara_ai/web/auth/routes.py:16`）。`data/config.yaml` 里的 `web.secret_key` 若为空或仍是示例占位值，启动时会自动生成随机密钥并落盘（`kirara_ai/entry.py`），无需手工处理。

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

打开「IM 平台」，选一个适配器（`telegram`、`http_legacy`、`qqbot`、`wecom` 等由 `kirara_ai/plugins/` 下的内置插件注册），填入平台令牌，保存并启动。

只想先本地验证、不想注册任何平台账号时，用 `http_legacy` 适配器最快：它把 `POST /v1/chat` 挂在主 Web 服务上（`kirara_ai/plugins/im_http_legacy_adapter/adapter.py` 的 `setup_routes`），可以用 curl 直接发消息。

---

## 第 3 步：配置 LLM 后端与模型

打开「模型 → 供应商」，新建一个后端：

- **名称**：后端标识，如 `deepseek-official`
- **适配器类型**：`openai` / `deepseek` / `claude` / `gemini` / `ollama` 等（由 `kirara_ai/plugins/llm_preset_adapters/` 注册）
- **配置**：`api_key`、`api_base` 等字段由适配器的配置类决定，界面根据 `GET /llm/types/<adapter_type>/config-schema` 动态渲染

**模型不会自动出现在工作流里，这一点必须理解清楚：**

1. 填好密钥后点「自动检测」。它调用 `GET /llm/backends/<backend_name>/auto-detect-models`，向供应商拉取当前可用的模型目录，经 `normalize_detected_models()`（`kirara_ai/scheduler/model_catalog.py`）去重、补全能力位后写入这个后端的 `models` 列表。适配器不支持自动检测时，界面会提示你手动添加模型。
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

相关接口：`GET /llm/auto-detect-schedule` 查看各后端计划与上次执行时间，`PUT /llm/backends/<backend_name>/auto-detect-schedule` 改间隔天数，`POST /llm/auto-detect-schedule/run` 立刻强制跑一轮。

> 注意：截至当前版本，这三个接口**没有对应的 WebUI 界面**，只能用 API 调用（下面第 6 步给了可直接复制的命令）。间隔天数也可以直接改 `data/config.yaml` 里后端的 `auto_detect_interval_days` 后重启。

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

改完先别急着发消息——用「试运行消息」按钮验证匹配结果，它调用 `POST /dispatch/preview`，按真实调度顺序解释每条规则会不会命中，但**不执行任何工作流**。详见 `docs/OBSERVABILITY.md`。

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

1. 「控制台」页看实时日志，或直接看 `logs/log_YYYY-MM-DD.log`
2. 「LLM 追踪」页看这次请求有没有发出去、报了什么错
3. 用 `POST /dispatch/preview` 确认消息到底命中了哪条规则

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
# 系统状态（运行时长、活跃适配器/后端数、已加载插件数、工作流数）
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/system/status

# 各后端的模型自动检测计划与上次执行时间
curl -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule

# 立刻强制跑一轮模型自动检测
curl -X POST -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8080/backend-api/api/llm/auto-detect-schedule/run
```

`<token>` 是 `POST /auth/login` 返回的 `access_token`，有效期 1 天。

---

## 下一步

- 想加自己的节点、插件、MCP 服务器、工作流模板或调度规则：`docs/EXTENDING.md`
- 想搞清楚系统在干什么、出错怎么定位：`docs/OBSERVABILITY.md`
