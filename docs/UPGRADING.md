# Kirara AI 升级指南

本文用于把已有 Kirara AI 实例升级到仓库当前版本。版本号只从 `pyproject.toml` 读取，文档与制品中的版本标识由工具在发布前动态生成，不在本指南中固定写入。核心原则是：先保留可独立恢复的 A 数据副本，再让 B 读取副本；确认替代成功前，不删除旧程序、旧数据或备份。

## 0. 版本维护规则

版本只在仓库根目录的 `pyproject.toml` `[project]` `version` 字段中确定。以后发布新版本时，不要逐个手改 `webui/package.json`、`uv.lock`、Yarn 锁文件、Docker、CI、源码或文档；在仓库根目录先预览，再执行智能升级。工具默认优先使用当前分支 upstream，其次使用 `origin` 或唯一远端；无法确定远端、远端 Tag 查询失败时会拒绝继续。离线操作必须显式使用 `--local-only`：

```powershell
$versionTool = ".venv-win/Scripts/python.exe"
$currentVersion = (& $versionTool scripts/version.py get).Trim()
$releasePlan = & $versionTool scripts/version.py plan --remote origin --json | ConvertFrom-Json
$nextVersion = $releasePlan.candidate
Write-Host "Current version: $currentVersion"
Write-Host "Next available version: $nextVersion"
# 兼容的简化查询：只输出下一可用版本
& $versionTool scripts/version.py next --remote origin
Write-Host "Git tag: $($releasePlan.tag)"
Write-Host "Docker/npm version: $($releasePlan.npm)"

# 默认延续当前 alpha、beta 或 rc 通道；正式发版前检查本地与 origin 的 Tag。
& $versionTool scripts/version.py bump --remote origin

$releaseTag = (& $versionTool scripts/version.py tag).Trim()
$imageVersion = (& $versionTool scripts/version.py get).Trim()
$commit = (git rev-parse HEAD).Trim()
& $versionTool scripts/version.py check
& $versionTool scripts/version.py verify-tag `
  --tag $releaseTag `
  --expected-commit $commit `
  --expect-head `
  --remote origin
```

`plan` 只读且不修改文件，一次返回当前版本、候选 Python/npm/Git Tag、发布通道、远端和已占用版本；`--json` 是供 PowerShell、CI 和后续发布工具消费的稳定输出。`next` 保留为只输出候选版本的兼容命令。`bump` 会沿用当前发布通道递增编号，自动跳过已存在的本地与已解析远端 Tag，并在写文件前第二次查询 Tag、重新计算完整候选；如果并行发布抢先占用候选，或发布线已被推进到更高版本，它会停止并要求重新生成计划。`bump` 默认拒绝脏工作树，避免把用户未提交的修改混入版本同步；只有确认这些修改可以共存时才使用 `--allow-dirty`。`--kind stable` 用于把预发布转为正式版，`--kind minor` 和 `--kind major` 用于明确的功能线升级，不能让默认命令猜测这些边界。没有网络或只想在本地试算时，使用 `plan --local-only`、`next --local-only` 或 `bump --local-only`；不要把网络失败误当成“没有冲突”。

`bump` 会把 Python 版本转换为 npm 版本，更新被发现的活动版本载体，调用 `uv lock`，并在任一步失败时恢复本次写入；`.version-sync.lock` 是 Git 忽略的工作区互斥锁，进程崩溃后由操作系统释放。事务只快照并恢复版本工具实际管理的载体，不删除无关新增文件，也拒绝把 `.env`、`data/`、密码哈希或私有 `docs/LOGO.jpg` 纳入恢复。`check` 会重新扫描并拒绝版本漂移、缺失载体、旧版本残留和 Git tag 不一致。`set` 仅保留用于修复明确指定的源版本，不应作为常规发版命令。正式发布前还应执行 `scripts/version.py discover` 查看审计结果。

`.version-artifacts.json` 是由工具生成的活动载体审计索引，不是第二个版本源，也不需要手工填写版本号或文件清单。它记录当前扫描到的路径和版本 token；后续新增 CI、Docker、文档或源码载体时，`check` 会要求它进入索引，载体被删除或跨版本残留时也会报错。历史 `CHANGELOG.md`、测试夹具和规划材料会被明确标记为排除项，不会被批量改写。

