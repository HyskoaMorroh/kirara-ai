<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDescriptions,
  NDescriptionsItem,
  NDivider,
  NEmpty,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NModal,
  NPagination,
  NSelect,
  NSkeleton,
  NSpace,
  NTag,
  NTooltip,
  NUpload,
  NUploadDragger,
  useDialog,
  useMessage,
  type DataTableColumns,
  type UploadFileInfo
} from 'naive-ui'
import {
  ArchiveOutline,
  BuildOutline,
  CloudDownloadOutline,
  CloudUploadOutline,
  EyeOutline,
  GitBranchOutline,
  PeopleOutline,
  RefreshOutline,
  SearchOutline,
  ShieldCheckmarkOutline,
  TrashOutline
} from '@vicons/ionicons5'
import { useRoute } from 'vue-router'
import {
  addRepository,
  bindResourceWorkflow,
  cancelDependencyTask,
  checkResourceUpdates,
  deleteResourceBackup,
  disableResource,
  enableResource,
  getCatalogItem,
  importResource,
  installCatalogItem,
  installSystemDependency,
  listDependencyTasks,
  listResourceAudit,
  listResourceBackups,
  listRepositories,
  listResources,
  listSystemDependencies,
  probeSystemDependency,
  restoreResourceBackup,
  retryDependencyTask,
  searchResourceCatalog,
  setRepositoryEnabled,
  updateRemoteResource
} from '@/api/resource'
import { listAgents } from '@/api/agent'
import type {
  AuditRecord,
  CatalogItem,
  DependencyInstallTask,
  ManagedResource,
  ResourceBackup,
  ResourceRepository,
  ResourceType,
  ResourceUpdateCheck,
  SystemDependency
} from '@/api/resource'
import type { AgentSummary } from '@/api/agent'

type ResourceFilter = ResourceType | 'all'
type PanelName = 'install' | 'discover' | 'backups' | 'relations' | 'dependencies' | null

