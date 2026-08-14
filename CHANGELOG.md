# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的分类方式，记录**源代码、默认配置、部署文件、文档与测试**变化。

比较基线为`3.2.0`，比较目标为当前工作区的 `3.3.0a4`。本文件是源码变更说明，不代表已经创建 GitHub Release、推送镜像或发布版本。

> 不纳入比较：`.git/`、编辑器缓存、测试缓存、运行日志、`data/db/`、记忆/媒体/插件运行数据、虚拟环境和任何本地密钥或密码文件。这些内容会随机器和使用状态变化，不属于可复现的产品功能。

## [Unreleased] - 本地增强

### Added

- **完整备份与恢复服务**：新增 `kirara_ai.backup.BackupService`，可导出便携式 `.kirara-backup.zip`，覆盖系统与 Web 设置、模型与机器人配置、工作流、触发规则、数据库、记忆、媒体、插件、字体及自动探测状态。
- **安全恢复机制**：导入前校验归档清单、文件哈希、路径、容量、压缩比和符号链接；写入前自动创建回滚包，验证或恢复失败时保留原数据，避免半恢复状态。
- **备份管理 API**：在系统 API 中提供备份创建、列表、下载、导入、删除和恢复状态接口；恢复成功后明确要求重启服务以重新加载配置与运行对象。
- **备份测试与设计文档**：新增备份服务/API 测试，以及完整备份恢复的设计和实施文档，便于后续维护与审计。
- **可配置镜像示例**：新增 `.env.example`，使用 `DOCKERHUB_IMAGE` 显式指定部署镜像，避免新部署默认依赖第三方固定镜像名。
- **MCP 资源读取与提示词采样 API**：新增 `GET /mcp/servers/<id>/resources/<resource_id>` 与 `POST /mcp/servers/<id>/prompts/sample`，补齐 WebUI MCP 详情页「查看资源」「采样提示」两个按钮所需的后端接口。
- **备份与恢复图形界面**：新增后端自带页面 `GET /backup`（源文件 `kirara_ai/web/static/backup.html`），提供导出、导入前检查、恢复和回滚包下载四组操作。该页面不属于 `kirara-ai-webui` 前端项目，因此不受 WebUI 版本影响，部署后立即可用；会自动复用浏览器中 WebUI 的登录令牌，也支持在页面内单独登录。页面本身不含任何凭据，所有接口调用仍需 Bearer 令牌。
- **WebUI 内置备份入口**：将 `kirara-ai-webui` 0.1.1-beta.3 源码受版本控制地纳入 `webui/`，在「系统设置」增加与原有界面协调的「备份与恢复」标签页。页面直接使用既有备份 API，支持导出、导入前检查、二次确认恢复和不含令牌 URL 的回滚包下载；`/backup` 旧入口继续保留。
- **打包声明**：`MANIFEST.in` 与 `pyproject.toml` 增加 `kirara_ai/web/static` 的分发声明，确保 wheel 与 Docker 镜像内包含后端自带页面。

### Fixed

- **模型列表在 WebUI 中空白**：Docker 不再从 npm 的 `latest` 或可变 `beta` 标签拉取前端，而是构建仓库内固定的 `kirara-ai-webui` 0.1.1-beta.3 源码。该版本按 `model.id` / `model.type` / `model.ability` 渲染，与 3.3 后端的 `ModelConfig` 对象数组兼容，因此模型卡片会正常显示名称和能力。
- **MCP 提示词与资源列表接口返回 500**：`/mcp/servers/<id>/prompts` 与 `/mcp/servers/<id>/resources` 此前直接 `jsonify` MCP 原始对象，`Resource.uri` 是 `AnyUrl` 类型无法被 JSON 序列化。现统一转换为 `MCPPromptInfo` / `MCPResourceInfo`，并补上 WebUI 需要的 `id` 字段。

### Changed

- **发布构建一致性**：Windows 快速启动包改为构建仓库内与 Docker 镜像相同的固定 WebUI 源码，不再下载独立仓库的最新前端产物；发布附件工作流明确申请 `contents: write`，并将前端 TypeScript 编译器升级至与 Vue 3.5 类型声明兼容的 5.2 系列。

