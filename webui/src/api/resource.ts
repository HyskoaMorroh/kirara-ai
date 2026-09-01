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

export interface ManagedResource extends ResourceDependencyProjection {
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
  /**
   * 绑定了这个资源的 Agent ID。
   *
   * 与 `in_effect` 分开：即使那条绑定当前被停用，绑定关系本身也值得显示——
   * 它解释了「为什么改这个 Agent 会影响这个资源」。
   * 读不到 Agent 注册表的部署里该字段不出现（而不是空数组）。
   */
  bound_agent_ids?: string[]
  /**
   * 这个资源当前是否真的进入 LLM 请求。
   *
   * 「已启用」不等于「生效」：一个 Skill 只有被某个启用的 Agent 以启用的绑定
   * 引用之后才会进入 system 消息。缺了这个字段，用户看到「已启用」却得到
   * 「什么都没变」，然后去怀疑模型或提示词。
   *
   * 字段**不出现**表示「不知道」（读不到 Agent 注册表），
   * 与 `false`（确定未生效）是两件事——把「不知道」显示成「未生效」
   * 等于给出一个我们没有依据的论断。
   */
  in_effect?: boolean
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

/**
 * 一个资源的系统依赖汇总档位，与后端 `_dependency_status` 的六个返回值一一对应。
 *
 * `unknown` 与 `missing` 必须分开：前者是「还没探测过」，后者是「探测过、确实没有」。
 * 显示成同一种状态会让刚装完还没探测的资源看起来像坏的，用户于是去装一个本来
 * 就在的东西。
 */
export type ResourceDependencyStatus =
  | 'not_required'
  | 'ready'
  | 'missing'
  | 'failed'
  | 'cancelled'
  | 'unknown'

/**
 * 后端在每个目录项与已安装资源上投影的依赖就绪信息。
 *
 * 声明为可选：老后端不带这几个字段，读到 undefined 表示「这个后端还不提供
 * 依赖信息」，与「不需要依赖」（`not_required`）不是一回事。
 */
export interface ResourceDependencyProjection {
  /** 这个资源需要哪些系统依赖；空数组表示确实不需要。 */
  dependency_ids?: string[]
  /** 每个依赖的探测结果。长度与 `dependency_ids` 不等时汇总档位为 unknown。 */
  system_dependencies?: SystemDependency[]
  dependencies_ready?: boolean
  dependency_status?: ResourceDependencyStatus
}

export interface CatalogItem extends ResourceDependencyProjection {
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
  /**
   * 该来源是否支持自动检查更新。
   *
   * 只有 GitHub 来源能比对远端内容；catalog / skills.sh / 本地导入装的 Skill
   * 拿不到可比对的远端版本。后端为这些来源返回 `false` 并在 `error` 里给出
   * 应该怎么拿新版本，`false` 与「检查失败」是两件事：前者重试永远不会成功。
   * 字段缺失按 `true` 处理，兼容不返回该字段的旧后端。
   */
  update_channel_supported?: boolean
  /** 来源标识（`github` / `catalog` / `skills.sh` / …），无来源信息时为 null。 */
  source_provider?: string | null
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

/**
 * 服务器 `resources/imports` 目录里已经存在的一个资源包。
 *
 * 只有文件名，没有宿主机路径——路径不该经由接口流出去。
 */
export interface ImportableArchive {
  file_name: string
  size: number | null
  resource_id: string | null
  type: string | null
  version: string | null
  installed: boolean
  installed_version: string | null
  /** 盘上这个版本高于已装版本；点「更新」而不是「安装」。 */
  is_upgrade: boolean
  /** 解析失败的原因；`null` 表示这个包可以安装。 */
  error: string | null
}

/**
 * 列出服务器上已经放好、还没安装的资源包。
 *
 * 覆盖的是「手里没有可上传文件」的场景：运维用 scp 把一批包放进了服务器，
 * 或者包有几十 MB 走浏览器上传既慢又容易断。
 *
 * 只读：不解包、不落盘。
 */
export async function listImportableArchives() {
  return http.get<{ imports: ImportableArchive[] }>('/resources/imports')
}

/**
 * 一个已注册版本的入口正文与它的已校验身份。
 *
 * `entry` 是**包内相对路径**，不是容器或宿主绝对路径。
 */
export interface ResourceContent {
  resource_id: string
  version: string
  entry: string
  content: string
  content_sha256: string
  source: string
  permissions: string[]
}

/**
 * 读一个资源某个版本的正文。
 *
 * prompt 这个类型的全部内容就是正文，而此前界面上没有任何地方能看到它——
 * 「提示词管理」回答不了它唯一要回答的问题「现在生效的提示词到底写了什么」。
 *
 * **只读，没有对应的写入接口。** 安装后的正文不能就地改：`content_sha256`
 * 把清单与文件绑在一起，运行时每次载入都重新校验摘要，就地编辑的后果不是
 * 「改了没生效」而是那个资源彻底不可用。改正文走「上传 ZIP 升级」。
 */
export async function getResourceContent(resourceId: string, version?: string) {
  const query = version ? `?version=${encodeURIComponent(version)}` : ''
  return http.get<ResourceContent>(
    `/resources/${encodeURIComponent(resourceId)}/content${query}`
  )
}

/** 安装一个已经在盘上的包。只传文件名，服务器不接受路径。 */
export async function installImportableArchive(fileName: string) {
  return http.post<ManagedResource>('/resources/imports/install', {
    file_name: fileName
  })
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
