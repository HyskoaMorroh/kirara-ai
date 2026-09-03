import { ref, computed } from 'vue'
import { http } from '@/utils/http'
import { useMessage, useDialog } from 'naive-ui'
import { useRouter } from 'vue-router'

export type MCPTransportType = 'stdio' | 'http' | 'sse'

export interface MCPTransport {
  type: MCPTransportType
  command?: string | null
  args: string[]
  env: Record<string, string>
  cwd?: string | null
  url?: string | null
  headers: Record<string, string>
}

export interface MCPApps {
  [key: string]: unknown
}

// MCP server uses the same canonical shape as the backend and other MCP clients.
export interface MCPServer {
  id: string
  name: string
  server: MCPTransport
  apps: MCPApps
  description: string
  tags: string[]
  homepage?: string | null
  docs?: string | null
  metadata: Record<string, unknown>
  connection_state: string
}

// MCP服务器统计信息
export interface MCPStatistics {
  total_servers: number
  stdio_servers: number
  http_servers: number
  sse_servers: number
  connected_servers: number
  disconnected_servers: number
  error_servers: number
  total_tools: number
}

// MCP服务器工具定义
export interface MCPTool {
  name: string
  description: string | null
  input_schema: Record<string, any>
}

// 分页响应接口
export interface PagedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/**
 * 一条新增 MCP 的预设模板。
 *
 * `runtime` 是「这台机器靠什么把它拉起来」，与后端
 * `resource_catalog.py` 里同一条目的 `runtime_dependency` 一致。它必须显示出来：
 * `npx` 与 `uvx` 都不是本项目的依赖，运行时镜像两个都没装——不说明的话，
 * 用户点了启用只会看到「连接失败 / 已连接 0 / 工具数 0」，而界面上没有任何
 * 线索指向真正的原因。
 */
export interface MCPPreset {
  id: string
  label: string
  description: string
  command: string
  args: string[]
  runtime: 'npx' | 'uvx'
  tags: string
  homepage: string
  /** 需要用户补参数时的提示（例如 filesystem 要追加可访问目录）。 */
  hint?: string
}

/**
 * 与后端内置目录（`resource_catalog.py` 的 `_BUILTINS`）逐条对应的预设表。
 *
 * 为什么要有这张表：同样八个 stdio MCP，从「资源管理 → 发现并安装」进去装得到，
 * 而 MCP 页此前只有一个「Context7 模板」按钮。缺的不是七个按钮，是**这条链路的
 * 对称性**——用户在 MCP 页找不到 `fetch`，会得出「这个项目不支持它」这个错误结论，
 * 而它就在另一个页面的目录里。
 *
 * `id` 必须与目录一致：两个入口装出两个不同 id 的同一个 MCP 之后，
 * 「为什么有两个 context7」无从解释，而 `refresh_managed_servers` 也按 id 对账。
 *
 * 表驱动而不是八个 `openXxxTemplate` 函数：后者每加一个预设要改三处
 * （函数、按钮、导出），漏掉任何一处都不会报错。
 */
