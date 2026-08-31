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
      const params = {
        page: currentPage.value,
        page_size: pageSize.value,
        type: filterParams.value.connectionType,
        status: filterParams.value.status,
        query: filterParams.value.query || undefined
      }

      const response = await http.get<PagedResponse<MCPServer>>('/mcp/servers', { params })
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

  // Context7 follows the canonical MCP stdio entry shape.
  const openContext7Template = () => {
    modalMode.value = 'create'
    formModel.value = {
      id: 'context7',
      name: 'Context7',
      description: 'Context7 文档检索 MCP 服务器',
      transportType: 'stdio',
      command: 'npx',
      args: ['-y', '@upstash/context7-mcp'],
      cwd: '',
      url: '',
      env: [],
      headers: [],
      tags: 'context7, documentation',
      homepage: 'https://context7.com',
      docs: 'https://github.com/upstash/context7',
      appsJson: '{}',
      metadataJson: '{\n  "provider": "context7"\n}'
    }
    showServerModal.value = true
  }

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
