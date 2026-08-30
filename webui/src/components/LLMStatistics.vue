<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import {
  NCard,
  NGrid,
  NGi,
  NStatistic,
  NProgress,
  NNumberAnimation,
  NSpace,
  NTabs,
  NTabPane,
  NDivider,
  NIcon,
  NTooltip,
  NAlert,
  NButton,
  NSpin
} from 'naive-ui'
import { http } from '@/utils/http'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent
} from 'echarts/components'
import VChart from 'vue-echarts'
import { TrendingUpOutline, TimeOutline, PieChartOutline, ServerOutline } from '@vicons/ionicons5'
import type { LLMStatistics } from '@/views/tracing/llm/llm-tracing.vm'
import { useThemeStore } from '@/stores/theme'
// 注册 ECharts 组件
use([
  CanvasRenderer,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent
])

// ECharts 的 Canvas 渲染器不解析 CSS 变量，配置项里必须给具体色值，
// 因此这里从主题取色，图表文字与浮层随主题一起变化。
const themeStore = useThemeStore()
const chartText = computed(() => themeStore.seed.text)
const chartTextSecondary = computed(() => themeStore.seed.textSecondary)
const chartSurface = computed(() => themeStore.seed.elevated)
const chartBorder = computed(() => themeStore.seed.border)
// 饼图扇区之间的描边需要与所在卡片同色，才能读作“留白”而不是一圈白边
const chartCard = computed(() => themeStore.seed.card)
// 缩略轴轨道原先按 isDark 手写两段 rgba，现改为直接取色板的分割线色，
// 明暗与色板切换都自动跟随，不再有硬编码分支
const chartZoomBg = computed(() => themeStore.seed.divider)

// LLM 统计数据
const llmStats = ref<LLMStatistics | null>(null)
const statisticsStatus = ref<'loading' | 'ready' | 'error'>('loading')
const statisticsError = ref('')

/**
 * 外部传入的筛选条件。
 *
 * 同一个统计接口在 `/tracing/llm` 上是带筛选与浏览器时区调用的
 * （`tracing.vm.ts` 的 `statisticsQueryParams`），此前这个组件却发裸 GET：
 * 跨时区用户在两处看到的「今天」不一致，而两处显示的是同一批数据。
 * 现在筛选由宿主页面下发，时区由本组件无条件补齐——两条都不能靠调用方记得。
 */
const props = defineProps<{
  filters?: Record<string, string | null | undefined>
}>()

/** 统计查询参数：丢掉空值，并始终带上浏览器时区。 */
const statisticsParams = computed<Record<string, string>>(() => {
  const params: Record<string, string> = {}
  for (const [key, value] of Object.entries(props.filters ?? {})) {
    // 空串会被后端当成一个真实的筛选值，导致「筛了但筛不到」。
    if (typeof value === 'string' && value.trim()) {
      params[key] = value
    }
  }
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (timezone) params.timezone = timezone
  return params
})

// 获取 LLM 统计数据
let statisticsRequestId = 0
const fetchLLMStats = async () => {
  const requestId = ++statisticsRequestId
  statisticsStatus.value = 'loading'
  statisticsError.value = ''
  try {
    // `http.get` 的第二个参数是 `RequestInit`，没有 `params` 这一项；
    // 查询串必须自己拼，与 `tracing.vm.ts` 的做法保持一致。
    const query = new URLSearchParams(statisticsParams.value).toString()
    const data = await http.get<LLMStatistics>(
      `/tracing/llm/statistics${query ? `?${query}` : ''}`
    )
    if (requestId !== statisticsRequestId) return
    llmStats.value = data
    statisticsStatus.value = 'ready'
  } catch {
    if (requestId !== statisticsRequestId) return
    llmStats.value = null
    statisticsStatus.value = 'error'
    statisticsError.value = '统计数据加载失败，请稍后重试。'
  }
}

// 筛选条件变化时立刻重取：沿用 requestId 守卫，晚到的旧响应不会覆盖新结果。
watch(
  () => statisticsParams.value,
  () => {
    void fetchLLMStats()
  },
  { deep: true }
)

// 保留自动刷新，同时避免用户离开引导页后旧组件继续发起请求。
let refreshTimer: ReturnType<typeof setInterval> | null = null

// 图表主题色
const themeColorsLight = [
  '#3b82f6', // 蓝色
  '#10b981', // 绿色
  '#6366f1', // 靛蓝色
  '#8b5cf6', // 紫色
  '#f59e0b', // 橙色
  '#ef4444', // 红色
  '#64748b', // 灰色
  '#0ea5e9' // 浅蓝色
]

// 深色底上同一组分类色需要整体提亮，色相保持不变，保证系列之间仍然可区分
const themeColorsDark = [
  '#60a5fa', // 蓝色
  '#34d399', // 绿色
  '#818cf8', // 靛蓝色
  '#a78bfa', // 紫色
  '#fbbf24', // 橙色
  '#f87171', // 红色
  '#94a3b8', // 灰色
  '#38bdf8' // 浅蓝色
]

const themeColors = computed(() => (themeStore.isDark ? themeColorsDark : themeColorsLight))

/**
 * 除主币种以外的其他货币合计。
 *
 * 「30天成本」那个数字只是 `cost_currency` 这一种货币的合计——后端刻意不把不同
 * 货币加在一起，因为那会得到一个没有单位的数字且不会报错。混币部署里必须把
 * 其余币种说出来，否则用户会把一个偏小的数字当成全部花费。
 */
