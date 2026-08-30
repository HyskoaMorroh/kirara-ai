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
  /** 同一供应商内的重试次数。`null` 表示没有 attempt 数据，与 0 不同。 */
  retry_count: number | null
  /** 切换供应商的次数。`A → B → A` 是 2 次，不是 1 次。 */
  failover_count: number | null
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
  /**
   * 四类 Token 拆分（需求 22.1）。
   *
   * 只有 `tokens` 时，「同样 100 万 Token」既可能是一家几乎全在读上下文、
   * 也可能是一家几乎全在生成，而两者的处置相反。
   *
   * 缓存两项为 `null` 表示这一组里没有任何上游报过缓存，与「报了 0」不同：
   * 前者要去查上游是否返回 usage，后者才是真的没命中。
   */
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number | null
  cache_write_tokens: number | null
  /**
   * 三态计数与成功率（需求 9 的 Provider 统计）。
   *
   * `error_categories` 回答「在失败什么」，回答不了「谁在失败」——一个 timeout
   * 分组里可能混着三家供应商。而故障转移队列该把谁排后面，依据正是各家成功率。
   *
   * `success_rate` 为 `null` 表示这一组还没有任何请求有结论（全是 pending）：
   * 报 0% 会让一家刚配好、只有一条在途请求的供应商看起来是最差的那一个。
   * `pending_requests` 不进成功率分母。
   */
  success_requests: number
  failed_requests: number
  pending_requests: number
  success_rate: number | null
}

/**
 * 趋势分桶的四类 Token 拆分与成本。
 *
 * 缓存两项在趋势里按 0 累加（不是 `null`），避免折线中间断开。
 *
 * 成本进趋势是为了回答「这个月贵了三倍，是哪天开始的」——只有一个 30 天合计时，
 * 这个问题只能靠手工二分时间范围反复重查。口径与 `overview` 一致：
 * `cost` 只是主币种的合计，其余币种在 `cost_by_currency` 里逐一列出，
 * 两种货币绝不相加。`cost_currency` 为 `null` 表示这一桶没有任何定价证据。
 */
