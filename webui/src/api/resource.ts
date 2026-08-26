import { http } from '@/utils/http'

export type ResourceType = 'skill' | 'prompt' | 'session' | 'memory' | 'mcp' | 'hook'

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
  branch: string | null
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

export interface CatalogItem {
  catalog_id: string
  type: Exclude<ResourceType, 'session'>
  name: string
  description: string
  version?: string
  source?: string
  source_key?: string
  branch?: string
  source_url?: string
  tags?: string[]
  owner?: string
  repository?: string
  directory?: string
  installed?: boolean
  installed_resource_id?: string | null
  enabled?: boolean
  installs?: number
}

export interface CatalogSearchResponse {
  query: string
  type: string | null
  items: CatalogItem[]
  total_count: number
  limit: number
  offset: number
  remote: {
    provider: 'skills.sh' | string
    status: 'not_requested' | 'ok' | 'error'
    error: string | null
    total_count: number | null
  }
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
  resource_id?: string | null
  type?: ResourceType | null
  component?: string | null
  event?: string | null
  operation: string
  current_version?: string | null
  resource_version?: string | null
  source_summary?: string | null
  content_sha256?: string | null
  resource_sha256?: string | null
  snapshot_sha256?: string | null
  result?: string | null
  outcome?: string | null
  status?: string | null
  agent_id?: string | null
  model_id?: string | null
  correlation_id?: string | null
  server?: string | null
  duration_ms?: number | null
  session?: Record<string, string> | null
  timestamp: string
  error_category?: string | null
}

export interface AuditFilters {
  resourceId?: string
  correlationId?: string
  component?: string
  event?: string
  operation?: string
  outcome?: string
  status?: string
  agentId?: string
  modelId?: string
  server?: string
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

export type SystemDependencyStatus = 'unknown' | 'ready' | 'missing' | 'failed' | 'cancelled'

export interface SystemDependency {
  dependency_id: string
  name: string
  description: string
  kind: string
  required_by: string[]
  prerequisites: string[]
  install_supported: boolean
  operator_guidance: string | null
  status: SystemDependencyStatus | string
  ready: boolean
  version: string | null
  summary: string | null
  checked_at: string | null
  last_task_id: string | null
}

export type DependencyTaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface DependencyInstallTask {
  task_id: string
  dependency_id: string
  operation: 'install' | string
  status: DependencyTaskStatus | string
  created_at: string
  started_at: string | null
  finished_at: string | null
  retry_of: string | null
  cancel_requested: boolean
  error_code: string | null
  error_summary: string | null
  output_tail: string
}

export async function listResources(type?: ResourceType) {
  const query = type ? `?type=${encodeURIComponent(type)}` : ''
  return http.get<ManagedResource[]>(`/resources${query}`)
}

export async function listSystemDependencies() {
  return http.get<SystemDependency[]>('/resources/dependencies')
}

export async function getSystemDependency(dependencyId: string) {
  return http.get<SystemDependency>(`/resources/dependencies/${encodeURIComponent(dependencyId)}`)
}

export async function probeSystemDependency(dependencyId: string) {
  return http.post<SystemDependency>(
    `/resources/dependencies/${encodeURIComponent(dependencyId)}/probe`,
    {}
  )
}

export async function installSystemDependency(dependencyId: string, confirmed = false) {
  return http.post<DependencyInstallTask>(
    `/resources/dependencies/${encodeURIComponent(dependencyId)}/install`,
    { confirmed }
  )
}

export async function listDependencyTasks(dependencyId?: string) {
  const query = dependencyId ? `?dependency_id=${encodeURIComponent(dependencyId)}` : ''
  return http.get<DependencyInstallTask[]>(`/resources/dependency-tasks${query}`)
}

export async function getDependencyTask(taskId: string) {
  return http.get<DependencyInstallTask>(`/resources/dependency-tasks/${encodeURIComponent(taskId)}`)
}

export async function retryDependencyTask(taskId: string, confirmed = false) {
  return http.post<DependencyInstallTask>(
    `/resources/dependency-tasks/${encodeURIComponent(taskId)}/retry`,
    { confirmed }
  )
}

export async function cancelDependencyTask(taskId: string) {
  return http.post<DependencyInstallTask>(
    `/resources/dependency-tasks/${encodeURIComponent(taskId)}/cancel`,
    {}
  )
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

export async function searchResourceCatalog(type: Exclude<ResourceType, 'session'> | undefined, query: string, limit = 20, offset = 0) {
  const params = new URLSearchParams({ q: query, limit: String(limit), offset: String(offset) })
  if (type) params.set('type', type)
  return http.get<CatalogSearchResponse>(`/resources/catalog/search?${params.toString()}`)
}

export async function getCatalogItem(catalogId: string) {
  return http.get<CatalogItem>(`/resources/catalog/${encodeURIComponent(catalogId)}`)
}

export async function installCatalogItem(catalogId: string, branch?: string | null) {
  const payload: { catalog_id: string; branch?: string } = { catalog_id: catalogId }
  if (branch) payload.branch = branch
  return http.post<ManagedResource>('/resources/catalog/install', payload)
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

export async function listResourceAudit(
  filtersOrResourceId: AuditFilters | string = {},
  offset = 0,
  limit = 20
) {
  const filters: AuditFilters = typeof filtersOrResourceId === 'string'
    ? { resourceId: filtersOrResourceId }
    : filtersOrResourceId
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  const mappings: Array<[keyof AuditFilters, string]> = [
    ['resourceId', 'resource_id'],
    ['correlationId', 'correlation_id'],
    ['component', 'component'],
    ['event', 'event'],
    ['operation', 'operation'],
    ['outcome', 'outcome'],
    ['status', 'status'],
    ['agentId', 'agent_id'],
    ['modelId', 'model_id'],
    ['server', 'server']
  ]
  for (const [key, parameter] of mappings) {
    const value = filters[key]
    if (value) params.set(parameter, value)
  }
  return http.get<AuditPage>(`/resources/audit?${params.toString()}`)
}
