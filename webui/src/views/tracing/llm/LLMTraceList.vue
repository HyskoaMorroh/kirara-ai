<!-- LLM 追踪列表组件 -->
<template>
  <div class="trace-list">
    <n-card title="LLM 请求追踪" class="trace-card">
      <!-- 顶部操作栏 -->
      <template #header-extra>
        <n-space align="center" :size="12">
          <n-badge
            :dot="isConnected"
            :color="isConnected ? 'success' : 'error'"
            class="connection-status"
          >
            <n-text>{{ isConnected ? '实时连接' : '未连接' }}</n-text>
          </n-badge>
          <n-button type="primary" @click="refreshData" :loading="isLoading" class="refresh-button">
            <template #icon>
              <n-icon><refresh-outline /></n-icon>
            </template>
            刷新
          </n-button>
        </n-space>
      </template>

      <!-- 统计信息卡片 -->
      <div v-if="statisticsStatus === 'loading'" class="statistics-section statistics-loading" data-test="statistics-loading" aria-busy="true">
        <n-grid :cols="5" :x-gap="16" :y-gap="16">
          <n-grid-item v-for="index in 5" :key="index"><n-skeleton height="120px" /></n-grid-item>
        </n-grid>
      </div>
      <n-alert
        v-else-if="statisticsStatus === 'error'"
        type="error"
        :show-icon="true"
        class="statistics-section"
        data-test="statistics-error"
        role="alert"
      >
        {{ statisticsError }}
      </n-alert>
      <n-empty
        v-else-if="statisticsStatus === 'ready' && formattedStatistics?.length === 0"
        class="statistics-section"
        data-test="statistics-empty"
        description="暂无统计数据"
      />
      <div v-else-if="statisticsStatus === 'ready'" class="statistics-section">
        <n-grid :cols="5" :x-gap="16" :y-gap="16">
          <n-grid-item v-for="stat in formattedStatistics" :key="stat.label">
            <n-card :class="['stat-card', stat.type]">
              <div class="stat-content">
                <div class="stat-value">{{ formatLargeNumber(stat.value) }}</div>
                <div class="stat-label">{{ stat.label }}</div>
              </div>
            </n-card>
          </n-grid-item>
        </n-grid>
      </div>

      <!-- 过滤和搜索 -->
      <div class="filter-section">
        <n-card class="filter-card">
          <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-input
                v-model:value="filterParams.correlationId"
                placeholder="回合 ID"
                clearable
                class="filter-input"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.modelId"
                placeholder="选择模型"
                clearable
                :options="filterOptions.modelId"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.provider"
                placeholder="选择供应商"
                clearable
                :options="filterOptions.provider"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.backendName"
                placeholder="选择后端"
                clearable
                :options="filterOptions.backendName"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.status"
                placeholder="请求状态"
                clearable
                :options="statusOptions"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.errorCategory"
                placeholder="失败类型"
                clearable
                :options="filterOptions.errorCategory"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-select
                v-model:value="filterParams.usageSource"
                placeholder="用量来源"
                clearable
                :options="filterOptions.usageSource"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item span="4 s:4 m:2 l:1">
              <n-input
                v-model:value="filterParams.query"
                placeholder="搜索关键词"
                clearable
                class="filter-input"
              >
                <template #prefix>
                  <n-icon><search-outline /></n-icon>
                </template>
              </n-input>
            </n-grid-item>
            <n-grid-item span="4 s:4 m:4 l:2">
              <!-- 时间范围此前完全没有入口：后端要求带时区的 ISO-8601，
                   这里用日期时间区间选择器直接产出符合要求的值。 -->
              <n-date-picker
                v-model:value="timeRange"
                type="datetimerange"
                clearable
                class="filter-range"
                :placeholder="'请求时间范围'"
                @update:value="handleTimeRangeChange"
              />
            </n-grid-item>
          </n-grid>

          <div class="filter-actions">
            <n-space>
              <n-button @click="handleReset" class="filter-button">重置</n-button>
              <n-button @click="applyFilter" type="primary" class="filter-button">应用</n-button>
              <n-button
                @click="exportTraces"
                :loading="isExporting"
                class="filter-button"
                aria-label="导出当前筛选结果"
              >
                <template #icon>
                  <n-icon><download-outline /></n-icon>
                </template>
                导出 CSV
              </n-button>
            </n-space>
          </div>
        </n-card>
      </div>

      <!-- 追踪列表 -->
      <div class="trace-list-section">
        <n-card class="trace-list-card">
          <template #header>
            <div class="list-header">
              <div class="list-title">追踪记录</div>
              <n-text class="list-count">共 {{ totalTraces }} 条记录</n-text>
            </div>
          </template>

          <n-data-table
            :columns="columns"
            :data="traces"
            :loading="isLoading"
            :pagination="{
              page: currentPage,
              pageSize: pageSize,
              itemCount: totalTraces,
              pageCount: totalPages,
              showSizePicker: true,
              pageSizes: [10, 20, 50, 100],
              onUpdatePage: handlePageChange,
              onUpdatePageSize: handlePageSizeChange,
              prefix: ({ itemCount }) => `共 ${itemCount} 条记录`
            }"
            :bordered="false"
            class="trace-table"
          />
        </n-card>
      </div>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import {
  NCard,
  NDataTable,
  NButton,
  NText,
  NIcon,
  NBadge,
  NSpace,
  NGrid,
  NGridItem,
  NSelect,
  NInput,
  NAlert,
  NEmpty,
  NSkeleton,
  NDatePicker,
  useMessage
} from 'naive-ui'
import { RefreshOutline, SearchOutline, DownloadOutline } from '@vicons/ionicons5'
import { useLLMTracingViewModel } from './llm-tracing.vm'
import { formatLargeNumber } from '@/utils/formatters'
import { http } from '@/utils/http'

