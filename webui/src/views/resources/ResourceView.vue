<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
  NList,
  NListItem,
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
  AddOutline,
  ArchiveOutline,
  BuildOutline,
  CheckmarkCircleOutline,
  CloudDownloadOutline,
  CloudUploadOutline,
  CreateOutline,
  EyeOutline,
  GitBranchOutline,
  OptionsOutline,
  PeopleOutline,
  RefreshOutline,
  SearchOutline,
  ShieldCheckmarkOutline,
  TrashOutline
} from '@vicons/ionicons5'
import { useRoute } from 'vue-router'
import {
  addRepository,
  authorResourceDocument,
  authorResourceDocumentVersion,
  bindResourceWorkflow,
  cancelDependencyTask,
  checkResourceUpdates,
  deleteResourceBackup,
  disableResource,
  discoverRepository,
  enableResource,
  getCatalogItem,
  getResourceContent,
  importResource,
  installCatalogItem,
  installImportableArchive,
  installRemoteSkill,
  installResource,
  installSystemDependency,
  listDependencyTasks,
  listImportableArchives,
  listResourceAudit,
  listResourceBackups,
  listRepositories,
  listResources,
  listSystemDependencies,
  probeSystemDependency,
  restoreResource,
  restoreResourceBackup,
  retryDependencyTask,
  searchResourceCatalog,
  searchSkills,
  removeRepository,
  setRepositoryEnabled,
  setResourceRuntime,
  updateRemoteResource,
  updateResource
} from '@/api/resource'
import { listAgents } from '@/api/agent'
import type {
  AuditRecord,
  CatalogItem,
  DependencyInstallTask,
  DiscoveredSkill,
  ImportableArchive,
  SkillsSearchResult,
  ManagedResource,
  ResourceBackup,
  ResourceContent,
  ResourceRepository,
  ResourceRuntimeOverrides,
  ResourceType,
  ResourceUpdateCheck,
  SystemDependency
} from '@/api/resource'
import type { AgentSummary } from '@/api/agent'
import { RESOURCE_TYPE_ORDER, countResourcesByType, matchesResourceKeyword } from './resourceFilter'
import { authoringFormError, suggestNextVersion } from './documentAuthoring'
import { entryDigestMatches as compareEntryDigest } from './entryDigest'
// 待安装包的三个显示判断（能不能点、按钮写什么、显示哪种标签）由
// `resource-staged-archives.test.ts` 调用函数验证：三态两两不同，
// 混淆任意一对都会误导（可升级的包被显示成已安装 → 用户不会去点）。
import { canInstallStaged, stagedActionLabel, stagedStatus } from './stagedArchives'
import { parseRepositoryCoordinate } from './repositoryCoordinate'

type ResourceFilter = ResourceType | 'all'
type PanelName =
  | 'install'
  | 'discover'
  | 'staged'
  | 'backups'
  | 'relations'
  | 'dependencies'
  // 从纯文本创建 / 编辑一条提示词、记忆或会话。
  | 'authoring'
  // 配置一条受管 MCP 资源在这台机器上怎么跑（目录、环境变量、超时）。
  | 'runtime'
  | null

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
/**
 * 登记仓库的表单。
 *
 * `coordinate` 接受粘贴进来的任意形态（`owner/name`、仓库主页 URL、
 * `git clone` 的地址、带 `/tree/<branch>` 的深链）；`branch` 只在坐标里没带分支
 * 时才生效。此前这里是三个独立输入框，而用户手上拿到的东西一定是一个 URL——
 * 要求他拆成三段手抄，正是拼错坐标的来源。
 */
const repositoryForm = ref({ coordinate: '', branch: '' })
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
/**
 * 离线包的两种用途。
 *
 * 后端是两个端点、两套语义：`POST /resources`（`installResource`）直接安装一个
 * **新**资源；`POST /resources/imports`（`importResource`）把一个已经准备好的包
 * 纳管。界面此前只接了后者，于是需求里的「从 ZIP 安装」实际落在了「导入」按钮上。
 * 现在同一个上传对话框按这个模式分流，不再混为一谈。
 */
const archiveMode = ref<'install' | 'import'>('install')
/** 上传 ZIP 升级到新版本时的目标资源；为空表示当前不是升级流程。 */
const versionUploadTarget = ref<ManagedResource | null>(null)
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

/**
 * 关键词：三个元数据面（名称 / ID / 描述）在前端即时过滤，**正文面走服务器**。
 *
 * 为什么不能全放前端：`GET /resources` 不返回正文。正文可能有几十 KB，
 * 且提示词正文里是用户写进去的规则——无条件塞进每一次列表响应，等于让一个
 * 只想看清单的请求把全部正文都取回浏览器。所以正文命中只有服务器算得出。
 *
 * 为什么不干脆全放服务器：那会让每敲一个字符都等一次网络往返。
 * 服务器返回的是「元数据命中 ∪ 正文命中」，是前端结果的**超集**，
 * 因此在请求回来之前先按元数据过滤，只会「少显示几行」而不会显示错的行——
 * 请求回来后正文命中的那几行补上，不会有行消失。
 */
const keyword = ref('')
/** 服务器算出的命中 ID（含正文面）。`null` = 还没搜 / 已清空。 */
const bodyMatchIds = ref<Set<string> | null>(null)
const searchingBody = ref(false)
const visibleResources = computed(() => {
  const needle = keyword.value.trim()
  if (!needle) return resources.value
  const ids = bodyMatchIds.value
  return resources.value.filter(
    (item) => matchesResourceKeyword(item, needle) || ids?.has(item.resource_id) === true
  )
})
/**
 * 滤空 ≠ 未安装，也 ≠ 「正文还在搜」。
 *
 * 正文请求在途时不报「没有匹配」：那时结论还没出来，
 * 先说没有再补上几行，比晚半秒给出答案更容易被读成 bug。
 */
const filteredEmpty = computed(
  () => resources.value.length > 0 && visibleResources.value.length === 0 && !searchingBody.value
)
const hasResources = computed(() => visibleResources.value.length > 0)
const isPanelOpen = computed(() => panel.value !== null)

/**
 * 摘要条的按类型分项。
 *
 * 统计的是 `resources`（服务端按当前 type 返回的全量）而不是 `visibleResources`：
 * 关键词是临时的取景框，分项要回答的是「这台机器上到底装了什么」。
 * 让分项跟着搜索框跳动，等于每敲一个字符就重画一次库存清单。
 */
const typeBreakdown = computed(() => {
  const counts = countResourcesByType(resources.value)
  const labelOf = (value: string) =>
    typeOptions.find((option) => option.value === value)?.label ?? value
  return RESOURCE_TYPE_ORDER.map((value) => ({ value, label: labelOf(value), count: counts[value] ?? 0 }))
})

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
  // 类型换了，正文命中是上一类的——不重搜就会把别的类型的 ID 留在集合里。
  runBodySearch()
}

/**
 * 服务器侧正文搜索，带节流。
 *
 * 节流而不是每次输入都发：正文命中要在服务器上逐条读文件并校验摘要，
 * 每敲一个字符发一次会让「边打边看」变成一串重复的全文件哈希。
 * 300ms 是「停手」与「还在打」的分界——比它短就是在为半个词做全文检索。
 *
 * 请求乱序会让旧关键词的结果覆盖新关键词的：`token` 只让最后一次生效。
 */
let bodySearchTimer: ReturnType<typeof setTimeout> | null = null
let bodySearchToken = 0
const runBodySearch = () => {
  if (bodySearchTimer !== null) clearTimeout(bodySearchTimer)
  const needle = keyword.value.trim()
  if (!needle) {
    // 清空搜索框时立刻丢掉正文命中，不留一份属于上一个关键词的集合。
    bodyMatchIds.value = null
    searchingBody.value = false
    bodySearchToken += 1
    return
  }
  searchingBody.value = true
  const token = ++bodySearchToken
  bodySearchTimer = setTimeout(async () => {
    try {
      const matched = await listResources(
        resourceType.value === 'all' ? undefined : resourceType.value,
        needle
      )
      if (token !== bodySearchToken) return
      bodyMatchIds.value = new Set(matched.map((item) => item.resource_id))
    } catch {
      // 正文搜索失败只丢正文这一面：元数据过滤仍然可用，
      // 让整个列表因为一次搜索失败而空掉才是更糟的结果。
      if (token === bodySearchToken) bodyMatchIds.value = null
    } finally {
      if (token === bodySearchToken) searchingBody.value = false
    }
  }, 300)
}

watch(keyword, () => runBodySearch())

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

/**
 * 从目录页直接启用一个刚装好的资源。
 *
 * 复用 `enable()` 的确认与刷新流程，只是入口在目录页——用户装完就在原地完成
 * 第二步，不必自己想起「还要去资源列表点启用」。此前那一步没有任何指引，
 * 而绑定区的灰按钮提示又在另一个页面上，两处不连通就是现场那个「全是灰的」。
 *
 * 启用之后重取目录：`installed_resource_id` 与 `enabled` 都来自服务端，
 * 本地改一个布尔会让界面与真实状态短暂不符，而那个不符恰好发生在用户
 * 最想确认「到底成没成」的时刻。
 */
