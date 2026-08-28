import { ref, computed, h } from 'vue'
import { useTracingViewModel } from '../tracing.vm'
import type { TraceBase, TracerDelegate, TraceStatistics } from '../tracing.vm'
import { NTag, NButton } from 'naive-ui'

// LLM 追踪记录接口
export interface LLMTrace extends TraceBase {
  model_id: string
  backend_name: string
  provider: string | null
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cached_tokens: number | null
  cache_write_tokens: number | null
  usage_source: string | null
  ttft_ms: number | null
  attempt_count: number | null
  error_category: string | null
  cost_snapshot: Record<string, any> | null
}

/** 一个聚合维度的公共形状：请求数、Token、平均耗时与成本。 */
interface GroupedStat {
  count: number
  tokens: number
  avg_duration: number
  cost: string
  unpriced_requests: number
}

// LLM 统计信息接口
export interface LLMStatistics {
  timezone?: string
  overview: {
    total_tokens: number
    total_requests: number
    pending_requests: number
    success_requests: number
    failed_requests: number
    total_cost: string
    cost_currency: string | null
    unpriced_requests: number
  }
  latency: {
    avg_ttft_ms: number | null
    max_ttft_ms: number | null
    avg_duration: number | null
    avg_attempt_count: number | null
  }
  daily_stats: Array<{
    date: string
    requests: number
    tokens: number
    success: number
    failed: number
  }>
  hourly_stats: Array<{
    hour: string
    requests: number
    tokens: number
  }>
  models: Array<GroupedStat & { model_id: string }>
  backends: Array<GroupedStat & { backend_name: string }>
  providers: Array<GroupedStat & { provider: string | null }>
  usage_sources: Array<GroupedStat & { usage_source: string | null }>
  error_categories: Array<GroupedStat & { error_category: string | null }>
}

/** Token 用量来源的中文标签：估算与未知必须与供应商实测区分开。 */
const USAGE_SOURCE_LABELS: Record<string, string> = {
  provider: '供应商返回',
  estimated: '本地估算',
  unknown: '未知'
}

// LLM 追踪器委托实现
class LLMTracerDelegate implements TracerDelegate<LLMTrace, LLMStatistics> {
  private filterOptions = ref({
    modelId: [] as { label: string; value: string }[],
    backendName: [] as { label: string; value: string }[],
    provider: [] as { label: string; value: string }[],
    usageSource: [] as { label: string; value: string }[],
    errorCategory: [] as { label: string; value: string }[],
    status: [
      { label: '请求中', value: 'pending' },
      { label: '成功', value: 'success' },
      { label: '失败', value: 'failed' }
    ]
  })

  getFilterOptions() {
    return this.filterOptions.value
  }

  updateFilterOptions(stats: LLMStatistics) {
    this.filterOptions.value = {
      ...this.filterOptions.value,
      modelId: stats.models.map((model) => ({
        label: model.model_id,
        value: model.model_id
      })),
      backendName: stats.backends.map((backend) => ({
        label: backend.backend_name,
        value: backend.backend_name
      })),
      // provider / usage_source / error_category 由后端返回但此前被前端丢弃，
      // 于是这三个维度既没有筛选项也没有可视化。
      provider: (stats.providers || [])
        .filter((row) => !!row.provider)
        .map((row) => ({ label: row.provider as string, value: row.provider as string })),
      usageSource: (stats.usage_sources || [])
        .filter((row) => !!row.usage_source)
        .map((row) => ({
          label: USAGE_SOURCE_LABELS[row.usage_source as string] || (row.usage_source as string),
          value: row.usage_source as string
        })),
      errorCategory: (stats.error_categories || [])
        .filter((row) => !!row.error_category)
        .map((row) => ({
          label: row.error_category as string,
          value: row.error_category as string
        }))
    }
  }