本地构建 WebUI 时由 `webui/vite.config.ts` 从 `VITE_APP_VERSION` 接收发布身份；CI 和 Docker 发布流程使用 `scripts/version.py tag` 动态生成该值。Docker 构建会再次校验它与 `pyproject.toml` 一致。因此切换版本只需要修改唯一源并运行同步工具，不能靠环境变量把构建伪装成另一个版本。正式发布必须先通过 `check` 和 `verify-tag`，确认 Tag、HEAD、远端 Tag 对象和期望 commit 是同一个不可变身份，再创建 GitHub Release、构建 Docker 镜像或生成 Windows 包；发布流程会把这个 commit 传给后续所有构建任务。

所有受保护 API 均使用 `/backend-api/api` 前缀和 `Authorization: Bearer <token>`。令牌由 `POST /backend-api/api/auth/login` 获取，请只放在当前终端变量中，不写进脚本、日志或文档。

## 1. 升级前备份

1. 记录旧版本、启动参数、数据目录、容器镜像标签和卷挂载；不要记录密钥值。
2. 登录旧实例，从「系统设置 -> 备份与恢复」导出 `.kirara-backup.zip`。等价接口为 `GET /backend-api/api/system/backups/export`。
3. 停止旧服务，确认没有进程继续写入 `data/`。
4. 将整个 A 数据目录复制到独立、受限的位置，例如 `E:\backup\kirara-data-before-<current-version>`。不要用移动或覆盖原目录代替复制。
5. 分别校验备份包能通过 `POST /backend-api/api/system/backups/inspect`，且数据副本包含配置、工作流、规则、数据库、记忆、媒体和插件目录。检查接口使用 multipart 字段 `backup`，会读取上传包但不恢复数据。

备份包可能包含 API Key、机器人令牌、Web 密钥和密码哈希，不得提交到 Git、上传到公开制品库或发给他人。

## 2. 安装并启动当前源码版本

```powershell
$currentVersion = (& .venv-win/Scripts/python.exe scripts/version.py get).Trim()
Write-Host "Starting Kirara AI $currentVersion"
```

源码部署在仓库根目录执行：

```powershell
uv sync --frozen
.venv-win/Scripts/python.exe -m kirara_ai -H 127.0.0.1 -p 8080
```

项目本机的 `.venv/` 不是可执行环境，不要删除或修复它。Windows 验证统一使用 `.venv-win/Scripts/python.exe`。容器部署应保留原 A 卷，只把其副本挂给 B。构建时必须把 UI 版本作为外部发布身份注入，不能在 Dockerfile 或命令中硬编码固定版本。例如本地构建可使用当前精确 tag，非 tag 检出则使用可追溯的 commit 标识：

```powershell
$versionTool = ".venv-win/Scripts/python.exe"
$releaseTag = (& $versionTool scripts/version.py tag).Trim()
$imageVersion = (& $versionTool scripts/version.py get).Trim()
$commit = (git rev-parse HEAD).Trim()
& $versionTool scripts/version.py check
& $versionTool scripts/version.py verify-tag `
  --tag $releaseTag `
  --expected-commit $commit `
  --expect-head `
  --remote origin
docker build --build-arg "VITE_APP_VERSION=$releaseTag" -t "kirara-ai:$imageVersion" .
```

GitHub Tag 使用 `scripts/version.py tag` 生成的 `v` 前缀形式；Docker 镜像标签使用 `scripts/version.py get` 生成的不带 `v` 的包版本。两者都来自 `pyproject.toml`，不要把版本号或提交哈希格式复制到脚本；Python、npm、WebUI、Docker 与发布标签都必须从同一命令派生。

首次启动可能生成并保存缺失的 `web.secret_key`，因此应始终在数据副本上试升。不要在 A 的唯一数据目录上直接试跑。

## 3. 登录与 readiness