const message = useMessage()
const dialog = useDialog()
const route = useRoute()
const resources = ref<ManagedResource[]>([])
const resourceType = ref<ResourceFilter>('all')
const loading = ref(true)
const errorMessage = ref('')
const panel = ref<PanelName>(null)
const selectedResource = ref<ManagedResource | null>(null)
const updateChecks = ref<Record<string, ResourceUpdateCheck>>({})
const audits = ref<AuditRecord[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditLoading = ref(false)
const auditFilters = ref({ correlationId: '', component: '', outcome: '' })
const repositories = ref<ResourceRepository[]>([])
const repositoryLoading = ref(false)
const repositoryForm = ref({ owner: '', name: '', branch: 'main' })
const remoteQuery = ref('')
const remoteType = ref<Exclude<ResourceType, 'session'> | 'all'>('all')
const remoteResults = ref<CatalogItem[]>([])
const remoteTotal = ref(0)
const remoteOffset = ref(0)
const remoteLimit = 20
const remoteLoading = ref(false)
const remoteSearched = ref(false)
const remoteStatus = ref<'not_requested' | 'ok' | 'error'>('not_requested')
const remoteError = ref('')
const selectedCatalogItem = ref<CatalogItem | null>(null)
const selectedFile = ref<File | null>(null)
const importLoading = ref(false)
const backups = ref<ResourceBackup[]>([])
const backupLoading = ref(false)
const agents = ref<AgentSummary[]>([])
const relationLoading = ref(false)
const systemDependencies = ref<SystemDependency[]>([])
const dependencyTasks = ref<DependencyInstallTask[]>([])
const dependencyLoading = ref(false)
const dependencyTaskLoading = ref(false)
const busyDependencyId = ref('')
const busyDependencyTaskId = ref('')
const compactDependencyLayout = ref(typeof window !== 'undefined' && window.innerWidth <= 768)
const workflowId = ref('')
const busyResourceId = ref('')
const checkingResourceId = ref('')
const showUpdateModal = ref(false)
const showDetailModal = ref(false)
const showCatalogDetailModal = ref(false)
const showImportModal = ref(false)
const selectedUpdate = ref<ManagedResource | null>(null)
const selectedUpdateCheck = computed(() =>
  selectedUpdate.value ? updateChecks.value[selectedUpdate.value.resource_id] : undefined
)
let dependencyPollTimer: ReturnType<typeof setTimeout> | null = null

const typeOptions = [
  { label: '全部资源', value: 'all' },
  { label: 'Skills', value: 'skill' },
  { label: 'Prompts', value: 'prompt' },
  { label: 'Sessions', value: 'session' },
  { label: 'Memory', value: 'memory' },
  { label: 'Hooks', value: 'hook' },
  { label: 'MCP', value: 'mcp' }
]

const catalogTypeOptions = [
  { label: '全部类型', value: 'all' },
  { label: 'Prompt', value: 'prompt' },
  { label: 'Skill', value: 'skill' },
  { label: 'Memory', value: 'memory' },
  { label: 'MCP', value: 'mcp' },
  { label: 'Hook', value: 'hook' }
]

const visibleResources = computed(() => resources.value)
const hasResources = computed(() => visibleResources.value.length > 0)
const isPanelOpen = computed(() => panel.value !== null)

const typeLabel = (type: ResourceType) =>
  ({ skill: 'Skill', prompt: 'Prompt', session: 'Session', memory: 'Memory', mcp: 'MCP', hook: 'Hook' })[type]

const bindingGroups = (agent: AgentSummary) => [
  { label: 'Prompt', bindings: agent.prompt_bindings || [] },
  { label: 'Skill', bindings: agent.skill_bindings || [] },
  { label: 'Memory', bindings: agent.memory_bindings || [] },
  { label: 'MCP', bindings: agent.mcp_bindings || [] },
  { label: 'Hook', bindings: agent.hook_bindings || [] }
].filter((group) => group.bindings.length > 0)

const bindingText = (binding: AgentSummary['prompt_bindings'][number]) =>
  binding.version_policy === 'current'
    ? `${binding.resource_id} · 跟随当前版本 (${binding.version})`
    : `${binding.resource_id}@${binding.version} · 固定版本`

const formatDate = (value: string | undefined) => {
  if (!value) return '未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

const shortHash = (value: string | null | undefined) =>
  value ? `${value.slice(0, 10)}...${value.slice(-8)}` : '未记录'

const sourceLabel = (resource: ManagedResource) => {
  const metadata = resource.source_metadata
  if (metadata && typeof metadata === 'object') {
    const provider = metadata.provider
    const owner = metadata.owner
    const repository = metadata.repository
    if (provider === 'github' && typeof owner === 'string' && typeof repository === 'string') {
      return `${owner}/${repository}`
    }
  }
  return resource.source || '服务器资源库'
}

const catalogSourceLabel = (item: CatalogItem) =>
  item.source_url || item.source || (item.owner && item.repository ? `${item.owner}/${item.repository}` : '服务器资源库')

const run = async <T,>(request: () => Promise<T>, failureText: string) => {
  try {
    return await request()
  } catch (error) {
    const detail = error instanceof Error ? error.message : '未知错误'
    message.error(`${failureText}：${detail}`)
    throw error
  }
}

const loadResources = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    resources.value = await listResources(resourceType.value === 'all' ? undefined : resourceType.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '资源列表加载失败'
  } finally {
    loading.value = false
  }
}

const changeType = async (value: ResourceFilter) => {
  resourceType.value = value
  await loadResources()
}

const reload = async () => {
  await loadResources()
  if (panel.value === 'backups') await loadBackups()
  if (panel.value === 'dependencies') await loadDependencyData()
}

const ask = (options: { title: string; content: string; positiveText?: string; onPositiveClick: () => Promise<void> }) =>
  dialog.warning({
    title: options.title,
    content: options.content,
    positiveText: options.positiveText || '确认',
    negativeText: '取消',
    onPositiveClick: options.onPositiveClick
  })

const enable = (resource: ManagedResource) => {
  ask({
    title: '确认启用资源',
    content: `启用 ${resource.resource_id} 后，Agent 可能在后续对话中读取它的固定版本和权限。`,
    positiveText: '启用',
    onPositiveClick: async () => {
      busyResourceId.value = resource.resource_id
      try {
        await run(() => enableResource(resource.resource_id, true), '启用资源失败')
        message.success('资源已启用')
        await loadResources()
      } finally {
        busyResourceId.value = ''
      }
    }
  })
}

const disable = (resource: ManagedResource) => {
  ask({
    title: '确认停用资源',
    content: `停用 ${resource.resource_id} 后，新的运行回合不会再加载它。`,
    positiveText: '停用',
    onPositiveClick: async () => {
      busyResourceId.value = resource.resource_id
      try {
        await run(() => disableResource(resource.resource_id), '停用资源失败')
        message.success('资源已停用')
        await loadResources()
      } finally {
        busyResourceId.value = ''
      }
    }
  })
}

const openDetail = (resource: ManagedResource) => {
  selectedResource.value = resource
  showDetailModal.value = true
}

const openUpdate = (resource: ManagedResource) => {
  selectedUpdate.value = resource
  delete updateChecks.value[resource.resource_id]
  showUpdateModal.value = true
}

const checkRemoteUpdate = async (resource: ManagedResource) => {
  checkingResourceId.value = resource.resource_id
  try {
    const results = await checkResourceUpdates(resource.resource_id)
    const result = results.find((item) => item.resource_id === resource.resource_id) || {
      resource_id: resource.resource_id,
      update_available: false,
      error: '检查服务未返回结果'
    }
    updateChecks.value[resource.resource_id] = result
  } catch {
    updateChecks.value[resource.resource_id] = {
      resource_id: resource.resource_id,
      update_available: false,
      error: '检查更新失败'
    }
    message.error('检查资源更新失败，请稍后重试')
  } finally {
    checkingResourceId.value = ''
  }
}

const confirmRemoteUpdate = (resource: ManagedResource) => {
  const check = updateChecks.value[resource.resource_id]
  if (!check || check.error || !check.update_available) {
    message.warning('请先确认资源存在可用更新')
    return
  }
  ask({
    title: '确认更新资源',
    content: `更新 ${resource.resource_id} 会先在服务器创建当前版本备份，再从已登记来源获取新版本。`,
    positiveText: '更新',
    onPositiveClick: async () => {
      busyResourceId.value = resource.resource_id
      try {
        await run(() => updateRemoteResource(resource.resource_id), '更新资源失败')
        message.success('资源更新完成')
        delete updateChecks.value[resource.resource_id]
        showUpdateModal.value = false
        await loadResources()
      } finally {
        busyResourceId.value = ''
      }
    }
  })
}

const onFileListChange = (files: UploadFileInfo[]) => {
  selectedFile.value = files[0]?.file || null
}

const chooseLocalInstall = () => {
  showImportModal.value = true
  panel.value = null
}

const confirmImport = async () => {
  if (!selectedFile.value) {
    message.warning('请先选择资源包')
    return
  }
  ask({
    title: '确认导入离线资源',
    content: '资源包会在服务器内校验并写入受控资源目录，导入成功后不会直接执行包内文件。',
    positiveText: '导入',
    onPositiveClick: async () => {
      importLoading.value = true
      try {
        await run(() => importResource(selectedFile.value as File), '导入资源失败')
        message.success('资源已导入')
        showImportModal.value = false
        selectedFile.value = null
        await loadResources()
      } finally {
        importLoading.value = false
      }
    }
  })
}

const loadRepositories = async () => {
  repositoryLoading.value = true
  try {
    repositories.value = await run(listRepositories, '读取仓库来源失败')
  } finally {
    repositoryLoading.value = false
  }
}

const saveRepository = async () => {
  const form = repositoryForm.value
  if (!form.owner.trim() || !form.name.trim()) {
    message.warning('请填写仓库所有者和仓库名称')
    return
  }
  await run(() => addRepository(form.owner.trim(), form.name.trim(), form.branch.trim() || 'main'), '登记仓库失败')
  message.success('仓库来源已登记')
  repositoryForm.value = { owner: '', name: '', branch: 'main' }
  await loadRepositories()
}

const toggleRepository = async (repository: ResourceRepository) => {
  await run(
    () => setRepositoryEnabled(repository.owner, repository.name, repository.branch, !repository.enabled),
    '更新仓库状态失败'
  )
  await loadRepositories()
}

const searchRemote = async (offset = 0) => {
  remoteLoading.value = true
  remoteSearched.value = true
  remoteOffset.value = offset
  try {
    const result = await run(
      () => searchResourceCatalog(
        remoteType.value === 'all' ? undefined : remoteType.value,
        remoteQuery.value.trim(),
        remoteLimit,
        offset
      ),
      '搜索资源目录失败'
    )
    remoteResults.value = result.items
    remoteTotal.value = result.total_count
    remoteStatus.value = result.remote?.status || 'not_requested'
    remoteError.value = result.remote?.error || ''
  } catch {
    remoteResults.value = []
    remoteTotal.value = 0
    remoteStatus.value = 'error'
    remoteError.value = '资源目录暂时无法访问，请稍后重试'
  } finally {
    remoteLoading.value = false
  }
}

const openCatalogDetail = async (item: CatalogItem) => {
  const detail = await run(
    () => getCatalogItem(item.catalog_id),
    '读取目录资源详情失败'
  )
  selectedCatalogItem.value = { ...detail, branch: item.branch || detail.branch }
  showCatalogDetailModal.value = true
}

const installCatalog = (item: CatalogItem) => {
  ask({
    title: '确认安装资源',
    content: `将从 ${catalogSourceLabel(item)} 获取“${item.name}”，并由服务器保存到受控资源目录。安装完成后仍需单独启用。`,
    positiveText: '安装',
    onPositiveClick: async () => {
      remoteLoading.value = true
      try {
        await run(() => installCatalogItem(item.catalog_id, item.branch), '安装资源失败')
        message.success('资源已安装，启用前需要确认')
        await Promise.all([loadResources(), searchRemote()])
        if (showCatalogDetailModal.value && selectedCatalogItem.value?.catalog_id === item.catalog_id) {
          try {
            const detail = await getCatalogItem(item.catalog_id)
            selectedCatalogItem.value = { ...detail, branch: item.branch || detail.branch }
          } catch {
            showCatalogDetailModal.value = false
          }
        }
      } finally {
        remoteLoading.value = false
      }
    }
  })
}

const loadBackups = async () => {
  backupLoading.value = true
  try {
    backups.value = await run(listResourceBackups, '读取备份失败')
  } finally {
    backupLoading.value = false
  }
}

const restoreBackup = (backup: ResourceBackup) => {
  ask({
    title: '确认恢复资源备份',
    content: `恢复 ${backup.resource_id} 的 ${backup.version} 版本会改变当前资源状态，并在恢复前保留当前版本。`,
    positiveText: '恢复',
    onPositiveClick: async () => {
      await run(() => restoreResourceBackup(backup.backup_id, true), '恢复备份失败')
      message.success('备份已恢复')
      await Promise.all([loadResources(), loadBackups()])
    }
  })
}

const removeBackup = (backup: ResourceBackup) => {
  ask({
    title: '确认删除备份',
    content: `删除备份 ${backup.backup_id} 后无法从资源管理页恢复该版本。`,
    positiveText: '删除',
    onPositiveClick: async () => {
      await run(() => deleteResourceBackup(backup.backup_id, true), '删除备份失败')
      message.success('备份已删除')
      await loadBackups()
    }
  })
}

const loadRelations = async () => {
  relationLoading.value = true
  try {
    agents.value = await run(listAgents, '读取 Agent 关系失败')
  } finally {
    relationLoading.value = false
  }
}

const dependencyStatusLabel = (dependency: SystemDependency) => {
  if (dependency.ready) return '已就绪'
  if (dependency.status === 'unknown') return '未检查'
  if (dependency.status === 'cancelled') return '已取消'
  if (dependency.status === 'failed') return '检查失败'
  return '未就绪'
}

const dependencyStatusType = (dependency: SystemDependency) => {
  if (dependency.ready) return 'success' as const
  if (dependency.status === 'failed') return 'error' as const
  if (dependency.status === 'cancelled') return 'warning' as const
  return 'default' as const
}

const dependencyTaskStatusLabel = (status: string) =>
  ({ queued: '排队中', running: '安装中', succeeded: '已完成', failed: '失败', cancelled: '已取消' })[status] || status

const dependencyTaskStatusType = (status: string) => {
  if (status === 'succeeded') return 'success' as const
  if (status === 'failed') return 'error' as const
  if (status === 'queued' || status === 'running') return 'warning' as const
  return 'default' as const
}

const dependencyName = (dependencyId: string) =>
  systemDependencies.value.find((item) => item.dependency_id === dependencyId)?.name || dependencyId

const activeDependencyTask = (dependencyId: string) =>
  dependencyTasks.value.find(
    (task) => task.dependency_id === dependencyId && ['queued', 'running'].includes(task.status)
  )

const clearDependencyPolling = () => {
  if (dependencyPollTimer !== null) {
    clearTimeout(dependencyPollTimer)
    dependencyPollTimer = null
  }
}

const scheduleDependencyPolling = () => {
  clearDependencyPolling()
  if (
    panel.value !== 'dependencies' ||
    !dependencyTasks.value.some((task) => ['queued', 'running'].includes(task.status))
  ) return
  dependencyPollTimer = setTimeout(() => {
    void loadDependencyData(false)
  }, 2000)
}

const loadSystemDependencies = async (reportError = true) => {
  dependencyLoading.value = true
  try {
    systemDependencies.value = await listSystemDependencies()
  } catch (error) {
    if (reportError) {
      const detail = error instanceof Error ? error.message : '未知错误'
      message.error(`读取系统依赖失败：${detail}`)
    }
  } finally {
    dependencyLoading.value = false
  }
}

const loadDependencyTaskList = async (reportError = true) => {
  dependencyTaskLoading.value = true
  try {
    dependencyTasks.value = await listDependencyTasks()
  } catch (error) {
    if (reportError) {
      const detail = error instanceof Error ? error.message : '未知错误'
      message.error(`读取依赖任务失败：${detail}`)
    }
  } finally {
    dependencyTaskLoading.value = false
    scheduleDependencyPolling()
  }
}

const loadDependencyData = async (reportError = true) => {
  await Promise.all([
    loadSystemDependencies(reportError),
    loadDependencyTaskList(reportError)
  ])
}

const probeDependency = async (dependency: SystemDependency) => {
  busyDependencyId.value = dependency.dependency_id
  try {
    const result = await run(
      () => probeSystemDependency(dependency.dependency_id),
      '检查系统依赖失败'
    )
    systemDependencies.value = systemDependencies.value.map((item) =>
      item.dependency_id === result.dependency_id ? result : item
    )
    message.success(`${dependency.name} 检查完成`)
  } finally {
    busyDependencyId.value = ''
  }
}

const installDependency = (dependency: SystemDependency) => {
  ask({
    title: '确认安装系统依赖',
    content: `服务器将使用已登记的受控安装流程处理 ${dependency.name}。该操作可能修改 VPS 的工具环境。`,
    positiveText: '安装',
    onPositiveClick: async () => {
      busyDependencyId.value = dependency.dependency_id
      try {
        await run(
          () => installSystemDependency(dependency.dependency_id, true),
          '创建依赖安装任务失败'
        )
        message.success('依赖安装任务已创建')
        await loadDependencyData()
      } finally {
        busyDependencyId.value = ''
      }
    }
  })
}

const retryDependency = (task: DependencyInstallTask) => {
  ask({
    title: '确认重试依赖任务',
    content: `服务器将为 ${dependencyName(task.dependency_id)} 创建新的受控安装任务。`,
    positiveText: '重试',
    onPositiveClick: async () => {
      busyDependencyTaskId.value = task.task_id
      try {
        await run(() => retryDependencyTask(task.task_id, true), '重试依赖任务失败')
        message.success('依赖重试任务已创建')
        await loadDependencyData()
      } finally {
        busyDependencyTaskId.value = ''
      }
    }
  })
}

const cancelDependency = async (task: DependencyInstallTask) => {
  busyDependencyTaskId.value = task.task_id
  try {
    await run(() => cancelDependencyTask(task.task_id), '取消依赖任务失败')
    message.success('已请求取消依赖任务')
    await loadDependencyData()
  } finally {
    busyDependencyTaskId.value = ''
  }
}

const loadAudit = async () => {
  auditLoading.value = true
  try {
    const page = await run(() => listResourceAudit({
      correlationId: auditFilters.value.correlationId.trim() || undefined,
      component: auditFilters.value.component || undefined,
      outcome: auditFilters.value.outcome || undefined
    }, (auditPage.value - 1) * 10, 10), '读取审计记录失败')
    audits.value = page.items
    auditTotal.value = page.total
  } finally {
    auditLoading.value = false
  }
}

const applyAuditFilters = async () => {
  auditPage.value = 1
  await loadAudit()
}

const handleAuditPageChange = async (page: number) => {
  auditPage.value = page
  await loadAudit()
}

const bindWorkflow = (resource: ManagedResource) => {
  if (!workflowId.value.trim()) {
    message.warning('请输入工作流 ID')
    return
  }
  ask({
    title: '确认绑定工作流',
    content: `资源将关联到工作流 ${workflowId.value.trim()}，运行时只会复用已存在的工作流。`,
    positiveText: '绑定',
    onPositiveClick: async () => {
      await run(() => bindResourceWorkflow(resource.resource_id, workflowId.value.trim()), '绑定工作流失败')
      message.success('工作流已绑定')
      workflowId.value = ''
      await loadResources()
    }
  })
}

const resourceColumns: DataTableColumns<ManagedResource> = [
  {
    title: '资源',
    key: 'resource_id',
    width: 240,
    render: (row) =>
      h('div', { class: 'resource-name' }, [
        h('strong', row.resource_id),
        h('small', `${typeLabel(row.type)} · ${sourceLabel(row)}`)
      ])
  },
  {
    title: '版本',
    key: 'current_version',
    width: 110,
    render: (row) => h('span', { class: 'mono' }, row.current_version)
  },
  {
    title: '权限',
    key: 'permissions',
    width: 230,
    render: (row) =>
      h('div', { class: 'permission-list' },
        row.permissions.length ? row.permissions.map((permission) => h(NTag, { size: 'small', bordered: false }, { default: () => permission })) : [h('span', '未声明')]
      )
  },
  {
    title: '状态',
    key: 'enabled',
    width: 100,
    render: (row) =>
      h(NTag, { type: row.enabled ? 'success' : 'default', bordered: false }, { default: () => (row.enabled ? '已启用' : '未启用') })
  },
  {
    title: '操作',
    key: 'actions',
    width: 170,
    render: (row) =>
      h(NSpace, { size: 6 }, {
        default: () => [
          h(NTooltip, {}, { trigger: () => h(NButton, { quaternary: true, circle: true, 'aria-label': '查看资源', onClick: () => openDetail(row) }, { icon: () => h(NIcon, { 'aria-hidden': 'true' }, { default: () => h(EyeOutline) }) }), default: () => '查看资源详情' }),
          h(NTooltip, {}, { trigger: () => h(NButton, { quaternary: true, circle: true, 'aria-label': '更新资源', onClick: () => openUpdate(row) }, { icon: () => h(NIcon, { 'aria-hidden': 'true' }, { default: () => h(RefreshOutline) }) }), default: () => '打开更新入口' }),
          h(NButton, {
            type: row.enabled ? 'warning' : 'primary',
            size: 'small',
            loading: busyResourceId.value === row.resource_id,
            'aria-label': row.enabled ? '停用资源' : '启用资源',
            onClick: () => (row.enabled ? disable(row) : enable(row))
          }, { default: () => (row.enabled ? '停用' : '启用') })
        ]
      })
  }
]

const backupColumns: DataTableColumns<ResourceBackup> = [
  { title: '资源', key: 'resource_id' },
  { title: '版本', key: 'version' },
  { title: '原因', key: 'reason' },
  { title: '创建时间', key: 'created_at', render: (row) => formatDate(row.created_at) },
  {
    title: '操作',
    key: 'actions',
    render: (row) => h(NSpace, { size: 4 }, { default: () => [h(NButton, { size: 'small', onClick: () => restoreBackup(row) }, { default: () => '恢复' }), h(NButton, { size: 'small', type: 'error', quaternary: true, 'aria-label': '删除备份', onClick: () => removeBackup(row) }, { icon: () => h(NIcon, { 'aria-hidden': 'true' }, { default: () => h(TrashOutline) }) })] })
  }
]

const dependencyColumns: DataTableColumns<SystemDependency> = [
  {
    title: '依赖',
    key: 'name',
    width: 240,
    render: (row) => h('div', { class: 'dependency-name' }, [
      h('strong', row.name),
      h('small', row.description)
    ])
  },
  {
    title: '用于',
    key: 'required_by',
    width: 210,
    render: (row) => h('div', { class: 'dependency-tags' },
      row.required_by.map((item) => h(NTag, { size: 'small', bordered: false }, { default: () => item }))
    )
  },
  {
    title: '就绪状态',
    key: 'status',
    width: 180,
    render: (row) => h('div', { class: 'dependency-status' }, [
      h(NTag, { type: dependencyStatusType(row), bordered: false }, { default: () => dependencyStatusLabel(row) }),
      h('small', row.version || `检查时间：${formatDate(row.checked_at || undefined)}`)
    ])
  },
  {
    title: '操作',
    key: 'actions',
    width: 210,
    render: (row) => h(NSpace, { size: 6, wrap: true }, {
      default: () => [
        h(NButton, {
          size: 'small',
          secondary: true,
          loading: busyDependencyId.value === row.dependency_id,
          'aria-label': `检查 ${row.name}`,
          onClick: () => probeDependency(row)
        }, { default: () => '检查' }),
        row.install_supported
          ? h(NButton, {
              size: 'small',
              type: 'primary',
              disabled: Boolean(activeDependencyTask(row.dependency_id)),
              loading: busyDependencyId.value === row.dependency_id,
              'aria-label': `安装 ${row.name}`,
              onClick: () => installDependency(row)
            }, { default: () => row.ready ? '重新检查安装' : '安装' })
          : h('span', { class: 'operator-guidance' }, row.operator_guidance || '需要 VPS 运维处理')
      ]
    })
  }
]

const dependencyTaskColumns: DataTableColumns<DependencyInstallTask> = [
  {
    title: '依赖',
    key: 'dependency_id',
    width: 210,
    render: (row) => h('div', { class: 'dependency-name' }, [
      h('strong', dependencyName(row.dependency_id)),
      h('small', row.retry_of ? '重试任务' : '安装任务')
    ])
  },
  {
    title: '任务状态',
    key: 'status',
    width: 120,
    render: (row) => h(NTag, { type: dependencyTaskStatusType(row.status), bordered: false }, {
      default: () => dependencyTaskStatusLabel(row.status)
    })
  },
  {
    title: '结果',
    key: 'error_summary',
    width: 260,
    render: (row) => h('div', { class: 'dependency-result' }, [
      h('span', row.error_summary || (row.status === 'succeeded' ? '依赖已通过安装后检查' : '等待任务状态更新')),
      h('small', formatDate(row.finished_at || row.started_at || row.created_at))
    ])
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render: (row) => h(NSpace, { size: 4 }, {
      default: () => [
        ['failed', 'cancelled'].includes(row.status)
          ? h(NButton, {
              size: 'small',
              loading: busyDependencyTaskId.value === row.task_id,
              'aria-label': '重试依赖任务',
              onClick: () => retryDependency(row)
            }, { default: () => '重试' })
          : null,
        ['queued', 'running'].includes(row.status)
          ? h(NButton, {
              size: 'small',
              type: 'warning',
              secondary: true,
              loading: busyDependencyTaskId.value === row.task_id,
              'aria-label': '取消依赖任务',
              onClick: () => cancelDependency(row)
            }, { default: () => '取消' })
          : null
      ]
    })
  }
]

const auditColumns: DataTableColumns<AuditRecord> = [
  { title: '时间', key: 'timestamp', render: (row) => formatDate(row.timestamp) },
  { title: '组件', key: 'component', render: (row) => row.component || row.type || '资源生命周期' },
  { title: '资源', key: 'resource_id', render: (row) => row.resource_id || row.server || '未记录' },
  { title: '操作', key: 'operation' },
  { title: '结果', key: 'outcome', render: (row) => row.outcome || row.result || row.status || '未记录' },
  { title: '关联 ID', key: 'correlation_id', render: (row) => row.correlation_id || '未记录' }
]

const openPanel = async (name: Exclude<PanelName, null>) => {
  clearDependencyPolling()
  panel.value = name
  if (name === 'discover') await Promise.all([loadRepositories(), searchRemote()])
  if (name === 'backups') await loadBackups()
  if (name === 'relations') await loadRelations()
  if (name === 'dependencies') await loadDependencyData()
}

const closePanel = () => {
  panel.value = null
  clearDependencyPolling()
}

const updateDependencyLayout = () => {
  compactDependencyLayout.value = window.innerWidth <= 768
}

onMounted(() => {
  window.addEventListener('resize', updateDependencyLayout)
  void loadResources()
  if (route.query.panel === 'dependencies') {
    void openPanel('dependencies')
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateDependencyLayout)
  clearDependencyPolling()
})
</script>

<template>
  <main id="resource-main" class="resource-page" aria-labelledby="resource-page-title">
    <header class="page-header">
      <div>
        <div class="eyebrow"><n-icon aria-hidden="true"><shield-checkmark-outline /></n-icon>服务器资源目录</div>
        <h1 id="resource-page-title">资源管理</h1>
        <p>管理 Prompt、Skill、Memory、Session、MCP 和 Hook 的版本、权限与运行时状态。</p>
      </div>
      <n-space class="header-actions" :wrap="true">
        <n-button secondary @click="reload"><template #icon><n-icon aria-hidden="true"><refresh-outline /></n-icon></template>刷新</n-button>
        <n-button secondary @click="openPanel('backups')"><template #icon><n-icon aria-hidden="true"><archive-outline /></n-icon></template>备份与恢复</n-button>
        <n-button secondary aria-label="系统依赖" @click="openPanel('dependencies')"><template #icon><n-icon aria-hidden="true"><build-outline /></n-icon></template>系统依赖</n-button>
        <n-button type="primary" aria-label="发现并安装资源" @click="openPanel('discover')"><template #icon><n-icon aria-hidden="true"><search-outline /></n-icon></template>发现并安装</n-button>
      </n-space>
    </header>

    <n-alert v-if="errorMessage" type="error" closable @close="errorMessage = ''">{{ errorMessage }}</n-alert>

    <section class="summary-strip" aria-label="资源摘要">
      <div><span>资源数量</span><strong>{{ resources.length }}</strong></div>
      <div><span>已启用</span><strong>{{ resources.filter((item) => item.enabled).length }}</strong></div>
      <div><span>需要确认</span><strong>{{ resources.filter((item) => item.confirmation_required).length }}</strong></div>
      <div><span>服务器存储</span><strong class="summary-note">受控目录</strong></div>
    </section>

    <n-card class="workspace-card">
      <h2 class="card-section-title">已安装资源</h2>
      <div class="resource-toolbar">
        <n-select :value="resourceType" :options="typeOptions" :input-props="{ 'aria-label': '资源类型筛选' }" @update:value="changeType" />
        <n-button quaternary @click="chooseLocalInstall"><template #icon><n-icon aria-hidden="true"><cloud-upload-outline /></n-icon></template>离线导入</n-button>
      </div>

      <div v-if="loading" class="loading-state" aria-busy="true"><n-skeleton text :repeat="4" /><n-skeleton text style="width: 82%" /></div>
      <n-empty v-else-if="!hasResources" description="暂无已安装资源">
        <template #extra><n-button type="primary" aria-label="安装第一个资源" @click="openPanel('discover')"><template #icon><n-icon aria-hidden="true"><search-outline /></n-icon></template>安装第一个资源</n-button></template>
      </n-empty>
      <n-data-table v-else class="resource-table" :columns="resourceColumns" :data="visibleResources" :pagination="false" :bordered="false" :single-line="false" :scroll-x="850" />
    </n-card>

    <section class="lower-grid">
      <n-card class="workspace-card">
        <h2 class="card-section-title">运行时关系</h2>
        <div class="section-intro"><n-icon aria-hidden="true"><people-outline /></n-icon><span>资源最终由 Agent 绑定后进入对话运行时。查看哪些 Agent、渠道和会话会使用它。</span></div>
        <n-button secondary @click="openPanel('relations')"><template #icon><n-icon aria-hidden="true"><people-outline /></n-icon></template>查看 Agent 关系</n-button>
      </n-card>
      <n-card class="workspace-card">
        <h2 class="card-section-title">变更审计</h2>
        <div class="section-intro"><n-icon aria-hidden="true"><build-outline /></n-icon><span>服务器仅展示元数据审计，不在界面回显资源正文或凭据字段。</span></div>
        <n-button secondary @click="loadAudit()"><template #icon><n-icon aria-hidden="true"><eye-outline /></n-icon></template>加载审计记录</n-button>
      </n-card>
    </section>

    <n-card v-if="audits.length || auditLoading" class="workspace-card audit-card">
      <h2 class="card-section-title">最近变更</h2>
      <div class="audit-toolbar">
        <n-input v-model:value="auditFilters.correlationId" clearable placeholder="关联 ID" aria-label="按关联 ID 筛选审计" @keyup.enter="applyAuditFilters" />
        <n-select v-model:value="auditFilters.component" clearable placeholder="全部组件" aria-label="按组件筛选审计" :options="[
          { label: '资源生命周期', value: 'resource_lifecycle' },
          { label: 'Agent', value: 'agent_runtime' },
          { label: 'Hook', value: 'agent_hook' },
          { label: 'MCP', value: 'mcp' }
        ]" />
        <n-select v-model:value="auditFilters.outcome" clearable placeholder="全部结果" aria-label="按结果筛选审计" :options="[
          { label: '成功', value: 'success' },
          { label: '失败', value: 'failure' },
          { label: '错误', value: 'error' },
          { label: '拒绝', value: 'denied' }
        ]" />
        <n-button secondary :loading="auditLoading" @click="applyAuditFilters"><template #icon><n-icon aria-hidden="true"><search-outline /></n-icon></template>筛选</n-button>
      </div>
      <n-data-table :loading="auditLoading" :columns="auditColumns" :data="audits" :pagination="false" :bordered="false" :scroll-x="820" />
      <n-pagination v-if="auditTotal > 10" :page="auditPage" :page-count="Math.ceil(auditTotal / 10)" @update:page="handleAuditPageChange" />
    </n-card>

    <n-modal v-model:show="showDetailModal" preset="card" title="资源详情" aria-label="资源详情" class="resource-modal">
      <template v-if="selectedResource">
        <n-descriptions bordered :column="1" label-placement="left">
          <n-descriptions-item label="资源 ID">{{ selectedResource.resource_id }}</n-descriptions-item>
          <n-descriptions-item label="类型">{{ typeLabel(selectedResource.type) }}</n-descriptions-item>
          <n-descriptions-item label="当前版本"><span class="mono">{{ selectedResource.current_version }}</span></n-descriptions-item>
          <n-descriptions-item label="来源">{{ sourceLabel(selectedResource) }}</n-descriptions-item>
          <n-descriptions-item label="入口">{{ selectedResource.entry }}</n-descriptions-item>
          <n-descriptions-item label="内容摘要"><span class="mono">{{ shortHash(selectedResource.content_sha256) }}</span>
          </n-descriptions-item>
          <n-descriptions-item label="权限">
            <n-space :wrap="true">
              <n-tag v-for="permission in selectedResource.permissions" :key="permission" size="small">{{ permission }}</n-tag>
              <span v-if="!selectedResource.permissions.length">未声明</span>
            </n-space>
          </n-descriptions-item>
          <n-descriptions-item label="来源摘要">{{ sourceLabel(selectedResource) }}</n-descriptions-item>
          <n-descriptions-item label="工作流">{{ selectedResource.workflow_id || '未绑定' }}</n-descriptions-item>
          <n-descriptions-item label="更新时间">{{ formatDate(selectedResource.updated_at) }}</n-descriptions-item>
        </n-descriptions>
        <n-divider />
        <n-form inline @submit.prevent="bindWorkflow(selectedResource)">
          <n-form-item label="绑定工作流"><n-input v-model:value="workflowId" placeholder="例如 chat:normal" /></n-form-item>
          <n-button type="primary" attr-type="submit"><template #icon><n-icon aria-hidden="true"><git-branch-outline /></n-icon></template>绑定</n-button>
        </n-form>
      </template>
    </n-modal>

    <n-modal v-model:show="showUpdateModal" preset="card" :title="`更新 ${selectedUpdate?.resource_id || ''}`" :aria-label="`更新 ${selectedUpdate?.resource_id || ''}`" class="resource-modal">
      <n-alert type="info" :show-icon="true">更新只适用于已登记来源的资源。服务器会先创建备份，并在内容校验通过后写入新版本。</n-alert>
      <n-space vertical class="modal-content">
        <n-alert v-if="selectedUpdateCheck?.error" type="error" :show-icon="true">检查更新失败，请重试。</n-alert>
        <n-alert v-else-if="selectedUpdateCheck && !selectedUpdateCheck.update_available" type="success" :show-icon="true">当前版本 {{ selectedUpdate?.current_version }} 已是最新。</n-alert>
        <n-alert v-else-if="selectedUpdateCheck" type="warning" :show-icon="true">发现新版本 {{ selectedUpdateCheck.next_version || '待确定' }}，当前版本为 {{ selectedUpdate?.current_version }}。</n-alert>
        <n-button v-if="selectedUpdate" secondary :loading="checkingResourceId === selectedUpdate.resource_id" aria-label="检查资源更新" @click="checkRemoteUpdate(selectedUpdate)">
          <template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>{{ selectedUpdateCheck ? '重新检查' : '检查更新' }}
        </n-button>
        <n-button v-if="selectedUpdate && selectedUpdateCheck && !selectedUpdateCheck.error && selectedUpdateCheck.update_available" type="primary" :loading="busyResourceId === selectedUpdate.resource_id" aria-label="执行资源更新" @click="confirmRemoteUpdate(selectedUpdate)">
          <template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>更新
        </n-button>
        <n-button v-if="selectedUpdate" secondary @click="openDetail(selectedUpdate)">
          <template #icon><n-icon aria-hidden="true"><eye-outline /></n-icon></template>查看当前版本
        </n-button>
      </n-space>
    </n-modal>

    <n-modal v-model:show="showImportModal" preset="card" title="离线导入资源" aria-label="离线导入资源" class="resource-modal">
      <n-alert type="warning" :show-icon="true">这是离线兜底入口。在线资源请从“发现并安装”进入，上传包会在服务器端校验、隔离和版本化保存。</n-alert>
      <n-upload accept=".zip" :default-upload="false" :max="1" @update:file-list="onFileListChange">
        <n-upload-dragger><n-icon size="28" aria-hidden="true"><cloud-upload-outline /></n-icon><div class="upload-title">选择资源包</div><div class="upload-hint">仅上传受控资源包，不会直接执行包内入口</div></n-upload-dragger>
      </n-upload>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showImportModal = false">取消</n-button>
          <n-button type="primary" :loading="importLoading" @click="confirmImport">导入</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal :show="isPanelOpen && panel === 'discover'" preset="card" title="发现并安装资源" aria-label="发现并安装资源" class="resource-modal discover-modal" @update:show="(value) => !value && (panel = null)">
      <n-card :bordered="false" size="small">
        <h2 class="card-section-title">统一资源目录</h2>
        <n-form inline @submit.prevent="searchRemote(0)">
          <n-form-item label="资源类型">
            <n-select v-model:value="remoteType" :options="catalogTypeOptions" clearable :input-props="{ 'aria-label': '目录资源类型' }" />
          </n-form-item>
          <n-form-item label="关键词"><n-input v-model:value="remoteQuery" placeholder="搜索资源名称或描述" clearable /></n-form-item>
          <n-button type="primary" attr-type="submit" :loading="remoteLoading"><template #icon><n-icon aria-hidden="true"><search-outline /></n-icon></template>搜索</n-button>
        </n-form>
        <div v-if="remoteLoading" class="loading-state"><n-skeleton text :repeat="3" /></div>
        <n-alert v-else-if="remoteStatus === 'error'" type="warning" :show-icon="true">
          {{ remoteError || '在线资源索引暂时不可用，当前仅展示本地资源。' }}
        </n-alert>
        <n-empty v-else-if="remoteSearched && !remoteResults.length" description="没有匹配的资源" />
        <div v-else class="remote-results">
          <article v-for="item in remoteResults" :key="item.catalog_id" class="remote-result">
            <div class="catalog-result-main">
              <div class="catalog-result-heading">
                <strong>{{ item.name }}</strong>
                <n-tag size="small" :type="item.installed ? 'success' : 'default'">{{ item.installed ? '已安装' : typeLabel(item.type) }}</n-tag>
              </div>
              <span>{{ typeLabel(item.type) }} · {{ item.version || '版本待定' }} · {{ catalogSourceLabel(item) }}</span>
              <p>{{ item.description || '暂无描述' }}</p>
              <small v-if="item.branch || item.installs">{{ item.branch ? `分支 ${item.branch}` : '' }}{{ item.branch && item.installs ? ' · ' : '' }}{{ item.installs ? `${item.installs} 次安装` : '' }}</small>
            </div>
            <n-space class="catalog-result-actions">
              <n-button quaternary circle aria-label="查看目录资源" @click="openCatalogDetail(item)"><template #icon><n-icon aria-hidden="true"><eye-outline /></n-icon></template></n-button>
              <n-button type="primary" size="small" :disabled="item.installed" :aria-label="item.installed ? '资源已安装' : '安装目录资源'" @click="installCatalog(item)"><template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>{{ item.installed ? (item.enabled ? '已启用' : '已安装') : '安装' }}</n-button>
            </n-space>
          </article>
        </div>
        <n-pagination v-if="remoteTotal > remoteLimit" :page="Math.floor(remoteOffset / remoteLimit) + 1" :page-count="Math.ceil(remoteTotal / remoteLimit)" @update:page="(page) => searchRemote((page - 1) * remoteLimit)" />
      </n-card>
      <n-divider />
      <n-card :bordered="false" size="small">
        <h2 class="card-section-title">仓库来源</h2>
        <n-form inline @submit.prevent="saveRepository"><n-form-item label="所有者"><n-input v-model:value="repositoryForm.owner" placeholder="owner" /></n-form-item><n-form-item label="仓库"><n-input v-model:value="repositoryForm.name" placeholder="repository" /></n-form-item><n-form-item label="分支"><n-input v-model:value="repositoryForm.branch" placeholder="main" /></n-form-item><n-button type="primary" attr-type="submit">登记</n-button></n-form>
        <n-data-table v-if="repositories.length" :loading="repositoryLoading" :data="repositories" :columns="[{ title: '仓库', key: 'name', render: (row: ResourceRepository) => `${row.owner}/${row.name}` }, { title: '分支', key: 'branch' }, { title: '状态', key: 'enabled', render: (row: ResourceRepository) => h(NTag, { type: row.enabled ? 'success' : 'default' }, { default: () => row.enabled ? '已启用' : '已停用' }) }, { title: '操作', key: 'actions', render: (row: ResourceRepository) => h(NButton, { size: 'small', onClick: () => toggleRepository(row) }, { default: () => row.enabled ? '停用' : '启用' }) }]" :pagination="false" :bordered="false" :scroll-x="560" />
        <n-empty v-else description="尚未登记仓库来源" />
      </n-card>
    </n-modal>

    <n-modal v-model:show="showCatalogDetailModal" preset="card" title="目录资源详情" aria-label="目录资源详情" class="resource-modal">
      <template v-if="selectedCatalogItem">
        <n-descriptions bordered :column="1" label-placement="left">
          <n-descriptions-item label="名称">{{ selectedCatalogItem.name }}</n-descriptions-item>
          <n-descriptions-item label="类型">{{ typeLabel(selectedCatalogItem.type) }}</n-descriptions-item>
          <n-descriptions-item label="版本"><span class="mono">{{ selectedCatalogItem.version || '版本待定' }}</span></n-descriptions-item>
          <n-descriptions-item label="来源">{{ catalogSourceLabel(selectedCatalogItem) }}</n-descriptions-item>
          <n-descriptions-item v-if="selectedCatalogItem.branch" label="分支"><span class="mono">{{ selectedCatalogItem.branch }}</span></n-descriptions-item>
          <n-descriptions-item label="描述">{{ selectedCatalogItem.description || '暂无描述' }}</n-descriptions-item>
          <n-descriptions-item label="安装状态">{{ selectedCatalogItem.installed ? (selectedCatalogItem.enabled ? '已启用' : '已安装，未启用') : '未安装' }}</n-descriptions-item>
        </n-descriptions>
        <n-space justify="end" class="modal-content">
          <n-button v-if="!selectedCatalogItem.installed" type="primary" aria-label="安装详情目录资源" @click="installCatalog(selectedCatalogItem)"><template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>安装</n-button>
          <n-tag v-else type="success">已安装</n-tag>
        </n-space>
      </template>
    </n-modal>

    <n-modal :show="panel === 'dependencies'" preset="card" title="VPS 系统依赖" aria-label="VPS 系统依赖" class="resource-modal dependency-modal" @update:show="(value) => !value && closePanel()">
      <n-alert type="info" :show-icon="true">这里管理 Agent 运行所需的服务器依赖。依赖就绪状态、资源安装状态和 MCP 连接状态分别记录，安装任务会在服务器后台执行。</n-alert>
      <div class="dependency-section-heading">
        <div>
          <h2 class="card-section-title">运行环境</h2>
          <p class="section-caption">检查已登记的 CLI、运行时和浏览器环境，以及它们被哪些资源使用。</p>
        </div>
        <n-button secondary :loading="dependencyLoading || dependencyTaskLoading" aria-label="刷新系统依赖" @click="loadDependencyData()">
          <template #icon><n-icon aria-hidden="true"><refresh-outline /></n-icon></template>刷新
        </n-button>
      </div>
      <div v-if="dependencyLoading" class="loading-state" aria-busy="true"><n-skeleton text :repeat="4" /></div>
      <n-empty v-else-if="!systemDependencies.length" description="暂无系统依赖登记" />
      <div v-else-if="compactDependencyLayout" class="dependency-mobile-list">
        <article v-for="dependency in systemDependencies" :key="dependency.dependency_id" class="dependency-mobile-item">
          <div class="dependency-mobile-heading">
            <strong>{{ dependency.name }}</strong>
            <n-tag :type="dependencyStatusType(dependency)" size="small" :bordered="false">{{ dependencyStatusLabel(dependency) }}</n-tag>
          </div>
          <p>{{ dependency.description }}</p>
          <div class="dependency-tags">
            <n-tag v-for="item in dependency.required_by" :key="item" size="small" :bordered="false">{{ item }}</n-tag>
          </div>
          <small>{{ dependency.version || `检查时间：${formatDate(dependency.checked_at || undefined)}` }}</small>
          <n-space :wrap="true">
            <n-button size="small" secondary :loading="busyDependencyId === dependency.dependency_id" :aria-label="`检查 ${dependency.name}`" @click="probeDependency(dependency)">检查</n-button>
            <n-button v-if="dependency.install_supported" size="small" type="primary" :disabled="Boolean(activeDependencyTask(dependency.dependency_id))" :loading="busyDependencyId === dependency.dependency_id" :aria-label="`安装 ${dependency.name}`" @click="installDependency(dependency)">{{ dependency.ready ? '重新检查安装' : '安装' }}</n-button>
          </n-space>
          <span v-if="!dependency.install_supported" class="operator-guidance">{{ dependency.operator_guidance || '需要 VPS 运维处理' }}</span>
        </article>
      </div>
      <n-data-table v-else class="modal-table" :loading="dependencyLoading" :columns="dependencyColumns" :data="systemDependencies" :pagination="false" :bordered="false" :scroll-x="760" />

      <n-divider />
      <div class="dependency-section-heading">
        <div>
          <h2 class="card-section-title">安装任务</h2>
          <p class="section-caption">任务状态会自动刷新；输出仅保留经过处理的结果摘要。</p>
        </div>
      </div>
      <div v-if="dependencyTaskLoading" class="loading-state" aria-busy="true"><n-skeleton text :repeat="3" /></div>
      <n-empty v-else-if="!dependencyTasks.length" description="暂无依赖安装任务" />
      <div v-else-if="compactDependencyLayout" class="dependency-mobile-list">
        <article v-for="task in dependencyTasks" :key="task.task_id" class="dependency-mobile-item">
          <div class="dependency-mobile-heading">
            <strong>{{ dependencyName(task.dependency_id) }}</strong>
            <n-tag :type="dependencyTaskStatusType(task.status)" size="small" :bordered="false">{{ dependencyTaskStatusLabel(task.status) }}</n-tag>
          </div>
          <p>{{ task.error_summary || (task.status === 'succeeded' ? '依赖已通过安装后检查' : '等待任务状态更新') }}</p>
          <small>{{ task.retry_of ? '重试任务' : '安装任务' }} · {{ formatDate(task.finished_at || task.started_at || task.created_at) }}</small>
          <n-space :wrap="true">
            <n-button v-if="['failed', 'cancelled'].includes(task.status)" size="small" :loading="busyDependencyTaskId === task.task_id" aria-label="重试依赖任务" @click="retryDependency(task)">重试</n-button>
            <n-button v-if="['queued', 'running'].includes(task.status)" size="small" type="warning" secondary :loading="busyDependencyTaskId === task.task_id" aria-label="取消依赖任务" @click="cancelDependency(task)">取消</n-button>
          </n-space>
        </article>
      </div>
      <n-data-table v-else class="modal-table" :loading="dependencyTaskLoading" :columns="dependencyTaskColumns" :data="dependencyTasks" :pagination="false" :bordered="false" :scroll-x="680" />
    </n-modal>

    <n-modal :show="panel === 'backups'" preset="card" title="备份与恢复" aria-label="备份与恢复" class="resource-modal" @update:show="(value) => !value && closePanel()">
      <n-alert type="warning" :show-icon="true">备份只保留资源版本元数据和受控文件。恢复或删除备份前必须确认，操作会写入服务器审计记录。</n-alert>
      <n-data-table class="modal-table" :loading="backupLoading" :columns="backupColumns" :data="backups" :pagination="false" :bordered="false" :scroll-x="720" />
      <n-empty v-if="!backupLoading && !backups.length" description="暂无资源备份" />
    </n-modal>

    <n-modal :show="panel === 'relations'" preset="card" title="Agent 与资源关系" aria-label="Agent 与资源关系" class="resource-modal" @update:show="(value) => !value && closePanel()">
      <div v-if="relationLoading" class="loading-state"><n-skeleton text :repeat="4" /></div>
      <n-empty v-else-if="!agents.length" description="暂无 Agent 关系" />
      <div v-else class="agent-list">
        <article v-for="agent in agents" :key="agent.agent_id" class="agent-row agent-row-detailed">
          <div class="agent-heading">
            <strong>{{ agent.display_name || agent.agent_id }}</strong>
            <span>{{ agent.agent_id }} · {{ agent.enabled ? '已启用' : '已停用' }}{{ agent.relations.is_default ? ' · 默认 Agent' : '' }}</span>
          </div>
          <div class="agent-policy">
            <div class="policy-line"><b>模型链</b><span class="mono">{{ agent.model_priority.join(' -> ') || '未配置' }}</span></div>
            <div class="policy-line"><b>Provider</b><span>{{ agent.provider_allowlist.join(', ') || '不限制' }}</span></div>
            <div class="policy-line"><b>工具策略</b><span>{{ agent.allow_tools ? '允许' : '禁止' }} · {{ agent.max_tool_iterations }} 轮 · {{ agent.mcp_allowlist.join(', ') || '无 MCP 工具白名单' }}</span></div>
            <div class="policy-line"><b>能力</b><span>{{ agent.capabilities.join(', ') || '未声明' }}</span></div>
          </div>
          <div class="agent-relations">
            <n-tag v-for="channel in agent.relations.channels" :key="channel" size="small">{{ channel }}</n-tag>
            <span>{{ agent.relations.sessions.length }} 个会话</span>
            <span>{{ agent.relations.accounts.length }} 个账号</span>
          </div>
          <div class="binding-groups">
            <div v-for="group in bindingGroups(agent)" :key="group.label" class="binding-group">
              <b>{{ group.label }}</b>
              <n-tag v-for="binding in group.bindings" :key="`${binding.resource_id}:${binding.version}`" size="small" :type="binding.enabled ? 'success' : 'default'">
                {{ bindingText(binding) }}
              </n-tag>
            </div>
            <span v-if="!bindingGroups(agent).length" class="muted">尚未绑定资源</span>
          </div>
        </article>
      </div>
    </n-modal>
  </main>