const otherCurrencyTotals = computed<string[]>(() => {
  const overview = llmStats.value?.overview
  if (!overview) return []
  const primary = overview.cost_currency
  return Object.entries(overview.cost_by_currency || {})
    .filter(([currency]) => currency && currency !== primary)
    .map(([currency, amount]) => `${amount} ${currency}`)
})

/**
 * Token 计数的显示文本。
 *
 * `null` / `undefined` 显示「未上报」而不是 0：
 *
 * - 缓存两项的 `null` 是后端明确表达的「没有上游报过缓存」；
 * - `undefined` 出现在旧后端（WebUI 可独立升级，这个组合是真的会发生的）
 *   ——它同样是「不知道」，编一个 0 出来会让人以为量测过且确实是零。
 */
const formatNullableTokens = (value: number | null | undefined) =>
  value === null || value === undefined ? '未上报' : value.toLocaleString()

/**
 * 成功率的显示文本。
 *
 * `null`（这一组还没有任何请求有结论，全是 pending）显示「未知」而不是 0%：
 * 一家刚配好、只有一条在途请求的供应商不该看起来是最差的那一个。
 * `undefined` 出现在旧后端上，同样是「不知道」。
 */
const formatSuccessRate = (value: number | null | undefined) => {
  if (value === null || value === undefined) return '未知'
  return `${(value * 100).toFixed(1)}%`
}

/**
 * 单次请求成本的显示文本。
 *
 * 这是回答「该不该换模型」的那个数，而合计成本回答不了：请求量最大的模型往往
 * 不是最贵的，合计成本高也可能只是因为调用多。两个模型合计成本相同、单次成本
 * 差十倍时，只看合计完全看不出差别。
 *
 * **分母是已定价的请求数，不是总请求数。** 未定价请求的成本是「不知道」，
 * 把它们算进分母会得到一个偏低的数字，而那个数字看起来完全正常——
 * 没有任何迹象表明它被稀释过。
 *
 * 全部未定价时返回「无数据」而不是 0：0 是「不花钱」这个论断，
 * 而这里的事实是「还没配定价」。
 */
const formatCostPerRequest = (
  cost: string | number | null | undefined,
  count: number | null | undefined,
  unpriced: number | null | undefined
) => {
  const total = Number(cost)
  if (cost === null || cost === undefined || Number.isNaN(total)) return '无数据'
  const priced = (count ?? 0) - (unpriced ?? 0)
  if (priced <= 0) return '无数据'
  const per = total / priced
  // 单次成本常常是很小的数（万分之几），两位小数会把它们全抹成 0.00。
  // 用有效数字而不是固定小数位：0.00042 与 0.00038 是不同的结论。
  return per >= 0.01 ? per.toFixed(4) : per.toPrecision(2)
}

/**
 * 缓存命中率的显示文本。
 *
 * `null` 显示「未上报」而不是 0%：两者在界面上长得一样，但处置相反——
 * 前者要去查上游是否返回 usage（很多兼容端点根本不报缓存字段），
 * 后者才是真的没命中、该去查提示词前缀是否稳定。把未知显示成 0%
 * 会让人去排查一个并不存在的缓存失效问题。
 */
const cacheHitRateText = computed(() => {
  const rate = llmStats.value?.overview.cache_hit_rate
  if (rate === null || rate === undefined) return '未上报'
  return `${(rate * 100).toFixed(1)}%`
})

// 更新图表配置
const dailyTokensOption = computed(() => ({
  title: {
    text: '每日 Token 使用趋势',
    subtext: '按输入 / 输出 / 缓存读写分类，最近30天',
    left: 'center',
    top: 10,
    textStyle: {
      fontSize: 15,
      fontWeight: 'normal',
      color: chartText.value
    },
    subtextStyle: {
      fontSize: 12,
      color: chartTextSecondary.value
    }
  },
  tooltip: {
    trigger: 'axis',
    axisPointer: {
      type: 'shadow'
    },
    backgroundColor: chartSurface.value
  },
  legend: {
    // 四类分项必须有图例，否则四条线看不出哪条是哪条。
    bottom: 0,
    icon: 'circle',
    textStyle: {
      color: chartTextSecondary.value
    }
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '22%',
    top: '20%',
    containLabel: true
  },
  xAxis: {
    type: 'category',
    data: llmStats.value?.daily_stats.map((item: { date: string }) => item.date) ?? [],
    axisLabel: {
      rotate: 45,
      formatter: (value: string) => value.slice(5), // 只显示月-日
      color: chartTextSecondary.value
    }
  },
  yAxis: {
    type: 'value',
    name: 'Tokens',
    nameLocation: 'middle',
    nameGap: 50,
    nameTextStyle: {
      color: chartTextSecondary.value,
      fontWeight: 'normal'
    },
    axisLabel: {
      color: chartTextSecondary.value
    }
  },
  dataZoom: [
    {
      type: 'slider',
      show: true,
      start: 50,
      end: 100,
      height: 20,
      borderColor: 'transparent',
      backgroundColor: chartZoomBg.value,
      handleStyle: {
        color: themeColors.value[0]
      }
    }
  ],
  // 四条堆叠面积 + 一条总量线。
  //
  // 一条总量线看得出「涨了」，看不出涨的是输入还是输出，而两者的处置相反：
  // 输入涨查上下文与历史长度，输出涨查 prompt 与 max_tokens。
  // 缓存读单独一条更关键——它涨而输入降是省钱，两者同涨才是真的用量上升。
  //
  // 总量线保留：堆叠四项之和不等于 `tokens`（`total_tokens` 只含输入+输出，
  // 缓存读写是输入的细分），把总量抹掉会让历史看板上的数字对不上。
  series: [
    {
      name: '输入',
      data: llmStats.value?.daily_stats.map((item) => item.prompt_tokens) ?? [],
      type: 'line',
      stack: 'token-split',
      smooth: true,
      symbolSize: 4,
      areaStyle: { opacity: 0.25 },
      itemStyle: { color: themeColors.value[0] }
    },
    {
      name: '输出',
      data: llmStats.value?.daily_stats.map((item) => item.completion_tokens) ?? [],
      type: 'line',
      stack: 'token-split',
      smooth: true,
      symbolSize: 4,
      areaStyle: { opacity: 0.25 },
      itemStyle: { color: themeColors.value[1] }
    },
    {
      name: '缓存读取',
      data: llmStats.value?.daily_stats.map((item) => item.cached_tokens) ?? [],
      type: 'line',
      stack: 'token-split',
      smooth: true,
      symbolSize: 4,
      areaStyle: { opacity: 0.25 },
      itemStyle: { color: themeColors.value[2] }
    },
    {
      name: '缓存写入',
      data: llmStats.value?.daily_stats.map((item) => item.cache_write_tokens) ?? [],
      type: 'line',
      stack: 'token-split',
      smooth: true,
      symbolSize: 4,
      areaStyle: { opacity: 0.25 },
      itemStyle: { color: themeColors.value[3] }
    },
    {
      name: 'Token 使用量',
      data: llmStats.value?.daily_stats.map((item: { tokens: number }) => item.tokens) ?? [],
      type: 'line',
      smooth: true,
      symbolSize: 6,
      // 不参与堆叠：它是输入+输出的总数，与上面四项不同口径。
      itemStyle: {
        color: themeColors.value[0]
      },
      lineStyle: {
        type: 'dashed'
      }
    }
  ]
}))

