<script setup lang="ts">
/**
 * 模型目录自动检测计划面板。
 *
 * 后端一直提供三个接口（`GET /llm/auto-detect-schedule`、
 * `PUT /llm/backends/<name>/auto-detect-schedule`、
 * `POST /llm/auto-detect-schedule/run`），但前端**零调用点**——文档里明写着
 * 「这三个接口没有对应的 WebUI 界面，只能用 API 调用」。后果不是「少一个页面」：
 * 「模型目录会定期自动刷新」这件事在产品上完全不可见，运维无法回答
 * 「下一轮什么时候跑」「上一轮成功了吗」「这个后端到底开没开」，
 * 改一个间隔要去改 `data/config.yaml` 再重启整个进程。
 *
 * 三条呈现原则，都是为了不给出错误的安心：
 *
 * 1. **`last_run` 为 `null` 必须与一个真实旧时间戳区分开。** `null` 可能是从没
 *    到期、也可能是每次都失败；显示成「—」而不是编一个时间。
 * 2. **`running: false` 要显著提示。** 调度循环没在跑时，所有间隔配置都不生效——
 *    此时逐行显示「每 5 天」是一句谎话。
 * 3. **写操作与只读操作在界面上必须不同。** 改间隔写 `data/config.yaml`，
 *    立即检测会访问每一个上游并可能改写模型目录。两者都要说清影响，
 *    后者还要确认。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NInputNumber,
  NPopconfirm,
  NTag,
  useMessage
} from 'naive-ui'

import { llmApi, type AutoDetectScheduleRow } from '@/api/llm'
// 纯逻辑放在 `autoDetectSchedule.ts` 里，由那份测试**调用函数**验证。
// 此前这一页的 40 条断言全是 `expect(viewSource).toContain(...)`——
// 改一个比较运算符或把 86_400_000 写错，字符串还在、测试照绿，
// 而用户会按一个错的时刻去等。
import {
  checkInterval,
  isDirty as isRowDirty,
  lastRunText as formatLastRun,
  nextRunText as formatNextRun,
  resultTag as buildResultTag,
  runSummary,
  savedMessage
} from './autoDetectSchedule'

const message = useMessage()

const rows = ref<AutoDetectScheduleRow[]>([])
const schedulerRunning = ref(true)
const loading = ref(true)
const errorMessage = ref('')
const lastUpdated = ref<Date | null>(null)
const running = ref(false)
/** 正在保存的后端名。用名字而不是布尔，多行才不会一起转圈。 */
const savingBackend = ref('')
/** 每行的待保存值。与 `rows` 分开存，避免一次失败的保存把显示值也改掉。 */
const draftIntervals = ref<Record<string, number>>({})
/** 上一次「立即检测」的逐后端结果。`null` 表示还没跑过。 */
const lastRunResults = ref<Record<string, boolean> | null>(null)

let timer: ReturnType<typeof setInterval> | null = null

const enabledCount = computed(
  () => rows.value.filter((row) => (row.interval_days ?? 0) > 0).length
)

const neverRunCount = computed(
  () =>
    rows.value.filter((row) => (row.interval_days ?? 0) > 0 && !row.last_run).length
)

const nextRunText = (row: AutoDetectScheduleRow) => formatNextRun(row)
const lastRunText = (row: AutoDetectScheduleRow) => formatLastRun(row)
const resultTag = (name: string) => buildResultTag(lastRunResults.value, name)

async function load(showSpinner = false) {
  if (showSpinner) loading.value = true
  try {
    const response = await llmApi.getAutoDetectSchedule()
    rows.value = response.backends || []
    schedulerRunning.value = Boolean(response.running)
    // 只为还没有草稿的行填初值：正在编辑的输入框不能被一次轮询改掉。
    for (const row of rows.value) {
      if (!(row.name in draftIntervals.value)) {
        draftIntervals.value[row.name] = row.interval_days ?? 0
      }
    }
    lastUpdated.value = new Date()
    errorMessage.value = ''
  } catch (error) {
    errorMessage.value =
      error instanceof Error ? error.message : '无法读取自动检测计划'
  } finally {
    loading.value = false
  }
}

const isDirty = (row: AutoDetectScheduleRow) =>
  isRowDirty(row, draftIntervals.value[row.name])

