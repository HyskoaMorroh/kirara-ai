<!-- LLM 追踪详情组件 -->
<template>
  <div class="trace-detail">
    <n-card title="LLM 请求详情" class="detail-card">
      <!-- 顶部操作栏 -->
      <template #header-extra>
        <n-space align="center" :size="12">
          <n-button type="default" @click="goBackToList" class="action-button">
            <template #icon>
              <n-icon><arrow-back-outline /></n-icon>
            </template>
            返回列表
          </n-button>
          <n-button type="primary" @click="refreshData" :loading="isLoading" class="action-button">
            <template #icon>
              <n-icon><refresh-outline /></n-icon>
            </template>
            刷新
          </n-button>
        </n-space>
      </template>

      <n-spin :show="isLoading">
        <!-- 404状态 -->
        <n-result
          v-if="!traceDetail && !isLoading"
          status="404"
          title="未找到记录"
          description="请求的记录不存在或已被删除"
          class="not-found"
        >
        </n-result>

        <template v-else>
          <!-- 基本信息 -->
          <div class="section">
            <n-card title="基本信息" class="info-card">
              <n-descriptions :column="3" bordered>
                <n-descriptions-item label="追踪ID">
                  <n-text code class="trace-id">{{ traceDetail?.trace_id }}</n-text>
                </n-descriptions-item>
                <n-descriptions-item label="回合 ID">
                  <n-text code class="trace-id">{{ traceDetail?.correlation_id || '---' }}</n-text>
                </n-descriptions-item>
                <n-descriptions-item label="请求时间">
                  {{ formatDate(traceDetail?.request_time) }}
                </n-descriptions-item>
                <n-descriptions-item label="响应时间">
                  {{ formatDate(traceDetail?.response_time) }}
                </n-descriptions-item>
                <n-descriptions-item label="耗时">
                  {{ formatDuration(traceDetail?.duration) }}
                </n-descriptions-item>
                <n-descriptions-item label="状态">
                  <n-tag :type="getStatusType(traceDetail?.status)" size="small" class="status-tag">
                    {{ getStatusText(traceDetail?.status) }}
                  </n-tag>
                </n-descriptions-item>
                <n-descriptions-item v-if="traceDetail?.error" label="错误信息">
                  <n-text type="error" class="error-text">{{ traceDetail.error }}</n-text>
                </n-descriptions-item>
              </n-descriptions>
            </n-card>
          </div>

          <!-- LLM信息 -->
          <div class="section">
            <n-card title="LLM信息" class="info-card">
              <n-descriptions :column="3" bordered>
                <n-descriptions-item label="模型">
                  <n-tag type="primary" size="small" class="model-tag">
                    {{ (traceDetail as any)?.model_id }}
                  </n-tag>
                </n-descriptions-item>
                <n-descriptions-item label="后端">
                  <n-tag type="info" size="small" class="backend-tag">
                    {{ (traceDetail as any)?.backend_name }}
                  </n-tag>
                </n-descriptions-item>
                <n-descriptions-item label="总Token">
                  {{ formatTokens((traceDetail as any)?.total_tokens) }}
                </n-descriptions-item>
                <n-descriptions-item label="提示Token">
                  {{ formatTokens((traceDetail as any)?.prompt_tokens) }}
                </n-descriptions-item>
                <n-descriptions-item label="补全Token">
                  {{ formatTokens((traceDetail as any)?.completion_tokens) }}
                </n-descriptions-item>
                <n-descriptions-item label="缓存Token">
                  {{ formatTokens((traceDetail as any)?.cached_tokens) }}
                </n-descriptions-item>
              </n-descriptions>
            </n-card>
          </div>

          <!-- 计量与容错明细。
               这一块此前完全没有：`getDetailFields()` 定义好了却没有消费者，
               详情页自己硬编码了更少的字段，于是「详情页信息比列表还少」。
               现在直接用同一份定义，两处口径不会再漂移。 -->
          <div class="section">
            <n-card title="计量与容错" class="info-card">
              <n-descriptions :column="3" bordered>
                <n-descriptions-item
                  v-for="(field, index) in detailFields"
                  :key="`${field.key}:${index}`"
                  :label="field.label"
                >
                  {{ renderDetailField(field) }}
                </n-descriptions-item>
              </n-descriptions>
            </n-card>
          </div>

          <!-- 逐次尝试明细。
               后端一直返回 `attempts`，此前没有任何界面消费它。只给「重试 2 次、
               转移 1 次」两个数字回答不了运维真正要问的：哪一家在失败、失败类型
               是什么、换到哪一家之后成功了——而这三件事对应完全不同的动作。 -->
          <div v-if="attemptRows.length > 0" class="section">
            <n-card title="逐次尝试" class="info-card" data-test="attempt-table">
              <div class="attempt-list">
                <div
                  v-for="(row, index) in attemptRows"
                  :key="`${row.attempt ?? index}:${row.provider ?? 'unknown'}`"
                  class="attempt-row"
                  :class="row.succeeded === false ? 'attempt-failed' : 'attempt-ok'"
                  data-test="attempt-row"
                >
                  <n-tag
                    size="small"
                    :type="row.succeeded === false ? 'error' : row.succeeded ? 'success' : 'default'"
                  >
                    {{ row.succeeded === false ? '失败' : row.succeeded ? '成功' : '未知' }}
                  </n-tag>
                  <span class="attempt-kind">{{ attemptKindLabel(row.kind) }}</span>
                  <span class="attempt-provider">{{ row.provider ?? '---' }}</span>
                  <span class="attempt-metric">
                    首字节 {{ row.ttftSeconds === null ? '---' : `${(row.ttftSeconds * 1000).toFixed(0)} ms` }}
                  </span>
                  <span class="attempt-metric">
                    耗时 {{ row.durationSeconds === null ? '---' : `${row.durationSeconds.toFixed(2)} s` }}
                  </span>
                  <span v-if="row.errorCategory" class="attempt-error">{{ row.errorCategory }}</span>
                  <!-- 产出过部分内容的失败与「什么都没发生」的失败不同：
                       前者用户可能已经看到半句话，重发会造成重复。 -->
                  <n-tag v-if="row.partialOutput" size="small" type="warning">已产出部分内容</n-tag>
                </div>
              </div>
            </n-card>
          </div>

          <!-- 请求内容 -->
          <div class="section">
            <n-card title="请求内容" class="content-card">
              <div class="content-actions">
                <n-space>
                  <n-button
                    size="small"
                    @click="copyToClipboard(formatJSON(traceDetail?.request))"
                    class="copy-button"
                  >
                    <template #icon>
                      <n-icon><copy-outline /></n-icon>
                    </template>
                    复制
                  </n-button>
                  <n-button size="small" @click="toggleRequestExpand" class="expand-button">
                    <template #icon>
                      <n-icon>
                        <contract-outline v-if="isRequestExpanded" />
                        <expand-outline v-else />
                      </n-icon>
                    </template>
                    {{ isRequestExpanded ? '收起' : '展开' }}
                  </n-button>
                </n-space>
              </div>
              <div class="code-container" :class="{ expanded: isRequestExpanded }">
                <n-code
                  :code="formatJSON(traceDetail?.request)"
                  language="json"
                  :word-wrap="true"
                  :show-line-numbers="true"
                  :highlight-current-line="true"
                  class="code-block"
                />
              </div>
            </n-card>
          </div>

          <!-- 响应内容 -->
          <div class="section">
            <n-card title="响应内容" class="content-card">
              <div class="content-actions">
                <n-space>
                  <n-button
                    size="small"
                    @click="copyToClipboard(formatJSON(traceDetail?.response))"
                    class="copy-button"
                  >
                    <template #icon>
                      <n-icon><copy-outline /></n-icon>
                    </template>
                    复制
                  </n-button>
                  <n-button size="small" @click="toggleResponseExpand" class="expand-button">
                    <template #icon>
                      <n-icon>
                        <contract-outline v-if="isResponseExpanded" />
                        <expand-outline v-else />
                      </n-icon>
                    </template>
                    {{ isResponseExpanded ? '收起' : '展开' }}
                  </n-button>
                </n-space>
              </div>
              <div class="code-container" :class="{ expanded: isResponseExpanded }">
                <n-code
                  :code="formatJSON(traceDetail?.response)"
                  language="json"
                  :word-wrap="true"
                  :show-line-numbers="true"
                  :highlight-current-line="true"
                  class="code-block"
                />
              </div>
            </n-card>
          </div>
        </template>
      </n-spin>
    </n-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import {
  NCard,
  NButton,
  NIcon,
  NSpace,
  NSpin,
  NResult,
  NDescriptions,
  NDescriptionsItem,
  NTag,
  NText,
  NCode
} from 'naive-ui'
import {
  ArrowBackOutline,
  RefreshOutline,
  CopyOutline,
  ExpandOutline,
  ContractOutline
} from '@vicons/ionicons5'
import { useLLMTracingViewModel } from './llm-tracing.vm'
import {
  summarizeTraceAttempts,
  TRACE_ATTEMPT_KIND_LABELS,
  type TraceAttemptKind
} from './trace-attempts'

