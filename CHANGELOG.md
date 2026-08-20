# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的分类方式，记录**源代码、默认配置、部署文件、文档与测试**变化。

比较基线为 `3.2.0`，比较目标为 `3.3.0b8`。本文件记录源码与发布行为的对应关系；实际发布状态以 GitHub 和镜像仓库为准。

> 不纳入比较：`.git/`、编辑器缓存、测试缓存、运行日志、`data/db/`、记忆/媒体/插件运行数据、虚拟环境和任何本地密钥或密码文件。这些内容会随机器和使用状态变化，不属于可复现的产品功能。

## [Unreleased]

### Added

- **多模态模板恢复角色扮演人设**：`chat/normal_multimodal.yaml` 的 `system_prompt` 此前只剩 `# Information` / `# Memories` 骨架，模型因此不再扮演角色。现补回与 `factories/persona.py` 的 `DEFAULT_PERSONA_SYSTEM_PROMPT` 一致的人设正文（1044 字符逐字节核对）。随包预设与 `data/workflows` 两份副本同步修改，`model_name` 继续留空由用户在下拉框选择。`chat/dsr_thinking.yaml` 不在此列：它针对思维链模型专门重写为 `# Rules` 指令集（控制标记不可见、代词记忆关联、拒绝客套、专家视角），是有意的差异，保持不变。

### Fixed

- **正文对比度达到 WCAG AA**：12 处正文说明文字读的是 `--text-color-tertiary`（浅色 `#909399`，对比度仅 2.87:1）或 `--n-text-color-3`（同色值），均低于 AA 要求的 4.5:1。现按 `main.css` 既有规则「background / border 用原键，color 用 `-text` 键」改读 `--text-color-tertiary-text`（浅色 4.62:1、深色 5.66:1）。该达标令牌此前定义了却无人使用。作为填充与描边使用的 3 处（状态标签底色、滚动条滑块）保持原键不变。
- **body 行高读排版令牌**：`base.css` 的 `body` 写死 `line-height: 1.6`，与 `--line-height-normal: 1.5` 长期不一致，使「用了令牌的文字」与「继承来的文字」行距对不齐。现改读令牌并保留原字面量作回退。`font-size` 保持 15px 不动——令牌 `--font-size-base` 是 14px，改读令牌会让全站继承文本整体缩小一档。

### Changed

- **仓库自身链接指向本仓库**：`pyproject.toml` 的 `Homepage`、`Bug Tracker`，以及 README 的 star / license / CI / codecov 徽章、问题列表与 star-history 均改指 `HyskoaMorroh/kirara-ai`。发布身份与外部资源保持上游不变：PyPI 包名 `kirara-ai`（`entry.py`、`system/routes.py`、`system/utils.py` 依赖它做自更新与版本读取）、npm 包名 `kirara-ai-webui`、文档站 `kirara-docs.app.lss233.com`、插件市场 API、Docker Hub 拉取徽章、作者署名与贡献者名单。
- **社区入口统一为 Telegram**：README 的 6 个 QQ 交流群、机器人调试群与开发者交流群链接（多数已标注「已满」）替换为单一入口 <https://t.me/kirara_ai>。

### Tests

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
