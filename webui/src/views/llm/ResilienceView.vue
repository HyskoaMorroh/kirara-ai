<script setup lang="ts">
/**
 * 供应商容错状态面板。
 *
 * 后端一直提供 `GET /llm/resilience/status`，但前端从未调用——需求 21.2 要求
 * 「优先级、可用模型、失败原因、尝试顺序、最终选中的供应商和 trace id 都要可查」，
 * 而在产品上这等于只能 curl。这个面板把那份数据落到界面上。
 *
 * 三条呈现原则，都是为了不让面板给出错误的安心：
 *
 * 1. **队列顺序即优先级顺序。** 按 `priority` 升序排，并显式标出序号，
 *    因为「P1 优先」这件事只有排出来才看得见。
 * 2. **错误率必须带样本数。** 3 次里错 1 次和 300 次里错 100 次都是 33%，
 *    但处置完全不同。只显示百分比会让前者看起来像系统性故障。
 * 3. **`partial_output` 单独标出。** 它表示「已经向用户产出过内容」，
 *    是判断「这次失败能不能安全重试」的唯一依据——已产出就不能静默切换并拼接，
 *    否则用户会看到两段重复回复。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NTag } from 'naive-ui'

import { llmApi, type ProviderResilienceRow } from '@/api/llm'

const rows = ref<ProviderResilienceRow[]>([])
const loading = ref(true)
const errorMessage = ref('')
const lastUpdated = ref<Date | null>(null)
const autoRefresh = ref(true)

let timer: ReturnType<typeof setInterval> | null = null

/** 熔断三态的展示映射。半开是「正在试探」，不是错误，因此用 info 而非 warning。 */
const CIRCUIT_LABELS: Record<string, { label: string; type: 'success' | 'warning' | 'error' | 'info' }> = {
  closed: { label: '正常', type: 'success' },
  'half-open': { label: '半开试探', type: 'info' },
  open: { label: '已熔断', type: 'error' }
}

const ERROR_CATEGORY_LABELS: Record<string, string> = {
  network: '网络错误',
  timeout: '超时',
  rate_limit: '限流',
  upstream: '上游错误',
  authentication: '认证失败',
  invalid_request: '请求参数错误',
  policy_rejection: '内容策略拒绝',
  cancelled: '已取消',
  circuit_open: '熔断跳过',
  unknown: '未分类'
}

/** 按模型分组，组内按优先级升序——这就是实际的故障转移队列顺序。 */
const queues = computed(() => {
  const grouped = new Map<string, ProviderResilienceRow[]>()
  for (const row of rows.value) {
    const list = grouped.get(row.model) || []
    list.push(row)
    grouped.set(row.model, list)
  }
  return [...grouped.entries()]
    .map(([model, providers]) => ({
      model,
      providers: [...providers].sort((a, b) => {
        if (a.priority !== b.priority) return a.priority - b.priority
        return a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0
      })
    }))
    .sort((a, b) => (a.model < b.model ? -1 : a.model > b.model ? 1 : 0))
})

const degradedCount = computed(
  () => rows.value.filter((row) => row.state !== 'closed').length
)

function circuitTag(state: string) {
  return CIRCUIT_LABELS[state] || { label: state || '未知', type: 'info' as const }
}

/** 只取文案，用于「A → B」这类行内叙述。 */
function circuitLabel(state: string): string {
  return circuitTag(state).label
}

function errorCategoryLabel(category: string | null): string {
  if (!category) return ''
  return ERROR_CATEGORY_LABELS[category] || category
}

/** 错误率永远与样本数一起显示：同一个百分比在不同样本量下含义完全不同。 */
function errorRateText(row: ProviderResilienceRow): string {
  const requests = row.requests ?? 0
  if (!requests) return '暂无样本'
  const rate = Math.round((row.error_rate ?? 0) * 100)
  return `${rate}% · ${requests} 次采样`
}

/**
 * 上游限额余量的显示文本。
 *
 * 「未上报」与「0%」必须分开：很多兼容端点根本不返回限额头，
 * 而 0% 是「余量用完」——最该立刻处置的状态。两者在界面上长得一样时，
 * 前者会被当成紧急情况，后者会被忽略。
 *
 * 请求数与 Token 两组分别显示：它们会分别见底，且处置相反——
 * 请求数见底要降频，Token 见底要缩短上下文。
 */