  getTableColumns(baseVM: ReturnType<typeof useTracingViewModel>) {
    return [
      {
        title: 'ID',
        key: 'trace_id',
        width: 120,
        ellipsis: {
          tooltip: true
        }
      },
      {
        title: '回合 ID',
        key: 'correlation_id',
        width: 150,
        ellipsis: {
          tooltip: true
        },
        render: (row: LLMTrace) => row.correlation_id || '---'
      },
      {
        title: '模型',
        key: 'model_id',
        width: 160
      },
      {
        title: '供应商',
        key: 'provider',
        width: 140,
        ellipsis: { tooltip: true },
        render: (row: LLMTrace) => row.provider || row.backend_name || '---'
      },
      {
        title: '后端',
        key: 'backend_name',
        width: 120
      },
      {
        title: '请求时间',
        key: 'request_time',
        width: 180,
        render: (row: LLMTrace) => baseVM.formatDate(row.request_time)
      },
      {
        title: '状态',
        key: 'status',
        width: 100,
        render: (row: LLMTrace) => {
          const statusMap = {
            pending: { type: 'warning' as const, text: '请求中' },
            success: { type: 'success' as const, text: '成功' },
            failed: { type: 'error' as const, text: '失败' }
          }
          const status = statusMap[row.status] || statusMap.pending
          return h(NTag, { type: status.type, size: 'small' }, { default: () => status.text })
        }
      },
      {
        title: '失败类型',
        key: 'error_category',
        width: 130,
        ellipsis: { tooltip: true },
        render: (row: LLMTrace) => row.error_category || '---'
      },
      {
        title: '耗时',
        key: 'duration',
        width: 100,
        render: (row: LLMTrace) => baseVM.formatDuration(row.duration)
      },
      {
        title: '首字节',
        key: 'ttft_ms',
        width: 100,
        // 非流式请求没有真实首字节，显示 --- 而不是 0，否则会被误读成「极快」。
        render: (row: LLMTrace) =>
          row.ttft_ms === null || row.ttft_ms === undefined ? '---' : `${row.ttft_ms} ms`
      },
      {
        title: '尝试次数',
        key: 'attempt_count',
        width: 100,
        render: (row: LLMTrace) => row.attempt_count ?? '---'
      },
      {
        title: 'Tokens',
        key: 'total_tokens',
        width: 100,
        render: (row: LLMTrace) => baseVM.formatTokens(row.total_tokens)
      },
      {
        title: '用量来源',
        key: 'usage_source',
        width: 120,
        render: (row: LLMTrace) =>
          row.usage_source ? USAGE_SOURCE_LABELS[row.usage_source] || row.usage_source : '---'
      },
      {
        title: '成本',
        key: 'cost',
        width: 120,
        render: (row: LLMTrace) => {
          const total = row.cost_snapshot?.total_cost
          if (total === undefined || total === null) return '未定价'
          const currency = row.cost_snapshot?.currency || ''
          return `${total} ${currency}`.trim()
        }
      },
      {
        title: '操作',
        key: 'actions',
        width: 100,
        render: (row: LLMTrace) => {
          return h(
            NButton,
            {
              text: true,
              type: 'primary',
              onClick: () => baseVM.viewTraceDetail(row.trace_id)
            },
            { default: () => '查看详情' }
          )
        }
      }
    ]
  }

  formatStatistics(stats: LLMStatistics) {
    const cards: { label: string; value: string | number; type?: string }[] = [
      { label: '总请求数', value: stats.overview.total_requests },
      { label: '请求中', value: stats.overview.pending_requests, type: 'warning' },
      { label: '成功请求', value: stats.overview.success_requests, type: 'success' },
      { label: '失败请求', value: stats.overview.failed_requests, type: 'error' },
      { label: '总Token数', value: stats.overview.total_tokens, type: 'info' }
    ]
    // 成本与首字节此前只存在于数据库里；界面上看不到，等于没有统计。
    if (stats.overview.total_cost !== undefined) {
      const currency = stats.overview.cost_currency || ''
      cards.push({
        label: '总成本',
        value: `${stats.overview.total_cost} ${currency}`.trim(),
        type: 'info'
      })
    }
    if (stats.overview.unpriced_requests) {
      cards.push({
        label: '未定价请求',
        value: stats.overview.unpriced_requests,
        type: 'warning'
      })
    }
    const ttft = stats.latency?.avg_ttft_ms
    if (ttft !== null && ttft !== undefined) {
      cards.push({ label: '平均首字节', value: `${Math.round(ttft)} ms`, type: 'info' })
    }
    return cards
  }

  getDetailFields() {
    return [
      { label: '回合 ID', key: 'correlation_id' },
      { label: '供应商', key: 'provider' },
      { label: '模型', key: 'model_id' },
      { label: '后端', key: 'backend_name' },
      { label: '提示Token', key: 'prompt_tokens' },
      { label: '补全Token', key: 'completion_tokens' },
      { label: '总Token', key: 'total_tokens' },
      { label: '缓存Token', key: 'cached_tokens' },
      { label: '缓存写入Token', key: 'cache_write_tokens' },
      {
        label: '用量来源',
        key: 'usage_source',
        formatter: (value: any) =>
          value ? USAGE_SOURCE_LABELS[String(value)] || String(value) : '---'
      },
      {
        label: '首字节耗时',
        key: 'ttft_ms',
        formatter: (value: any) =>
          value === null || value === undefined ? '---' : `${value} ms`
      },
      { label: '尝试次数', key: 'attempt_count' },
      { label: '失败类型', key: 'error_category' },
      {
        label: '成本快照',
        key: 'cost_snapshot',
        formatter: (value: any) =>
          value ? `${value.total_cost ?? '---'} ${value.currency ?? ''}`.trim() : '未定价'
      }
    ]
  }
}

/**
 * LLM 追踪视图模型
 * 扩展基础追踪视图模型,添加 LLM 特定的功能
 */
export function useLLMTracingViewModel() {
  const delegate = new LLMTracerDelegate()
  const baseVM = useTracingViewModel('llm', delegate)

  // 状态选项
  const statusOptions = delegate.getFilterOptions().status

  // 表格列定义
  const columns = computed(() => delegate.getTableColumns(baseVM))

  return {
    ...baseVM,
    statusOptions,
    columns
  }
}
