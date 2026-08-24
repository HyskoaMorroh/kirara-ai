import { http } from '@/utils/http'

export type ResourceType = 'skill' | 'prompt' | 'session' | 'mcp'

export interface ResourceVersion {
  version: string
  source: string
  source_key?: string | null
  source_metadata?: Record<string, unknown> | null
  entry: string
  permissions: string[]
  content_sha256: string
  installed_at: string
}

export interface ManagedResource {
  resource_id: string
  type: ResourceType
  current_version: string
  source: string
  source_key?: string | null
  source_metadata?: Record<string, unknown> | null
  entry: string
  permissions: string[]
  content_sha256: string
  enabled: boolean
  confirmation_required: boolean
  workflow_id: string | null
  installed_at: string
  updated_at: string
  versions: ResourceVersion[]
}

export interface ResourceRepository {
  owner: string
  name: string
  branch: string
  enabled: boolean
}

export interface DiscoveredSkill {
  source_key: string
  owner: string
  repository: string
  branch: string
  directory: string
  name: string
  description: string
  source_url: string
}

export interface SkillsSearchResult extends DiscoveredSkill {
  installs: number
}

export interface SkillsSearchResponse {
  query: string
  skills: SkillsSearchResult[]
  total_count: number
  limit: number
  offset: number
}

export interface ResourceUpdateCheck {
  resource_id: string
  source_key?: string | null
  current_version?: string
  current_content_sha256?: string | null
  remote_content_sha256?: string | null
  update_available: boolean
  next_version?: string
  source_metadata?: Record<string, unknown>
  error?: string
}

export interface ResourceBackup {
  backup_id: string
  resource_id: string
  version: string
  reason: string
  created_at: string
  content_sha256?: string | null
}

export interface AuditRecord {
  resource_id: string
  type: ResourceType
  operation: string
  current_version: string | null
  source_summary?: string | null
  content_sha256?: string | null
  result: string
  timestamp: string
  error_category?: string | null
}

export interface AuditPage {
  items: AuditRecord[]
  total: number
  offset: number
  limit: number
}

export interface ResourceStorageStatus {
  mode: 'server_managed' | string
  data_root: string
  resource_root: string
  install_root: string
  backup_root: string
  writable: boolean
  versioned: boolean
}

export interface AgentResourceBinding {
  resource_id: string
  resource_type: ResourceType
  version: string
  enabled: boolean
}

export interface AgentSummary {
  agent_id: string
  display_name?: string | null
  enabled: boolean
  workflow_id?: string | null
  model_priority: string[]
  prompt_bindings: AgentResourceBinding[]
  skill_bindings: AgentResourceBinding[]
  mcp_bindings: AgentResourceBinding[]
  relations: {
    channels: string[]
    accounts: Array<{
      channel_type: string
      adapter_instance: string
      account_scope: string
    }>
    sessions: string[]
    is_default: boolean
  }
}

export async function listResources(type?: ResourceType) {
  const query = type ? `?type=${encodeURIComponent(type)}` : ''
  return http.get<ManagedResource[]>(`/resources${query}`)
}

export async function getResource(resourceId: string) {
  return http.get<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}`)
}

export async function getResourceStorageStatus() {
  return http.get<ResourceStorageStatus>('/resources/storage')
}

export async function installResource(file: File) {
  const formData = new FormData()
  formData.append('resource', file)
  return http.postForm<ManagedResource>('/resources', formData)
}

export async function importResource(file: File) {
  const formData = new FormData()
  formData.append('resource', file)
  return http.postForm<ManagedResource>('/resources/imports', formData)
}

export async function updateResource(resourceId: string, file: File) {
  const formData = new FormData()
  formData.append('resource', file)
  return http.postForm<ManagedResource>(
    `/resources/${encodeURIComponent(resourceId)}/versions`,
    formData
  )
}

export async function enableResource(resourceId: string, confirmed = false) {
  return http.post<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}/enable`, {
    confirmed
  })
}

export async function disableResource(resourceId: string) {
  return http.post<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}/disable`, {})
}

export async function bindResourceWorkflow(resourceId: string, workflowId: string) {
  return http.post<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}/workflow`, {
    workflow_id: workflowId
  })
}

export async function restoreResource(resourceId: string, version: string, confirmed = false) {
  return http.post<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}/restore`, {
    version,
    confirmed
  })
}

export async function checkResourceUpdates(resourceId?: string) {
  const query = resourceId ? `?resource_id=${encodeURIComponent(resourceId)}` : ''
  return http.get<ResourceUpdateCheck[]>(`/resources/updates${query}`)
}

export async function updateRemoteResource(resourceId: string) {
  return http.post<ManagedResource>(`/resources/${encodeURIComponent(resourceId)}/update`, {})
}

export async function listRepositories() {
  return http.get<ResourceRepository[]>('/resources/repositories')
}

export async function addRepository(owner: string, name: string, branch = 'main') {
  return http.post<ResourceRepository>('/resources/repositories', { owner, name, branch })
}

export async function setRepositoryEnabled(
  owner: string,
  name: string,
  branch: string,
  enabled: boolean
) {
  return http.post<ResourceRepository>(
    `/resources/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${encodeURIComponent(branch)}/enabled`,
    { enabled }
  )
}

export async function discoverRepository(owner: string, name: string, branch = 'main') {
  return http.get<DiscoveredSkill[]>(
    `/resources/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${encodeURIComponent(branch)}/discover`
  )
}

export async function searchSkills(query: string, limit = 20, offset = 0) {
  const params = new URLSearchParams({ q: query, limit: String(limit), offset: String(offset) })
  return http.get<SkillsSearchResponse>(`/resources/skills-sh/search?${params.toString()}`)
}

export async function installRemoteSkill(skill: {
  owner: string
  name: string
  branch?: string
  directory: string
  source_key?: string
}) {
  return http.post<ManagedResource>('/resources/remote-install', skill)
}

export async function listResourceBackups(resourceId?: string) {
  const query = resourceId ? `?resource_id=${encodeURIComponent(resourceId)}` : ''
  return http.get<ResourceBackup[]>(`/resources/backups${query}`)
}

export async function restoreResourceBackup(backupId: string, confirmed = false) {
  return http.post<ManagedResource>(`/resources/backups/${encodeURIComponent(backupId)}/restore`, {
    confirmed
  })
}

export async function deleteResourceBackup(backupId: string, confirmed = false) {
  return http.delete(`/resources/backups/${encodeURIComponent(backupId)}`, {
    body: JSON.stringify({ confirmed })
  })
}

export async function listAgents() {
  return http.get<AgentSummary[]>('/agents')
}

export async function listResourceAudit(resourceId?: string, offset = 0, limit = 20) {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (resourceId) params.set('resource_id', resourceId)
  return http.get<AuditPage>(`/resources/audit?${params.toString()}`)
}