const requestStatusOption = computed(() => ({
  title: {
    text: '请求状态分布',
    subtext: '各状态请求数量占比',
    left: 'center',
    top: 10,
    textStyle: {
      fontSize: 15,
      fontWeight: 'normal',
      color: chartText.value
    },
    subtextStyle: {
      fontSize: 12,
      color: chartTextSecondary.value
    }
  },
  tooltip: {
    trigger: 'item',
    formatter: '{b}: {c} ({d}%)',
    backgroundColor: chartSurface.value
  },
  legend: {
    orient: 'horizontal',
    bottom: 10,
    icon: 'circle',
    textStyle: {
      color: chartTextSecondary.value
    }
  },
  series: [
    {
      type: 'pie',
      radius: ['45%', '70%'],
      avoidLabelOverlap: true,
      itemStyle: {
        /* 例外：ECharts 在 canvas 里绘制，只接受数字，无法读 CSS 变量；
           这是环形图扇区的几何参数，不属于界面表面的圆角体系 */
        borderRadius: 10,
        borderColor: chartCard.value,
        borderWidth: 2
      },
      label: {
        show: false
      },
      emphasis: {
        label: {
          show: true,
          fontSize: 14,
          fontWeight: 'bold'
        },
        scaleSize: 10
      },
      data: [
        {
          value: llmStats.value?.overview.success_requests ?? 0,
          name: '成功',
          itemStyle: { color: themeColors.value[1] }
        },
        {
          value: llmStats.value?.overview.failed_requests ?? 0,
          name: '失败',
          itemStyle: { color: themeColors.value[5] }
        },
        {
          value: llmStats.value?.overview.pending_requests ?? 0,
          name: '处理中',
          itemStyle: { color: themeColors.value[4] }
        }
      ]
    }
  ]
}))

