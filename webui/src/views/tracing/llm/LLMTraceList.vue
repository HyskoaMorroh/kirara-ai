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
          <n-grid :cols="5" :x-gap="16" :y-gap="16" responsive="screen">
            <n-grid-item>
              <n-input
                v-model:value="filterParams.correlationId"
                placeholder="回合 ID"
                clearable
                class="filter-input"
              />
            </n-grid-item>
            <n-grid-item>
              <n-select
                v-model:value="filterParams.modelId"
                placeholder="选择模型"
                clearable
                :options="filterOptions.modelId"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item>
              <n-select
                v-model:value="filterParams.backendName"
                placeholder="选择后端"
                clearable
                :options="filterOptions.backendName"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item>
              <n-select
                v-model:value="filterParams.status"
                placeholder="请求状态"
                clearable
                :options="statusOptions"
                class="filter-select"
              />
            </n-grid-item>
            <n-grid-item>
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
          </n-grid>

          <div class="filter-actions">
            <n-space>
              <n-button @click="resetFilter" class="filter-button">重置</n-button>
              <n-button @click="applyFilter" type="primary" class="filter-button">应用</n-button>
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
import { onMounted, onUnmounted } from 'vue'
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
  NSkeleton
} from 'naive-ui'
import { RefreshOutline, SearchOutline } from '@vicons/ionicons5'
import { useLLMTracingViewModel } from './llm-tracing.vm'
import { formatLargeNumber } from '@/utils/formatters'

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