- **默认聊天工作流与实际使用配置对齐**：`data/workflows/chat/` 下 5 个工作流按当前线上配置更新，部署后无需在 WebUI 里手动调整。`normal.yaml` 换为「刘思思（全能专家版）」人设并配置 `grok-4.5` 主模型加 4 个备用模型；`dsr_thinking.yaml` 精简为专家视角提示词并配置 `claude-opus-4-8` 主模型加 4 个备用模型；`normal_multimodal.yaml` 精简提示词并指定 `gemini-3-pro-preview`；三个文件同时清理了重复的 `connected_to` 连线（同一对端口被声明两次）。`memory_store.yaml` 与 `talk_break.yaml` 内容与线上一致，未改动。所有文件区块数量保持不变，无功能块增减。

- **Docker Hub 自动发布流程**：`.github/workflows/docker-latest.yml` 会为每个非草稿 GitHub Release 构建并推送 `<Release 标签>` 镜像；只有 GitHub 标记为当前 Latest 的正式 Release 才额外更新 `latest`。预发布和非 Latest Release 仍可获得自己的版本镜像，不会覆盖稳定版。`.github/workflows/docker-tag.yml` 不再监听 Tag 推送，仅作为需要单独重建版本标签时的手动应急入口。工作流增加并发控制及 Docker Hub 账号、令牌、镜像名的前置校验。
- **Compose 部署来源**：`docker-compose.yml` 和示例文件改用环境变量解析镜像；示例 Compose 移除源码热挂载，生产部署以镜像内容为准，降低“仓库已更新、容器仍运行旧代码”的风险。
- **前端构建来源**：Docker 新增固定 WebUI 构建阶段，使用项目内 `webui/` 源码和锁文件生成静态资源。锁文件统一改为 npm 官方源，避免把本机不可用的镜像地址带进 Docker 构建。
- **部署说明**：README 增加 Docker Hub、环境变量、默认工作流初始化和完整备份恢复说明，强调已有 `data/` 卷不会被新镜像自动覆盖。
- **忽略规则**：`.gitignore` 忽略本机 `.env`，防止部署参数和敏感配置被误提交。

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
- **默认数据**：`data/dispatch_rules/rules.yaml`、`data/workflows/chat/dsr_thinking.yaml`、`data/workflows/chat/normal.yaml`；测试工作流位于 `data/workflows/test-group/`。
- **应用、配置与调度**：`kirara_ai/entry.py`、`kirara_ai/config/`、`kirara_ai/scheduler/scheduler.py`、`kirara_ai/web/app.py`。
- **LLM 与预置适配器**：`kirara_ai/llm/`、`kirara_ai/plugins/llm_preset_adapters/`，包括新增的模型类型、Embedding/Rerank/Tool 格式与 Voyage 适配器。
- **MCP 与工作流能力**：`kirara_ai/mcp_module/`、`kirara_ai/web/api/mcp/`、`kirara_ai/workflow/core/`、`kirara_ai/workflow/implementations/blocks/mcp/`、`kirara_ai/workflow/implementations/blocks/llm/chat.py`、`kirara_ai/workflow/implementations/blocks/system_blocks.py`。
- **记忆、媒体与聊天平台**：`kirara_ai/memory/`、`kirara_ai/media/`、`kirara_ai/plugins/im_qqbot_adapter/`、`kirara_ai/plugins/im_telegram_adapter/`、`kirara_ai/plugins/im_wecom_adapter/`、`kirara_ai/im/text_render.py`。
- **Web API 与编辑诊断**：`kirara_ai/web/api/block/`、`kirara_ai/web/api/im/`、`kirara_ai/web/api/llm/`、`kirara_ai/web/api/media/`、`kirara_ai/web/api/system/`、`kirara_ai/web/api/workflow/`。
- **备份恢复与质量保证**：`kirara_ai/backup/`、`tests/backup/`、`tests/llm_adapters/`、`tests/memory/`、`tests/web/api/`、`tests/test_mcp_server.py`、`tests/test_compatibility_regressions.py`，以及 `docs/superpowers/` 下的备份设计与实施文档。
