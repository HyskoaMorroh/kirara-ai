<script setup lang="ts">
/**
 * 投递时间线视图：回答「为什么这条回复慢」和「上周二 QQ 慢在哪一段」。
 *
 * 后端一直提供 `/tracing/delivery/summary` 与 `/recent`（表 `im_delivery_timings`），
 * 但前端从未调用——需求 19.5 要求给出 QQ / Telegram / WeCom 的**可比**链路耗时，
 * 而在产品上此前等于只能 curl。
 *
 * 两条呈现原则：
 *
 * 1. **null 不是 0。** 阶段耗时为空表示「没测到」（例如非流式请求没有首字节），
 *    显示成 0 会被读成「极快」，正好与事实相反。这里显示为「未测到」并给出原因。
 * 2. **平均值必须带样本数。** 一次 30 秒的请求和一百次里有一次 30 秒，
 *    平均值可能相同，处置完全不同。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NSelect, NTag } from 'naive-ui'

import {
  DELIVERY_COUNT_LABELS,
  DELIVERY_PHASES,
  DELIVERY_PHASE_LABELS,
  tracingApi,
  type DeliveryCountKey,
  type DeliveryRecord,
  type DeliverySummary
} from '@/api/tracing'

/** 需求 19.5 九项里的后两项，与阶段耗时并列呈现。 */
const DELIVERY_COUNT_KEYS: DeliveryCountKey[] = ['segment_count', 'retry_count']

const summary = ref<DeliverySummary | null>(null)
const records = ref<DeliveryRecord[]>([])
const channels = ref<string[]>([])
const selectedChannel = ref<string | null>(null)
const loading = ref(true)
const errorMessage = ref('')
const unavailable = ref(false)
const lastUpdated = ref<Date | null>(null)

let timer: ReturnType<typeof setInterval> | null = null

const channelOptions = computed(() => [
  { label: '全部渠道', value: '' },
  ...channels.value.map((channel) => ({ label: channel, value: channel }))
])

/** 只有真正测到过的阶段才参与「最慢阶段」判断：null 阶段不能参与比较。 */
const slowestPhase = computed(() => {
  if (!summary.value) return null
  let worst: { phase: string; seconds: number } | null = null
  for (const phase of DELIVERY_PHASES) {
    // total_seconds 是各段之和，拿它比较会永远胜出，没有诊断价值。
    if (phase === 'total_seconds') continue
    const stat = summary.value.phases?.[phase]
    if (!stat || stat.avg_seconds === null || stat.samples === 0) continue
    if (!worst || stat.avg_seconds > worst.seconds) {
      worst = { phase: DELIVERY_PHASE_LABELS[phase], seconds: stat.avg_seconds }
    }
  }
  return worst
})

function formatSeconds(value: number | null): string {
  if (value === null || value === undefined) return '未测到'
  if (value < 1) return `${Math.round(value * 1000)} ms`
  return `${value.toFixed(2)} s`
}

/**
 * 计数的显示。
 *
 * `null` 必须显示成「未测到」而不是 `0`：后者是一个论断
 * （`retry_count: 0` 读作「都没重试过」），会让人以为链路一切正常，
 * 而实际只是没有任何投递记录过这一项。
 */