const modelUsageOption = computed(() => ({
  title: {
    text: '模型使用分析',
    subtext: '各模型请求量、Token、成本与平均响应时间',
    left: 'center',
    top: 10,
    textStyle: {
      fontSize: 15,
      fontWeight: 'normal',
      color: chartText.value
    },
    subtextStyle: {
      fontSize: 12,
      color: chartTextSecondary.value
    }
  },
  /**
   * 逐模型的 Token 与成本必须出现在 tooltip 里。
   *
   * `models[]` 一直带 `tokens` / `cost` / `unpriced_requests`，但此前图上只画了
   * 请求数与平均响应时间，tooltip 又是默认的 `trigger: 'axis'`（无 formatter），
   * 于是「哪个模型最贵」在界面上无从回答——而那是按模型看统计的首要问题：
   * 请求数最多的模型往往不是花钱最多的那一个。
   *
   * `unpriced_requests` 必须一起标出：未定价请求按 0 元并入合计会让某个模型
   * 看起来很便宜，而这个标注是「这个数字不完整」的唯一提示。
   */
  tooltip: {
    trigger: 'axis',
    axisPointer: {
      type: 'shadow'
    },
    formatter: (params: any) => {
      const list = Array.isArray(params) ? params : [params]
      const index = list[0]?.dataIndex ?? -1
      const row = llmStats.value?.models?.[index]
      if (!row) return ''
      const lines = [
        `<strong>${row.model_id || '未标注'}</strong>`,
        `请求次数：${row.count}`,
        `成功率：${formatSuccessRate(row.success_rate)}`,
        `Token：${row.tokens}`,
        // 输入重与输出重的两个模型处置完全不同，合成一个数就看不出来。
        `输入 Token：${row.prompt_tokens}`,
        `输出 Token：${row.completion_tokens}`,
        `缓存读取：${formatNullableTokens(row.cached_tokens)}`,
        `平均响应：${Math.round(row.avg_duration)} ms`,
        `成本：${row.cost} ${llmStats.value?.overview.cost_currency || ''}`.trim(),
        // 单次成本回答「该不该换模型」；合计成本回答不了——
        // 请求量最大的模型往往不是最贵的那一个。
        `单次成本：${formatCostPerRequest(row.cost, row.count, row.unpriced_requests)}`
      ]
      if (row.unpriced_requests) {
        // 说明这笔成本不完整，而不是「这个模型很便宜」。
        lines.push(`未定价：${row.unpriced_requests} 条（未计入上面的成本）`)
      }
      return lines.join('<br/>')
    }
  },
  legend: {
    data: ['请求次数', '平均响应时间'],
    bottom: 10,
    textStyle: {
      color: chartTextSecondary.value
    }
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '15%',
    top: '20%',
    containLabel: true
  },
  xAxis: {
    type: 'category',
    data: llmStats.value?.models.map((item: { model_id: string }) => item.model_id) ?? [],
    axisLabel: {
      rotate: 45,
      interval: 0,
      color: chartTextSecondary.value
    },
    axisLine: {}
  },
  yAxis: [
    {
      type: 'value',
      name: '请求次数',
      position: 'left',
      axisLabel: {
        color: chartTextSecondary.value
      },
      nameTextStyle: {
        color: chartTextSecondary.value
      }
    },
    {
      type: 'value',
      name: '响应时间(ms)',
      position: 'right',
      axisLabel: {
        color: chartTextSecondary.value
      },
      nameTextStyle: {
        color: chartTextSecondary.value
      },
      splitLine: {
        show: false
      }
    }
  ],
  series: [
    {
      name: '请求次数',
      type: 'bar',
      data: llmStats.value?.models.map((item: { count: number }) => item.count) ?? [],
      itemStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            {
              offset: 0,
              color: themeColors.value[1]
            },
            {
              offset: 1,
              color: themeColors.value[1] + '80'
            }
          ]
        },
        /* 例外：同上，ECharts canvas 绘制的柱顶圆角，只接受数字 */
        borderRadius: [4, 4, 0, 0]
      }
    },
    {
      name: '平均响应时间',
      type: 'line',
      yAxisIndex: 1,
      data: llmStats.value?.models.map((item: { avg_duration: number }) =>
        Math.round(item.avg_duration)
      ) ?? [],
      itemStyle: {
        color: themeColors.value[2]
      },
      symbolSize: 6,
      smooth: true
    }
  ]
}))

/**
 * Provider 维度统计。
 *
 * 后端一直返回 `providers` 分组（甚至为它建了索引），但前端只把它当成筛选项的
 * 数据源——需求 9 要的是「Provider 统计」，一个下拉框不是统计。
 * 这里把它渲染成与「模型使用分析」同构的图：请求量柱状 + 平均响应时间折线，
 * 并额外给出成本，因为按 Provider 看账单是这个维度最主要的用途。
 *
 * `provider` 可能为 null（旧数据或未标注 Provider 的请求），显示成「未标注」
 * 而不是丢弃：丢弃会让各 Provider 请求数之和小于总请求数，读起来像数据缺失。
 */
const providerUsageOption = computed(() => {
  const rows = llmStats.value?.providers ?? []
  const labels = rows.map((item) => item.provider || '未标注')
  return {
    title: {
      text: 'Provider 统计',
      subtext: '各上游的请求量、平均响应时间与成本',
      left: 'center',
      top: 10,
      textStyle: { fontSize: 15, fontWeight: 'normal', color: chartText.value },
      subtextStyle: { fontSize: 12, color: chartTextSecondary.value }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      // 成本不进坐标轴（量级与请求数差几个数量级），只在 tooltip 里给出，
      // 避免为了同框显示而把请求量压成一条平线。
      formatter: (params: Array<{ dataIndex: number; seriesName: string; value: number }>) => {
        const index = params[0]?.dataIndex ?? 0
        const row = rows[index]
        if (!row) return ''
        const lines = [
          `<strong>${labels[index]}</strong>`,
          `请求次数：${row.count}`,
          // 成功率是「该把哪家排到故障转移队列后面」的依据。
          // 未知（全是在途请求）与 0% 必须分开，否则刚配好的供应商会被误判成最差。
          `成功率：${formatSuccessRate(row.success_rate)}`,
          `成功 / 失败 / 进行中：${row.success_requests} / ${row.failed_requests} / ${row.pending_requests}`,
          `Token：${row.tokens}`,
          `输入 Token：${row.prompt_tokens}`,
          `输出 Token：${row.completion_tokens}`,
          `缓存读取：${formatNullableTokens(row.cached_tokens)}`,
          `平均响应：${Math.round(row.avg_duration)} ms`,
          `成本：${row.cost}`,
          `单次成本：${formatCostPerRequest(row.cost, row.count, row.unpriced_requests)}`
        ]
        if (row.unpriced_requests > 0) {
          // 未定价请求不能按 0 元并入成本，否则账单看起来比实际便宜。
          lines.push(`未定价请求：${row.unpriced_requests}`)
        }
        return lines.join('<br/>')
      }
    },
    legend: {
      data: ['请求次数', '平均响应时间'],
      bottom: 10,
      textStyle: { color: chartTextSecondary.value }
    },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '20%', containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { rotate: 45, interval: 0, color: chartTextSecondary.value },
      axisLine: {}
    },
    yAxis: [
      {
        type: 'value',
        name: '请求次数',
        position: 'left',
        axisLabel: { color: chartTextSecondary.value },
        nameTextStyle: { color: chartTextSecondary.value }
      },
      {
        type: 'value',
        name: '响应时间(ms)',
        position: 'right',
        axisLabel: { color: chartTextSecondary.value },
        nameTextStyle: { color: chartTextSecondary.value },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: '请求次数',
        type: 'bar',
        data: rows.map((item) => item.count),
        itemStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: themeColors.value[3] },
              { offset: 1, color: themeColors.value[3] + '80' }
            ]
          },
          /* 例外：同上，ECharts canvas 绘制的柱顶圆角，只接受数字 */
          borderRadius: [4, 4, 0, 0]
        }
      },
      {
        name: '平均响应时间',
        type: 'line',
        yAxisIndex: 1,
        data: rows.map((item) => Math.round(item.avg_duration)),
        itemStyle: { color: themeColors.value[4] },
        symbolSize: 6,
        smooth: true
      }
    ]
  }
})