interface BucketTokenBreakdown {
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  cache_write_tokens: number
  cost: string
  cost_currency: string | null
  cost_by_currency: Record<string, string>
  /** 未定价请求单列：按 0 元并入合计会把「没匹配到价格版本」显示成「这天便宜」。 */
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
    /**
     * 每种货币各自的合计。
     *
     * `total_cost` 只是金额最大的那个币种的合计——把两种货币相加会得到一个
     * 没有单位的数字，而那种错误不会报错。单币种部署下这里只有一个键。
     */
    cost_by_currency: Record<string, string>
    unpriced_requests: number
    /**
     * 四类 Token 的合计（需求 22.1 逐项点名了它们）。
     *
     * `total_tokens` 的含义不变，这四项与它并列。缺了它们，缓存命中率无从计算，
     * 而输入 Token 的单价通常是缓存读取的 5~10 倍：一份「总 Token 没变」的账单
     * 在命中率从 80% 掉到 0% 时会翻几倍。
     */
    total_prompt_tokens: number
    total_completion_tokens: number
    /** `null` = 没有上游报过缓存（未知）；`0` = 报了、确实没命中。 */
    total_cached_tokens: number | null
    total_cache_write_tokens: number | null
    /**
     * 缓存命中率 = 缓存读取 /（输入 + 缓存写入 + 缓存读取），范围 0~1。
     *
     * `null` 表示未知（没有任何上游报缓存）。显示 0% 会让人去查一个并不存在的
     * 缓存失效问题，所以这一档必须与真实的 0% 区分开。
     */
    cache_hit_rate: number | null
  }
  latency: {
    avg_ttft_ms: number | null
    max_ttft_ms: number | null
    avg_duration: number | null
    avg_attempt_count: number | null
    /**
     * 重试与故障转移分开统计。
     *
     * `avg_attempt_count` 分不开这两件事：平均 3 次尝试可能是「一家重试两次」
     * 也可能是「换了两家」，而处置相反——前者调超时与退避，
     * 后者查供应商健康与熔断。
     */
    avg_retry_count: number | null
    avg_failover_count: number | null
  }
  daily_stats: Array<
    {
      date: string
      requests: number
      tokens: number
      success: number
      failed: number
    } & BucketTokenBreakdown
  >
  hourly_stats: Array<
    {
      hour: string
      requests: number
      tokens: number
    } & BucketTokenBreakdown
  >
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
    // 重试与故障转移分成两张卡片。
    //
    // 两者回答的问题相反：平均重试高说明该调超时与退避，平均转移高说明该查
    // 供应商健康与熔断。合并成 `avg_attempt_count` 一个数就分不开，
    // 而分不开时任何处置都是猜。
    //
    // `null` 表示「没有 attempt 数据」（旧记录、第三方调用方、从未走过故障转移
    // 路径的请求），此时**不显示卡片**——显示 0 等于断言「确实没重试过」，
    // 而我们不知道。0 本身要显示：那是「跑过、没重试过」这个有用的正面结论。
    //
    // 保留两位小数：平均 0.25 次转移与 0 次是完全不同的结论，取整会把前者抹成后者。
    const retries = stats.latency?.avg_retry_count
    if (retries !== null && retries !== undefined) {
      cards.push({ label: '平均重试次数', value: retries.toFixed(2), type: 'info' })
    }
    const failovers = stats.latency?.avg_failover_count
    if (failovers !== null && failovers !== undefined) {
      cards.push({
        label: '平均故障转移次数',
        value: failovers.toFixed(2),
        // 转移意味着有供应商已经不可用，比重试更值得注意。
        type: failovers > 0 ? 'warning' : 'info'
      })
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
      // 重试与故障转移分列。`attempt_count` 分不开它们：3 次尝试可能是
      // 「一家重试两次」也可能是「换了两家」，而处置相反。
      // `null` 显示 `---` 而不是 0——0 是「确实没重试过」这个论断。
      {
        label: '重试次数',
        key: 'retry_count',
        formatter: (value: any) =>
          value === null || value === undefined ? '---' : String(value)
      },
      {
        label: '故障转移次数',
        key: 'failover_count',
        formatter: (value: any) =>
          value === null || value === undefined ? '---' : String(value)
      },
      { label: '失败类型', key: 'error_category' },
      {
        label: '成本快照',
        key: 'cost_snapshot',
        formatter: (value: any) =>
          value ? `${value.total_cost ?? '---'} ${value.currency ?? ''}`.trim() : '未定价'
      },
      {
        // 「这条请求按哪个定价版本算的」——改价之后回看历史账单时，
        // 这是唯一能回答该问题的字段。它一直在快照里，只是没有出口：
        // 于是「为什么这两条同样的请求价格不同」无从查证。
        label: '定价版本',
        key: 'cost_snapshot',
        formatter: (value: any) => value?.price_version_id || '---'
      },
      {
        // 计价时刻与请求时刻可能不同（快照在请求完成时冻结）。
        label: '计价时刻',
        key: 'cost_snapshot',
        formatter: (value: any) => value?.priced_at || '---'
      }
    ]
  }
}

/**
 * LLM 追踪视图模型
 * 扩展基础追踪视图模型,添加 LLM 特定的功能
 */
/**
 * 共享的 delegate 实例。
 *
 * 导出的理由是 `formatStatistics` / `getDetailFields` / `getTableColumns` 都是
 * 纯函数式的判定（哪些卡片出现、`null` 与 0 怎么区分），直接测它比挂载整个
 * 统计页去间接断言更精确——后者一旦图表库或布局改动就会连带失败，
 * 而那与这些判定无关。
 *
 * 单实例也保证「界面上看到的口径」与「测试里断言的口径」是同一份代码。
 */
export const llmTracingDelegate = new LLMTracerDelegate()

export function useLLMTracingViewModel() {
  const delegate = llmTracingDelegate
  const baseVM = useTracingViewModel('llm', delegate)

  // 状态选项
  const statusOptions = delegate.getFilterOptions().status

  // 表格列定义
  const columns = computed(() => delegate.getTableColumns(baseVM))

  /**
   * 详情页字段定义。
   *
   * `getDetailFields()` 此前**没有任何消费者**：详情页自己硬编码了一套
   * `n-descriptions`，比这里少了首字节、尝试/重试/转移次数、失败类型、
   * 用量来源、缓存写入 Token 与成本快照。字段在、后端返回也在，
   * 只有渲染那一跳断了——那类缺口在界面上表现为「详情页信息比列表还少」。
   * 暴露出来让详情页直接用同一份定义，两处口径不会再漂移。
   */
  const detailFields = delegate.getDetailFields()

  return {
    ...baseVM,
    statusOptions,
    columns,
    detailFields
  }
}
