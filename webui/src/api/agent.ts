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
  relations: AgentRelations
}

export async function listAgents() {
  return http.get<AgentSummary[]>('/agents')
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