/** 用量来源分布：真实 / 估算 / 未知必须能一眼看出比例。 */
const usageSourceOption = computed(() => {
  const rows = llmStats.value?.usage_sources ?? []
  const labels: Record<string, string> = {
    provider: '供应商返回',
    estimated: '本地估算',
    unknown: '未知'
  }
  return {
    title: {
      text: 'Token 来源构成',
      subtext: '估算与未知不能当作实测消耗',
      left: 'center',
      top: 10,
      textStyle: { fontSize: 15, fontWeight: 'normal', color: chartText.value },
      subtextStyle: { fontSize: 12, color: chartTextSecondary.value }
    },
    // 请求数一并给出：两个比例常常不同（少量估算请求各自很大时差得最远），
    // 而扇形只能表示其中一个。把另一个放进 tooltip 而不是另开一张图。
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const row = rows[params.dataIndex]
        const requests = row?.count ?? 0
        return `${params.name}<br/>Token ${params.value}（${params.percent}%）<br/>请求 ${requests} 条`
      }
    },
    legend: { bottom: 10, textStyle: { color: chartTextSecondary.value } },
    series: [
      {
        name: 'Token 来源',
        type: 'pie',
        radius: ['40%', '62%'],
        center: ['50%', '48%'],
        avoidLabelOverlap: true,
        label: { color: chartTextSecondary.value },
        // 这张图的标题是「Token 来源构成」，副标题是「估算与未知不能当作实测
        // 消耗」——所以扇形必须按 token 分，不能按请求数。
        // 按请求数画时，「3 条估算请求但占了一半 token」会显示成「估算只占 30%」，
        // 而这张图存在的全部理由就是回答「有多少消耗数字是不可当账单依据的」。
        // 后端 `usage_sources[].tokens` 一直返回，此前没有消费者。
        data: rows.map((item, index) => ({
          name: labels[item.usage_source || 'unknown'] || item.usage_source || '未知',
          value: item.tokens,
          itemStyle: { color: themeColors.value[index % themeColors.value.length] }
        }))
      }
    ]
  }
})

/**
 * 每日成本趋势。
 *
 * 单独一张图而不是塞进 Token 趋势：金额与 Token 数差好几个数量级，
 * 同框时其中一条必然被压成一条平线。
 *
 * 这张图回答的是「这个月贵了三倍，是哪天开始的」——只有一个 30 天合计时，
 * 这个问题只能手工二分时间范围反复重查，而账单异常恰恰最需要快速定位到某一天
 * （换了模型、上了新流量、缓存失效）。
 *
 * **不同货币不画在同一条线上。** 每个币种一条：把两种货币加进同一条曲线，
 * 得到的是一串没有单位的数字，而那不会报错。单币种部署下就只有一条线。
 */
const dailyCostOption = computed(() => {
  const buckets = llmStats.value?.daily_stats ?? []
  // 币种集合从数据里推导：写死 USD 会让人民币结算的部署看到一张空图。
  const currencies = Array.from(
    new Set(buckets.flatMap((item) => Object.keys(item.cost_by_currency ?? {})))
  ).sort()
  return {
    title: {
      text: '每日成本趋势',
      subtext:
        currencies.length > 1
          ? '按币种分别累计，不同货币不相加'
          : '按请求当时的价格快照累计，不受后来改价影响',
      left: 'center',
      top: 10,
      textStyle: { fontSize: 15, fontWeight: 'normal', color: chartText.value },
      subtextStyle: { fontSize: 12, color: chartTextSecondary.value }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: chartSurface.value,
      borderColor: chartBorder.value,
      textStyle: { color: chartText.value },
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        const index = list[0]?.dataIndex ?? -1
        const bucket = buckets[index]
        if (!bucket) return ''
        const lines = [`<strong>${bucket.date}</strong>`]
        for (const [currency, amount] of Object.entries(bucket.cost_by_currency ?? {})) {
          lines.push(`${amount} ${currency}`.trim())
        }
        if (!Object.keys(bucket.cost_by_currency ?? {}).length) {
          lines.push('这一天没有定价证据')
        }
        if (bucket.unpriced_requests) {
          // 说明这条曲线偏低是「没匹配到价格版本」，而不是「这天便宜」。
          lines.push(`未定价：${bucket.unpriced_requests} 条（未计入曲线）`)
        }
        return lines.join('<br/>')
      }
    },
    legend: {
      bottom: 0,
      icon: 'circle',
      textStyle: { color: chartTextSecondary.value }
    },
    grid: { left: '3%', right: '4%', bottom: '22%', top: '20%', containLabel: true },
    xAxis: {
      type: 'category',
      data: buckets.map((item) => item.date),
      axisLabel: {
        rotate: 45,
        formatter: (value: string) => value.slice(5),
        color: chartTextSecondary.value
      }
    },
    yAxis: {
      type: 'value',
      name: '成本',
      nameLocation: 'middle',
      nameGap: 50,
      nameTextStyle: { color: chartTextSecondary.value, fontWeight: 'normal' },
      axisLabel: { color: chartTextSecondary.value }
    },
    series: currencies.map((currency, index) => ({
      name: currency || '未标注币种',
      // 缺这个币种的那一天用 0 而不是 null：曲线是累计量，断开会读作「缺数据」。
      data: buckets.map((item) => Number(item.cost_by_currency?.[currency] ?? 0)),
      type: 'line',
      smooth: true,
      symbolSize: 5,
      itemStyle: { color: themeColors.value[index % themeColors.value.length] }
    }))
  }
})