</template>

<style scoped>
.resource-page { box-sizing: border-box; max-width: 1440px; margin: 0 auto; padding: 32px 36px 48px; color: var(--text-color); }
.resource-page :deep(.n-empty__description) { color: var(--text-color-secondary); }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { display: flex; align-items: center; gap: 6px; color: var(--primary-color-text); font-size: 13px; font-weight: 600; }
h1 { margin: 8px 0 6px; font-size: 30px; line-height: 1.2; letter-spacing: 0; }
.page-header p { margin: 0; color: var(--text-color-secondary); }
.header-actions { justify-content: flex-end; }
.summary-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; margin: 24px 0; overflow: hidden; border: 1px solid var(--border-color); border-radius: var(--border-radius-large); background: var(--border-color); }
.summary-strip > div { display: flex; flex-direction: column; gap: 6px; min-width: 0; padding: 16px 20px; background: var(--card-bg-color); }
.summary-strip span { color: var(--text-color-secondary); font-size: 13px; }
.summary-strip strong { font-size: 22px; font-weight: 650; }
.summary-note { color: var(--success-color-text); font-size: 16px !important; }
.workspace-card { margin-bottom: 20px; }
.card-section-title { margin: 0 0 16px; font-size: 18px; line-height: 1.4; font-weight: 600; }
.resource-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-bottom: 16px; }
.resource-toolbar :deep(.n-select) { width: 150px; }
.loading-state { display: grid; gap: 14px; min-height: 150px; padding: 18px 4px; }
.resource-name { display: flex; flex-direction: column; gap: 4px; min-width: 180px; }
.resource-name small { color: var(--text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.permission-list { display: flex; flex-wrap: wrap; gap: 4px; max-width: 250px; }
.lower-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.section-intro { display: flex; gap: 10px; min-height: 48px; margin-bottom: 16px; color: var(--text-color-secondary); line-height: 1.6; }
.section-intro .n-icon { flex: 0 0 auto; color: var(--primary-color); font-size: 20px; }
.audit-card .n-pagination { margin-top: 16px; justify-content: flex-end; }
.resource-modal { width: min(720px, calc(100vw - 32px)); }
.discover-modal { width: min(900px, calc(100vw - 32px)); }
.dependency-modal { width: min(980px, calc(100vw - 32px)); }
.modal-content { margin-top: 18px; }
.modal-table { margin-top: 18px; }
.dependency-section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-top: 20px; }
.dependency-section-heading .card-section-title { margin-bottom: 4px; }
.section-caption { margin: 0; color: var(--text-color-secondary); font-size: 13px; line-height: 1.5; }
.dependency-name, .dependency-status, .dependency-result { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dependency-name small, .dependency-status small, .dependency-result small { color: var(--text-color-secondary); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.dependency-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.dependency-mobile-list { display: grid; gap: 0; margin-top: 16px; }
.dependency-mobile-item { display: grid; gap: 10px; padding: 16px 0; border-bottom: 1px solid var(--border-color); }
.dependency-mobile-item:last-child { border-bottom: 0; }
.dependency-mobile-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.dependency-mobile-heading strong { min-width: 0; overflow-wrap: anywhere; }
.dependency-mobile-item p { margin: 0; color: var(--text-color-secondary); font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }
.dependency-mobile-item > small { color: var(--text-color-secondary); font-size: 12px; line-height: 1.45; }
.operator-guidance { display: inline-block; max-width: 220px; color: var(--text-color-secondary); font-size: 12px; line-height: 1.45; }
.upload-title { margin-top: 8px; font-weight: 600; }
.upload-hint { margin-top: 4px; color: var(--text-color-secondary); font-size: 13px; }
.remote-results { display: grid; gap: 8px; margin-top: 16px; }
.remote-result, .agent-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--border-color); }
.remote-result:last-child, .agent-row:last-child { border-bottom: 0; }
.remote-result strong, .agent-row strong { display: block; }
.remote-result span, .agent-row span { display: block; margin-top: 4px; color: var(--text-color-secondary); font-size: 13px; }
.remote-result p { margin: 8px 0 0; color: var(--text-color-secondary); line-height: 1.5; }
.agent-relations { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px 10px; }
.agent-relations span { display: inline-block; }
.agent-row-detailed { display: grid; grid-template-columns: minmax(150px, .8fr) minmax(260px, 1.4fr); align-items: start; }
.agent-heading, .agent-policy, .binding-groups { min-width: 0; }
.agent-policy { display: grid; gap: 6px; }
.policy-line { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; color: var(--text-color-secondary); font-size: 13px; }
.policy-line b, .binding-group b { color: var(--text-color); font-weight: 600; }
.policy-line span { min-width: 0; overflow-wrap: anywhere; }
.binding-groups { grid-column: 1 / -1; display: grid; gap: 8px; padding-top: 10px; border-top: 1px solid var(--border-color); }
.binding-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.binding-group b { width: 68px; font-size: 13px; }
.muted { color: var(--text-color-secondary); font-size: 13px; }

@media (max-width: 768px) {
  .resource-page { padding: 20px 16px 36px; }
  .page-header { flex-direction: column; gap: 16px; }
  .header-actions { justify-content: flex-start; }
  .summary-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lower-grid { grid-template-columns: 1fr; }
  .workspace-card :deep(.n-card-header) { align-items: flex-start; }
  .workspace-card :deep(.n-card-header__extra) { max-width: 100%; }
  .dependency-section-heading { align-items: stretch; flex-direction: column; }
}

@media (max-width: 480px) {
  h1 { font-size: 26px; }
  .summary-strip > div { padding: 14px; }
  .summary-strip strong { font-size: 19px; }
  .remote-result, .agent-row { flex-direction: column; }
  .agent-relations { justify-content: flex-start; }
  .agent-row-detailed { grid-template-columns: 1fr; }
  .binding-groups { grid-column: 1; }
}
</style>