function headroomText(row: ProviderResilienceRow): string {
  const limit = row.rate_limit
  if (limit === null || limit === undefined) return '未上报'
  const parts: string[] = []
  if (limit.remaining_requests !== null && limit.remaining_requests !== undefined) {
    const pct =
      limit.request_headroom === null || limit.request_headroom === undefined
        ? ''
        : ` (${Math.round(limit.request_headroom * 100)}%)`
    parts.push(`请求 ${limit.remaining_requests}${pct}`)
  }
  if (limit.remaining_tokens !== null && limit.remaining_tokens !== undefined) {
    const pct =
      limit.token_headroom === null || limit.token_headroom === undefined
        ? ''
        : ` (${Math.round(limit.token_headroom * 100)}%)`
    parts.push(`Token ${limit.remaining_tokens.toLocaleString()}${pct}`)
  }
  // 重置倒计时改变处置：「还剩 12 次」要立刻降频，
  // 「还剩 12 次、8 秒后重置」等一会儿就好。
  const reset = limit.reset_requests_seconds ?? limit.reset_tokens_seconds
  if (reset !== null && reset !== undefined) {
    parts.push(`${Math.ceil(reset)}s 后重置`)
  }
  if (limit.retry_after_seconds !== null && limit.retry_after_seconds !== undefined) {
    parts.push(`上游要求等待 ${Math.ceil(limit.retry_after_seconds)}s`)
  }
  return parts.length ? parts.join(' · ') : '未上报'
}

function recoveryText(row: ProviderResilienceRow): string {
  if (row.state === 'half-open') {
    const done = row.recovery_successes ?? 0
    const need = row.recovery_success_threshold ?? 0
    return need ? `恢复进度 ${done}/${need}` : ''
  }
  if (row.state === 'open') {
    // next_recovery_time 用的是单调时钟，不能格式化成墙上时间；
    // 只说明「等待恢复」，具体秒数由后端的恢复等待时间配置决定。
    return '等待进入半开试探'
  }
  return ''
}

/** 尝试耗时：优先用首字节，没有首字节就用完成时刻。 */
function attemptDuration(attempt: ProviderResilienceRow['recent_attempts'][number]): string {
  const end = attempt.first_byte_at ?? attempt.completed_at
  if (end === null || end === undefined) return '—'
  const seconds = Math.max(0, end - attempt.started_at)
  return `${seconds.toFixed(2)}s`
}

/**
 * 熔断迁移原因的可读文案。
 *
 * 两种「打开」的处置完全不同：连续失败达标说明刚开始出问题，
 * 错误率达标说明持续不稳定；混成一句「已熔断」等于没说。
 */
const TRANSITION_REASON_TEXT: Record<string, string> = {
  failure_threshold: '连续失败达到阈值',
  error_rate: '错误率达到阈值',
  recovery_timeout: '恢复等待期结束，转入半开试探',
  recovery_success: '试探成功，恢复正常',
  half_open_probe_failed: '半开试探再次失败，重新隔离'
}

function transitionReasonText(reason: string): string {
  return TRANSITION_REASON_TEXT[reason] || reason
}

/** 迁移发生在多久以前。`at` 是单调时钟，不能当墙上时间格式化。 */
function transitionAgo(
  transition: ProviderResilienceRow['recent_transitions'][number],
  latest: number
): string {
  const seconds = Math.max(0, latest - transition.at)
  if (seconds < 1) return '刚刚'
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  return `${Math.round(seconds / 3600)} 小时前`
}

/**
 * 用于计算「多久以前」的参考时刻。
 *
 * 后端给的是**单调时钟**，与浏览器时间无关，所以不能用 `Date.now()`。
 * 取所有行里最新的一次迁移时刻当作「现在」——它是本次响应里唯一可用的时基锚点。
 */
const latestTransitionAt = computed(() => {
  let latest = 0
  for (const row of rows.value) {
    for (const transition of row.recent_transitions ?? []) {
      if (transition.at > latest) latest = transition.at
    }
  }
  return latest
})

async function load(showSpinner = false) {
  if (showSpinner) loading.value = true
  try {
    const response = await llmApi.getResilienceStatus()
    rows.value = response.data || []
    lastUpdated.value = new Date()
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '无法读取供应商容错状态'
  } finally {
    loading.value = false
  }
}