const hourlyRequestsOption = computed(() => ({
  title: {
    text: '24小时请求趋势',
    subtext: '最近24小时的请求量与Token消耗',
    left: 'center',
    top: 10,
    textStyle: {
      fontSize: 15,
      fontWeight: 'normal',
      color: chartText.value
    },
    subtextStyle: {
      fontSize: 12,
      color: chartTextSecondary.value
    }
  },
  tooltip: {
    trigger: 'axis',
    axisPointer: {
      type: 'cross'
    },
    backgroundColor: chartSurface.value,
    borderColor: chartBorder.value,
    textStyle: {
      color: chartText.value
    }
  },
  legend: {
    data: ['请求次数', 'Token消耗'],
    bottom: 10,
    textStyle: {
      color: chartTextSecondary.value
    }
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '15%',
    top: '20%',
    containLabel: true
  },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: llmStats.value?.hourly_stats.map((item: { hour: string }) => item.hour.split(' ')[1]) ?? [],
    axisLabel: {
      rotate: 45,
      color: chartTextSecondary.value
    }
  },
  yAxis: [
    {
      type: 'value',
      name: '请求次数',
      position: 'left',
      axisLabel: {
        color: chartTextSecondary.value
      },
      nameTextStyle: {
        color: chartTextSecondary.value
      }
    },
    {
      type: 'value',
      name: 'Token数',
      position: 'right',
      axisLabel: {
        color: chartTextSecondary.value
      },
      nameTextStyle: {
        color: chartTextSecondary.value
      },
      splitLine: {
        show: false
      }
    }
  ],
  series: [
    {
      name: '请求次数',
      type: 'line',
      smooth: true,
      symbolSize: 6,
      data: llmStats.value?.hourly_stats.map((item: { requests: number }) => item.requests) ?? [],
      itemStyle: {
        color: themeColors.value[0]
      },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            {
              offset: 0,
              color: themeColors.value[0] + '20'
            },
            {
              offset: 1,
              color: themeColors.value[0] + '05'
            }
          ]
        }
      }
    },
    {
      name: 'Token消耗',
      type: 'line',
      smooth: true,
      symbolSize: 6,
      yAxisIndex: 1,
      data: llmStats.value?.hourly_stats.map((item: { tokens: number }) => item.tokens) ?? [],
      itemStyle: {
        color: themeColors.value[1]
      },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            {
              offset: 0,
              color: themeColors.value[1] + '20'
            },
            {
              offset: 1,
              color: themeColors.value[1] + '05'
            }
          ]
        }
      }
    }
  ]
}))

// 格式化持续时间
const formatDuration = (ms: number): string => {
  if (isNaN(ms)) {
    return '0ms'
  }
  if (ms < 1000) {
    return `${ms}ms`
  } else if (ms < 60000) {
    return `${(ms / 1000).toFixed(1)}s`
  } else {
    const minutes = Math.floor(ms / 60000)
    const seconds = ((ms % 60000) / 1000).toFixed(1)
    return `${minutes}m ${seconds}s`
  }
}