const enableInstalledCatalogItem = (item: CatalogItem) => {
  const resourceId = item.installed_resource_id
  if (!resourceId) return
  ask({
    title: '确认启用资源',
    content: `启用 ${resourceId} 后，Agent 可能在后续对话中读取它的固定版本和权限。`,
    positiveText: '启用',
    onPositiveClick: async () => {
      busyResourceId.value = resourceId
      try {
        await run(() => enableResource(resourceId, true), '启用资源失败')
        message.success('资源已启用，现在可以在 Agent 里绑定它')
        await Promise.all([loadResources(), runDiscoverSearch(remoteOffset.value)])
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

/**
 * 从纯文本创建 / 编辑一条提示词、记忆或会话。
 *
 * 参考界面上「提示词管理」有一个「+ 添加提示词」按钮：填名称、描述、正文，保存。
 * 本项目此前唯一的写入路径是上传一个手工打包的 ZIP——用户得自己写
 * `manifest.json` 的八个必填字段、手算 `content_sha256`。而提示词这个类型的
 * 全部内容就是正文，要求为一段纯文本走那一遍等于把它最主要的用法排除在产品之外。
 *
 * 打包与摘要在服务器侧完成（`POST /resources/documents`），落盘后与一条内置
 * 提示词同形。**不提供就地编辑**：`content_sha256` 把清单与文件绑在一起，
 * 就地改的后果不是「改了没生效」而是下一次载入直接失败——所以改正文走版本递增。
 */
const AUTHORABLE_TYPES = ['prompt', 'memory', 'session'] as const
type AuthorableType = (typeof AUTHORABLE_TYPES)[number]

const authoringTypeOptions = AUTHORABLE_TYPES.map((value) => ({
  label: typeLabel(value),
  value
}))

const authoringForm = ref({
  resource_id: '',
  type: 'prompt' as AuthorableType,
  name: '',
  description: '',
  content: '',
  version: '1.0.0'
})
/** 非空表示在改一条已装资源的正文；此时 id 与类型不可改。 */
const authoringTarget = ref<ManagedResource | null>(null)
const authoringSaving = ref(false)

/** 只有纯文本类型能走这条路：skill 的正文会被当成行为说明执行，hook 能起进程。 */
const isAuthorable = (resource: ManagedResource) =>
  (AUTHORABLE_TYPES as readonly string[]).includes(resource.type)

/**
 * 受管 MCP 资源的运行时配置。
 *
 * 此前受管 MCP 资源完全没有配置入口：`PUT /mcp/servers/<id>` 只在
 * `config.mcp.servers` 里查，而受管资源住在资源注册表里——那条路由对它们一律
 * 返回 404。最明显的一条是 `mcp:filesystem`，它的描述要求「启用前必须在 args
 * 末尾追加允许访问的目录」，而在产品里没有任何地方能追加。
 *
 * 表单只覆盖「这台机器怎么跑它」那几个键。`command` / `args` / `type` / `url`
 * 是摘要保护的身份，后端会 400——这里连输入框都不给，避免让人以为可以改。
 */
const runtimeTarget = ref<ManagedResource | null>(null)
const runtimeSaving = ref(false)
/** 目录与环境变量在表单里是可增删的行，不是一段要用户自己拼的 JSON。 */
const runtimeForm = ref({
  extraArgs: [] as string[],
  env: [] as { key: string; value: string }[],
  cwd: '',
  roots: [] as string[],
  startupTimeoutMs: ''
})

/** 只有 mcp 资源有传输配置可言。 */
const isRuntimeConfigurable = (resource: ManagedResource) => resource.type === 'mcp'

const openRuntimePanel = (resource: ManagedResource) => {
  runtimeTarget.value = resource
  const overrides = resource.runtime_overrides || {}
  runtimeForm.value = {
    extraArgs: [...(overrides.extra_args || [])],
    // 值是掩码（`********`）。原样显示、原样比较：提交时只发改过的键，
    // 把掩码当真值回传会把凭据写成八个星号。
    env: Object.entries(overrides.env || {}).map(([key, value]) => ({ key, value })),
    cwd: overrides.cwd || '',
    roots: [...(overrides.roots || [])],
    startupTimeoutMs: overrides.startup_timeout_ms ? String(overrides.startup_timeout_ms) : ''
  }
  panel.value = 'runtime'
}

const addRuntimeArg = () => runtimeForm.value.extraArgs.push('')
const removeRuntimeArg = (index: number) => runtimeForm.value.extraArgs.splice(index, 1)
const addRuntimeRoot = () => runtimeForm.value.roots.push('')
const removeRuntimeRoot = (index: number) => runtimeForm.value.roots.splice(index, 1)
const addRuntimeEnv = () => runtimeForm.value.env.push({ key: '', value: '' })
const removeRuntimeEnv = (index: number) => runtimeForm.value.env.splice(index, 1)

/**
 * 超时的边界与后端逐字一致（1000–600000 毫秒）。
 *
 * 在这里拦下而不是等后端 400：一个越界值提交上去，返回的是英文校验串，
 * 而用户看不出自己填的哪个字段越界。
 */
const runtimeError = computed(() => {
  const raw = runtimeForm.value.startupTimeoutMs.trim()
  if (!raw) return ''
  if (!/^\d+$/.test(raw)) return '启动超时需为整数毫秒'
  const value = Number(raw)
  if (value < 1000 || value > 600000) return '启动超时需在 1000–600000 毫秒之间'
  return ''
})

const saveRuntimeOverrides = async () => {
  if (runtimeError.value) {
    message.error(runtimeError.value)
    return
  }
  const target = runtimeTarget.value
  if (!target) return
  const form = runtimeForm.value
  const stored = target.runtime_overrides || {}
  // 空数组与空串是「清空」，与后端约定一致；不提交的键才是「不动」。
  const payload: ResourceRuntimeOverrides = {
    extra_args: form.extraArgs.map((item) => item.trim()).filter(Boolean),
    roots: form.roots.map((item) => item.trim()).filter(Boolean),
    cwd: form.cwd.trim(),
    startup_timeout_ms: form.startupTimeoutMs.trim()
      ? Number(form.startupTimeoutMs.trim())
      : undefined
  }
  // 环境变量只发改过的键：读回来的值是掩码，回传等于把凭据写成掩码本身。
  const env: Record<string, string> = {}
  for (const { key, value } of form.env) {
    const name = key.trim()
    if (!name) continue
    if (stored.env && stored.env[name] === value) continue
    env[name] = value
  }
  // 被删掉的键要显式清空（后端按键合并，不发就是保留）。
  for (const name of Object.keys(stored.env || {})) {
    if (!form.env.some((item) => item.key.trim() === name)) env[name] = ''
  }
  if (Object.keys(env).length > 0) payload.env = env
  if (payload.startup_timeout_ms === undefined) delete payload.startup_timeout_ms

  runtimeSaving.value = true
  try {
    await run(() => setResourceRuntime(target.resource_id, payload), '保存运行时配置失败')
    message.success('已保存。已启用的服务器会按新配置重连')
    panel.value = null
    await loadResources()
  } finally {
    runtimeSaving.value = false
  }
}

const openAuthoring = () => {
  authoringTarget.value = null
  authoringForm.value = {
    resource_id: '',
    type: 'prompt',
    name: '',
    description: '',
    content: '',
    version: '1.0.0'
  }
  panel.value = 'authoring'
}

/**
 * 打开「改正文」。
 *
 * 预填当前正文而不是留空：这是编辑而不是重写，空白输入框会让用户以为旧内容
 * 已经没了。版本号预填一个**递增**的建议值——后端要求严格递增，
 * 让用户自己猜下一个版本号是把一个必然的约束留给他去撞。
 *
 * 名称与描述同样预填。它们此前留空，而后端把空值当作「不改」（回落到已存的
 * 元数据），所以行为上不会丢——但界面上看不出这条资源现在叫什么，
 * 想改名就得先去详情面板抄一遍。
 */
const openAuthoringForEdit = async (resource: ManagedResource) => {
  authoringTarget.value = resource
  authoringForm.value = {
    resource_id: resource.resource_id,
    type: resource.type as AuthorableType,
    name: resource.name || '',
    description: resource.description || '',
    content: '',
    version: suggestNextVersion(resource.current_version)
  }
  panel.value = 'authoring'
  try {
    const detail = await getResourceContent(resource.resource_id, resource.current_version)
    authoringForm.value.content = detail.content
  } catch (error) {
    message.error(
      `读取当前正文失败：${error instanceof Error ? error.message : '未知错误'}`
    )
  }
}

/**
 * 校验与版本建议放在 `documentAuthoring.ts` 里，由那份测试直接调用。
 *
 * 曾经这两条正则写在这里，且都丢了反斜杠（`/^d+.d+.d+/`）：合法正则、不报错、
 * 永远匹配不上，于是 `authoringError` 对任何输入都返回「版本号需形如 1.0.0」，
 * 整条「从纯文本创建提示词」在界面上完全不可用——而当时的测试只 grep 源码字符串，
 * 看得见那一行、看不见它匹配不上任何东西。
 */
const authoringError = computed(() =>
  authoringFormError(authoringForm.value, { editing: authoringTarget.value !== null })
)

const saveAuthoredDocument = async () => {
  if (authoringError.value) {
    message.error(authoringError.value)
    return
  }
  const form = authoringForm.value
  authoringSaving.value = true
  try {
    if (authoringTarget.value) {
      await run(
        () =>
          authorResourceDocumentVersion(authoringTarget.value!.resource_id, {
            content: form.content,
            version: form.version.trim(),
            name: form.name.trim() || undefined,
            description: form.description.trim() || undefined
          }),
        '保存新版本失败'
      )
      message.success('已保存为新版本，请确认后再启用')
    } else {
      await run(
        () =>
          authorResourceDocument({
            resource_id: form.resource_id.trim(),
            type: form.type,
            content: form.content,
            name: form.name.trim() || undefined,
            description: form.description.trim() || undefined,
            version: form.version.trim()
          }),
        '创建资源失败'
      )
      message.success('已创建，请确认后再启用')
    }
    panel.value = null
    await loadResources()
  } finally {
    authoringSaving.value = false
  }
}

const openDetail = (resource: ManagedResource) => {
  selectedResource.value = resource
  showDetailModal.value = true
  // 正文与详情一起取：打开详情就是为了看「这个资源到底是什么」，
  // 而对 prompt 而言正文就是全部内容。取失败只让正文那一块显示原因，
  // 不影响详情其余字段。
  entryContent.value = null
  entryContentError.value = ''
  void loadEntryContent(resource)
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
  archiveMode.value = 'install'
  versionUploadTarget.value = null
  showImportModal.value = true
  panel.value = null
}

/** 「导入已有」：把一个已准备好的包纳管，与直接安装是两个后端端点。 */
const chooseLocalImport = () => {
  archiveMode.value = 'import'
  versionUploadTarget.value = null
  showImportModal.value = true
  panel.value = null
}

/**
 * 当前正文预览。
 *
 * prompt 类型的全部内容就是正文，而此前界面上看不到它——「提示词管理」
 * 回答不了它唯一要回答的问题。只读：安装后的正文不能就地改（摘要与清单绑定），
 * 改正文走「上传 ZIP 升级」。
 */
const entryContent = ref<ResourceContent | null>(null)
const entryContentLoading = ref(false)
const entryContentError = ref('')
const entryContentVersion = ref('')

const loadEntryContent = async (resource: ManagedResource, version?: string) => {
  entryContentLoading.value = true
  entryContentError.value = ''
  try {
    const result = await getResourceContent(resource.resource_id, version)
    entryContent.value = result
    entryContentVersion.value = result.version
  } catch (error) {
    entryContent.value = null
    entryContentError.value = error instanceof Error ? error.message : '读取正文失败'
  } finally {
    entryContentLoading.value = false
  }
}

/**
 * 正文与运行时载入的是否同一份：摘要一致才成立，不靠信任。
 *
 * 比较本身在 `entryDigest.ts` 里，由那份测试调用验证。此前这个论断只被
 * `toContain('entryDigestMatches')` 覆盖——那只证明这个名字存在，
 * 而判断错的两个方向都很糟：说匹配时用户以为看到的就是运行时那份（文件可能已被
 * 篡改），说不匹配时一个完好的资源被显示成可疑。
 */
const entryDigestMatches = computed(() =>
  compareEntryDigest(entryContent.value, selectedResource.value?.versions)
)

/** 服务器 `resources/imports` 目录里已经放好的包。 */
const stagedArchives = ref<ImportableArchive[]>([])
const stagedLoading = ref(false)
const stagedBusyFile = ref('')

/**
 * 列出服务器上已经放好、还没安装的包。
 *
 * 这条路径与「导入已有」的上传弹窗解决的是不同处境：用户手里没有可上传的文件，
 * 包已经用 scp 放在服务器上了。此前只能上传，于是几十 MB 的包要经浏览器
 * 再走一遍，慢且容易断。
 */
const loadStagedArchives = async () => {
  stagedLoading.value = true
  try {
    const result = await run(() => listImportableArchives(), '读取服务器待导入目录失败')
    stagedArchives.value = result?.imports ?? []
  } finally {
    stagedLoading.value = false
  }
}

const openStagedPanel = async () => {
  panel.value = 'staged'
  await loadStagedArchives()
}

const installStaged = (entry: ImportableArchive) => {
  ask({
    title: `确认安装「${entry.resource_id ?? entry.file_name}」`,
    content:
      '资源包会在服务器内校验并写入受控资源目录；安装后资源保持停用状态，等待你确认权限后再启用。',
    positiveText: '安装',
    onPositiveClick: async () => {
      stagedBusyFile.value = entry.file_name
      try {
        await run(() => installImportableArchive(entry.file_name), '安装服务器资源包失败')
        message.success('资源已安装，请确认后再启用')
        await Promise.all([loadResources(), loadStagedArchives()])
      } finally {
        stagedBusyFile.value = ''
      }
    }
  })
}

/** 「上传 ZIP 升级到新版本」：离线环境下 checkResourceUpdates 无法工作时用它。 */
const chooseVersionUpload = (resource: ManagedResource) => {
  archiveMode.value = 'install'
  versionUploadTarget.value = resource
  selectedFile.value = null
  showImportModal.value = true
}

const confirmImport = async () => {
  if (!selectedFile.value) {
    message.warning('请先选择资源包')
    return
  }
  const target = versionUploadTarget.value
  const mode = archiveMode.value
  const title = target
    ? `确认为「${target.resource_id}」上传新版本`
    : mode === 'install'
      ? '确认从资源包安装'
      : '确认导入离线资源'
  const content = target
    ? '包内清单的版本号必须高于当前版本；升级前会自动备份，升级后资源保持停用状态等待再次确认。'
    : '资源包会在服务器内校验并写入受控资源目录，导入成功后不会直接执行包内文件。'
  ask({
    title,
    content,
    positiveText: target ? '上传新版本' : mode === 'install' ? '安装' : '导入',
    onPositiveClick: async () => {
      importLoading.value = true
      try {
        const file = selectedFile.value as File
        if (target) {
          await run(() => updateResource(target.resource_id, file), '上传新版本失败')
          message.success('新版本已安装，请确认后再启用')
        } else if (mode === 'install') {
          await run(() => installResource(file), '从资源包安装失败')
          message.success('资源已安装，请确认后再启用')
        } else {
          await run(() => importResource(file), '导入资源失败')
          message.success('资源已导入')
        }
        showImportModal.value = false
        selectedFile.value = null
        versionUploadTarget.value = null
        await loadResources()
      } finally {
        importLoading.value = false
      }
    }
  })
}

/**
 * 回滚到历史版本（需求 22.3 的「可回滚机制」）。
 *
 * 回滚会把生效版本改回旧版并**强制停用**该资源，等待再次确认——
 * 旧版本的权限声明可能与当前不同，直接沿用启用状态等于跳过一次权限确认。
 */
const rollbackVersion = (resource: ManagedResource, version: string) => {
  ask({
    title: `确认回滚「${resource.resource_id}」到 ${version}`,
    content:
      '回滚后该资源会被停用并要求重新确认权限；当前版本仍保留在版本列表里，可以再滚回来。',
    positiveText: '回滚',
    onPositiveClick: async () => {
      busyResourceId.value = resource.resource_id
      try {
        await run(() => restoreResource(resource.resource_id, version, true), '回滚版本失败')
        message.success('已回滚，请确认后再启用')
        showDetailModal.value = false
        await loadResources()
      } finally {
        busyResourceId.value = ''
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
  const parsed = parseRepositoryCoordinate(form.coordinate)
  if (parsed === null) {
    // 说清接受什么形态，而不是只说「格式不对」：后者让人无从改。
    message.warning('请填写 owner/name 或 GitHub 仓库地址')
    return
  }
  // 坐标里带的分支优先（用户粘的是 `/tree/master`，他要的就是那个分支）；
  // 没带时用分支输入框，仍为空则由后端按 `main` 处理。
  const branch = parsed.branch || form.branch.trim() || 'main'
  await run(() => addRepository(parsed.owner, parsed.name, branch), '登记仓库失败')
  message.success('仓库来源已登记')
  repositoryForm.value = { coordinate: '', branch: '' }
  await loadRepositories()
}

const toggleRepository = async (repository: ResourceRepository) => {
  await run(
    () => setRepositoryEnabled(repository.owner, repository.name, repository.branch, !repository.enabled),
    '更新仓库状态失败'
  )
  await loadRepositories()
}

/**
 * 摘掉一条仓库来源登记。
 *
 * 与「停用」是两件事：停用表达「这个来源暂时不用」，删除表达「这个来源是错的 /
 * 不再存在」。没有删除时，一个拼错的坐标会永久留在仓库表上——可以停用，
 * 但那条死项再也去不掉。
 *
 * 确认文案必须说清**不会**动已装资源：那是用户在按这个按钮之前最想知道的事，
 * 而「删除仓库」这四个字读起来像会一起删掉从它装过的东西。
 */
const removeRepositoryRow = (repository: ResourceRepository) => {
  ask({
    title: `确认移除 ${repository.owner}/${repository.name}`,
    content:
      `移除后将不再从这个仓库发现或安装新的 Skill（分支 ${repository.branch}）。` +
      '已经装好的资源不受影响，它们在服务器上是独立的包。',
    positiveText: '移除',
    onPositiveClick: async () => {
      await run(
        () => removeRepository(repository.owner, repository.name, repository.branch),
        '移除仓库失败'
      )
      message.success('仓库来源已移除')
      await loadRepositories()
    }
  })
}

/**
 * 仓库直查：列出某个已登记仓库下的全部 Skill。
 *
 * 与「统一目录搜索」不是一回事——目录搜索按关键词跨来源找，这里是
 * 「我知道是哪个仓库，把它下面的都列出来」。需求 10 的「发现技能」两种都要：
 * 只有关键词搜索时，一个刚登记的私有仓库在目录里搜不到任何东西。
 */
const discoveredSkills = ref<DiscoveredSkill[]>([])
const discoverLoading = ref(false)
const discoverTarget = ref<ResourceRepository | null>(null)

const browseRepository = async (repository: ResourceRepository) => {
  discoverLoading.value = true
  discoverTarget.value = repository
  discoveredSkills.value = []
  try {
    const result = await run(
      () => discoverRepository(repository.owner, repository.name, repository.branch),
      '读取仓库内容失败'
    )
    // 接口直接返回数组：一个仓库下的 SKILL.md 全量清单，不分页。
    discoveredSkills.value = result ?? []
    if (!discoveredSkills.value.length) {
      message.info('该仓库下没有找到可安装的 Skill')
    }
  } finally {
    discoverLoading.value = false
  }
}

/** 从仓库直查结果安装：走 remote-install，与目录安装是两个端点。 */
const installRemoteFromDiscovery = (skill: DiscoveredSkill) => {
  ask({
    title: `确认安装「${skill.name || skill.directory}」`,
    content:
      '资源会从该仓库下载并写入受控资源目录；安装后保持停用状态，需要确认权限后才生效。',
    positiveText: '安装',
    onPositiveClick: async () => {
      await run(
        () =>
          installRemoteSkill({
            owner: skill.owner,
            name: skill.repository,
            branch: skill.branch || 'main',
            directory: skill.directory,
            source_key: skill.source_key
          }),
        '安装资源失败'
      )
      message.success('已安装，请确认后再启用')
      await loadResources()
    }
  })
}

/**
 * 仓库表格列。
 *
 * 从模板里的内联数组抽出来：内联写法把渲染函数塞进一行 400 字符的属性里，
 * 加一列就得整行重排，且 diff 完全不可读。
 */
const repositoryColumns = computed(() => [
  {
    title: '仓库',
    key: 'name',
    render: (row: ResourceRepository) => `${row.owner}/${row.name}`
  },
  { title: '分支', key: 'branch' },
  {
    // 「识别到几个技能」是判断一个仓库配对没配对的唯一线索：坐标拼错、分支写错、
    // 或压根不含 SKILL.md 的仓库，与装着几百个技能的仓库此前长得一模一样，
    // 都只是「已启用」。要点进「发现」才知道，而那要出一次网下载整个归档。
    title: '技能数',
    key: 'discovered_skills',
    render: (row: ResourceRepository) =>
      row.discovered_skills === null || row.discovered_skills === undefined
        // 「还没发现过」与「发现过、里面是 0 个」必须分开：后者才是配错的信号。
        ? h(NTag, { size: 'small', bordered: false }, { default: () => '未发现过' })
        : h(
            NTag,
            {
              size: 'small',
              bordered: false,
              type: row.discovered_skills === 0 ? 'warning' : 'success',
              'data-test': 'repository-skill-count'
            },
            { default: () => `识别到 ${row.discovered_skills} 个` }
          )
  },
  {
    title: '状态',
    key: 'enabled',
    render: (row: ResourceRepository) =>
      h(
        NTag,
        { type: row.enabled ? 'success' : 'default' },
        { default: () => (row.enabled ? '已启用' : '已停用') }
      )
  },
  {
    title: '操作',
    key: 'actions',
    render: (row: ResourceRepository) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(
            NButton,
            {
              size: 'small',
              quaternary: true,
              'data-test': 'discover-repository',
              disabled: !row.enabled,
              loading: discoverLoading.value && discoverTarget.value?.name === row.name,
              onClick: () => browseRepository(row)
            },
            { default: () => '浏览 Skill' }
          ),
          h(
            NButton,
            { size: 'small', onClick: () => toggleRepository(row) },
            { default: () => (row.enabled ? '停用' : '启用') }
          ),
          h(
            NButton,
            {
              size: 'small',
              type: 'error',
              quaternary: true,
              'data-test': 'remove-repository',
              'aria-label': `移除仓库 ${row.owner}/${row.name}`,
              onClick: () => removeRepositoryRow(row)
            },
            { default: () => '移除' }
          )
        ]
      })
  }
])

/**
 * 发现资源有两个来源，返回结构和覆盖面都不同，必须让用户自己选：
 * - catalog：服务器内置的一份挑选清单，条目固定、可离线；
 * - skills.sh：活的远程索引，能搜到清单之外的长尾技能。
 * 混在一起搜会让人分不清「搜不到」是清单没收录还是远程没有这个技能。
 */
const discoverSource = ref<'catalog' | 'skills-sh'>('catalog')
const skillsShResults = ref<SkillsSearchResult[]>([])
const skillsShTotal = ref(0)

/** skills.sh 直查：结果结构继承 DiscoveredSkill，安装可直接复用仓库直查那条路径。 */
const searchSkillsSh = async (offset = 0) => {
  remoteLoading.value = true
  remoteSearched.value = true
  remoteOffset.value = offset
  try {
    const result = await run(
      () => searchSkills(remoteQuery.value.trim(), remoteLimit, offset),
      '搜索 skills.sh 失败'
    )
    skillsShResults.value = result.skills
    skillsShTotal.value = result.total_count
    remoteStatus.value = 'ok'
    remoteError.value = ''
  } catch {
    skillsShResults.value = []
    skillsShTotal.value = 0
    remoteStatus.value = 'error'
    remoteError.value = 'skills.sh 暂时无法访问，请稍后重试或改用内置目录'
  } finally {
    remoteLoading.value = false
  }
}

/** 统一入口：按当前来源分派，翻页与回车都只认这一个函数。 */
/** 分页页数要按当前来源的总数算，否则 skills.sh 的页码是内置目录的。 */
const discoverTotal = computed(() =>
  discoverSource.value === 'skills-sh' ? skillsShTotal.value : remoteTotal.value
)

const runDiscoverSearch = (offset = 0) =>
  discoverSource.value === 'skills-sh' ? searchSkillsSh(offset) : searchRemote(offset)

/** 换来源等于换了一份数据，旧结果留在屏幕上会被误读成新来源的返回。 */
const changeDiscoverSource = (value: 'catalog' | 'skills-sh') => {
  discoverSource.value = value
  remoteResults.value = []
  skillsShResults.value = []
  remoteTotal.value = 0
  skillsShTotal.value = 0
  remoteSearched.value = false
  remoteError.value = ''
}

const discoverSourceOptions = [
  { label: '内置目录', value: 'catalog' },
  { label: 'skills.sh', value: 'skills-sh' }
]

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
        await Promise.all([loadResources(), runDiscoverSearch()])
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

/**
 * 资源的系统依赖提示。
 *
 * 后端已经在每个资源上投影了 `dependency_status` 与 `system_dependencies`，
 * 但界面此前从不渲染：装了 `mcp:fetch`（靠 uvx 启动）而机器上没有 uvx 时，
 * 唯一线索是 MCP 面板的「连接失败 / 工具数 0」——没有一处说缺什么，
 * 用户会去查网络、查配置、查 API Key。
 *
 * 返回 null 表示「无需提示」：不需要依赖（not_required）、全部就绪（ready），
 * 以及后端没提供这些字段（老后端，undefined）三种情况都不该占用界面。
 */
const resourceDependencyHint = (
  row: Pick<ManagedResource, 'dependency_status' | 'system_dependencies'>
): { label: string; detail: string; type: 'warning' | 'error' | 'default' } | null => {
  const status = row.dependency_status
  // undefined 是「这个后端不提供依赖信息」，与「不需要依赖」不是一回事，
  // 但两者都不该显示提示。
  if (!status || status === 'not_required' || status === 'ready') return null

  const dependencies = row.system_dependencies || []
  // 说得出名字才算说了话。只讲「依赖未就绪」，用户不知道该去装什么。
  const blocking = dependencies.filter((item) => !item.ready)
  const names = (blocking.length ? blocking : dependencies)
    .map((item) => item.name || item.dependency_id)
    .filter(Boolean)
  const nameText = names.length ? names.join('、') : '未知依赖'

  if (status === 'unknown') {
    // 「还没探测过」与「探测过、确实没有」是两种处境：前者的下一步是去检查，
    // 后者的下一步是去安装。混成一句会让用户装一个本来就在的东西。
    return {
      label: '依赖待检查',
      detail: `${nameText} 尚未探测。请在「系统依赖」里检查它是否已安装。`,
      type: 'default'
    }
  }
  if (status === 'missing') {
    return {
      label: '缺少依赖',
      detail: `${nameText} 未安装，这个资源启用后也不会生效。请在「系统依赖」里安装它。`,
      type: 'warning'
    }
  }
  if (status === 'failed') {
    return {
      label: '依赖安装失败',
      detail: `${nameText} 上次安装失败。请在「系统依赖」里查看原因并重试。`,
      type: 'error'
    }
  }
  return {
    label: '依赖已取消',
    detail: `${nameText} 的安装被取消，需要重新安装后这个资源才会生效。`,
    type: 'warning'
  }
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
        // 有显示名时把它放第一行，ID 降到第二行——但**不省略 ID**：
        // 每个确认框、每条审计记录都按 ID 称呼这条资源，
        // 界面上只给名字会让「确认删除 prompt.office-research」对不上任何一行。
        h('strong', { title: row.name || row.resource_id }, row.name || row.resource_id),
        h(
          'small',
          { title: row.resource_id },
          row.name
            ? `${row.resource_id} · ${typeLabel(row.type)} · ${sourceLabel(row)}`
            : `${typeLabel(row.type)} · ${sourceLabel(row)}`
        ),
        row.description
          ? h('small', { class: 'resource-description', title: row.description }, row.description)
          : null
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
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4, vertical: true }, {
        default: () => {
          const tags = [
            h(
              NTag,
              { type: row.enabled ? 'success' : 'default', bordered: false },
              { default: () => (row.enabled ? '已启用' : '未启用') }
            )
          ]
          // 「已启用但没被任何 Agent 绑定」= 实际效果为零。不说出来的话，
          // 用户看到「已启用」、得到「什么都没变」，然后去怀疑模型或提示词。
          // `in_effect` 缺失表示「读不到绑定关系」，那时什么都不说。
          if (row.enabled && row.in_effect === false) {
            tags.push(
              h(
                NTooltip,
                {},
                {
                  trigger: () =>
                    h(
                      NTag,
                      {
                        type: 'warning',
                        size: 'small',
                        bordered: false,
                        'data-test': 'not-in-effect'
                      },
                      { default: () => '未生效' }
                    ),
                  default: () =>
                    '已启用但没有任何 Agent 绑定它，因此不会进入对话。请在「模型与 Agent」里把它绑定到 Agent。'
                }
              )
            )
          }
          // 依赖缺失与「未生效」是两个独立的失效原因，可以同时成立：
          // 一个没绑定 Agent、又缺 uvx 的 MCP 资源两条都要说。
          const dependencyHint = resourceDependencyHint(row)
          if (dependencyHint) {
            tags.push(
              h(
                NTooltip,
                {},
                {
                  trigger: () =>
                    h(
                      NTag,
                      {
                        type: dependencyHint.type,
                        size: 'small',
                        bordered: false,
                        'data-test': 'resource-dependency-hint'
                      },
                      { default: () => dependencyHint.label }
                    ),
                  default: () => dependencyHint.detail
                }
              )
            )
          }
          return tags
        }
      })
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
          // 「编辑正文」只对纯文本类型出现：skill 的正文是给模型的行为说明，
          // hook 是能起进程的命令声明，那两类的正文不该从一个输入框改。
          // 它保存的是一个**新版本**而不是就地改文件——后者会让下一次载入直接失败。
          isAuthorable(row)
            ? h(NTooltip, {}, {
                trigger: () => h(NButton, {
                  quaternary: true,
                  circle: true,
                  'aria-label': '编辑正文',
                  'data-test': 'edit-document',
                  onClick: () => void openAuthoringForEdit(row)
                }, { icon: () => h(NIcon, { 'aria-hidden': 'true' }, { default: () => h(CreateOutline) }) }),
                default: () => '编辑正文并保存为新版本'
              })
            : null,
          // 「运行时配置」只对 mcp 出现：其余类型没有传输配置可言。
          // 这是受管 MCP 资源唯一的配置入口——`PUT /mcp/servers/<id>` 只认
          // `config.mcp.servers` 里的条目，对受管资源一律 404。
          isRuntimeConfigurable(row)
            ? h(NTooltip, {}, {
                trigger: () => h(NButton, {
                  quaternary: true,
                  circle: true,
                  'aria-label': '运行时配置',
                  'data-test': 'edit-runtime',
                  onClick: () => openRuntimePanel(row)
                }, { icon: () => h(NIcon, { 'aria-hidden': 'true' }, { default: () => h(OptionsOutline) }) }),
                default: () => '配置可访问目录、环境变量与启动超时'
              })
            : null,
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
  if (name === 'discover') await Promise.all([loadRepositories(), runDiscoverSearch()])
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

/** query 里带来的类型必须是真实存在的筛选项，否则外部链接能把界面筛成空白。 */
const isResourceFilter = (value: unknown): value is ResourceFilter =>
  typeof value === 'string' && typeOptions.some((option) => option.value === value)

onMounted(() => {
  window.addEventListener('resize', updateDependencyLayout)
  // 先认下 query 里的类型再加载：反过来会先拉一次全部资源，再因类型变化拉第二次。
  const requested = route.query.type
  if (isResourceFilter(requested)) {
    resourceType.value = requested
  }
  void loadResources()
  if (route.query.panel === 'dependencies') {
    void openPanel('dependencies')
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateDependencyLayout)
  clearDependencyPolling()
  // 页面已经走了，节流中的那一次搜索没有接收方。
  if (bodySearchTimer !== null) clearTimeout(bodySearchTimer)
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
        <!--
          「新建提示词」与「发现并安装」是两件不同的事：后者从外部拿一个现成的包，
          前者写自己的内容。提示词这个类型的全部内容就是正文，没有可执行文件、
          没有依赖，因此它是唯一能从一个输入框创建的类型（连同记忆与会话）。
        -->
        <n-button secondary data-test="author-document" aria-label="新建提示词、记忆或会话" @click="openAuthoring"><template #icon><n-icon aria-hidden="true"><add-outline /></n-icon></template>新建提示词</n-button>
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

    <!--
      按类型分项。装了 0 个的类型照样显示，且可点击直接切筛选——
      「Hooks 0」本身就是绑定 Agent 时挑不到 Hook 的答案，比让用户逐个翻下拉去数快得多。
    -->
    <section class="type-breakdown" aria-label="按类型统计">
      <button
        v-for="entry in typeBreakdown"
        :key="entry.value"
        type="button"
        class="type-chip"
        :class="{ 'type-chip--active': resourceType === entry.value, 'type-chip--empty': entry.count === 0 }"
        :aria-pressed="resourceType === entry.value"
        :data-test="`type-count-${entry.value}`"
        @click="changeType(resourceType === entry.value ? 'all' : entry.value)"
      >
        <span class="type-chip-label">{{ entry.label }}</span>
        <span class="type-chip-count">{{ entry.count }}</span>
      </button>
    </section>

    <n-card class="workspace-card">
      <h2 class="card-section-title">已安装资源</h2>
      <div class="resource-toolbar">
        <n-select :value="resourceType" :options="typeOptions" :input-props="{ 'aria-label': '资源类型筛选' }" @update:value="changeType" />
        <!--
          搜索框紧跟类型下拉：两者是同一件事的两个维度（哪一类、叫什么），
          分开摆会让人以为搜索只作用于某一类。clearable 是必要的——
          没有清除按钮时，用户滤空之后往往以为资源被删了。
        -->
        <n-input
          v-model:value="keyword"
          class="resource-search"
          clearable
          :loading="searchingBody"
          placeholder="搜索名称、ID、描述或正文"
          data-test="resource-search"
          :input-props="{ 'aria-label': '搜索已安装资源（含提示词正文）' }"
        >
          <template #prefix><n-icon aria-hidden="true"><search-outline /></n-icon></template>
        </n-input>
        <!--
          「从 ZIP 安装」与「导入已有」是两个后端端点、两套语义：
          前者直接安装一个新资源，后者把一个已准备好的包纳管。
          此前界面只有一个按钮接到后者，需求里的「从ZIP安装」实际没有入口。
        -->
        <n-button quaternary data-test="install-archive" @click="chooseLocalInstall"><template #icon><n-icon aria-hidden="true"><cloud-upload-outline /></n-icon></template>从 ZIP 安装</n-button>
        <n-button quaternary data-test="import-archive" @click="chooseLocalImport"><template #icon><n-icon aria-hidden="true"><cloud-upload-outline /></n-icon></template>导入已有</n-button>
        <!--
          「服务器上的包」与上面两个按钮的区别是**包在哪里**：那两个要浏览器上传，
          这个列服务器 resources/imports 目录里已经放好的。运维 scp 完一批包之后，
          手里没有可上传的文件，只需要「列出来让我选」。
        -->
        <n-button quaternary data-test="staged-archives" @click="openStagedPanel"><template #icon><n-icon aria-hidden="true"><archive-outline /></n-icon></template>服务器上的包</n-button>
      </div>

      <div v-if="loading" class="loading-state" aria-busy="true"><n-skeleton text :repeat="4" /><n-skeleton text style="width: 82%" /></div>
      <!-- 滤空 ≠ 未安装：前者要给的出路是「清除关键词」，后者是「去安装」。 -->
      <n-empty
        v-else-if="filteredEmpty"
        :description="`没有匹配「${keyword.trim()}」的已安装资源（已含提示词正文）`"
        data-test="resource-search-empty"
      >
        <template #extra>
          <n-button aria-label="清除搜索关键词" @click="keyword = ''">清除关键词</n-button>
        </template>
      </n-empty>
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
          <!--
            名称与描述在详情里完整显示（表格里那两行是截断的）。
            「未命名」而不是留空：空白格子看起来像没加载出来。
          -->
          <n-descriptions-item label="名称">{{ selectedResource.name || '未命名' }}</n-descriptions-item>
          <n-descriptions-item label="描述">{{ selectedResource.description || '未填写' }}</n-descriptions-item>
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
        <!--
          入口正文（需求 10 的「提示词管理」）。prompt 类型的全部内容就是正文，
          而此前界面上没有任何地方能看到它。只读：安装后的正文不能就地改
          （`content_sha256` 与清单绑定，运行时每次载入都重新校验），
          改正文走下面的「上传 ZIP 升级」。
        -->
        <div class="entry-content" data-test="entry-content">
          <div class="entry-content-header">
            <h3 class="card-section-title">入口正文</h3>
            <n-space align="center" :size="8">
              <span class="mono entry-content-path">{{ selectedResource.entry }}</span>
              <n-select
                v-if="selectedResource.versions.length > 1"
                v-model:value="entryContentVersion"
                class="entry-version-select"
                size="small"
                :options="selectedResource.versions.map((item) => ({
                  label: item.version === selectedResource!.current_version
                    ? `${item.version}（当前生效）`
                    : item.version,
                  value: item.version
                }))"
                @update:value="(value) => loadEntryContent(selectedResource!, value)"
              />
              <n-button
                size="tiny"
                quaternary
                data-test="reload-entry-content"
                :loading="entryContentLoading"
                @click="loadEntryContent(selectedResource!, entryContentVersion)"
              >
                重新读取
              </n-button>
            </n-space>
          </div>

          <div v-if="entryContentLoading" class="loading-state" aria-busy="true">
            <n-skeleton text :repeat="3" />
          </div>
          <n-alert v-else-if="entryContentError" type="warning" :show-icon="true">
            {{ entryContentError }}
          </n-alert>
          <template v-else-if="entryContent">
            <!--
              摘要一致才说明「你看到的」与「运行时载入的」是同一份。
              不显示这一行时，用户只能靠信任——而这正是完整性校验存在的理由。
            -->
            <p class="entry-digest">
              <n-tag
                :type="entryDigestMatches ? 'success' : 'warning'"
                size="small"
                data-test="entry-digest"
              >
                {{ entryDigestMatches ? '摘要一致' : '摘要待核对' }}
              </n-tag>
              <span class="mono">{{ shortHash(entryContent.content_sha256) }}</span>
            </p>
            <pre class="entry-body">{{ entryContent.content }}</pre>
            <p class="upload-hint">
              正文不可就地编辑：内容摘要与清单绑定，运行时每次载入都会重新校验，
              就地改会让这个资源在下一次载入时失败。要改正文请用下面的
              「上传 ZIP 升级」装一个新版本。
            </p>
          </template>
          <n-empty v-else description="该资源没有可显示的入口正文" />
        </div>

        <n-divider />
        <!--
          版本历史与回滚（需求 22.3 的「可回滚机制」）。
          离线环境下 checkResourceUpdates 拿不到上游版本，因此这里同时提供
          「上传 ZIP 升级到新版本」——两条路径都落在同一份版本列表上。
        -->
        <div class="version-history">
          <div class="version-history-header">
            <h3 class="card-section-title">版本历史</h3>
            <n-button
              size="small"
              secondary
              data-test="upload-version"
              @click="chooseVersionUpload(selectedResource)"
            >
              <template #icon><n-icon aria-hidden="true"><cloud-upload-outline /></n-icon></template>
              上传 ZIP 升级
            </n-button>
          </div>
          <n-list v-if="selectedResource.versions.length" bordered>
            <n-list-item v-for="item in selectedResource.versions" :key="item.version">
              <n-space align="center" justify="space-between" :wrap="true">
                <div>
                  <span class="mono">{{ item.version }}</span>
                  <span
                    v-if="item.version === selectedResource.current_version"
                    class="summary-note"
                  >
                    （当前生效）
                  </span>
                  <div class="upload-hint">
                    安装于 {{ formatDate(item.installed_at) }} · {{ shortHash(item.content_sha256) }}
                  </div>
                </div>
                <n-button
                  v-if="item.version !== selectedResource.current_version"
                  size="small"
                  quaternary
                  data-test="rollback-version"
                  :loading="busyResourceId === selectedResource.resource_id"
                  @click="rollbackVersion(selectedResource, item.version)"
                >
                  回滚到此版本
                </n-button>
              </n-space>
            </n-list-item>
          </n-list>
          <n-empty v-else description="只有一个版本，暂无可回滚的历史" />
        </div>
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
        <!--
          「该来源不支持自动检查更新」必须排在「检查失败」之前。
          两者都带 error 文本，但含义相反：前者是这条来源的固有边界，重试永远
          不会成功；把它显示成「请重试」等于让人反复点一个注定失败的按钮。
        -->
        <n-alert
          v-if="selectedUpdateCheck && selectedUpdateCheck.update_channel_supported === false"
          type="info"
          :show-icon="true"
          data-test="update-channel-unsupported"
        >
          {{ selectedUpdateCheck.error || '该来源暂不支持自动检查更新；请从来源页面重新安装以获取新版本。' }}
        </n-alert>
        <n-alert v-else-if="selectedUpdateCheck?.error" type="error" :show-icon="true">检查更新失败，请重试。</n-alert>
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
        <n-form inline @submit.prevent="runDiscoverSearch(0)">
          <n-form-item label="来源">
            <!--
              两个来源覆盖面不同：内置目录是挑好的固定清单，skills.sh 是活的远程索引。
              不给选择的话，清单里没有的技能就等于「不存在」。
            -->
            <n-select
              :value="discoverSource"
              :options="discoverSourceOptions"
              data-test="discover-source"
              :input-props="{ 'aria-label': '发现来源' }"
              @update:value="changeDiscoverSource"
            />
          </n-form-item>
          <!-- skills.sh 只索引 skill，留着类型下拉会让人以为能在那里搜 MCP 或 Hook。 -->
          <n-form-item v-if="discoverSource === 'catalog'" label="资源类型">
            <n-select v-model:value="remoteType" :options="catalogTypeOptions" clearable :input-props="{ 'aria-label': '目录资源类型' }" />
          </n-form-item>
          <n-form-item label="关键词"><n-input v-model:value="remoteQuery" placeholder="搜索资源名称或描述" clearable /></n-form-item>
          <n-button type="primary" attr-type="submit" :loading="remoteLoading"><template #icon><n-icon aria-hidden="true"><search-outline /></n-icon></template>搜索</n-button>
        </n-form>
        <div v-if="remoteLoading" class="loading-state"><n-skeleton text :repeat="3" /></div>
        <n-alert v-else-if="remoteStatus === 'error'" type="warning" :show-icon="true">
          {{ remoteError || '在线资源索引暂时不可用，当前仅展示本地资源。' }}
        </n-alert>
        <template v-else-if="discoverSource === 'skills-sh'">
          <n-empty v-if="remoteSearched && !skillsShResults.length" description="skills.sh 上没有匹配的技能" />
          <!--
            与内置目录同一种卡片形态。此前这里是 `n-list`：同一个面板里切换来源
            时布局整体换一次，读起来像换了一个页面，而两者的操作（查看、安装）
            完全相同。
          -->
          <div v-else-if="skillsShResults.length" class="remote-results">
            <article v-for="item in skillsShResults" :key="item.source_key" class="remote-result">
              <div class="catalog-result-main">
                <div class="catalog-result-heading">
                  <strong>{{ item.name || item.directory }}</strong>
                  <n-tag v-if="item.installs" size="small" :bordered="false">{{ item.installs }} 次安装</n-tag>
                </div>
                <span>{{ item.owner }}/{{ item.repository }}{{ item.branch ? ` · 分支 ${item.branch}` : '' }}</span>
                <p>{{ item.description || item.source_key }}</p>
              </div>
              <n-space class="catalog-result-actions">
                <n-button
                  size="small"
                  type="primary"
                  data-test="install-skills-sh-result"
                  aria-label="安装 skills.sh 技能"
                  @click="installRemoteFromDiscovery(item)"
                ><template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>安装</n-button>
              </n-space>
            </article>
          </div>
          <n-empty v-else description="输入关键词后搜索 skills.sh" />
        </template>
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
              <!--
                「已安装但未启用」必须给出下一步动作，而不是一个灰按钮。

                安装后资源一律是 `enabled=false, confirmation_required=true`
                （见 `resource_lifecycle.install_archive`），这是刻意的安全设计：
                装包不自动跑脚本，启用要显式确认。但此前这里只把按钮置灰改成
                「已安装」，而「请先在资源管理中启用并确认」这句提示出现在
                **另一个页面**（Agent 编辑器的绑定区）。两处不连通，用户装完就卡住，
                以为功能没做——现场报障正是「五个添加按钮全是灰的」。
              -->
              <n-button
                v-if="item.installed && !item.enabled && item.installed_resource_id"
                type="warning"
                size="small"
                :loading="busyResourceId === item.installed_resource_id"
                data-test="catalog-enable"
                aria-label="启用已安装的目录资源"
                @click="enableInstalledCatalogItem(item)"
              ><template #icon><n-icon aria-hidden="true"><checkmark-circle-outline /></n-icon></template>启用</n-button>
              <n-button v-else type="primary" size="small" :disabled="item.installed" :aria-label="item.installed ? '资源已启用' : '安装目录资源'" @click="installCatalog(item)"><template #icon><n-icon aria-hidden="true"><cloud-download-outline /></n-icon></template>{{ item.installed ? '已启用' : '安装' }}</n-button>
            </n-space>
          </article>
        </div>
        <n-pagination v-if="discoverTotal > remoteLimit" :page="Math.floor(remoteOffset / remoteLimit) + 1" :page-count="Math.ceil(discoverTotal / remoteLimit)" @update:page="(page) => runDiscoverSearch((page - 1) * remoteLimit)" />
      </n-card>
      <n-divider />
      <n-card :bordered="false" size="small">
        <h2 class="card-section-title">仓库来源</h2>
        <!--
          一个坐标输入框 + 一个可选分支。坐标框接受粘贴进来的任意形态，
          因为用户手上拿到的东西一定是一个 URL；分支框只在坐标里没带分支时生效。
        -->
        <n-form inline @submit.prevent="saveRepository">
          <n-form-item label="仓库">
            <n-input
              v-model:value="repositoryForm.coordinate"
              class="repository-coordinate"
              placeholder="owner/name 或 https://github.com/owner/name"
              data-test="repository-coordinate"
              :input-props="{ 'aria-label': '仓库坐标或 GitHub 地址' }"
            />
          </n-form-item>
          <n-form-item label="分支">
            <n-input
              v-model:value="repositoryForm.branch"
              placeholder="留空为 main"
              :input-props="{ 'aria-label': '分支（可选）' }"
            />
          </n-form-item>
          <n-button type="primary" attr-type="submit">登记</n-button>
        </n-form>
        <n-data-table v-if="repositories.length" :loading="repositoryLoading" :data="repositories" :columns="repositoryColumns" :pagination="false" :bordered="false" :scroll-x="760" />
        <n-empty v-else description="尚未登记仓库来源" />

        <!--
          仓库直查结果。与上方「统一目录」搜索的区别：目录按关键词跨来源找，
          这里是「我知道是哪个仓库，把它下面的都列出来」。只有关键词搜索时，
          一个刚登记的私有仓库在目录里搜不到任何东西。
        -->
        <template v-if="discoverTarget">
          <n-divider />
          <h3 class="card-section-title">
            {{ discoverTarget.owner }}/{{ discoverTarget.name }} 下的 Skill
          </h3>
          <div v-if="discoverLoading" class="loading-state" aria-busy="true">
            <n-skeleton text :repeat="3" />
          </div>
          <!--
            仓库直查的结果与上方两个来源同一种卡片形态：三处都是「发现结果 +
            安装」，形态不同会让人以为它们能做的事不一样。
          -->
          <div v-else-if="discoveredSkills.length" class="remote-results">
            <article v-for="item in discoveredSkills" :key="item.source_key" class="remote-result">
              <div class="catalog-result-main">
                <div class="catalog-result-heading">
                  <strong>{{ item.name || item.directory }}</strong>
                </div>
                <span>{{ item.directory }}</span>
                <p>{{ item.description || item.source_key }}</p>
              </div>
              <n-space class="catalog-result-actions">
                <n-button
                  size="small"
                  type="primary"
                  data-test="install-discovered-skill"
                  aria-label="安装该仓库下的技能"
                  @click="installRemoteFromDiscovery(item)"
                >
                  安装
                </n-button>
              </n-space>
            </article>
          </div>
          <n-empty v-else description="该仓库下没有找到可安装的 Skill" />
        </template>
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

    <!--
      新建 / 编辑纯文本资源。
      「保存即新版本」而不是就地改文件：`content_sha256` 把清单与文件绑在一起，
      就地改的后果不是「改了没生效」，而是这个资源在下一次载入时直接失败。
    -->
    <n-modal
      :show="panel === 'authoring'"
      preset="card"
      :title="authoringTarget ? '编辑正文（保存为新版本）' : '新建提示词 / 记忆 / 会话'"
      :aria-label="authoringTarget ? '编辑资源正文' : '新建纯文本资源'"
      class="resource-modal"
      @update:show="(value) => !value && closePanel()"
    >
      <n-alert type="info" :show-icon="true">
        打包与摘要由服务器完成，落盘后与内置条目同形。装完保持<strong>停用</strong>，
        需要在资源列表里确认后才生效——提示词会进系统提示词、改变每一轮回复。
      </n-alert>
      <n-form class="authoring-form" @submit.prevent="saveAuthoredDocument">
        <n-form-item label="资源 ID">
          <n-input
            v-model:value="authoringForm.resource_id"
            :disabled="Boolean(authoringTarget)"
            placeholder="例如 prompt.my-office"
            data-test="authoring-resource-id"
            :input-props="{ 'aria-label': '资源 ID' }"
          />
        </n-form-item>
        <n-form-item label="类型">
          <n-select
            v-model:value="authoringForm.type"
            :options="authoringTypeOptions"
            :disabled="Boolean(authoringTarget)"
            data-test="authoring-type"
            :input-props="{ 'aria-label': '资源类型' }"
          />
        </n-form-item>
        <n-form-item label="名称">
          <n-input v-model:value="authoringForm.name" placeholder="可选，用于列表显示" :input-props="{ 'aria-label': '名称' }" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="authoringForm.description" placeholder="可选，一句话说明用途" :input-props="{ 'aria-label': '描述' }" />
        </n-form-item>
        <n-form-item label="版本">
          <!-- 后端要求严格递增；编辑时这里预填的是当前版本的下一个 patch。 -->
          <n-input v-model:value="authoringForm.version" placeholder="1.0.0" data-test="authoring-version" :input-props="{ 'aria-label': '版本号' }" />
        </n-form-item>
        <n-form-item label="正文">
          <n-input
            v-model:value="authoringForm.content"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 20 }"
            placeholder="直接写提示词正文"
            data-test="authoring-content"
            :input-props="{ 'aria-label': '正文' }"
          />
        </n-form-item>
        <n-alert v-if="authoringError" type="warning" :show-icon="true" data-test="authoring-error">
          {{ authoringError }}
        </n-alert>
        <n-space justify="end">
          <n-button @click="closePanel">取消</n-button>
          <n-button
            type="primary"
            attr-type="submit"
            data-test="authoring-save"
            :loading="authoringSaving"
            :disabled="Boolean(authoringError)"
          >
            {{ authoringTarget ? '保存为新版本' : '创建' }}
          </n-button>
        </n-space>
      </n-form>
    </n-modal>


    <!--
      受管 MCP 资源的运行时配置。归档里的 `server.json` 有摘要护着，是「目录发布了
      什么」；这里配的是「这台机器允许什么」。因此没有 command / args / 传输类型
      的输入框——那几个字段改了等于把资源指向另一个程序，后端会拒。
    -->
    <n-modal
      :show="panel === 'runtime'"
      preset="card"
      title="运行时配置"
      aria-label="受管 MCP 资源运行时配置"
      class="resource-modal"
      @update:show="(value) => !value && closePanel()"
    >
      <n-alert type="info" :show-icon="true">
        这里配置 <strong>{{ runtimeTarget?.name || runtimeTarget?.resource_id }}</strong>
        在本机怎么运行。命令与传输类型来自已签名的资源包，不能在此修改。
        保存后已启用的服务器会按新配置重新加载。
      </n-alert>
      <n-form class="authoring-form" @submit.prevent="saveRuntimeOverrides">
        <n-form-item label="追加启动参数">
          <div class="runtime-rows">
            <!--
              filesystem 这类服务器靠启动参数声明可访问目录：不给目录时它没有
              任何可操作范围。参数追加在资源包自带参数之后，包名本身不受影响。
            -->
            <p class="runtime-caption">
              追加在资源包自带参数之后。文件类服务器在这里填允许访问的目录，
              例如 <code>/srv/data/docs</code>。
            </p>
            <div v-for="(_, index) in runtimeForm.extraArgs" :key="`arg-${index}`" class="runtime-row">
              <n-input
                v-model:value="runtimeForm.extraArgs[index]"
                placeholder="/srv/data/docs"
                data-test="runtime-extra-arg"
                :input-props="{ 'aria-label': `启动参数 ${index + 1}` }"
              />
              <n-button quaternary circle :aria-label="`删除启动参数 ${index + 1}`" @click="removeRuntimeArg(index)">
                <template #icon><n-icon aria-hidden="true"><trash-outline /></n-icon></template>
              </n-button>
            </div>
            <n-button size="small" quaternary data-test="runtime-add-arg" @click="addRuntimeArg">
              <template #icon><n-icon aria-hidden="true"><add-outline /></n-icon></template>添加参数
            </n-button>
          </div>
        </n-form-item>
        <n-form-item label="环境变量">
          <div class="runtime-rows">
            <!-- 已保存的值显示为掩码；不改动的行不会被回传。 -->
            <p class="runtime-caption">已保存的值以掩码显示。留空值即删除该变量。</p>
            <div v-for="(item, index) in runtimeForm.env" :key="`env-${index}`" class="runtime-row">
              <n-input v-model:value="item.key" placeholder="变量名" :input-props="{ 'aria-label': `环境变量名 ${index + 1}` }" />
              <n-input v-model:value="item.value" placeholder="值" :input-props="{ 'aria-label': `环境变量值 ${index + 1}` }" />
              <n-button quaternary circle :aria-label="`删除环境变量 ${index + 1}`" @click="removeRuntimeEnv(index)">
                <template #icon><n-icon aria-hidden="true"><trash-outline /></n-icon></template>
              </n-button>
            </div>
            <n-button size="small" quaternary data-test="runtime-add-env" @click="addRuntimeEnv">
              <template #icon><n-icon aria-hidden="true"><add-outline /></n-icon></template>添加变量
            </n-button>
          </div>
        </n-form-item>
        <n-form-item label="可访问根目录">
          <div class="runtime-rows">
            <!-- MCP 协议层的 roots，与启动参数是两回事：前者由客户端声明。 -->
            <p class="runtime-caption">
              通过 MCP 协议声明给服务器的可访问根，与上面的启动参数是两套机制，
              按服务器文档决定用哪一种。
            </p>
            <div v-for="(_, index) in runtimeForm.roots" :key="`root-${index}`" class="runtime-row">
              <n-input
                v-model:value="runtimeForm.roots[index]"
                placeholder="/srv/data"
                data-test="runtime-root"
                :input-props="{ 'aria-label': `可访问根 ${index + 1}` }"
              />
              <n-button quaternary circle :aria-label="`删除可访问根 ${index + 1}`" @click="removeRuntimeRoot(index)">
                <template #icon><n-icon aria-hidden="true"><trash-outline /></n-icon></template>
              </n-button>
            </div>
            <n-button size="small" quaternary data-test="runtime-add-root" @click="addRuntimeRoot">
              <template #icon><n-icon aria-hidden="true"><add-outline /></n-icon></template>添加根目录
            </n-button>
          </div>
        </n-form-item>
        <n-form-item label="工作目录">
          <n-input
            v-model:value="runtimeForm.cwd"
            placeholder="留空则用服务进程的工作目录"
            data-test="runtime-cwd"
            :input-props="{ 'aria-label': '工作目录' }"
          />
        </n-form-item>
        <n-form-item label="启动超时（毫秒）">
          <n-input
            v-model:value="runtimeForm.startupTimeoutMs"
            placeholder="留空使用默认 120000"
            data-test="runtime-timeout"
            :input-props="{ 'aria-label': '启动超时毫秒' }"
          />
        </n-form-item>
        <n-alert v-if="runtimeError" type="warning" :show-icon="true" data-test="runtime-error">
          {{ runtimeError }}
        </n-alert>
        <n-space justify="end">
          <n-button @click="closePanel">取消</n-button>
          <n-button
            type="primary"
            attr-type="submit"
            data-test="runtime-save"
            :loading="runtimeSaving"
            :disabled="Boolean(runtimeError)"
          >
            保存
          </n-button>
        </n-space>
      </n-form>
    </n-modal>

    <n-modal :show="panel === 'backups'" preset="card" title="备份与恢复" aria-label="备份与恢复" class="resource-modal" @update:show="(value) => !value && closePanel()">
      <n-alert type="warning" :show-icon="true">备份只保留资源版本元数据和受控文件。恢复或删除备份前必须确认，操作会写入服务器审计记录。</n-alert>
      <n-data-table class="modal-table" :loading="backupLoading" :columns="backupColumns" :data="backups" :pagination="false" :bordered="false" :scroll-x="720" />
      <n-empty v-if="!backupLoading && !backups.length" description="暂无资源备份" />
    </n-modal>

    <n-modal :show="panel === 'staged'" preset="card" title="服务器上的资源包" aria-label="服务器上的资源包" class="resource-modal" @update:show="(value) => !value && closePanel()">
      <n-alert type="info" :show-icon="true">
        这里列出服务器 <code>resources/imports</code> 目录里已经存在的资源包——
        用 scp 放上去的、或者从备份目录里翻出来的，都不需要再经浏览器上传一次。
        列举是只读的：点「安装」才会解包，且安装后资源保持停用等待你确认权限。
      </n-alert>
      <div v-if="stagedLoading" class="loading-state" aria-busy="true"><n-skeleton text :repeat="3" /></div>
      <n-empty
        v-else-if="!stagedArchives.length"
        description="服务器待导入目录里没有资源包"
      >
        <template #extra>
          <span class="staged-empty-hint">
            把 .zip 放进数据目录下的 <code>resources/imports</code>，再回到这里刷新。
          </span>
        </template>
      </n-empty>
      <ul v-else class="staged-list">
        <li v-for="entry in stagedArchives" :key="entry.file_name" class="staged-row">
          <div class="staged-main">
            <strong class="mono">{{ entry.resource_id ?? entry.file_name }}</strong>
            <span class="staged-meta">
              <template v-if="entry.error">
                <!-- 坏包单独标错：一个损坏的 ZIP 不该让整份列表打不开。 -->
                <n-tag size="small" type="error">无法读取</n-tag>
                {{ entry.error }}
              </template>
              <template v-else>
                <n-tag v-if="entry.type" size="small">{{ typeLabel(entry.type as ResourceType) }}</n-tag>
                <span class="mono">{{ entry.version }}</span>
                <!-- 「已装 1.0.0、盘上有 2.0.0」与「已装 2.0.0」处置不同：
                     前者点更新，后者什么都不用做。 -->
                <n-tag v-if="stagedStatus(entry) === 'upgradable'" size="small" type="warning">
                  可更新（已装 {{ entry.installed_version }}）
                </n-tag>
                <n-tag v-else-if="stagedStatus(entry) === 'installed'" size="small" type="success">已安装</n-tag>
                <span class="staged-file mono">{{ entry.file_name }}</span>
              </template>
            </span>
          </div>
          <n-button
            size="small"
            :disabled="!canInstallStaged(entry)"
            :loading="stagedBusyFile === entry.file_name"
            @click="installStaged(entry)"
          >
            {{ stagedActionLabel(entry) }}
          </n-button>
        </li>
      </ul>
      <template #footer>
        <n-space justify="end">
          <n-button quaternary :loading="stagedLoading" @click="loadStagedArchives">
            <template #icon><n-icon aria-hidden="true"><refresh-outline /></n-icon></template>
            重新扫描
          </n-button>
        </n-space>
      </template>
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

/* 分项紧贴摘要条下方：同一个「库存概览」的第二行，用负 margin 收掉重复间距。 */
.type-breakdown { display: flex; flex-wrap: wrap; gap: 8px; margin: -12px 0 24px; }
.type-chip { display: inline-flex; align-items: baseline; gap: 8px; padding: 6px 12px; border: 1px solid var(--border-color); border-radius: 999px; background: var(--card-bg-color); color: var(--text-color-secondary); font: inherit; font-size: 13px; cursor: pointer; transition: border-color 0.15s, color 0.15s, background 0.15s; }
.type-chip:hover { border-color: var(--primary-color); color: var(--text-color-base); }
.type-chip:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.type-chip-count { color: var(--text-color-base); font-size: 15px; font-weight: 650; font-variant-numeric: tabular-nums; }
.type-chip--active { border-color: var(--primary-color); background: var(--primary-color-suppl); color: var(--text-color-base); }
/* 装了 0 个的类型压暗但不隐藏——它是「压根没装」的诊断信号，不是噪声。 */
.type-chip--empty { opacity: 0.55; }
.type-chip--empty .type-chip-count { font-weight: 500; }
.workspace-card { margin-bottom: 20px; }
.card-section-title { margin: 0 0 16px; font-size: 18px; line-height: 1.4; font-weight: 600; }
.resource-toolbar { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-bottom: 16px; }
.resource-toolbar :deep(.n-select) { width: 150px; }
/* 搜索框推到最左：工具栏整体右对齐，筛选类控件靠左、动作类按钮靠右，
   两组之间自然分开，不用再画分隔线。 */
.resource-toolbar .resource-search { width: 240px; margin-right: auto; }
@media (max-width: 900px) {
  /* 窄屏按钮会换行，此时搜索框独占一行比挤成 240px 更好读 */
  .resource-toolbar { flex-wrap: wrap; justify-content: flex-start; }
  .resource-toolbar .resource-search { width: 100%; margin-right: 0; }
}
.loading-state { display: grid; gap: 14px; min-height: 150px; padding: 18px 4px; }
/* 可增删的行：输入框吃满剩余宽度，删除按钮不被压扁。 */
.runtime-rows { display: flex; flex-direction: column; gap: 8px; width: 100%; }
.runtime-row { display: flex; gap: 8px; align-items: center; }
.runtime-row .n-input { flex: 1 1 auto; min-width: 0; }
.runtime-caption { margin: 0 0 4px; color: var(--text-color-secondary); font-size: 12px; line-height: 1.6; }
.resource-name { display: flex; flex-direction: column; gap: 4px; min-width: 180px; }
.resource-name small { color: var(--text-color-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 描述可能很长：截断到一行并保留 title，展开靠详情面板而不是把表格撑高。 */
.resource-description { max-width: 220px; }
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
/* 正文输入框要占满宽度：提示词按行读，被压窄之后每一行都要横向扫视。 */
.authoring-form { margin-top: 18px; }
.authoring-form :deep(.n-form-item) { margin-bottom: 12px; }
.authoring-form :deep(.n-input) { width: 100%; }
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
/*
 * 发现结果是**卡片网格**而不是分隔行。
 *
 * 「发现」这件事的本质是横向比较：同一个关键词往往返回十几个来源不同、名字相近
 * 的候选，逐行下拉看不出差别。列数由 `auto-fill` + `minmax` 决定而不是写死三列：
 * 写死会在窄屏把卡片挤成一条，先被压掉的正是操作区，而那是这个面板唯一的目的。
 */
.remote-results { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-top: 16px; }
/*
 * 卡片靠边框与内边距成形，不靠分隔线：两列以上时横向相邻的两张卡之间没有分隔线
 * 可用，读不出「哪几段属于同一个候选」。
 */
.remote-result {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}
.agent-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--border-color); }
.agent-row:last-child { border-bottom: 0; }
.remote-result strong, .agent-row strong { display: block; }
.remote-result span, .agent-row span { display: block; margin-top: 4px; color: var(--text-color-secondary); font-size: 13px; }
/*
 * 描述限三行：网格里一张卡变高会把整行拉高，其余卡片下方留出大片空白。
 * 截断而不是隐藏——摘要的作用是「值不值得点查看」，三行足够判断。
 */
.remote-result p {
  display: -webkit-box;
  margin: 8px 0 0;
  overflow: hidden;
  color: var(--text-color-secondary);
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}
.catalog-result-main { display: grid; gap: 2px; min-width: 0; }
.catalog-result-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.catalog-result-heading strong { overflow-wrap: anywhere; }
.catalog-result-actions { justify-content: flex-end; }
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
  /* 窄屏降到单列：两列各 <180px 时卡片里的操作按钮会先换行再被压扁。 */
  .remote-results { grid-template-columns: 1fr; }
  .workspace-card :deep(.n-card-header) { align-items: flex-start; }
  .workspace-card :deep(.n-card-header__extra) { max-width: 100%; }
  .dependency-section-heading { align-items: stretch; flex-direction: column; }
}

@media (max-width: 480px) {
  h1 { font-size: 26px; }
  .summary-strip > div { padding: 14px; }
  .summary-strip strong { font-size: 19px; }
  /* `.remote-result` 本来就是竖排卡片，这里只需要 `.agent-row` 折行。 */
  .agent-row { flex-direction: column; }
  .agent-relations { justify-content: flex-start; }
  .agent-row-detailed { grid-template-columns: 1fr; }
  .binding-groups { grid-column: 1; }
  /* 窄屏下把每一行折成两层：文件名与版本挤在一行时会被截断，
     而截断掉的恰恰是「哪一个包」这个唯一有区分度的信息。 */
  .staged-row { align-items: stretch; flex-direction: column; }
}

.staged-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}

.staged-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.24));
  border-radius: 10px;
}

.staged-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.staged-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--text-color-secondary);
  font-size: 0.85rem;
}

/* 文件名比资源 ID 弱一档：出问题时要找的是「哪个文件」，
   但正常情况下用户关心的是「哪个资源」。 */
.staged-file {
  color: var(--text-color-tertiary);
}

.staged-empty-hint {
  color: var(--text-color-tertiary);
  font-size: 0.85rem;
}

.entry-content {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.entry-content-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.entry-content-path {
  color: var(--text-color-tertiary);
  font-size: 0.85rem;
}

.entry-version-select {
  width: 190px;
}

.entry-digest {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--text-color-secondary);
  font-size: 0.85rem;
}

/* 正文用等宽 + 保留换行：提示词的段落与缩进是它语义的一部分，
   按普通段落折行会让「先给结论，再给依据」这类结构读不出来。
   限高并可滚动，避免一份长提示词把整个详情弹窗挤成一条长卷。 */
.entry-body {
  max-height: 320px;
  margin: 0;
  padding: 12px 14px;
  overflow: auto;
  border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.24));
  border-radius: 10px;
  background-color: var(--code-bg-color, rgba(128, 128, 128, 0.08));
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.85rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  tab-size: 2;
}
</style>