const route = useRoute()
const traceId = route.params.traceId as string

const {
  traceDetail,
  isLoading,
  getTraceDetail,
  connectWebSocket,
  disconnectWebSocket,
  goBackToList,
  formatDate,
  formatDuration,
  formatTokens,
  detailFields
} = useLLMTracingViewModel()

/**
 * 按字段定义取值。
 *
 * 同一个 `key` 可以出现多次（成本快照被拆成金额、定价版本、计价时刻三行），
 * 因此渲染由 formatter 决定，而不是假设 key 与显示项一一对应。
 * 没有值时统一显示 `---`：空白会被读成「这项是 0」。
 */
function renderDetailField(field: {
  key: string
  formatter?: (value: any) => any
}): string {
  const raw = (traceDetail.value as any)?.[field.key]
  const value = field.formatter ? field.formatter(raw) : raw
  if (value === null || value === undefined || value === '') return '---'
  return String(value)
}

/** 逐次尝试明细。没有数据时是空数组，界面整块不显示——而不是显示一张空表。 */
const attemptRows = computed(() =>
  summarizeTraceAttempts((traceDetail.value as any)?.attempts)
)

const attemptKindLabel = (kind: TraceAttemptKind) => TRACE_ATTEMPT_KIND_LABELS[kind]

// 展开/收起状态
const isRequestExpanded = ref(false)
const isResponseExpanded = ref(false)