// 自动获取数据
onMounted(() => {
  fetchLLMStats()
  // 每5分钟刷新一次数据
  refreshTimer = setInterval(fetchLLMStats, 5 * 60 * 1000)
})

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<template>
  <n-spin v-if="statisticsStatus === 'loading'" data-test="statistics-loading" description="正在加载统计数据" />
  <n-alert
    v-else-if="statisticsStatus === 'error'"
    type="error"
    :show-icon="true"
    data-test="statistics-error"
    role="alert"
  >
    {{ statisticsError }}
    <template #action>
      <n-button size="small" data-test="retry-statistics" @click="fetchLLMStats">重试</n-button>
    </template>
  </n-alert>
  <n-space v-else-if="llmStats" vertical :size="12">
    <!-- 概览统计卡片 -->
    <n-card title="LLM 使用分析" :bordered="false" class="overview-card">
      <template #header-extra>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-icon size="18">
              <time-outline />
            </n-icon>
          </template>
          每5分钟刷新一次
        </n-tooltip>
      </template>
      <n-grid :cols="24" :x-gap="12" :y-gap="12" responsive="screen" :item-responsive="true">
        <n-gi :span="6" :xs="24" :sm="12" :md="12" :lg="6">
          <div class="statistic-item">
            <div class="statistic-icon">
              <n-icon size="24" :depth="3">
                <trending-up-outline />
              </n-icon>
            </div>
            <div class="statistic-content">
              <div class="statistic-label">30天请求数</div>
              <div class="statistic-value">
                <n-number-animation
                  :from="0"
                  :to="llmStats.overview.total_requests"
                  :duration="1000"
                />
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="24" :sm="12" :md="12" :lg="6">
          <div class="statistic-item">
            <div class="statistic-icon success">
              <n-icon size="24" :depth="3">
                <pie-chart-outline />
              </n-icon>
            </div>
            <div class="statistic-content">
              <div class="statistic-label">成功率</div>
              <div class="statistic-value">
                {{
                  llmStats.overview.total_requests
                    ? Math.round(
                        (llmStats.overview.success_requests / llmStats.overview.total_requests) *
                          100
                      )
                    : 0
                }}%
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="24" :sm="12" :md="12" :lg="6">
          <div class="statistic-item">
            <div class="statistic-icon info">
              <n-icon size="24" :depth="3">
                <server-outline />
              </n-icon>
            </div>
            <div class="statistic-content">
              <div class="statistic-label">Token 消耗</div>
              <div class="statistic-value">
                <n-number-animation
                  :from="0"
                  :to="llmStats.overview.total_tokens"
                  :duration="1000"
                  :precision="0"
                />
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="24" :sm="12" :md="12" :lg="6">
          <div class="statistic-item">
            <div class="statistic-icon warning">
              <n-icon size="24" :depth="3">
                <time-outline />
              </n-icon>
            </div>
            <div class="statistic-content">
              <div class="statistic-label">平均响应时间</div>
              <div class="statistic-value">
                {{
                  formatDuration(
                    Math.round(
                      llmStats.models.reduce((acc, cur) => acc + cur.avg_duration, 0) /
                        (llmStats.models.length || 1)
                    )
                  )
                }}
              </div>
            </div>
          </div>
        </n-gi>
      </n-grid>

      <!-- Token 四类拆分与缓存命中率。
           只显示一个总数时，「总量没变、缓存命中从 80% 掉到 0%」这种账单翻几倍的
           变化在界面上完全不可见——输入 Token 单价通常是缓存读取的 5~10 倍。 -->
      <n-grid
        :cols="24"
        :x-gap="12"
        :y-gap="12"
        responsive="screen"
        :item-responsive="true"
        class="cost-row"
      >
        <n-gi :span="6" :xs="12" :sm="12" :md="6" :lg="6">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">输入 Token</div>
              <div class="statistic-value" data-test="input-tokens">
                {{ formatNullableTokens(llmStats.overview.total_prompt_tokens) }}
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="12" :sm="12" :md="6" :lg="6">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">输出 Token</div>
              <div class="statistic-value" data-test="output-tokens">
                {{ formatNullableTokens(llmStats.overview.total_completion_tokens) }}
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="12" :sm="12" :md="6" :lg="6">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">缓存读取 / 写入</div>
              <div class="statistic-value" data-test="cache-tokens">
                {{ formatNullableTokens(llmStats.overview.total_cached_tokens) }} /
                {{ formatNullableTokens(llmStats.overview.total_cache_write_tokens) }}
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="6" :xs="12" :sm="12" :md="6" :lg="6">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">缓存命中率</div>
              <div class="statistic-value" data-test="cache-hit-rate">
                {{ cacheHitRateText }}
              </div>
              <!-- 「未上报」与 0% 不是同一件事：前者查上游是否返回 usage，
                   后者查提示词前缀是否稳定。显示 0% 会指向错误的排查方向。 -->
              <div
                v-if="cacheHitRateText === '未上报'"
                class="statistic-hint"
                data-test="cache-hit-rate-unknown"
              >
                这些上游没有返回缓存用量，命中率未知，不代表没有命中
              </div>
            </div>
          </div>
        </n-gi>
      </n-grid>

      <!-- 成本单独一行：它是「统计」里最被追问的一项，此前后端返回却完全没展示。
           未定价请求必须与成本并列显示——把它们按 0 元算会让账单看起来更便宜。 -->
      <n-grid
        :cols="24"
        :x-gap="12"
        :y-gap="12"
        responsive="screen"
        :item-responsive="true"
        class="cost-row"
      >
        <n-gi :span="12" :xs="24" :sm="12" :md="12" :lg="12">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">
                30天成本
                <span v-if="llmStats.overview.cost_currency" class="statistic-unit">
                  {{ llmStats.overview.cost_currency }}
                </span>
              </div>
              <div class="statistic-value" data-test="total-cost">
                {{ llmStats.overview.total_cost }}
              </div>
              <!-- 出现第二种货币时必须说出来：上面那个数字只是其中一种的合计，
                   不是全部花费。不提示就等于让人把它当总额读。 -->
              <div v-if="otherCurrencyTotals.length" class="statistic-hint" data-test="other-currency-totals">
                另有 {{ otherCurrencyTotals.join('、') }}，不同货币不相加
              </div>
            </div>
          </div>
        </n-gi>
        <n-gi :span="12" :xs="24" :sm="12" :md="12" :lg="12">
          <div class="statistic-item">
            <div class="statistic-content">
              <div class="statistic-label">未定价请求</div>
              <div class="statistic-value" data-test="unpriced-requests">
                {{ llmStats.overview.unpriced_requests }}
              </div>
              <div v-if="llmStats.overview.unpriced_requests > 0" class="statistic-hint">
                这些请求没有匹配的价格版本，未计入上面的成本
              </div>
            </div>
          </div>
        </n-gi>
      </n-grid>
    </n-card>

    <!-- 图表区域 -->
    <n-grid
      cols="1 s:1 m:2 l:2"
      :x-gap="12"
      :y-gap="12"
      responsive="screen"
      :item-responsive="true"
    >
      <!-- Token 使用趋势 -->
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="dailyTokensOption" autoresize />
        </n-card>
      </n-gi>

      <!-- 成本趋势：单独一张图，金额与 Token 数差几个数量级，同框会压成平线。
           它回答的是「贵了三倍是哪天开始的」——只有 30 天合计时这个问题
           只能靠手工二分时间范围反复重查。 -->
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="dailyCostOption" autoresize />
        </n-card>
      </n-gi>

      <!-- 请求状态和模型使用分析 -->
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="requestStatusOption" autoresize />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="modelUsageOption" autoresize />
        </n-card>
      </n-gi>

      <!-- Provider 维度：后端一直返回这组数据，此前只被当成筛选项 -->
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="providerUsageOption" autoresize />
        </n-card>
      </n-gi>
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="usageSourceOption" autoresize />
        </n-card>
      </n-gi>

      <!-- 24小时趋势 -->
      <n-gi>
        <n-card :bordered="false" class="chart-card">
          <v-chart class="chart" :option="hourlyRequestsOption" autoresize />
        </n-card>
      </n-gi>
    </n-grid>
  </n-space>
