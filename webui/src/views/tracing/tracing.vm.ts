import { ref, computed, type Ref } from 'vue'
import { http } from '@/utils/http'
import { useMessage, useDialog } from 'naive-ui'
import { useRouter, useRoute } from 'vue-router'
import { format } from 'date-fns'

export interface TracingViewModel<S extends TraceStatistics> {
  traces: Ref<TraceBase[]>
  formattedStatistics: Ref<{ label: string; value: string | number; type?: string }[] | null>
  statisticsStatus: Ref<'idle' | 'loading' | 'ready' | 'error'>
  statisticsError: Ref<string | null>
  traceDetail: Ref<TraceBase | null>
  isConnected: Ref<boolean>
  isLoading: Ref<boolean>
  totalTraces: Ref<number>
  currentPage: Ref<number>
  pageSize: Ref<number>
  totalPages: Ref<number>
  filterParams: Ref<{ [key: string]: string | null }>
  filterOptions: Ref<{ [key: string]: { label: string; value: string }[] }>
  fetchTraces: () => Promise<void>
  fetchStatistics: () => Promise<void>
  getTraceDetail: (traceId: string) => Promise<TraceBase | null>
  viewTraceDetail: (traceId: string) => void
  goBackToList: () => void
  connectWebSocket: () => Promise<void>
  disconnectWebSocket: () => void
  resetFilter: () => void
  applyFilter: () => void
  handlePageChange: (page: number) => void
  handlePageSizeChange: (size: number) => void
  refreshData: () => void
  initialize: () => Promise<void>
  formatDate: (date: string | Date | null | undefined) => string
  formatDuration: (durationMs: number | null | undefined) => string
  formatTokens: (tokens: number | null) => string
}
// 基础接口定义
export interface TraceBase {
  id: number
  trace_id: string
  correlation_id: string | null
  request_time: string
  response_time: string | null
  duration: number | null
  status: 'pending' | 'success' | 'failed'
  error: string | null
  request: any
  response: any
}

// 分页响应接口
export interface PagedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// 基础统计信息接口
export interface TraceStatistics {}

// 基础追踪器委托接口
export interface TracerDelegate<T extends TraceBase, S extends TraceStatistics> {
  getFilterOptions(): { [key: string]: { label: string; value: string }[] }
  updateFilterOptions?(stats: S): void
  getTableColumns(baseVM: TracingViewModel<S>): any[]
  formatStatistics(stats: S): { label: string; value: string | number; type?: string }[]
  getDetailFields(): { label: string; key: string; formatter?: (value: any) => any }[]
}

// LLM追踪记录接口
export interface LLMTrace extends TraceBase {
  model_id: string
  backend_name: string
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
}

/**
 * 追踪视图模型
 * @param traceType 追踪类型
 * @param delegate 追踪器委托
 */