export const MCP_PRESETS: readonly MCPPreset[] = [
  {
    id: 'context7',
    label: 'Context7',
    description: '通过 MCP 获取最新软件库和框架文档，用于 AI 功能调试。',
    command: 'npx',
    args: ['-y', '@upstash/context7-mcp'],
    runtime: 'npx',
    tags: 'documentation, debugging',
    homepage: 'https://context7.com'
  },
  {
    id: 'fetch',
    label: 'Fetch',
    description: '抓取网页并转成适合模型阅读的文本，用于让 AI 读取在线内容。',
    command: 'uvx',
    args: ['mcp-server-fetch'],
    runtime: 'uvx',
    tags: 'web, fetch',
    homepage: 'https://github.com/modelcontextprotocol/servers'
  },
  {
    id: 'time',
    label: 'Time',
    description: '提供当前时间与时区换算，避免模型凭训练数据猜测日期。',
    command: 'uvx',
    args: ['mcp-server-time'],
    runtime: 'uvx',
    tags: 'time, utility',
    homepage: 'https://github.com/modelcontextprotocol/servers'
  },
  {
    id: 'memory',
    label: 'Knowledge Graph Memory',
    description: '以知识图谱方式保存与检索事实，跨会话复用。',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-memory'],
    runtime: 'npx',
    tags: 'memory, knowledge-graph',
    homepage: 'https://github.com/modelcontextprotocol/servers'
  },
  {
    id: 'sequential-thinking',
    label: 'Sequential Thinking',
    description: '把复杂问题拆成可回溯的思考步骤，用于多步推理调试。',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-sequential-thinking'],
    runtime: 'npx',
    tags: 'reasoning, debugging',
    homepage: 'https://github.com/modelcontextprotocol/servers'
  },
  {
    id: 'filesystem',
    label: 'Filesystem',
    description: '读写指定目录下的文件。',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem'],
    runtime: 'npx',
    tags: 'filesystem',
    homepage: 'https://github.com/modelcontextprotocol/servers',
    // 不预填一个目录：填任何具体路径都是替用户决定「哪些文件可以被读写」，
    // 而这条 MCP 的全部风险就在那个参数上。
    hint: '启用前需在参数末尾追加允许访问的目录，否则它没有任何可操作范围'
  },
  {
    id: 'chrome-devtools',
    label: 'Chrome DevTools',
    description: '连接 Chrome 检查 DOM、控制台与网络请求，用于前端调试。',
    command: 'npx',
    args: ['-y', 'chrome-devtools-mcp@latest'],
    runtime: 'npx',
    tags: 'browser, debugging',
    homepage: 'https://github.com/ChromeDevTools/chrome-devtools-mcp'
  },
  {
    id: 'playwright',
    label: 'Playwright',
    description: '以可访问性树驱动浏览器，完成导航、点击与截图。',
    command: 'npx',
    args: ['-y', '@playwright/mcp@latest'],
    runtime: 'npx',
    tags: 'browser, automation',
    homepage: 'https://github.com/microsoft/playwright-mcp'
  }
]

/**
 * MCP服务器视图模型
 */