// 切换请求内容展开状态
const toggleRequestExpand = () => {
  isRequestExpanded.value = !isRequestExpanded.value
}

// 切换响应内容展开状态
const toggleResponseExpand = () => {
  isResponseExpanded.value = !isResponseExpanded.value
}

// 复制到剪贴板
const copyToClipboard = (text: string) => {
  navigator.clipboard
    .writeText(text)
    .then(() => {
      // 可以添加一个成功提示
      console.log('内容已复制到剪贴板')
    })
    .catch((err) => {
      console.error('复制失败:', err)
    })
}

// 格式化JSON
const formatJSON = (data: any) => {
  try {
    return JSON.stringify(data, null, 2)
  } catch (error) {
    return String(data)
  }
}

// 获取状态类型
/** 状态到 n-tag type 的映射。上游可能返回表外的状态，一律按「请求中」处理。 */
const STATUS_TAG_TYPES: Record<string, 'warning' | 'success' | 'error'> = {
  pending: 'warning',
  success: 'success',
  failed: 'error'
}

const getStatusType = (status: string | undefined) =>
  STATUS_TAG_TYPES[status || 'pending'] || 'warning'

// 获取状态文本
const STATUS_TEXTS: Record<string, string> = {
  pending: '请求中',
  success: '成功',
  failed: '失败'
}

const getStatusText = (status: string | undefined) =>
  STATUS_TEXTS[status || 'pending'] || '请求中'