```powershell
$base = "http://127.0.0.1:8080/backend-api/api"
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/login" `
  -ContentType "application/json" -Body (@{ password = $env:KIRARA_WEB_PASSWORD } | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod -Uri "$base/system/readiness" -Headers $headers
```

`GET /backend-api/api/system/readiness` 返回 `ready`、`timestamp` 和有固定 ID 的 `checks`。重点检查：

- `data_directories_writable`、`configuration_parseable`、`workflows_valid`、`dispatch_targets_exist`
- `im_available`、`llm_available`、`mcp_health`

readiness 是有超时上限、不会回显密钥的本地诊断，不保证远端 LLM 或 MCP 此刻一定可用。未配置 MCP 会是 `skip`；部分 MCP 不可用通常是 `warn`。Docker 的 TCP healthcheck 也不能替代该接口。

## 4. 无副作用验证

### 工作流

先列出工作流并确认自定义工作流、已编辑预设和手工坐标仍在：

```powershell
$workflows = Invoke-RestMethod -Uri "$base/workflow" -Headers $headers
$workflows
```

对关键工作流调用 `GET /backend-api/api/workflow/<group_id>/<workflow_id>` 取得完整定义，再把该定义原样提交给 `POST /backend-api/api/workflow/validate`。校验请求是完整定义，不是工作流 ID；它只做静态检查，不保存、不执行、不改注册表。

### 调度

```powershell
$preview = @{
  content = "/help"; chat_type = "群聊"; sender_id = "upgrade-check"
  group_id = "upgrade-check"; mentioned = $false
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$base/dispatch/preview" -Headers $headers `
  -ContentType "application/json" -Body $preview
Invoke-RestMethod -Method Post -Uri "$base/dispatch/reachability" -Headers $headers `
  -ContentType "application/json" -Body '{"draft_rule":null}'
```

`preview` 按真实优先级解释命中，但不执行工作流、不发消息、不保存规则；`reachability` 只做静态遮蔽分析。确认 `/help`、`/清空记忆`、骰子、抽卡、群聊 `/chat`、私聊和兜底记忆仍指向预期工作流。

### 模型目录与手工选择

```powershell
Invoke-RestMethod -Uri "$base/llm/auto-detect-schedule" -Headers $headers
```

该 GET 只读取周期计划、上次执行时间和模型数。随后在 WebUI 打开每个关键工作流，逐项确认主模型和最多四个备用槽位仍保持原值。周期刷新只允许更新后端模型目录，不会替用户改写这些槽位。

`GET /backend-api/api/llm/backends/<backend>/auto-detect-models` 和 `POST /backend-api/api/llm/auto-detect-schedule/run` 会访问远端，并可能写入模型目录或状态；`PUT /backend-api/api/llm/backends/<backend>/auto-detect-schedule` 会写配置。它们不是默认升级 smoke 命令。需要验证时应先确认供应商、费用和变更窗口，再观察调度状态与日志，并重新核对手工模型选择。

## 5. 内置能力抽查

内置 recipe、前置条件、触发样例与诊断接口见 [工作流操作与部署指南](WORKFLOW_OPERATIONS_GUIDE.md#3-模板怎么选)。先用 `POST /backend-api/api/dispatch/preview` 验证触发，再只在受控会话发送真实测试消息。MCP 工具、函数调用、自定义脚本和外部模型可能产生副作用或费用，不能作为无人值守 smoke。

## 6. 回滚

满足任一条件就停止放量：readiness 的关键本地检查失败、工作流/规则缺失、已删预设复活、模型槽位改变，或真实消息行为与 A 不一致。

1. 停止当前源码版本，避免继续写 B 数据。
2. 保存故障现场的脱敏日志和 B 数据副本，不覆盖 A 备份。
3. 恢复旧程序版本及原启动参数。
4. 优先挂回未被 B 写过的 A 数据目录副本；或在空的数据目录中通过 `POST /backend-api/api/system/backups/import` 恢复升级前备份。导入会写数据并先创建自动回滚包，必须人工确认。
5. 导入成功响应含 `restart_required: true`；完整重启后重新登录。
6. 再运行 `GET /backend-api/api/system/readiness`、工作流列表、工作流静态校验、调度 preview/reachability 和模型手工选择核对。

恢复期间不要把新旧进程同时指向同一个数据目录，也不要手工拼接两边的规则、tombstone 或工作流文件。事务恢复会在启动时处理已记录的中断写入；人工混合文件会破坏这一边界。