function formatCount(value: number | null): string {
  if (value === null || value === undefined) return '未测到'
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

/** 为什么某个阶段会「未测到」——直接说清楚，避免被当成故障。 */
function missingReason(phase: string): string {
  if (phase === 'llm_first_byte_seconds' || phase === 'llm_generation_seconds') {
    return '仅流式模式（reply_stream_mode=aggregate）可测'
  }
  return '该时间范围内没有样本'
}

async function load(showSpinner = false) {
  if (showSpinner) loading.value = true
  const channel = selectedChannel.value || undefined
  try {
    const [summaryResponse, recentResponse] = await Promise.all([
      tracingApi.getDeliverySummary({ channel }),
      tracingApi.getRecentDeliveries({ channel, limit: 50 })
    ])
    summary.value = summaryResponse
    channels.value = summaryResponse.channels || []
    records.value = recentResponse.items || []
    lastUpdated.value = new Date()
    errorMessage.value = ''
    unavailable.value = false
  } catch (error) {
    const text = error instanceof Error ? error.message : ''
    // 503 表示这台部署没有启用投递计时存储，那不是错误，只是没开这个功能。
    if (text.includes('not configured') || text.includes('503')) {
      unavailable.value = true
      errorMessage.value = ''
    } else {
      errorMessage.value = text || '无法读取投递时间线'
    }
  } finally {
    loading.value = false
  }
}

function onChannelChange(value: string) {
  selectedChannel.value = value || null
  load(true)
}

onMounted(() => {
  load(true)
  timer = setInterval(() => load(false), 30_000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="delivery-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">可观测性</p>
        <h1>投递时间线</h1>
        <p class="subtitle">
          按渠道比较回复各阶段耗时。四个渠道使用同一套阶段命名，
          因此 QQ、Telegram、WeCom 的链路耗时可以直接横向比较。
        </p>
      </div>
      <div class="header-actions">
        <n-select
          class="channel-select"
          :value="selectedChannel || ''"
          :options="channelOptions"
          data-test="channel-filter"
          @update:value="onChannelChange"
        />
        <span v-if="lastUpdated" class="updated">
          更新于 {{ lastUpdated.toLocaleTimeString() }}
        </span>
        <n-button type="primary" data-test="refresh-delivery" @click="load(true)">
          刷新
        </n-button>
      </div>
    </header>

    <n-alert v-if="errorMessage" type="error" role="alert" class="notice">
      {{ errorMessage }}
    </n-alert>
    <n-alert
      v-else-if="unavailable"
      type="info"
      role="status"
      class="notice"
      data-test="store-unavailable"
    >
      这台部署没有启用投递计时存储，因此没有历史耗时可查。
      它不影响消息投递，只影响事后回查。
    </n-alert>

    <section v-if="loading" class="loading-state" aria-busy="true">
      正在读取投递时间线...
    </section>

    <template v-else-if="summary && !unavailable">
      <n-card :bordered="false" class="overview-card">
        <div class="overview-grid">
          <div class="overview-item">
            <span class="overview-label">投递次数</span>
            <strong class="overview-value" data-test="delivery-count">
              {{ summary.deliveries }}
            </strong>
          </div>
          <div class="overview-item">
            <span class="overview-label">失败投递</span>
            <strong class="overview-value" data-test="failed-count">
              {{ summary.failed_deliveries }}
            </strong>
          </div>
          <div v-if="slowestPhase" class="overview-item">
            <span class="overview-label">平均最慢阶段</span>
            <strong class="overview-value" data-test="slowest-phase">
              {{ slowestPhase.phase }} · {{ formatSeconds(slowestPhase.seconds) }}
            </strong>
          </div>
        </div>
      </n-card>

      <section class="phase-section" aria-label="各阶段耗时">
        <div class="section-heading">
          <h2>各阶段耗时</h2>
          <span>平均值只统计测到该阶段的记录</span>
        </div>
        <div class="phase-list">
          <article
            v-for="phase in DELIVERY_PHASES"
            :key="phase"
            class="phase-row"
            :class="{ total: phase === 'total_seconds' }"
            data-test="phase-row"
          >
            <div class="phase-name">{{ DELIVERY_PHASE_LABELS[phase] }}</div>
            <div class="phase-metrics">
              <span class="metric">
                平均
                <b>{{ formatSeconds(summary.phases?.[phase]?.avg_seconds ?? null) }}</b>
              </span>
              <span class="metric">
                最大
                <b>{{ formatSeconds(summary.phases?.[phase]?.max_seconds ?? null) }}</b>
              </span>
              <!-- 样本数与平均值同等重要：没有它无法判断平均值的可信度 -->
              <span class="metric samples">
                样本<b>{{ summary.phases?.[phase]?.samples ?? 0 }}</b>
              </span>
              <span
                v-if="!summary.phases?.[phase]?.samples"
                class="metric missing"
                :title="missingReason(phase)"
              >
                {{ missingReason(phase) }}
              </span>
            </div>
          </article>
        </div>
      </section>

      <section class="phase-section" aria-label="分段与重试">
        <div class="section-heading">
          <h2>分段与重试</h2>
          <span>回答「慢是因为分了很多页，还是重试过」</span>
        </div>
        <div class="phase-list">
          <article
            v-for="key in DELIVERY_COUNT_KEYS"
            :key="key"
            class="phase-row"
            data-test="count-row"
          >
            <div class="phase-name">{{ DELIVERY_COUNT_LABELS[key] }}</div>
            <div class="phase-metrics">
              <span class="metric">
                平均
                <b>{{ formatCount(summary.counts?.[key]?.avg ?? null) }}</b>
              </span>
              <span class="metric">
                最大
                <b>{{ formatCount(summary.counts?.[key]?.max ?? null) }}</b>
              </span>
              <span class="metric samples">
                样本<b>{{ summary.counts?.[key]?.samples ?? 0 }}</b>
              </span>
              <!-- 没有样本时说明「没有数据」，而不是让读者把空白当成 0。
                   `retry_count` 显示成 0 会被读成「都没重试过」，那是一个论断。 -->
              <span v-if="!summary.counts?.[key]?.samples" class="metric missing">
                该范围内没有记录这项的投递
              </span>
            </div>
          </article>
        </div>
      </section>

      <section class="records-section" aria-label="最近投递记录">
        <div class="section-heading">
          <h2>最近投递</h2>
          <span>只含时长与计数，不含任何消息正文</span>
        </div>
        <p v-if="records.length === 0" class="empty-inline" role="status">
          该范围内还没有投递记录。
        </p>
        <div v-else class="record-list">
          <article
            v-for="record in records"
            :key="record.id"
            class="record-row"
            data-test="record-row"
          >
            <div class="record-main">
              <n-tag :type="record.status === 'succeeded' ? 'success' : 'error'" size="small">
                {{ record.status === 'succeeded' ? '成功' : '失败' }}
              </n-tag>
              <strong>{{ record.channel }}</strong>
              <small>{{ record.adapter_instance }}</small>
            </div>
            <div class="record-metrics">
              <span>总计 <b>{{ formatSeconds(record.total_seconds) }}</b></span>
              <span>发送 <b>{{ formatSeconds(record.send_seconds) }}</b></span>
              <span v-if="record.segment_count">分段 <b>{{ record.segment_count }}</b></span>
              <span v-if="record.retry_count">重试 <b>{{ record.retry_count }}</b></span>
              <time v-if="record.recorded_at" :datetime="record.recorded_at">
                {{ new Date(record.recorded_at).toLocaleString() }}
              </time>
            </div>
          </article>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.delivery-view { max-width: 1180px; margin: 0 auto; padding: 28px 32px 48px; color: var(--text-color); }
.page-header, .section-heading, .header-actions { display: flex; align-items: center; }
.page-header { justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 6px; color: var(--primary-color); font-size: 12px; font-weight: 700; text-transform: uppercase; }
h1, h2, p { margin-top: 0; } h1 { margin-bottom: 8px; font-size: 28px; } h2 { margin-bottom: 0; font-size: 18px; }
.subtitle { margin-bottom: 0; max-width: 680px; line-height: 1.6; color: var(--text-color-2); }
.header-actions { flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.channel-select { min-width: 160px; }
.updated { padding: 6px 10px; color: var(--text-color-2); font-size: 13px; white-space: nowrap; }
.notice { margin-bottom: 16px; }
.loading-state { padding: 64px 24px; text-align: center; border: 1px dashed var(--border-color); border-radius: var(--radius-sm); }
.overview-card { margin-bottom: 8px; }
.overview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; }
.overview-item { display: grid; gap: 4px; }
.overview-label { color: var(--text-color-2); font-size: 13px; }
.overview-value { font-size: 20px; font-variant-numeric: tabular-nums; }
.section-heading { justify-content: space-between; margin: 28px 0 10px; }
.section-heading span { color: var(--text-color-2); font-size: 13px; }
.phase-list, .record-list { border-top: 1px solid var(--border-color); }
.phase-row, .record-row { display: grid; grid-template-columns: minmax(120px, 180px) 1fr; gap: 8px 20px; padding: 14px 0; border-bottom: 1px solid var(--border-color); }
/* 总计行加重：它是其余各段之和，视觉上应当收束 */
.phase-row.total { font-weight: 600; }
.phase-name { min-width: 0; overflow-wrap: anywhere; }
.phase-metrics, .record-metrics { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; font-size: 12px; color: var(--text-color-2); }
.metric, .record-metrics span { display: inline-grid; gap: 2px; }
.metric b, .record-metrics b { color: var(--text-color); font-size: 13px; font-variant-numeric: tabular-nums; }
/* 「未测到」是解释而非告警：用弱化配色，不用错误色 */
.metric.missing { color: var(--text-color-3, var(--text-color-2)); font-style: italic; }
.record-main { display: flex; align-items: center; gap: 10px; min-width: 0; }
.record-main small { color: var(--text-color-2); overflow-wrap: anywhere; }
.record-metrics time { margin-left: auto; color: var(--text-color-3, var(--text-color-2)); }
.empty-inline { padding: 32px 0; color: var(--text-color-2); }
@media (max-width: 768px) {
  .delivery-view { padding: 20px 16px 40px; }
  .page-header { align-items: stretch; flex-direction: column; }
  .header-actions { justify-content: flex-start; }
  .phase-row, .record-row { grid-template-columns: 1fr; }
  .record-metrics time { margin-left: 0; }
}
</style>