</template>

<style scoped>
.overview-card {
  background: rgba(var(--card-bg-color-rgb), 0.8);
  backdrop-filter: blur(20px);
  border-radius: var(--radius-lg);
  box-shadow: var(--box-shadow-lg, 0 8px 24px rgba(0, 0, 0, 0.08));
  border: 1px solid rgba(var(--primary-color-rgb), 0.1);
  overflow: hidden;
  transition: all 0.3s ease;
}

.overview-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-overlay, var(--box-shadow-hover, 0 12px 32px rgba(0, 0, 0, 0.12)));
}

.chart-card {
  background: rgba(var(--card-bg-color-rgb), 0.8);
  backdrop-filter: blur(20px);
  border-radius: var(--radius-lg);
  box-shadow: var(--box-shadow-lg, 0 8px 24px rgba(0, 0, 0, 0.08));
  border: 1px solid rgba(var(--primary-color-rgb), 0.1);
  transition: all 0.3s ease;
  height: 100%;
}

.statistic-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 4px;
  border-radius: var(--radius-md);
  background: rgba(var(--card-bg-color-rgb), 0.8);
  transition: all 0.3s ease;
  height: 100%;
  border: 1px solid rgba(var(--primary-color-rgb), 0.1);
}

.statistic-item:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow, 0 8px 24px rgba(0, 0, 0, 0.08));
}

.statistic-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  height: 44px;
  /* 图标底板嵌在 .statistic-item（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  background: linear-gradient(
    135deg,
    rgba(var(--primary-color-rgb), 0.1) 0%,
    rgba(var(--primary-color-rgb), 0.2) 100%
  );
  color: var(--primary-color);
}

.statistic-icon.success {
  background: linear-gradient(
    135deg,
    rgba(var(--success-color-rgb), 0.1) 0%,
    rgba(var(--success-color-rgb), 0.2) 100%
  );
  color: var(--success-color);
}

.statistic-icon.warning {
  background: linear-gradient(
    135deg,
    rgba(var(--warning-color-rgb), 0.1) 0%,
    rgba(var(--warning-color-rgb), 0.2) 100%
  );
  color: var(--warning-color);
}

.statistic-icon.info {
  background: linear-gradient(
    135deg,
    rgba(var(--info-color-rgb), 0.1) 0%,
    rgba(var(--info-color-rgb), 0.2) 100%
  );
  color: var(--info-color);
}

.statistic-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.statistic-label {
  font-size: var(--font-size-sm, 0.9rem);
  color: var(--text-color-secondary);
  white-space: nowrap;
}

.statistic-value {
  font-size: var(--font-size-2xl, 1.35rem);
  font-weight: 600;
  color: var(--text-color);
  line-height: 1.2;
}

/* 成本行与上面的概览卡片同处一张卡内，用一条分隔线区分层级：
   成本与未定价请求是一组，不该看起来像另外两个并列指标 */
.cost-row {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--divider-color, rgba(128, 128, 128, 0.2));
}

/* 货币单位跟在标签后面而不是拼进数值：拼进去会破坏 tabular-nums 对齐 */
.statistic-unit {
  margin-left: 4px;
  font-size: var(--font-size-xs, 0.75rem);
  opacity: 0.75;
}

/* 未定价提示比数值弱一档：它是解释，不是指标 */
.statistic-hint {
  margin-top: 4px;
  font-size: var(--font-size-xs, 0.75rem);
  color: var(--text-color-tertiary, var(--text-color-secondary));
  line-height: 1.4;
  white-space: normal;
}

.chart {
  height: 320px;
  width: 100%;
}

@media (max-width: 1400px) {
  .chart {
    height: 300px;
  }
}

@media (max-width: 768px) {
  .statistic-item {
    flex-direction: column;
    align-items: center;
    text-align: center;
    padding: 14px;
  }

  .statistic-content {
    width: 100%;
    align-items: center;
  }

  .chart {
    height: 280px;
  }
}

@media (max-width: 480px) {
  .chart {
    height: 240px;
  }

  .statistic-icon {
    width: 40px;
    height: 40px;
  }

  .statistic-value {
    font-size: var(--font-size-xl, 1.25rem);
  }

  :deep(.n-card-header) {
    padding: 12px 16px;
  }

  :deep(.n-card__content) {
    padding: 12px;
  }
}
</style>
