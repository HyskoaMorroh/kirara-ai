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
  relations: AgentRelations
}

export async function listAgents() {
  return http.get<AgentSummary[]>('/agents')
}

export async function createAgentConfiguration(payload: AgentConfigurationRequest) {
  return http.post<AgentSummary>('/agents/configuration', payload)
}

export async function updateAgentConfiguration(agentId: string, payload: AgentConfigurationRequest) {
  return http.put<AgentSummary>(`/agents/${encodeURIComponent(agentId)}/configuration`, payload)
}