const {
  traces,
  formattedStatistics,
  statisticsStatus,
  statisticsError,
  isConnected,
  isLoading,
  totalTraces,
  currentPage,
  pageSize,
  totalPages,
  filterParams,
  filterOptions,
  statusOptions,
  columns,
  fetchTraces,
  resetFilter,
  applyFilter,
  handlePageChange,
  handlePageSizeChange,
  refreshData,
  initialize,
  disconnectWebSocket
} = useLLMTracingViewModel()

const message = useMessage()
const timeRange = ref<[number, number] | null>(null)
const isExporting = ref(false)

/**
 * 时间范围控件产出毫秒时间戳，后端要求带时区的 ISO-8601；
 * `toISOString()` 得到的是 UTC 表示，带 `Z` 后缀，符合该要求。
 */
const handleTimeRangeChange = (value: [number, number] | null) => {
  if (!value) {
    filterParams.value.startTime = null
    filterParams.value.endTime = null
    return
  }
  filterParams.value.startTime = new Date(value[0]).toISOString()
  filterParams.value.endTime = new Date(value[1]).toISOString()
}

const handleReset = () => {
  timeRange.value = null
  resetFilter()
}

/**
 * 导出当前筛选结果。
 *
 * 后端的 `/tracing/llm/export` 一直存在但界面上没有任何入口。
 * 这里复用同一份筛选条件，保证「看到的」与「导出的」是同一批数据。
 */
const exportTraces = async () => {
  isExporting.value = true
  try {
    const payload: Record<string, unknown> = {
      format: 'csv',
      limit: 10000,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
    }
    const filters = filterParams.value
    if (filters.correlationId) payload.correlation_id = filters.correlationId
    if (filters.modelId) payload.model = filters.modelId
    if (filters.backendName) payload.backend = filters.backendName
    if (filters.provider) payload.provider = filters.provider
    if (filters.status) payload.status = filters.status
    if (filters.usageSource) payload.usage_source = filters.usageSource
    if (filters.errorCategory) payload.error_category = filters.errorCategory
    if (filters.startTime) payload.start_time = filters.startTime
    if (filters.endTime) payload.end_time = filters.endTime
    if (filters.query) payload.query = filters.query

    // 导出返回 text/csv，不能走会解析 JSON 的 http.post；
    // http.fetch 保留原始 Response，同时仍带上鉴权头与基础路径。
    const response = await http.fetch('/tracing/llm/export', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
    if (!response.ok) {
      throw new Error(`导出失败 (HTTP ${response.status})`)
    }
    const csv = await response.text()
    const truncated = response.headers.get('X-Export-Truncated') === 'true'
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'llm-traces.csv'
    link.click()
    URL.revokeObjectURL(url)
    if (truncated) {
      message.warning('结果超过单次导出上限，已导出前 10000 条；请收窄筛选条件')
    } else {
      message.success('已导出当前筛选结果')
    }
  } catch (error) {
    message.error('导出失败，请稍后重试')
  } finally {
    isExporting.value = false
  }
}

onMounted(() => {
  initialize()
})

onUnmounted(() => {
  disconnectWebSocket()
})
</script>

<style scoped>
/* 继承基础追踪列表的样式 */
.trace-list {
  padding: 1.5rem;
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.trace-card {
  min-height: calc(100vh - 28px);
  /* 页面级主卡片，用大型表面档 */
  border-radius: var(--radius-lg);
  background-color: var(--card-bg-color);
  box-shadow: var(--box-shadow);
}

/* 连接状态样式 */
.connection-status {
  padding: 6px 12px;
  /* 状态指示是胶囊形状态徽标 */
  border-radius: var(--radius-pill);
  background-color: var(--bg-color);
}

/* 刷新按钮样式 */
.refresh-button {
  height: 40px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.refresh-button:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-hover);
}

/* 统计卡片样式 */
.statistics-section {
  margin-bottom: 24px;
}

.stat-card {
  height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  /* 统计卡位于 .trace-card（lg 档）内部，按嵌套原则降一档到 md */
  border-radius: var(--radius-md);
  transition: all 0.3s ease;
  background: linear-gradient(
    135deg,
    var(--card-bg-color) 0%,
    color-mix(in oklab, var(--card-bg-color), var(--primary-color) 10%) 100%
  );
}

.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--box-shadow-hover);
}