async function saveInterval(row: AutoDetectScheduleRow) {
  const checked = checkInterval(draftIntervals.value[row.name])
  if (!checked.ok) {
    message.error(checked.error)
    return
  }
  savingBackend.value = row.name
  try {
    await llmApi.updateAutoDetectSchedule(row.name, checked.value)
    message.success(savedMessage(row.name, checked.value))
    await load(false)
  } catch (error) {
    message.error(
      error instanceof Error ? error.message : `保存 ${row.name} 的检测间隔失败`
    )
    // 失败时把草稿退回服务端值：留着一个没保存成功的数字会让人以为已经生效。
    draftIntervals.value[row.name] = row.interval_days ?? 0
  } finally {
    savingBackend.value = ''
  }
}

async function runNow() {
  running.value = true
  try {
    const response = await llmApi.runAutoDetectNow()
    lastRunResults.value = response.results || {}
    const summary = runSummary(lastRunResults.value)
    message[summary.level](summary.text)
    await load(false)
  } catch (error) {
    message.error(error instanceof Error ? error.message : '立即检测失败')
  } finally {
    running.value = false
  }
}

function startTimer() {
  if (timer) clearInterval(timer)
  // 60 秒：这份数据以天为周期变化，更快的轮询只是噪声。
  timer = setInterval(() => load(false), 60_000)
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
  <div class="schedule-view">
    <header class="page-header">
      <div>
        <p class="eyebrow">LLM</p>
        <h1>自动检测计划</h1>
        <p class="subtitle">
          后台调度器按每个后端的间隔天数重跑模型目录检测。目录有变化时写回配置并重载
          该后端；已在工作流里选中的模型不会被静默替换。
        </p>
      </div>
      <div class="header-actions">
        <span v-if="lastUpdated" class="updated">
          更新于 {{ lastUpdated.toLocaleTimeString() }}
        </span>
        <n-button data-test="refresh-schedule" @click="load(true)">立即刷新</n-button>
        <!--
          写操作要确认。这个动作会访问每一个上游、可能改写配置里的模型目录，
          与「刷新一下页面」完全不是一回事，因此按钮文案与确认文案都要说清影响。
        -->
        <n-popconfirm @positive-click="runNow">
          <template #trigger>
            <n-button
              type="primary"
              :loading="running"
              :disabled="enabledCount === 0"
              data-test="run-auto-detect"
            >
              立即检测全部
            </n-button>
          </template>
          将对 {{ enabledCount }} 个已启用自动检测的后端各发起一次模型目录请求。
          这会访问上游、消耗配额，并在目录有变化时改写 <code>data/config.yaml</code>
          （后端会先备份）。确定继续？
        </n-popconfirm>
      </div>
    </header>

    <n-alert v-if="errorMessage" type="error" role="alert" class="notice">
      {{ errorMessage }}
    </n-alert>
    <!--
      调度循环没在跑时，下面每一行的「每 N 天」都不会发生。不显著说出来的话，
      这个页面会给出一个完全错误的安心。
    -->
    <n-alert
      v-else-if="!schedulerRunning"
      type="warning"
      role="status"
      class="notice"
      data-test="scheduler-stopped"
    >
      后台调度循环当前没有运行，下面的间隔配置都不会自动触发。可以用「立即检测全部」
      手动跑一轮；要恢复自动执行需要检查进程启动日志。
    </n-alert>
    <n-alert
      v-else-if="neverRunCount > 0"
      type="info"
      role="status"
      class="notice"
      data-test="never-run-notice"
    >
      有 {{ neverRunCount }} 个后端启用了自动检测但从未成功检测过。启动后首轮有
      60 秒基础延迟加 0–300 秒随机抖动；若长期为「—」，请检查该后端的凭据与网络。
    </n-alert>

    <section v-if="loading" class="loading-state" aria-busy="true">
      正在读取自动检测计划...
    </section>
    <section v-else-if="rows.length === 0" class="empty-state" role="status">
      <h2>还没有可调度的后端</h2>
      <p>在「模型配置」里添加至少一个后端后，这里会显示它的检测计划。</p>
    </section>

    <table v-else class="schedule-table" data-test="schedule-table">
      <caption class="visually-hidden">各后端的模型目录自动检测计划</caption>
      <thead>
        <tr>
          <th scope="col">后端</th>
          <th scope="col">间隔（天）</th>
          <th scope="col">上次成功</th>
          <th scope="col">下一轮预计</th>
          <th scope="col">模型数</th>
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.name" :data-test="`schedule-row-${row.name}`">
          <th scope="row" class="backend-name">
            <span class="mono">{{ row.name }}</span>
            <n-tag
              v-if="resultTag(row.name)"
              size="small"
              :type="resultTag(row.name)!.type"
            >
              {{ resultTag(row.name)!.label }}
            </n-tag>
          </th>
          <td>
            <div class="interval-cell">
              <n-input-number
                v-model:value="draftIntervals[row.name]"
                :min="0"
                :max="365"
                :step="1"
                size="small"
                :aria-label="`${row.name} 的检测间隔天数`"
                :data-test="`interval-${row.name}`"
              />
              <span v-if="(draftIntervals[row.name] ?? 0) === 0" class="hint">0 = 关闭</span>
            </div>
          </td>
          <td :data-test="`last-run-${row.name}`">{{ lastRunText(row) }}</td>
          <td :data-test="`next-run-${row.name}`">{{ nextRunText(row) }}</td>
          <td class="numeric">{{ row.model_count }}</td>
          <td>
            <n-button
              size="small"
              type="primary"
              :disabled="!isDirty(row)"
              :loading="savingBackend === row.name"
              :data-test="`save-${row.name}`"
              @click="saveInterval(row)"
            >
              保存
            </n-button>
          </td>
        </tr>
      </tbody>
    </table>

    <p class="footnote">
      保存会写入 <code>data/config.yaml</code>（后端保存前会自动备份），并立即生效，
      不需要重启进程。上次成功时间记录在 <code>data/auto_detect_state.json</code>；
      「—」表示从未成功检测过，与「很久以前检测过」是两件不同的事。
    </p>
  </div>
</template>

<style scoped>
.schedule-view { display: flex; flex-direction: column; gap: var(--space-4); padding: var(--space-5); }
.page-header { display: flex; flex-wrap: wrap; gap: var(--space-4); align-items: flex-start; justify-content: space-between; }
.eyebrow { margin: 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); letter-spacing: .08em; text-transform: uppercase; }
.page-header h1 { margin: var(--space-1) 0; font-size: var(--font-size-xl); }
.subtitle { max-width: 62ch; margin: 0; color: var(--text-color-secondary); line-height: var(--line-height-relaxed); }
.header-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; }
.updated { color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.notice { margin: 0; }
.loading-state, .empty-state { padding: var(--space-6); border: 1px dashed var(--border-color); border-radius: var(--border-radius); color: var(--text-color-secondary); text-align: center; }
.empty-state h2 { margin: 0 0 var(--space-2); font-size: var(--font-size-lg); }
.schedule-table { width: 100%; border-collapse: collapse; }
.schedule-table th, .schedule-table td { padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--border-color); text-align: left; vertical-align: middle; }
.schedule-table thead th { color: var(--text-color-secondary); font-size: var(--font-size-sm); font-weight: 500; }
.backend-name { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; font-weight: 500; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.interval-cell { display: flex; gap: var(--space-2); align-items: center; }
.interval-cell :deep(.n-input-number) { width: 110px; }
.hint { color: var(--text-color-secondary); font-size: var(--font-size-sm); }
/* 数字右对齐：一列数量左对齐时，位数不同的值看起来像不同量级。 */
.numeric { font-variant-numeric: tabular-nums; text-align: right; }
.footnote { max-width: 78ch; margin: 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); line-height: var(--line-height-relaxed); }
.footnote code, .header-actions code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
/* 表格标题给读屏用户，视觉上不占位。不用 display:none —— 那会让它对辅助技术也消失。 */
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

@media (max-width: 900px) {
  /* 窄屏下表格改成卡片式：六列挤在手机上会让间隔输入框只剩几十像素。 */
  .schedule-table thead { display: none; }
  .schedule-table tr { display: grid; gap: var(--space-1); padding: var(--space-3) 0; border-bottom: 1px solid var(--border-color); }
  .schedule-table td, .schedule-table th { border: 0; padding: var(--space-1) 0; }
  .numeric { text-align: left; }
}
</style>
