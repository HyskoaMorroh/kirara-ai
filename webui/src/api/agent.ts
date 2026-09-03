import { http } from '@/utils/http'

import type { ResourceType } from './resource'

export type AgentChannel = 'webui' | 'onebot' | 'qqbot' | 'telegram' | 'wecom'
export type AgentResourceType = Exclude<ResourceType, 'session'>
export type ResourceVersionPolicy = 'fixed' | 'current'

export interface AgentResourceBinding {
  resource_id: string
  resource_type: AgentResourceType
  version: string
  version_policy: ResourceVersionPolicy
  enabled: boolean
  content_sha256: string
  permissions: string[]
  source: string
}

export interface AgentAccountRelation {
  channel_type: AgentChannel | string
  adapter_instance: string
  account_scope: string
}

export interface AgentRelations {
  channels: AgentChannel[]
  accounts: AgentAccountRelation[]
  sessions: string[]
  is_default: boolean
}

/**
 * 一个 Agent 的回复取回方式。
 *
 * `inherit` 表示跟随渠道默认与进程默认（三层优先级里的下两层），也是默认值。
 * 它必须是一个可选项而不是「留空」：一个曾被显式设成 `off` 的 Agent 需要能改回
 * 跟随，否则运维只能靠猜上层是什么再手填同一个值。
 *
 * `incremental` 需要渠道能改写已交付出去的内容：Telegram 靠 `editMessageText`，
 * WebUI 的在线对话靠 SSE（一条事件就是一次追加）。QQ / OneBot 与企业微信没有等价
 * 能力，在那里它静默退化成 `aggregate`（仍走流式请求，用户仍只看到一条完整回复）。
 */
export type AgentReplyStreamMode = 'inherit' | 'off' | 'aggregate' | 'incremental'

export interface AgentSummary {
  agent_id: string
  display_name?: string | null
  enabled: boolean
  workflow_id?: string | null
  model_priority: string[]
  provider_allowlist: string[]
  capabilities: string[]
  prompt_bindings: AgentResourceBinding[]
  skill_bindings: AgentResourceBinding[]
  memory_bindings: AgentResourceBinding[]
  mcp_bindings: AgentResourceBinding[]
  hook_bindings: AgentResourceBinding[]
  mcp_allowlist: string[]
  allow_tools: boolean
  max_tool_iterations: number
  /**
   * 可被本 Agent 作为工具委派的队友 Agent（需求 8 的 Teammates 模式）。
   * 为空表示不启用；模型会额外获得 `delegate_to_<agent_id>` 工具。
   */
  teammate_agent_ids: string[]
  /** 本 Agent 的回复取回方式；早于该字段的后端不返回它，按 `inherit` 处理。 */
  reply_stream_mode?: AgentReplyStreamMode
  relations: AgentRelations
}

export interface AgentResourceBindingInput {
  resource_id: string
  resource_type: AgentResourceType
  version_policy: ResourceVersionPolicy
  version?: string
  enabled: boolean
}

export interface AgentConfigurationRequest {
  agent_id: string
  display_name: string | null
  enabled: boolean
  workflow_id: string | null
  model_priority: string[]
  provider_allowlist: string[]
  capabilities: string[]
  prompt_bindings: AgentResourceBindingInput[]
  skill_bindings: AgentResourceBindingInput[]
  memory_bindings: AgentResourceBindingInput[]
  mcp_bindings: AgentResourceBindingInput[]
  hook_bindings: AgentResourceBindingInput[]
  mcp_allowlist: string[]
  allow_tools: boolean
  max_tool_iterations: number
  /**
   * 可被本 Agent 作为工具委派的队友 Agent（需求 8 的 Teammates 模式）。
   * 为空表示不启用；模型会额外获得 `delegate_to_<agent_id>` 工具。
   */
  teammate_agent_ids: string[]
  /** 本 Agent 的回复取回方式，`inherit` 表示跟随渠道 / 进程默认。 */
  reply_stream_mode: AgentReplyStreamMode
  relations: AgentRelations
}

export async function listAgents() {
  return http.get<AgentSummary[]>('/agents')
}

/**
 * 一个会话来自哪个渠道、哪个账号、哪个人。
 *
 * 与后端 `SessionStore._CHANNEL_IDENTITY_FIELDS` 的五个字段一一对应。
 * 五项是一组：只有渠道类型回答不了「是谁」（同一个渠道上有几十个会话），
 * 只有发送者标识回答不了「同一个人在私聊和群里的两个会话」。
 */