.stat-card.success {
  background: linear-gradient(
    135deg,
    var(--card-bg-color) 0%,
    color-mix(in oklab, var(--card-bg-color), var(--success-color) 10%) 100%
  );
}

.stat-card.error {
  background: linear-gradient(
    135deg,
    var(--card-bg-color) 0%,
    color-mix(in oklab, var(--card-bg-color), var(--error-color) 10%) 100%
  );
}

.stat-card.warning {
  background: linear-gradient(
    135deg,
    var(--card-bg-color) 0%,
    color-mix(in oklab, var(--card-bg-color), var(--warning-color) 10%) 100%
  );
}

.stat-card.info {
  background: linear-gradient(
    135deg,
    var(--card-bg-color) 0%,
    color-mix(in oklab, var(--card-bg-color), var(--info-color) 10%) 100%
  );
}

.stat-content {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stat-value {
  font-size: 2.5rem;
  font-weight: 700;
  margin-bottom: 8px;
  background: linear-gradient(to right, var(--primary-color), var(--primary-color-hover));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.success .stat-value {
  background: linear-gradient(
    to right,
    var(--success-color),
    color-mix(in oklab, var(--success-color), white 20%)
  );
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.error .stat-value {
  background: linear-gradient(
    to right,
    var(--error-color),
    color-mix(in oklab, var(--error-color), white 20%)
  );
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.warning .stat-value {
  background: linear-gradient(
    to right,
    var(--warning-color),
    color-mix(in oklab, var(--warning-color), white 20%)
  );
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.info .stat-value {
  background: linear-gradient(
    to right,
    var(--info-color),
    color-mix(in oklab, var(--info-color), white 20%)
  );
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.stat-label {
  font-size: 1rem;
  color: var(--text-color-secondary);
}

/* 过滤区域样式 */
.filter-section {
  margin-bottom: 24px;
}

.filter-card {
  /* 位于 .trace-card（lg 档）内部，按嵌套原则降一档到 md */
  border-radius: var(--radius-md);
  background-color: var(--bg-color);
  padding: 20px;
}

.filter-select,
.filter-input {
  width: 100%;
  transition: all 0.3s ease;
}

.filter-select:hover,
.filter-input:hover {
  transform: translateY(-2px);
}

.filter-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.filter-button {
  height: 36px;
  border-radius: var(--radius-sm);
  min-width: 80px;
  font-weight: 600;
}

/* 列表区域样式 */
.trace-list-section {
  margin-bottom: 24px;
}

.trace-list-card {
  /* 与 .filter-card 同层，取同一档 md */
  border-radius: var(--radius-md);
  background-color: var(--bg-color);
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 0;
}

.list-title {
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--text-color);
}

.list-count {
  color: var(--text-color-secondary);
}

.trace-table {
  /* 表格嵌在 .trace-list-card（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  overflow: hidden;
}

/* 响应式调整 */
@media (max-width: 992px) {
  .trace-list {
    padding: 1rem;
  }

  .stat-card {
    height: 100px;
  }

  .stat-value {
    font-size: 2rem;
  }
}

@media (max-width: 768px) {
  .trace-list {
    padding: 0.5rem;
  }

  .statistics-section :deep(.n-grid) {
    grid-template-columns: repeat(2, 1fr) !important;
  }

  .filter-section :deep(.n-grid) {
    grid-template-columns: repeat(2, 1fr) !important;
  }
}

@media (max-width: 480px) {
  .statistics-section :deep(.n-grid) {
    grid-template-columns: repeat(1, 1fr) !important;
  }

  .filter-section :deep(.n-grid) {
    grid-template-columns: repeat(1, 1fr) !important;
  }

  .stat-card {
    height: 80px;
  }

  .stat-value {
    font-size: 1.8rem;
  }
}
</style>