function toggleAutoRefresh() {
  autoRefresh.value = !autoRefresh.value
  if (autoRefresh.value) {
    startTimer()
  } else if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function startTimer() {
  if (timer) clearInterval(timer)
  // 10 秒足以观察熔断恢复，又不至于把一个诊断面板变成压测。
  timer = setInterval(() => load(false), 10_000)
}

onMounted(() => {
  load(true)
  startTimer()
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="resilience-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">LLM</p>
        <h1>容错状态</h1>
        <p class="subtitle">
          按模型查看故障转移队列的实际顺序、熔断三态与最近尝试记录。
          队列按优先级升序排列，序号即请求实际尝试的次序。
        </p>
      </div>
      <div class="header-actions">
        <span v-if="lastUpdated" class="updated">
          更新于 {{ lastUpdated.toLocaleTimeString() }}
        </span>
        <n-button data-test="toggle-auto-refresh" @click="toggleAutoRefresh">
          {{ autoRefresh ? '暂停自动刷新' : '恢复自动刷新' }}
        </n-button>
        <n-button type="primary" data-test="refresh-resilience" @click="load(true)">
          立即刷新
        </n-button>
      </div>
    </header>

    <n-alert v-if="errorMessage" type="error" role="alert" class="notice">
      {{ errorMessage }}
    </n-alert>
    <n-alert
      v-else-if="degradedCount > 0"
      type="warning"
      role="status"
      class="notice"
      data-test="degraded-notice"
    >
      有 {{ degradedCount }} 个供应商不处于正常状态；熔断期间该供应商会被跳过。
    </n-alert>

    <section v-if="loading" class="loading-state" aria-busy="true">
      正在读取供应商容错状态...
    </section>
    <section v-else-if="queues.length === 0" class="empty-state" role="status">
      <h2>还没有可观测的供应商</h2>
      <p>在「模型管理」里添加至少一个启用的后端后，这里会显示它的故障转移队列。</p>
    </section>

    <section
      v-for="queue in queues"
      v-else
      :key="queue.model"
      class="queue-section"
      :aria-label="`${queue.model} 的故障转移队列`"
    >
      <div class="queue-heading">
        <h2>{{ queue.model }}</h2>
        <span>{{ queue.providers.length }} 个供应商</span>
      </div>
      <div class="provider-list">
        <article
          v-for="(row, index) in queue.providers"
          :key="`${queue.model}:${row.provider}`"
          class="provider-row"
          data-test="provider-row"
        >
          <div class="provider-main">
            <span class="order" :aria-label="`队列第 ${index + 1} 位`">P{{ index + 1 }}</span>
            <div class="provider-identity">
              <strong>{{ row.provider }}</strong>
              <small>priority {{ row.priority }}</small>
            </div>
          </div>

          <div class="provider-metrics">
            <n-tag :type="circuitTag(row.state).type" size="small">
              {{ circuitTag(row.state).label }}
            </n-tag>
            <span class="metric">
              错误率<b>{{ errorRateText(row) }}</b>
            </span>
            <span class="metric">
              连续失败<b>{{ row.failure_count ?? 0 }}</b>
            </span>
            <!--
              上游限额余量：唯一能在撞上限之前给出信号的东西。
              熔断说的是「它已经坏了」，余量说的是「它还剩多少」。
            -->
            <span class="metric" data-test="rate-limit-headroom">
              上游余量<b>{{ headroomText(row) }}</b>
            </span>
            <span v-if="recoveryText(row)" class="metric recovery">
              {{ recoveryText(row) }}
            </span>
            <span v-if="row.recent_error_category" class="metric">
              最近失败<b>{{ errorCategoryLabel(row.recent_error_category) }}</b>
            </span>
          </div>

          <details v-if="row.recent_transitions?.length" class="transitions">
            <summary>状态变化 {{ row.recent_transitions.length }} 次</summary>
            <ul>
              <li
                v-for="(transition, index) in [...row.recent_transitions].reverse()"
                :key="`${transition.at}:${index}`"
                :class="{ failed: transition.to_state === 'open' }"
              >
                <span class="transition-arrow">
                  {{ circuitLabel(transition.from_state) }} →
                  {{ circuitLabel(transition.to_state) }}
                </span>
                <span class="transition-reason">
                  {{ transitionReasonText(transition.reason) }}
                </span>
                <span class="transition-ago">
                  {{ transitionAgo(transition, latestTransitionAt) }}
                </span>
              </li>
            </ul>
          </details>

          <details v-if="row.recent_attempts?.length" class="attempts">
            <summary>最近 {{ row.recent_attempts.length }} 次尝试</summary>
            <ul>
              <li
                v-for="attempt in [...row.recent_attempts].reverse()"
                :key="`${attempt.trace_id}:${attempt.attempt}:${attempt.retry_index}`"
                :class="{ failed: !attempt.success }"
              >
                <span class="attempt-outcome">{{ attempt.success ? '成功' : '失败' }}</span>
                <span class="attempt-meta">
                  第 {{ attempt.attempt }} 次
                  <template v-if="attempt.retry_index > 0">
                    （重试 {{ attempt.retry_index }}）
                  </template>
                  · {{ attemptDuration(attempt) }}
                </span>
                <span v-if="attempt.error_category" class="attempt-reason">
                  {{ errorCategoryLabel(attempt.error_category) }}
                </span>
                <!-- 已产出内容的失败不能安全重试：切换并拼接会让用户看到重复回复。 -->
                <n-tag v-if="attempt.partial_output" type="warning" size="small">
                  已产出内容
                </n-tag>
                <code class="trace-id" :title="attempt.trace_id">{{ attempt.trace_id }}</code>
              </li>
            </ul>
          </details>
          <p v-else class="no-attempts">尚无尝试记录</p>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.resilience-view { max-width: 1180px; margin: 0 auto; padding: 28px 32px 48px; color: var(--text-color); }
.page-header, .queue-heading, .header-actions { display: flex; align-items: center; }
.page-header { justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 6px; color: var(--primary-color); font-size: 12px; font-weight: 700; text-transform: uppercase; }
h1, h2, p { margin-top: 0; } h1 { margin-bottom: 8px; font-size: 28px; } h2 { margin-bottom: 0; font-size: 18px; }
.subtitle { margin-bottom: 0; max-width: 660px; line-height: 1.6; color: var(--text-color-2); }
.header-actions { flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.updated { padding: 6px 10px; color: var(--text-color-2); font-size: 13px; white-space: nowrap; }
.notice { margin-bottom: 16px; }
.loading-state, .empty-state { padding: 64px 24px; text-align: center; border: 1px dashed var(--border-color); border-radius: var(--radius-sm); }
.empty-state h2 { margin-bottom: 8px; } .empty-state p { color: var(--text-color-2); }
.queue-section { margin-top: 28px; }
.queue-heading { justify-content: space-between; margin-bottom: 10px; }
.queue-heading span { color: var(--text-color-2); font-size: 13px; }
.provider-list { border-top: 1px solid var(--border-color); }
.provider-row { display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 12px 20px; padding: 16px 0; border-bottom: 1px solid var(--border-color); }
.provider-main { display: flex; align-items: center; gap: 12px; min-width: 0; }
/* 序号是这个面板最重要的一个字：没有它就看不出「队列」 */
.order { display: inline-flex; align-items: center; justify-content: center; min-width: 34px; height: 26px; padding: 0 8px; color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); border-radius: var(--radius-sm); font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }
.provider-identity { display: grid; gap: 2px; min-width: 0; }
.provider-identity strong { overflow-wrap: anywhere; }
.provider-identity small { color: var(--text-color-2); font-variant-numeric: tabular-nums; }
.provider-metrics { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; font-size: 12px; color: var(--text-color-2); }
.metric { display: inline-grid; gap: 2px; }
.metric b { color: var(--text-color); font-size: 13px; font-variant-numeric: tabular-nums; }
.metric.recovery { color: var(--primary-color); }
.attempts { grid-column: 1 / -1; margin-top: 4px; font-size: 12px; }
.attempts summary { color: var(--text-color-2); cursor: pointer; }
.attempts ul { margin: 10px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.attempts li { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 10px; background: var(--card-color); border: 1px solid var(--border-color); border-radius: var(--radius-sm); }
.attempts li.failed { border-color: color-mix(in srgb, var(--error-color, #d03050) 40%, var(--border-color)); }
.attempt-outcome { font-weight: 600; }
.attempts li.failed .attempt-outcome { color: var(--error-color, #d03050); }
.attempt-meta, .attempt-reason { color: var(--text-color-2); }

/* 状态变化沿用尝试列表的排版，只把「变成 open」这一类标红——
   那是需要动作的一刻，其余是过程。 */
.transitions { grid-column: 1 / -1; margin-top: 4px; font-size: 12px; }
.transitions summary { color: var(--text-color-2); cursor: pointer; }
.transitions ul { margin: 10px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
.transitions li { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 10px; background: var(--card-color); border: 1px solid var(--border-color); border-radius: var(--radius-sm); }
.transitions li.failed { border-color: color-mix(in srgb, var(--error-color, #d03050) 40%, var(--border-color)); }
.transition-arrow { font-weight: 600; }
.transitions li.failed .transition-arrow { color: var(--error-color, #d03050); }
.transition-reason { color: var(--text-color-2); }
.transition-ago { margin-left: auto; color: var(--text-color-3, var(--text-color-2)); font-size: 11px; white-space: nowrap; }
.trace-id { margin-left: auto; max-width: 220px; overflow: hidden; color: var(--text-color-3, var(--text-color-2)); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.no-attempts { grid-column: 1 / -1; margin: 0; color: var(--text-color-2); font-size: 12px; }
@media (max-width: 768px) {
  .resilience-view { padding: 20px 16px 40px; }
  .page-header { align-items: stretch; flex-direction: column; }
  .header-actions { justify-content: flex-start; }
  .provider-row { grid-template-columns: 1fr; }
  .trace-id { margin-left: 0; }
  .transition-ago { margin-left: 0; }
}
</style>