export interface SessionChannelIdentity {
  channel_type: string
  adapter_instance: string
  account_scope: string
  conversation_scope: string
  sender_scope: string
}

/**
 * 一个已持久化会话的元数据。
 *
 * 后端只返回条数与时间戳，不含任何对话正文：会话列表不应该变成
 * 一个可以读取全部聊天内容的接口。
 */
export interface SessionSummary {
  session_id: string
  agent_id: string | null
  message_count: number
  updated_at: string | null
  pending_confirmations: number
  /**
   * 渠道身份；`null` 表示这个会话建于渠道身份落盘之前（本版以前）。
   *
   * 必须与「有身份」区分开：`session_id` 是一个 64 位摘要，对人没有含义，
   * 而清空历史与删除会话都以它为唯一标识——分不清哪一行属于谁的时候，
   * 这两个动作只能靠猜。
   */
  channel_identity: SessionChannelIdentity | null
}

/** 一条仍在等待人工决定的确认记录（不含工具参数）。 */
export interface PendingConfirmation {
  confirmation_id: string
  agent_id: string | null
  status: string
  created_at: string | null
  updated_at: string | null
  expires_at: string | null
  correlation_id: string | null
  tool_name: string | null
}

export async function listSessions(limit = 200) {
  return http.get<{ items: SessionSummary[] }>(`/agents/sessions?limit=${limit}`)
}

export async function deleteSession(sessionId: string) {
  return http.delete<{ deleted: boolean }>(
    `/agents/sessions/${encodeURIComponent(sessionId)}`
  )
}

export async function clearSessionHistory(sessionId: string) {
  return http.delete<{ cleared: boolean }>(
    `/agents/sessions/${encodeURIComponent(sessionId)}/history`
  )
}

export async function listPendingConfirmations() {
  return http.get<{ items: PendingConfirmation[] }>('/agents/confirmations')
}

/** 一个 Hook 声明的单个事件；`enabled` 为假表示声明了但被关停。 */
export interface HookEventSummary {
  event: string
  enabled?: boolean
  kind?: 'handler' | 'command'
  matcher?: string | null
  timeout_ms?: number
  max_output_bytes?: number
  deny?: boolean
  requires_process_execution?: boolean
  required_capabilities?: string[]
  required_permissions?: string[]
  error?: string
}

export interface HookDeclarationSummary {
  resource_id: string
  version: string
  enabled: boolean
  events: HookEventSummary[]
  error?: string
}

/** dry-run 结果：这个 Hook 会不会因为某个工具而触发。 */
export interface HookPreviewResult {
  would_run: boolean
  reason?: string
  kind?: 'handler' | 'command'
  matcher?: string | null
  deny?: boolean
  requires_process_execution?: boolean
  error?: string
}

export async function listHookDeclarations() {
  return http.get<{ items: HookDeclarationSummary[] }>('/agents/hooks')
}

export async function previewHookEvent(
  resourceId: string,
  event: string,
  toolName?: string
) {
  return http.post<HookPreviewResult>(
    `/agents/hooks/${encodeURIComponent(resourceId)}/preview`,
    { event, tool_name: toolName || null }
  )
}

export async function createAgentConfiguration(payload: AgentConfigurationRequest) {
  return http.post<AgentSummary>('/agents/configuration', payload)
}

export async function updateAgentConfiguration(agentId: string, payload: AgentConfigurationRequest) {
  return http.put<AgentSummary>(`/agents/${encodeURIComponent(agentId)}/configuration`, payload)
}

/**
 * 删除一个 Agent 配置。
 *
 * 后端 `AgentRegistry.remove()` 有三道拒绝：默认 Agent 不能删、还有渠道绑定的
 * 不能删、还有账号或会话绑定的不能删，各自抛带原因的错误。调用方应把那句原因
 * 原样显示出来——它们都是用户能照做的（先改默认、先解绑），
 * 换成一句「删除失败」等于把一个可解的问题变成死胡同。
 *
 * 此前这条路由**没有任何前端调用点**：建错一个 Agent 就永久留在列表里，
 * 而它仍然参与「渠道身份 → Agent」的解析。
 */
export async function deleteAgent(agentId: string) {
  return http.delete<Record<string, never>>(`/agents/${encodeURIComponent(agentId)}`)
}
