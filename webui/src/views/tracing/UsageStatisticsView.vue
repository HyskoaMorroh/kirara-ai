<script setup lang="ts">
/**
 * 使用统计：把「趋势 / Provider 统计 / 模型统计 / 成本」放在一个地方看。
 *
 * 此前这些能力分散在三处：图表组件挂在**引导页**、请求日志在 `/tracing/llm`、
 * 成本定价在 `/llm/pricing`，`/tracing` 侧栏里也没有统计入口。能力都在，
 * 但要对一次账单得在三个页面之间来回跳，而引导页上的图表还不带筛选与时区。
 *
 * 两条设计取舍：
 *
 * 1. **复用而不重做。** 图表仍由 `LLMStatistics.vue` 渲染，本页只负责筛选状态；
 *    请求日志与成本定价用链接跳过去，不在这里重新实现一遍——重做会立刻产生
 *    两套口径，而口径不一致比少一个入口糟得多。
 * 2. **筛选下发，时区可选而非只能自动。** 本页把 provider / model / 时间范围与
 *    时区下发给图表组件。时区默认取浏览器时区，但必须可以改：跨时区对账时
 *    需要看到对方眼里的「今天」，而后端本来就接受任意 IANA 名。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NDatePicker, NSelect, NSpace, NText, useMessage } from 'naive-ui'

import LLMStatistics from '@/components/LLMStatistics.vue'
import { http } from '@/utils/http'
import type { LLMStatistics as LLMStatisticsPayload } from '@/views/tracing/llm/llm-tracing.vm'
import {
  USAGE_RANGE_PRESETS,
  resolveUsageRange,
  type UsageRangePreset
} from '@/views/tracing/usage-range-presets'

const router = useRouter()
const message = useMessage()

/**
 * 「未标注」Provider 的哨兵值。
 *
 * 不能用空串：筛选构造器会把空值当成「没填」丢掉，于是选了「未标注」却拿到
 * 全量数据——那比没有这个选项更糟，它给出一个错误的答案而不是拒绝回答。
 * 后端用一组独立的 `*_unset` 参数表达「该列为 NULL」。
 */
const PROVIDER_UNSET = '__unset__'

const provider = ref<string | null>(null)
const model = ref<string | null>(null)
const range = ref<[number, number] | null>(null)
const timezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
const isExporting = ref(false)

/**
 * 时间范围预设。
 *
 * 「最近 7 天」此前要自己算两个时刻再点两次日历，而按天回看是这个页面最常做的
 * 第一步动作（账单异常、流量变化、某个模型上线）。
 *
 * 默认 `custom`（即不限制）而不是某个预设：本页初始要显示全量数据，
 * 替用户默认选一个窗口等于悄悄隐藏了窗口外的请求。
 */
const rangePreset = ref<UsageRangePreset>('custom')
const rangePresetOptions = USAGE_RANGE_PRESETS

/** 应用预设。`custom` 不动 `range`——那是用户自己选的区间，不该被覆盖。 */
function applyRangePreset() {
  const resolved = resolveUsageRange(rangePreset.value, timezone.value)
  if (resolved) range.value = resolved
}

/** 常用时区。后端接受任意 IANA 名，这里只是把最常用的几个做成可选项。 */
const timezoneOptions = computed(() => {
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone
  const common = [
    browser,
    'UTC',
    'Asia/Shanghai',
    'Asia/Tokyo',
    'Europe/London',
    'America/New_York',
    'America/Los_Angeles'
  ]
  return [...new Set(common.filter(Boolean))].map((zone) => ({
    label: zone === browser ? `${zone}（本地）` : zone,
    value: zone
  }))
})

const providerOptions = ref<{ label: string; value: string }[]>([])
const modelOptions = ref<{ label: string; value: string }[]>([])
const loadError = ref('')

/**
 * 下发给图表组件的筛选条件。
 *
 * 只放真正有值的键：空串会被后端当成一个真实的筛选值，
 * 表现为「筛了但筛不到」。
 */
const filters = computed<Record<string, string | null | undefined>>(() => {
  const result: Record<string, string> = {}
  if (provider.value === PROVIDER_UNSET) {
    // 「未标注」走独立参数：空串会被当成「没填」丢掉。
    result.provider_unset = '1'
  } else if (provider.value) {
    result.provider = provider.value
  }
  if (model.value) result.model = model.value
  if (range.value) {
    result.start_time = new Date(range.value[0]).toISOString()
    result.end_time = new Date(range.value[1]).toISOString()
  }
  // 时区显式下发：图表组件也会兜底补齐，但由本页给出用户选择的那个。
  if (timezone.value) result.timezone = timezone.value
  return result
})

/**
 * 导出当前筛选结果。
 *
 * 复用请求日志页的同一个后端端点与同一份筛选条件，保证「看到的」与
 * 「导出的」是同一批数据。走 `http.fetch` 而不是 `http.post`：
 * 响应是 `text/csv`，用会解析 JSON 的封装会把它读坏。
 */
