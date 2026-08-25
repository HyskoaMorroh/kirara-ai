<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
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
import {
  addRepository,
  bindResourceWorkflow,
  checkResourceUpdates,
  deleteResourceBackup,
  disableResource,
  enableResource,
  getCatalogItem,
  importResource,
  installCatalogItem,
  listResourceAudit,
  listResourceBackups,
  listRepositories,
  listResources,
  restoreResourceBackup,
  searchResourceCatalog,
  setRepositoryEnabled,
  updateRemoteResource
} from '@/api/resource'
import { listAgents } from '@/api/agent'
import type {
  AuditRecord,
  CatalogItem,
  ManagedResource,
  ResourceBackup,
  ResourceRepository,
  ResourceType,
  ResourceUpdateCheck
} from '@/api/resource'
import type { AgentSummary } from '@/api/agent'

type ResourceFilter = ResourceType | 'all'
type PanelName = 'install' | 'discover' | 'backups' | 'relations' | null

const message = useMessage()
const dialog = useDialog()
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
        await run(() => installCatalogItem(item.catalog_id, item.branch || 'main'), '安装资源失败')
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

const loadAudit = async (resourceId?: string) => {
  auditLoading.value = true
  try {
    const page = await run(() => listResourceAudit(resourceId, (auditPage.value - 1) * 10, 10), '读取审计记录失败')
    audits.value = page.items
    auditTotal.value = page.total
  } finally {
    auditLoading.value = false
  }
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

const auditColumns: DataTableColumns<AuditRecord> = [
  { title: '时间', key: 'timestamp', render: (row) => formatDate(row.timestamp) },
  { title: '资源', key: 'resource_id' },
  { title: '操作', key: 'operation' },
  { title: '结果', key: 'result' },
  { title: '版本', key: 'current_version' }
]

const openPanel = async (name: Exclude<PanelName, null>) => {
  panel.value = name
  if (name === 'discover') await Promise.all([loadRepositories(), searchRemote()])
  if (name === 'backups') await loadBackups()
  if (name === 'relations') await loadRelations()
}

onMounted(() => {
  void loadResources()
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
      <n-data-table :loading="auditLoading" :columns="auditColumns" :data="audits" :pagination="false" :bordered="false" :scroll-x="680" />
      <n-pagination v-if="auditTotal > 10" v-model:page="auditPage" :page-count="Math.ceil(auditTotal / 10)" @update:page="loadAudit" />
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

    <n-modal :show="panel === 'backups'" preset="card" title="备份与恢复" aria-label="备份与恢复" class="resource-modal" @update:show="(value) => !value && (panel = null)">
      <n-alert type="warning" :show-icon="true">备份只保留资源版本元数据和受控文件。恢复或删除备份前必须确认，操作会写入服务器审计记录。</n-alert>
      <n-data-table class="modal-table" :loading="backupLoading" :columns="backupColumns" :data="backups" :pagination="false" :bordered="false" :scroll-x="720" />
      <n-empty v-if="!backupLoading && !backups.length" description="暂无资源备份" />
    </n-modal>

    <n-modal :show="panel === 'relations'" preset="card" title="Agent 与资源关系" aria-label="Agent 与资源关系" class="resource-modal" @update:show="(value) => !value && (panel = null)">
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
.modal-content { margin-top: 18px; }
.modal-table { margin-top: 18px; }
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
