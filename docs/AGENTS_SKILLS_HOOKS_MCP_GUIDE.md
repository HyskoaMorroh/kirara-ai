# Agents、Skills、Hooks 与 MCP 实用指南

本指南只描述 `3.3.0b8` 已有的真实 primitives。这里的 Agent 和 Skill 是现有工作流、目录元数据与规则的组合，不是第二套执行器；Hook 是插件生命周期回调，不是任意中间件或安全沙箱；MCP 也没有通用人工审批中心。

## 1. 能力边界

| 名称 | 当前实现 | 不代表什么 |
| --- | --- | --- |
| Agent | 一个既有 Workflow，加模型、工具、记忆和调度策略元数据 | 没有独立 Agent 循环、规划器或绕过 Block 校验的执行器 |
| Skill | `catalog.json` 中对版本化工作流模板的名称、前置条件、能力和触发示例描述 | 没有单独的 Skill 运行时或任意脚本下载器 |
| Hook | manifest 声明并经 `PluginEventBus` 注册的允许列表生命周期 | 不是 Python sandbox，也不是每个请求/Block 的通用拦截器 |
| MCP | 服务器、工具、prompt、resource 的发现和调用，以及工作流工具 provider | 没有跨工具的通用审批队列；prompt/resource 当前没有工作流 Block |

插件仍在主进程内运行，可以直接导入 Python 的文件、网络和进程库。manifest 权限只约束框架注入给插件的 host facade，不能把不可信 Python 代码变成安全代码。

## 2. 工作流支持的 Agent

一个“资料助手”可以直接由 `chat:mcp_tools` 副本表示：

1. 复制「聊天 - 工具调用 (MCP)」模板，不修改内置原件。
2. 在 LLM 节点手工选择支持函数调用的模型。
3. 在 `mcp:mcp_tool_provider` 的 `enabled_tools` 中只勾选任务必需的工具；空列表表示不开放工具。
4. 保留模板的记忆节点和 `max_iterations` 上限。
5. 建立仅限管理员私聊或指定群的 DispatchRule，先调用 `POST /backend-api/api/dispatch/preview` 验证命中。

Agent 的实际执行仍由 WorkflowExecutor 完成；禁用对应规则或删除用户副本即可停止它。不要把能写文件、发消息、改配置、执行命令或产生费用的工具直接挂到公开规则。

## 3. 目录支持的 Skill

`kirara_ai/workflow/presets/catalog.json` 为每个随包 YAML 提供稳定 ID、中文名、用途、前置条件、触发示例、能力和难度。比如 `chat:time_aware` 可描述成“输入聊天消息，注入当前时间后输出回复”的 Skill，但执行物仍是 `time_aware.yaml`。

通过 `GET /backend-api/api/workflow` 查看目录元数据和已加载工作流，通过 `GET /backend-api/api/workflow/<group_id>/<workflow_id>` 取得完整定义，再用 `POST /backend-api/api/workflow/validate` 静态校验。移除 Skill 时删除用户副本及指向它的规则；删除随包预设会记录 tombstone，升级不会把它复活。

## 4. 权限化 lifecycle Hook

扩展 manifest 的核心字段为 `name`、`version`、`capabilities` 和 `hooks`。示例只申请监听模型目录刷新：

```json
{
  "name": "catalog-audit",
  "version": "1.0.0",
  "capabilities": ["lifecycle_hooks"],
  "hooks": ["model_catalog_refreshed"]
}
```

允许的 capability 为 `lifecycle_hooks`、`events`、`file`、`network`、`process`、`config_write`、`secret`。允许的 lifecycle 为：

- `startup_completed`
- `shutdown_requested`
- `workflow_before`
- `workflow_after`
- `workflow_error`
- `dispatch_preview`
- `model_catalog_refreshed`
- `mcp_operation`

注册未声明或未知 lifecycle 会被拒绝并审计。审计记录包含扩展名、动作、结果、lifecycle/capability 等结构化元数据，不保存 payload 内容或密钥。Hook 异常会记录失败；具体主流程是否继续取决于触发点实现，不能把它当事务回滚或强制审批机制。

用 `GET /backend-api/api/plugin/plugins` 查看插件清单，用 `GET /backend-api/api/plugin/plugins/<plugin_name>` 查看单个插件及 manifest。禁用或删除插件属于运行状态变更，应先确认受影响工作流；旧式无 manifest 插件为兼容性仍可加载，不能据此推断其最小权限。

## 5. MCP 工具集成

只读盘点接口：

- `GET /backend-api/api/mcp/statistics`
- `GET /backend-api/api/mcp/servers`
- `GET /backend-api/api/mcp/servers/<server_id>`
- `GET /backend-api/api/mcp/servers/<server_id>/tools`
- `GET /backend-api/api/mcp/tools`

推荐用 `mcp_tools.yaml` 集成：先连接可信服务器，再在用户工作流副本中设置 `enabled_tools` allowlist，最后把受限规则指向该副本。服务器断开、工具不在 allowlist 或模型不支持函数调用时应明确失败或退化为普通对话，不得静默扩大工具权限。

直接调用接口为 `POST /backend-api/api/mcp/servers/<server_id>/tools/call`，请求体形如：

```json
{
  "toolName": "read_file",
  "params": {"path": "/approved/example.txt"}
}
```

这是真实外部操作，不是 dry-run。工具可能读取或写入数据、执行命令、发送消息、访问网络或产生费用；调用前必须由操作人员核对服务器、工具名、参数、数据范围和费用。Kirara AI 当前没有通用 MCP 人工审批中心，`enabled_tools` 只是工作流 allowlist，不等于逐次审批。

MCP 的 prompt/resource 可通过 `/backend-api/api/mcp/servers/<server_id>/prompts`、`/backend-api/api/mcp/servers/<server_id>/resources` 等 API 浏览或采样，但当前没有对应的工作流 Block。卸载时先禁用引用规则，删除/修改工作流中的 provider，再停止或删除服务器；服务器启停、配置更新和删除都有副作用，应在 WebUI 中人工确认。

## 6. 上线检查

1. manifest 只声明实际需要的 capability 与 lifecycle。
2. 工作流通过 `POST /backend-api/api/workflow/validate`，规则通过 `POST /backend-api/api/dispatch/preview` 和 `/backend-api/api/dispatch/reachability`。
3. Agent/Skill 的模型、工具、记忆、最大迭代和会话范围明确，工具 allowlist 默认为空。
4. 日志、错误和审计不含 token、密码、prompt 正文或工具 payload。
5. 已写明失败行为和移除顺序，并验证禁用扩展后主系统仍可运行。