// 刷新数据
const refreshData = async () => {
  await getTraceDetail(traceId)
}

onMounted(async () => {
  await getTraceDetail(traceId)
  connectWebSocket()
})

onUnmounted(() => {
  disconnectWebSocket()
})
</script>

<style scoped>
/* 继承基础追踪详情的样式 */
.trace-detail {
  padding: 1.5rem;
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.detail-card {
  min-height: calc(100vh - 28px);
  /* 页面级主卡片，用大型表面档 */
  border-radius: var(--radius-lg);
  background-color: var(--card-bg-color);
  box-shadow: var(--box-shadow);
}

/* 操作按钮样式 */
.action-button {
  height: 40px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.action-button:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-hover);
}

/* 404状态样式 */
.not-found {
  padding: 48px;
  background-color: var(--bg-color);
  /* 位于 .detail-card（lg 档）内部，按嵌套原则降一档到 md */
  border-radius: var(--radius-md);
  margin: 24px 0;
}

/* 内容区域样式 */
.section {
  margin-bottom: 24px;
  animation: slide-up 0.4s ease;
}

.info-card,
.content-card {
  /* 内容卡片位于 .detail-card（lg 档）内部，按嵌套原则降一档到 md */
  border-radius: var(--radius-md);
  background-color: var(--bg-color);
  transition: all 0.3s ease;
  overflow: hidden;
}

.info-card:hover,
.content-card:hover {
  box-shadow: var(--box-shadow-hover);
}

/* 描述列表样式 */
:deep(.n-descriptions) {
  background-color: var(--card-bg-color);
  /* 位于 .info-card（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  overflow: hidden;
}

:deep(.n-descriptions-table-header) {
  background-color: var(--bg-color);
}

/* 标签样式 */
.trace-id {
  font-family: monospace;
  padding: 4px 8px;
  background-color: var(--card-bg-color);
  border-radius: var(--radius-xs);
}

.status-tag {
  padding: 2px 12px;
  border-radius: var(--radius-pill);
}

.model-tag,
.backend-tag {
  padding: 2px 12px;
  border-radius: var(--radius-pill);
}

/* 代码块容器 */
.code-container {
  position: relative;
  max-height: 300px;
  overflow: auto;
  transition: max-height 0.3s ease;
  border-radius: var(--radius-sm);
}

.code-container.expanded {
  max-height: 800px;
}

/* 代码块样式 */
.code-block {
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 0.9rem;
  line-height: 1.5;
}

:deep(.n-code) {
  border-radius: var(--radius-sm);
}

:deep(.n-code-line-numbers) {
  border-right: 1px solid var(--border-color);
}

:deep(.n-code-word-wrap) {
  word-break: break-word;
}

/* 高亮当前行 */
:deep(.n-code-current-line) {
  background-color: var(--node-header-bg);
}

/* 自定义滚动条 */
.code-container::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.code-container::-webkit-scrollbar-track {
  background: var(--node-muted-bg);
  /* 滚动条只有 8px 宽，属于内联小件档 */
  border-radius: var(--radius-xs);
}

.code-container::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: var(--radius-xs);
}

.code-container::-webkit-scrollbar-thumb:hover {
  background: var(--text-color-tertiary);
}

/* 内容操作按钮 */
.content-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.copy-button,
.expand-button {
  border-radius: var(--radius-sm);
  transition: all 0.2s ease;
}

.copy-button:hover,
.expand-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

/* 错误文本 */
.error-text {
  padding: 4px 8px;
  background-color: rgba(237, 60, 80, 0.1);
  border-radius: var(--radius-xs);
}

/* 动画 */
@keyframes fade-in {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(20px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式调整 */
@media (max-width: 768px) {
  .trace-detail {
    padding: 1rem;
  }

  :deep(.n-descriptions) {
    font-size: 0.9rem;
  }

  .code-container {
    max-height: 200px;
  }

  .code-container.expanded {
    max-height: 500px;
  }
}
</style>