export function useTracingViewModel<S extends TraceStatistics>(
  traceType: string = 'llm',
  delegate: TracerDelegate<any, S>
): TracingViewModel<S> {
  const router = useRouter()
  const route = useRoute()
  const message = useMessage()
  const dialog = useDialog()

  // 状态
  const traces = ref<TraceBase[]>([])
  const statistics = ref<S | null>(null)
  const statisticsStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const statisticsError = ref<string | null>(null)
  const traceDetail = ref<TraceBase | null>(null)
  const isConnected = ref(false)
  const isLoading = ref(false)
  const totalTraces = ref(0)
  const currentPage = ref(1)
  const pageSize = ref(20)
  const totalPages = ref(1)

  // WebSocket相关
  let socket: WebSocket | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempts = 0
  const maxReconnectAttempts = 5
  const reconnectInterval = 3000

  // 过滤和搜索
  const filterParams = ref({
    correlationId: null as string | null,
    modelId: null as string | null,
    backendName: null as string | null,
    status: null as string | null,
    // 后端一直支持这三个维度（provider 与 usage_source 甚至建了索引），
    // 但界面上没有入口，于是「哪个供应商在失败」「哪些用量是估算的」问不出来。
    provider: null as string | null,
    usageSource: null as string | null,
    errorCategory: null as string | null,
    startTime: null as string | null,
    endTime: null as string | null,
    query: ''
  })

  // 过滤选项
  const filterOptions = computed(() => delegate.getFilterOptions())

  // 统计信息格式化
  const formattedStatistics = computed(() => {
    if (statisticsStatus.value !== 'ready' || !statistics.value) return null
    return delegate.formatStatistics(statistics.value as S)
  })

  // API路径构造
  const getApiPrefix = () => `/tracing/${traceType}`

  /**
   * 统计接口的查询参数。
   *
   * 与列表接口用同一份筛选条件，避免「列表看到 3 条失败、卡片显示 0 条失败」这类
   * 自相矛盾的画面。时区取浏览器时区：后端按该时区分桶，不传就会按服务器时区分桶，
   * 跨时区用户看到的「今天」是错的。
   */
  const statisticsQueryParams = () => {
    const params: Record<string, string> = {}
    const { correlationId, modelId, backendName, status, provider, usageSource, errorCategory } =
      filterParams.value as Record<string, string | null>
    if (correlationId) params.correlation_id = correlationId
    if (modelId) params.model = modelId
    if (backendName) params.backend = backendName
    if (status) params.status = status
    if (provider) params.provider = provider
    if (usageSource) params.usage_source = usageSource
    if (errorCategory) params.error_category = errorCategory
    if (filterParams.value.startTime) params.start_time = filterParams.value.startTime as string
    if (filterParams.value.endTime) params.end_time = filterParams.value.endTime as string
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (timezone) params.timezone = timezone
    return params
  }

  // 获取追踪记录列表
  const fetchTraces = async () => {
    try {
      isLoading.value = true
      const response = await http.post<PagedResponse<TraceBase>>(`${getApiPrefix()}/traces`, {
        page: currentPage.value,
        page_size: pageSize.value,
        correlation_id: filterParams.value.correlationId,
        model_id: filterParams.value.modelId,
        backend_name: filterParams.value.backendName,
        status: filterParams.value.status,
        provider: filterParams.value.provider,
        usage_source: filterParams.value.usageSource,
        error_category: filterParams.value.errorCategory,
        start_time: filterParams.value.startTime,
        end_time: filterParams.value.endTime,
        query: filterParams.value.query || undefined
      })

      traces.value = response.items
      totalTraces.value = response.total
      totalPages.value = response.total_pages
    } catch (error) {
      message.error('获取追踪记录失败')
      console.error('获取追踪记录失败:', error)
    } finally {
      isLoading.value = false
    }
  }

  // 获取统计信息
  let statisticsRequestId = 0
  const fetchStatistics = async () => {
    const requestId = ++statisticsRequestId
    statisticsStatus.value = 'loading'
    statisticsError.value = null
    try {
      // 此前这里不带任何参数：后端支持按 provider、模型、状态、时间范围和时区筛选，
      // 但界面上选好的筛选条件从未送达，于是「筛选」只影响列表、不影响统计卡片。
      const query = new URLSearchParams(statisticsQueryParams()).toString()
      const response = await http.get<S>(
        `${getApiPrefix()}/statistics${query ? `?${query}` : ''}`
      )
      if (requestId !== statisticsRequestId) return
      statistics.value = response
      statisticsStatus.value = 'ready'

      // 更新过滤选项
      if (delegate.updateFilterOptions) {
        delegate.updateFilterOptions(response as any)
      }
    } catch (error) {
      if (requestId !== statisticsRequestId) return
      statistics.value = null
      statisticsStatus.value = 'error'
      statisticsError.value = '统计信息加载失败，请稍后重试。'
      message.error('获取统计信息失败')
    }
  }

  // 获取追踪详情
  const getTraceDetail = async (traceId: string) => {
    try {
      isLoading.value = true
      const response = await http.get<TraceBase>(`${getApiPrefix()}/detail/${traceId}`)
      traceDetail.value = response
      return response
    } catch (error) {
      message.error('获取追踪详情失败')
      console.error('获取追踪详情失败:', error)
      return null
    } finally {
      isLoading.value = false
    }
  }

  // WebSocket连接管理
  const connectWebSocket = async () => {
    try {
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.close()
      }

      socket = http.ws('/tracing/ws')

      socket.onopen = () => {
        socket!.send(
          JSON.stringify({
            token: localStorage.getItem('token') || ''
          })
        )

        socket!.send(
          JSON.stringify({
            action: 'subscribe',
            tracer_type: traceType
          })
        )

        isConnected.value = true
        reconnectAttempts = 0
        if (reconnectTimer !== null) {
          clearTimeout(reconnectTimer)
          reconnectTimer = null
        }
      }

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          handleWebSocketMessage(data)
        } catch (error) {
          console.error('处理WebSocket消息失败:', error)
        }
      }

      socket.onclose = handleWebSocketClose
      socket.onerror = handleWebSocketError
    } catch (error) {
      console.error('连接WebSocket失败:', error)
      isConnected.value = false
      message.error('连接追踪系统失败')
    }
  }

  // WebSocket消息处理
  const handleWebSocketMessage = (data: any) => {
    if (data.type === 'new' || data.type === 'update') {
      if (currentPage.value === 1 && !hasActiveFilters()) {
        updateTracesList(data)
      }
      if (traceDetail.value?.trace_id === data.data.trace_id) {
        traceDetail.value = data.data
      }
      fetchStatistics()
    }
  }

  // 检查是否有活动的过滤条件
  const hasActiveFilters = () => {
    return (
      filterParams.value.correlationId ||
      filterParams.value.modelId ||
      filterParams.value.backendName ||
      filterParams.value.status ||
      filterParams.value.query
    )
  }

  // 更新追踪列表
  const updateTracesList = (data: any) => {
    const trace = data.data
    if (data.type === 'new') {
      traces.value = [trace, ...traces.value].slice(0, pageSize.value)
      totalTraces.value += 1
    } else {
      const index = traces.value.findIndex((t) => t.trace_id === trace.trace_id)
      if (index !== -1) {
        traces.value[index] = trace
      }
    }
  }

  // WebSocket关闭处理
  const handleWebSocketClose = (event: CloseEvent) => {
    isConnected.value = false
    if (!event.wasClean && reconnectAttempts < maxReconnectAttempts) {
      reconnectTimer = window.setTimeout(() => {
        reconnectAttempts++
        connectWebSocket()
      }, reconnectInterval)
    } else if (reconnectAttempts >= maxReconnectAttempts) {
      message.error('重连次数已达上限，请手动刷新页面重试')
    }
  }

  // WebSocket错误处理
  const handleWebSocketError = (error: Event) => {
    console.error('WebSocket错误:', error)
    isConnected.value = false
    message.error('连接追踪系统失败')
  }

  // 断开WebSocket连接
  const disconnectWebSocket = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
      isConnected.value = false
    }
  }

  // 查看详情
  const viewTraceDetail = (traceId: string) => {
    router.push(`/tracing/${traceType}/detail/${traceId}`)
  }

  // 返回列表
  const goBackToList = () => {
    router.push(`/tracing/${traceType}`)
  }

  // 过滤器操作
  const resetFilter = () => {
    filterParams.value = {
      correlationId: null,
      modelId: null,
      backendName: null,
      status: null,
      provider: null,
      usageSource: null,
      errorCategory: null,
      startTime: null,
      endTime: null,
      query: ''
    }
    currentPage.value = 1
    fetchTraces()
    // 统计卡片同样受筛选影响，重置后必须一起刷新，否则卡片仍显示上一次的筛选结果。
    fetchStatistics()
  }

  const applyFilter = () => {
    currentPage.value = 1
    fetchTraces()
    fetchStatistics()
  }

  // 分页操作
  const handlePageChange = (page: number) => {
    currentPage.value = page
    fetchTraces()
  }

  const handlePageSizeChange = (size: number) => {
    pageSize.value = size
    currentPage.value = 1
    fetchTraces()
  }

  // 刷新数据
  const refreshData = () => {
    disconnectWebSocket()
    fetchTraces()
    fetchStatistics()
    connectWebSocket()
  }

  // 格式化函数
  const formatDate = (date: string | Date | null | undefined) => {
    if (!date) return '---'
    try {
      const dateObj = typeof date === 'string' ? new Date(date) : date
      return format(dateObj, 'yyyy-MM-dd HH:mm:ss')
    } catch (error) {
      return String(date)
    }
  }

  // 接 undefined 而不只是 null：调用方传的是 `traceDetail?.duration`，
  // 追踪详情尚未加载时那是 undefined，不是 null。两者都该显示「未知」，
  // 而不是让 undefined 走到 `.toFixed` 抛 TypeError。
  const formatDuration = (durationMs: number | null | undefined) => {
    if (durationMs === null || durationMs === undefined) return '未知'
    return durationMs < 1000
      ? `${durationMs.toFixed(2)} 毫秒`
      : `${(durationMs / 1000).toFixed(2)} 秒`
  }

  const formatTokens = (tokens: number | null) => {
    if (tokens === null || tokens === undefined) return '未知'
    return tokens.toLocaleString()
  }

  // 初始化
  const initialize = async () => {
    const query = route.query
    if (query.model) {
      filterParams.value.modelId = query.model as string
    }
    if (query.correlation) {
      filterParams.value.correlationId = query.correlation as string
    }
    if (query.backend) {
      filterParams.value.backendName = query.backend as string
    }

    await fetchTraces()
    await fetchStatistics()
    connectWebSocket()
  }

  return {
    // 状态
    traces,
    formattedStatistics,
    statisticsStatus,
    statisticsError,
    traceDetail,
    isConnected,
    isLoading,
    totalTraces,
    currentPage,
    pageSize,
    totalPages,
    filterParams,
    filterOptions,

    // 方法
    fetchTraces,
    fetchStatistics,
    getTraceDetail,
    viewTraceDetail,
    goBackToList,
    connectWebSocket,
    disconnectWebSocket,
    resetFilter,
    applyFilter,
    handlePageChange,
    handlePageSizeChange,
    refreshData,
    initialize,

    // 格式化函数
    formatDate,
    formatDuration,
    formatTokens
  }
}