export function useMCPViewModel() {
  const router = useRouter()
  const message = useMessage()
  const dialog = useDialog()

  // 状态
  const servers = ref<MCPServer[]>([])
  const statistics = ref<MCPStatistics | null>(null)
  const currentServer = ref<MCPServer | null>(null)
  const serverTools = ref<MCPTool[]>([])
  const isLoading = ref(false)
  const totalServers = ref(0)
  const currentPage = ref(1)
  const pageSize = ref(20)
  const totalPages = ref(1)

  // 过滤和搜索
  const filterParams = ref({
    connectionType: null as string | null,
    status: null as string | null,
    query: ''
  })

  // 表单
  const formModel = ref({
    id: '',
    name: '',
    description: '',
    transportType: 'stdio' as MCPTransportType,
    command: '',
    args: [] as string[],
    cwd: '',
    url: '',
    env: [] as { key: string; value: string }[],
    headers: [] as { key: string; value: string }[],
    tags: '',
    homepage: '',
    docs: '',
    appsJson: '{}',
    metadataJson: '{}'
  })

  // 模态框状态
  const showServerModal = ref(false)
  const modalMode = ref<'create' | 'edit'>('create')

  // 过滤选项
  const filterOptions = computed(() => ({
    connectionType: [
      { label: '标准IO', value: 'stdio' },
      { label: 'HTTP', value: 'http' },
      { label: 'SSE', value: 'sse' }
    ],
    status: [
      { label: '已连接', value: 'connected' },
      { label: '已断开', value: 'disconnected' },
      { label: '错误', value: 'error' }
    ]
  }))

  // 统计信息格式化
  const formattedStatistics = computed(() => {
    if (!statistics.value) return []

    return [
      { label: '总服务器数', value: statistics.value.total_servers },
      { label: '标准IO服务器', value: statistics.value.stdio_servers },
      { label: 'SSE服务器', value: statistics.value.sse_servers },
      { label: '已连接', value: statistics.value.connected_servers, type: 'success' },
      { label: '已断开', value: statistics.value.disconnected_servers, type: 'warning' },
      { label: '错误', value: statistics.value.error_servers, type: 'error' },
      { label: '工具总数', value: statistics.value.total_tools, type: 'info' },
      { label: 'HTTP服务器', value: statistics.value.http_servers }
    ]
  })

  // 获取服务器列表
  const fetchServers = async () => {
    try {
      isLoading.value = true
      // 查询参数必须拼进 URL。此前写的是 `http.get(path, { params })`——
      // 第二个参数是 `Omit<RequestInit, 'method'>`（原生 fetch 配置），没有
      // `params` 这一项，fetch 会静默忽略它：分页永远停在第 1 页、筛选和搜索
      // 一律返回全量，而请求成功、控制台干净，界面上完全看不出。
      const search = new URLSearchParams({
        page: String(currentPage.value),
        page_size: String(pageSize.value)
      })
      // 三个可选筛选项只在有值时才拼；直接 String(undefined) 会得到字面量
      // "undefined"，后端会把它当成一个有效的筛选值。
      const { connectionType, status, query } = filterParams.value
      if (connectionType) search.set('type', connectionType)
      if (status) search.set('status', status)
      if (query) search.set('query', query)

      const response = await http.get<PagedResponse<MCPServer>>(
        `/mcp/servers?${search.toString()}`
      )
      servers.value = response.items
      totalServers.value = response.total
      totalPages.value = response.total_pages
    } catch (error) {
      message.error('获取MCP服务器列表失败')
      console.error('获取MCP服务器列表失败:', error)
    } finally {
      isLoading.value = false
    }
  }

  // 获取统计信息
  const fetchStatistics = async () => {
    try {
      const response = await http.get<MCPStatistics>('/mcp/statistics')
      statistics.value = response
    } catch (error) {
      console.error('获取MCP统计信息失败:', error)
    }
  }

  // 获取服务器详情
  const getServerDetail = async (serverId: string) => {
    try {
      isLoading.value = true
      const response = await http.get<MCPServer>(`/mcp/servers/${serverId}`)
      currentServer.value = response
      return response
    } catch (error) {
      message.error('获取MCP服务器详情失败')
      console.error('获取MCP服务器详情失败:', error)
      return null
    } finally {
      isLoading.value = false
    }
  }

  // 获取服务器工具列表
  const getServerTools = async (serverId: string) => {
    try {
      isLoading.value = true
      const response = await http.get<MCPTool[]>(`/mcp/servers/${serverId}/tools`)
      serverTools.value = response
      return response
    } catch (error) {
      message.error('获取MCP服务器工具列表失败')
      console.error('获取MCP服务器工具列表失败:', error)
      return []
    } finally {
      isLoading.value = false
    }
  }

  // 创建服务器
  const createServer = async () => {
    try {
      isLoading.value = true

      // 验证ID是否唯一
      const checkResponse = await http.get<{ is_available: boolean }>(
        `/mcp/servers/check/${formModel.value.id}`
      )
      if (!checkResponse.is_available) {
        message.error('服务器ID已存在，请使用唯一的ID')
        return
      }

      const serverData = toCanonicalPayload()

      await http.post('/mcp/servers', serverData)
      message.success('MCP服务器创建成功')
      showServerModal.value = false
      resetForm()
      fetchServers()
      fetchStatistics()
    } catch (error) {
      message.error('创建MCP服务器失败')
      console.error('创建MCP服务器失败:', error)
      throw error // 重新抛出错误以便上层处理
    } finally {
      isLoading.value = false
    }
  }

  // 更新服务器
  const updateServer = async () => {
    try {
      isLoading.value = true

      const serverData = toCanonicalPayload()

      await http.put(`/mcp/servers/${formModel.value.id}`, serverData)
      message.success('MCP服务器更新成功')
      showServerModal.value = false
      resetForm()
      fetchServers()
      fetchStatistics()
    } catch (error) {
      message.error('更新MCP服务器失败')
      console.error('更新MCP服务器失败:', error)
      throw error // 重新抛出错误以便上层处理
    } finally {
      isLoading.value = false
    }
  }

  // 删除服务器
  const deleteServer = async (serverId: string) => {
    try {
      isLoading.value = true
      await http.delete(`/mcp/servers/${serverId}`)
      message.success('MCP服务器删除成功')
      fetchServers()
      fetchStatistics()
    } catch (error) {
      message.error('删除MCP服务器失败')
      console.error('删除MCP服务器失败:', error)
    } finally {
      isLoading.value = false
    }
  }

  // 启动服务器改为连接服务器
  const startServer = async (serverId: string) => {
    try {
      isLoading.value = true
      await http.post(`/mcp/servers/${serverId}/start`)
      message.success('MCP服务器连接成功')
      fetchServers()
      fetchStatistics()
    } catch (error) {
      message.error('连接MCP服务器失败')
      console.error('连接MCP服务器失败:', error)
    } finally {
      isLoading.value = false
    }
  }

  // 停止服务器改为断开服务器
  const stopServer = async (serverId: string) => {
    try {
      isLoading.value = true
      await http.post(`/mcp/servers/${serverId}/stop`)
      message.success('MCP服务器断开成功')
      fetchServers()
      fetchStatistics()
    } catch (error) {
      message.error('断开MCP服务器失败')
      console.error('断开MCP服务器失败:', error)
    } finally {
      isLoading.value = false
    }
  }

  // 打开创建模态框
  const openCreateModal = () => {
    modalMode.value = 'create'
    resetForm()
    showServerModal.value = true
  }

  /** 当前选中的预设标签；`null` 表示「自定义」（空白起点）。 */
  const activePresetId = ref<string | null>(null)

  /**
   * 应用一条预设，或回到自定义。
   *
   * 一个入口按 id 取表，而不是八个 `openXxxTemplate`：后者是同一段逻辑抄八遍，
   * 每加一个预设要改函数、按钮、导出三处，漏掉任何一处都不会报错。
   *
   * 预设**只填字段，不锁字段**：参考界面明确「切换类型后应更新默认字段，
   * 但保留用户可编辑能力」。填完仍走同一条唯一性校验与保存路径。
   */
  const applyPreset = (presetId: string | null) => {
    modalMode.value = 'create'
    activePresetId.value = presetId
    const preset = presetId === null
      ? undefined
      : MCP_PRESETS.find((item) => item.id === presetId)
    if (!preset) {
      // 未知 id 退回自定义而不是留一个半填的表单——后者看起来像预设生效了。
      activePresetId.value = null
      resetForm()
      showServerModal.value = true
      return
    }
    formModel.value = {
      id: preset.id,
      name: preset.label,
      description: preset.description,
      transportType: 'stdio',
      command: preset.command,
      // 拷贝而不是共享引用：表单里删一个参数不该改掉这张常量表，
      // 那会让下一次选同一个预设填出被改过的参数。
      args: [...preset.args],
      cwd: '',
      url: '',
      env: [],
      headers: [],
      tags: preset.tags,
      homepage: preset.homepage,
      docs: preset.homepage,
      appsJson: '{}',
      metadataJson: JSON.stringify({ catalog_id: `mcp:${preset.id}` }, null, 2)
    }
    showServerModal.value = true
  }

  /** 兼容既有调用点：Context7 是最常用的一条，保留一个直达入口。 */
  const openContext7Template = () => applyPreset('context7')

  // 打开编辑模态框
  const openEditModal = (server: MCPServer) => {
    modalMode.value = 'edit'

    const envArray = server.server.env
      ? Object.entries(server.server.env).map(([key, value]) => ({ key, value }))
      : []

    const headersArray = server.server.headers
      ? Object.entries(server.server.headers).map(([key, value]) => ({ key, value }))
      : []

    formModel.value = {
      id: server.id,
      name: server.name || '',
      description: server.description || '',
      transportType: server.server.type,
      command: server.server.command || '',
      args: [...(server.server.args || [])],
      cwd: server.server.cwd || '',
      url: server.server.url || '',
      env: envArray,
      headers: headersArray,
      tags: server.tags.join(', '),
      homepage: server.homepage || '',
      docs: server.docs || '',
      appsJson: JSON.stringify(server.apps || {}, null, 2),
      metadataJson: JSON.stringify(server.metadata || {}, null, 2)
    }
    showServerModal.value = true
  }

  // 保存服务器
  const saveServer = async () => {
    if (modalMode.value === 'create') {
      await createServer()
    } else {
      await updateServer()
    }
  }

  // 重置表单
  const resetForm = () => {
    formModel.value = {
      id: '',
      name: '',
      description: '',
      transportType: 'stdio',
      command: '',
      args: [],
      cwd: '',
      url: '',
      env: [],
      headers: [],
      tags: '',
      homepage: '',
      docs: '',
      appsJson: '{}',
      metadataJson: '{}'
    }
  }

  const pairsToRecord = (pairs: { key: string; value: string }[]) =>
    pairs.reduce((result, pair) => {
      if (pair.key.trim()) result[pair.key.trim()] = pair.value
      return result
    }, {} as Record<string, string>)

  const parseJsonObject = (value: string, label: string) => {
    try {
      const parsed = JSON.parse(value || '{}')
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
        throw new Error(`${label}必须是对象`)
      }
      return parsed as Record<string, unknown>
    } catch (error) {
      throw new Error(`${label} JSON 格式不正确`)
    }
  }

  const toCanonicalPayload = () => ({
    id: formModel.value.id,
    name: formModel.value.name || formModel.value.id,
    description: formModel.value.description,
    server: {
      type: formModel.value.transportType,
      command: formModel.value.transportType === 'stdio' ? formModel.value.command : null,
      args: formModel.value.transportType === 'stdio' ? formModel.value.args : [],
      cwd: formModel.value.transportType === 'stdio' ? formModel.value.cwd || null : null,
      env: formModel.value.transportType === 'stdio' ? pairsToRecord(formModel.value.env) : {},
      url: formModel.value.transportType !== 'stdio' ? formModel.value.url : null,
      headers:
        formModel.value.transportType !== 'stdio' ? pairsToRecord(formModel.value.headers) : {}
    },
    apps: parseJsonObject(formModel.value.appsJson, 'apps'),
    tags: formModel.value.tags
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    homepage: formModel.value.homepage || null,
    docs: formModel.value.docs || null,
    metadata: parseJsonObject(formModel.value.metadataJson, 'metadata')
  })

  // 过滤器操作
  const resetFilter = () => {
    filterParams.value = {
      connectionType: null,
      status: null,
      query: ''
    }
    currentPage.value = 1
    fetchServers()
  }

  const applyFilter = () => {
    currentPage.value = 1
    fetchServers()
  }

  // 分页操作
  const handlePageChange = (page: number) => {
    currentPage.value = page
    fetchServers()
  }

  const handlePageSizeChange = (size: number) => {
    pageSize.value = size
    currentPage.value = 1
    fetchServers()
  }

  // 刷新数据
  const refreshData = () => {
    fetchServers()
    fetchStatistics()
  }

  // 初始化
  const initialize = async () => {
    await fetchServers()
    await fetchStatistics()
  }

  // 获取单个服务器的工具列表
  const fetchServerTools = async (serverId: string) => {
    try {
      const response = await http.get<MCPTool[]>(`/mcp/servers/${serverId}/tools`)
      return response
    } catch (error) {
      console.error('获取MCP服务器工具列表失败:', error)
      return []
    }
  }

  // 获取单个服务器的详细信息
  const getServerById = async (serverId: string) => {
    try {
      isLoading.value = true
      const response = await http.get<MCPServer>(`/mcp/servers/${serverId}`)
      return response
    } catch (error) {
      console.error(`获取MCP服务器 ${serverId} 详情失败:`, error)
      message.error('获取服务器详情失败')
      throw error
    } finally {
      isLoading.value = false
    }
  }

  return {
    // 状态
    servers,
    formattedStatistics,
    currentServer,
    serverTools,
    isLoading,
    totalServers,
    currentPage,
    pageSize,
    totalPages,
    filterParams,
    filterOptions,
    formModel,
    showServerModal,
    modalMode,

    // 方法
    fetchServers,
    fetchStatistics,
    getServerById,
    createServer,
    updateServer,
    deleteServer,
    startServer,
    stopServer,
    openCreateModal,
    openContext7Template,
    applyPreset,
    activePresetId,
    presets: MCP_PRESETS,
    openEditModal,
    saveServer,
    resetForm,
    resetFilter,
    applyFilter,
    handlePageChange,
    handlePageSizeChange,
    refreshData,
    initialize,
    fetchServerTools
  }
}