async function exportStatistics() {
  if (isExporting.value) return
  isExporting.value = true
  try {
    const payload: Record<string, unknown> = {
      format: 'csv',
      limit: 10000,
      ...filters.value
    }
    const response = await http.fetch('/tracing/llm/export', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
    if (!response.ok) throw new Error(`导出失败 (HTTP ${response.status})`)
    const csv = await response.text()
    const truncated = response.headers.get('X-Export-Truncated') === 'true'
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'llm-usage.csv'
    anchor.click()
    URL.revokeObjectURL(url)
    if (truncated) {
      message.warning('结果超过单次导出上限，已导出前 10000 条；请收窄筛选条件')
    } else {
      message.success('已导出当前筛选结果')
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : '导出失败')
  } finally {
    isExporting.value = false
  }
}

/** 筛选项来自统计接口本身：它已经按 provider / model 分好组。 */
async function loadFilterOptions() {
  try {
    const query = timezone.value
      ? `?timezone=${encodeURIComponent(timezone.value)}`
      : ''
    const stats = await http.get<LLMStatisticsPayload>(`/tracing/llm/statistics${query}`)
    providerOptions.value = (stats.providers ?? []).map((item) => ({
      // null provider 是「未标注」而不是「不存在」，丢弃它会让各分组之和
      // 小于总数，读起来像数据缺失。用哨兵值而不是空串，见 PROVIDER_UNSET。
      label: item.provider ?? '未标注',
      value: item.provider ?? PROVIDER_UNSET
    }))
    modelOptions.value = (stats.models ?? []).map((item) => ({
      label: item.model_id,
      value: item.model_id
    }))
    loadError.value = ''
  } catch {
    providerOptions.value = []
    modelOptions.value = []
    loadError.value = '筛选项加载失败，图表仍会显示全量数据。'
  }
}

function resetFilters() {
  provider.value = null
  model.value = null
  range.value = null
  rangePreset.value = 'custom'
  timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone
}

onMounted(loadFilterOptions)

// 时间范围变化后筛选项本身也可能变化（例如某个 Provider 在该区间没有请求）。
watch(range, loadFilterOptions)

watch(rangePreset, applyRangePreset)

// 改时区后要按新时区重算日界，否则界面上的「今天」还是上一个时区的今天——
// 而这正是「时区可选」存在的场景（跨时区对账时看对方眼里的今天）。
watch(timezone, () => {
  if (rangePreset.value !== 'custom') applyRangePreset()
})

// 用户直接改日历时把预设切回「自定义」：留着「近 7 天」的标签而区间已经不是
// 近 7 天，那个标签就是错的。
watch(range, () => {
  const resolved = resolveUsageRange(rangePreset.value, timezone.value)
  if (!resolved) return
  const current = range.value
  if (!current || current[0] !== resolved[0] || current[1] !== resolved[1]) {
    rangePreset.value = 'custom'
  }
})

defineExpose({
  providerOptions,
  modelOptions,
  filters,
  timezone,
  rangePreset,
  applyRangePreset
})
</script>

<template>
  <div class="usage-statistics">
    <header class="page-header">
      <div>
        <p class="eyebrow">可观测性</p>
        <h1>使用统计</h1>
        <p class="subtitle">
          Token 消耗、使用趋势、Provider 与模型分布、成本汇总。
          估算用量与未定价请求单独标注，不会混入账单口径。
        </p>
      </div>
      <n-space class="header-actions" align="center" :size="8">
        <n-button
          data-test="export-statistics"
          :loading="isExporting"
          @click="exportStatistics"
        >
          导出 CSV
        </n-button>
        <n-button data-test="open-request-log" @click="router.push('/tracing/llm')">
          请求日志
        </n-button>
        <n-button data-test="open-pricing" @click="router.push('/llm/pricing')">
          成本定价
        </n-button>
      </n-space>
    </header>

    <n-card :bordered="false" class="filter-card">
      <n-space align="center" :size="12" :wrap="true">
        <n-select
          v-model:value="provider"
          class="filter-control"
          clearable
          placeholder="全部 Provider"
          data-test="provider-filter"
          :options="providerOptions"
        />
        <n-select
          v-model:value="model"
          class="filter-control"
          clearable
          placeholder="全部模型"
          data-test="model-filter"
          :options="modelOptions"
        />
        <!--
          预设是快捷方式而非替代品：日历选择器保留，对一次具体账单仍要能填精确
          区间。用户直接改日历时预设会自动切回「自定义」，避免标签与区间不一致。
        -->
        <n-select
          v-model:value="rangePreset"
          class="filter-control"
          placeholder="时间范围"
          data-test="range-preset"
          :options="rangePresetOptions"
        />
        <n-date-picker
          v-model:value="range"
          type="datetimerange"
          clearable
          data-test="range-filter"
        />
        <!--
          时区可选而非只能自动：跨时区对账时需要看到对方眼里的「今天」，
          后端本来就接受任意 IANA 名。
        -->
        <n-select
          v-model:value="timezone"
          class="filter-control"
          filterable
          tag
          placeholder="时区"
          data-test="timezone-filter"
          :options="timezoneOptions"
        />
        <n-button data-test="reset-filters" @click="resetFilters">重置</n-button>
        <n-text v-if="loadError" depth="3" data-test="filter-error">{{ loadError }}</n-text>
      </n-space>
    </n-card>

    <LLMStatistics :filters="filters" />
  </div>
</template>

<style scoped>
.usage-statistics {
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4, 16px);
  flex-wrap: wrap;
}

.eyebrow {
  margin: 0;
  font-size: var(--font-size-xs, 11px);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-color-tertiary, #888);
}

.page-header h1 {
  margin: 2px 0 4px;
  font-size: var(--font-size-xl, 22px);
  font-weight: 600;
  color: var(--text-color, #222);
}

.subtitle {
  margin: 0;
  max-width: 62ch;
  font-size: var(--font-size-sm, 13px);
  line-height: 1.6;
  color: var(--text-color-secondary, #666);
}

.header-actions {
  flex-shrink: 0;
}

.filter-control {
  min-width: 180px;
}
</style>
