# 完整备份与恢复设计

## 目标

为 Kirara AI 增加受认证保护的完整数据导入、导出和回滚能力，使用户能够把一套实例的前端设置、后端配置、工作流、规则、插件和运行数据迁移到另一套实例。

## 已确认的范围

- 采用完整备份，导出 `config.yaml` 中的机器人令牌、LLM API Key、MCP Header 和环境变量、Web 密钥等敏感值。
- 备份包含 `config.yaml`、`dispatch_rules`、`workflows`、`memory`、`media`、`db`、`plugins`、`fonts`、`web` 和 `auto_detect_state.json`（存在时）。
- 不备份 `venv`、日志、Python 缓存、`.pyc`、`__pycache__`、已有回滚备份和 Git 文件。
- 导入完成后要求重启。进程运行期间不尝试热加载配置、工作流和插件，避免内存注册表与磁盘状态不一致。

## 包格式

文件扩展名为 `.kirara-backup.zip`。归档顶层包含 `manifest.json` 和各个允许的数据路径。清单记录格式版本、创建时间、应用版本、文件清单、文件 SHA-256、压缩前总大小和备份范围。

备份是完整的明文 ZIP，以兼容标准操作系统工具。因此下载文件必须只保存到可信本地位置，绝不能提交到 GitHub、Docker Hub 或发送给无关人员。API 必须经过现有登录认证。

## 导入安全性与一致性

导入服务先在系统临时目录中解压，拒绝不在白名单中的路径、绝对路径、路径穿越、符号链接、重复成员、过大归档、过多文件和异常压缩比。随后验证清单格式、所有 SHA-256 校验值、YAML 语法和 `GlobalConfig` Pydantic 模型。

导入前将当前可迁移数据写入 `data/backups/` 的自动回滚包。服务把已验证内容放在同一文件系统的暂存目录，通过逐个顶层组件替换完成应用；若任何替换失败，则将已替换组件恢复到导入前状态。回滚文件本身永不被导入包覆盖。

## API

- `GET /api/system/backups/export`：下载完整备份。
- `POST /api/system/backups/inspect`：上传 `backup` 文件字段，只校验并返回清单及范围，不写入数据。
- `POST /api/system/backups/import`：上传 `backup` 文件字段，校验、创建自动回滚包并应用，返回 `restart_required: true` 和回滚包标识。
- `GET /api/system/backups/rollbacks`：列出本地自动回滚包。
- `GET /api/system/backups/rollbacks/<backup_name>`：下载指定回滚包。

所有路由复用 `require_auth`，不会在响应、日志或错误消息中回显任何配置值或密钥。

## 前端边界

当前仓库只有后端和 Docker 下载的预编译 WebUI，不含可编辑的 WebUI 源码。此变更提供稳定 API；要在菜单中增加文件选择、导出和导入按钮，需在 `kirara-ai-webui` 源码仓库调用上述 API 并重新构建前端包。

## 验证

测试必须覆盖完整导出/导入往返、清单校验失败、ZIP 路径穿越、非法配置、导入失败自动恢复和 API 认证边界。现有配置、工作流和规则 API 不改变其 URL、函数或数据格式。
