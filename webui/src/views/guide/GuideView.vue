<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NSpace,
  NSteps,
  NStep,
  NGrid,
  NGi,
  NStatistic,
  NButton,
  useMessage,
  NIcon,
  NProgress,
  NTooltip,
  NPopconfirm,
  NRow,
  NCol,
  NDivider,
  NAlert,
  NTag
} from 'naive-ui'
import { useAppStore } from '@/stores/app'
import {
  ArrowForwardOutline,
  CloseCircleOutline,
  CheckmarkCircleOutline,
  AlertCircleOutline,
  ServerOutline,
  ExtensionPuzzleOutline,
  ChatbubblesOutline,
  AppsOutline,
  TimeOutline,
  HardwareChipOutline,
  RefreshOutline
} from '@vicons/ionicons5'
import LLMStatistics from '@/components/LLMStatistics.vue'
import { getBrowserLocalStorage, readJsonRecord, writeStorageItem } from '@/utils/safe-storage'
import {
  READINESS_CHECK_LABELS,
  systemApi,
  type ReadinessCheck,
  type ReadinessResponse
} from '@/api/system'
import {
  hideQuickStartGuide,
  isQuickStartGuideVisible,
  resetQuickStartGuideProgress,
  shouldShowQuickStartRestore,
  showQuickStartGuide
} from './guide-visibility'

const router = useRouter()
const appStore = useAppStore()
const message = useMessage()

// 控制引导卡片的显示
const getGuideStorage = () => getBrowserLocalStorage()
const showGuide = ref(isQuickStartGuideVisible(getGuideStorage()))
const completedGuideSteps = ref(readJsonRecord(getGuideStorage(), 'completedSteps'))

/**
 * 就绪状态。
 *
 * `null` 表示**还没拿到**（正在读或读失败），与「没就绪」是两件事：
 * 把前者显示成后者会让人去修一个不存在的问题，而真正的问题只是这一个
 * 诊断接口没响应。因此所有判断都必须先区分「不知道」。
 */
const readiness = ref<ReadinessResponse | null>(null)
const readinessUnavailable = ref(false)

/** 需要用户动手的检查：`pass` 与 `skip` 不出现在这里。 */
const readinessActions = computed<ReadinessCheck[]>(() => {
  const checks = readiness.value?.checks || []
  // fail 排在 warn 前面：前者阻塞可用性，后者只是没达到最佳状态。
  const weight = (status: string) => (status === 'fail' ? 0 : 1)
  return checks
    .filter((item) => item.status === 'fail' || item.status === 'warn')
    .sort((left, right) => weight(left.status) - weight(right.status))
})

const readinessLabel = (id: string) => READINESS_CHECK_LABELS[id] || id

const loadReadiness = async () => {
  try {
    readiness.value = await systemApi.getReadiness()
    readinessUnavailable.value = false
  } catch {
    // 读不到就绪状态不等于「没就绪」。保持 null 并单独说明。
    readiness.value = null
    readinessUnavailable.value = true
  }
}

/**
 * 从真实就绪状态推导的步骤完成情况。
 *
 * 步骤完成此前是**纯前端点击痕迹**：点一下就写 localStorage，不校验是否真配了
 * IM/LLM。于是换个浏览器全部归零、配好了也不打勾——一个勾选状态与事实无关的
 * 清单比没有清单更糟，它会让人以为自己配完了。
 *
 * 拿不到就绪状态时这里返回空表：不能凭点击痕迹断言「已完成」。
 */
const verifiedSteps = computed<Record<string, boolean>>(() => {
  const checks = readiness.value?.checks
  if (!checks) return {}
  const byId = new Map(checks.map((item) => [item.id, item.status]))
  const verified: Record<string, boolean> = {}
  const mark = (key: string, checkId: string) => {
    const status = byId.get(checkId)
    if (status !== undefined) verified[key] = status === 'pass'
  }
  mark('im', 'im_available')
  mark('llm', 'llm_available')
  mark('workflow', 'workflows_valid')
  mark('dispatch', 'dispatch_targets_exist')
  return verified
})

// 关闭引导卡片
const hideGuide = () => {
  showGuide.value = false
  hideQuickStartGuide(getGuideStorage())
}

// 允许用户从快速开始页恢复之前隐藏的引导卡片
const restoreGuide = () => {
  showGuide.value = true
  showQuickStartGuide(getGuideStorage())
}

const resetGuideProgress = () => {
  completedGuideSteps.value = {}
  resetQuickStartGuideProgress(getGuideStorage())
  message.success('快速开始进度已重置')
}

/**
 * 计算每个步骤的完成状态。
 *
 * `completed` 有两个来源，优先级不同：
 *
 * - `verified`（来自就绪检查）是**事实**：配好了就是配好了，哪怕从没点过这一步；
 *   没配好就是没配好，哪怕点过十次。
 * - 点击痕迹只用于就绪检查覆盖不到的步骤（浏览插件市场没有对应的服务端事实），
 *   以及拿不到就绪状态时的退化行为。
 *
 * 此前只有点击痕迹：换个浏览器全部归零、配好了也不打勾。一个勾选状态与事实
 * 无关的清单比没有清单更糟——它会让人以为自己配完了。
 */
const stepsStatus = computed(() => {
  const verified = verifiedSteps.value
  const steps = [
    {
      key: 'plugins',
      // 「浏览插件市场」没有对应的服务端事实，只能用点击痕迹。
      completed: completedGuideSteps.value.plugins,
      verified: false,
      title: '浏览插件市场',
      description: '发现并安装适合您需求的插件',
      path: '/plugins/market'
    },
    {
      key: 'im',
      completed: verified.im ?? completedGuideSteps.value.im,
      verified: verified.im === true,
      title: '添加 IM',
      description: '连接您常用的聊天平台',
      path: '/im'
    },
    {
      key: 'llm',
      completed: verified.llm ?? completedGuideSteps.value.llm,
      verified: verified.llm === true,
      title: '添加 LLM',
      description: '连接 AI 模型服务',
      path: '/llm'
    },
    {
      key: 'dispatch',
      completed: verified.dispatch ?? completedGuideSteps.value.dispatch,
      verified: verified.dispatch === true,
      title: '了解调度规则',
      description: '学习如何召唤和使用 Bot',
      path: '/workflow/dispatch-rules'
    },
    {
      key: 'workflow',
      completed: verified.workflow ?? completedGuideSteps.value.workflow,
      verified: verified.workflow === true,
      title: '自定义工作流',
      description: '打造专属于您的 AI 助手',
      path: '/workflow'
    }
  ]

  return steps
})

// 计算当前应该进行的步骤
const currentStep = computed(() => {
  return stepsStatus.value.findIndex((step) => !step.completed)
})

const handleStepClick = (step: number, path: string) => {
  // 标记当前步骤为已完成
  completedGuideSteps.value = {
    ...completedGuideSteps.value,
    [stepsStatus.value[step].key]: true
  }
  writeStorageItem(getGuideStorage(), 'completedSteps', JSON.stringify(completedGuideSteps.value))

  // 跳转到目标页面
  router.push(path)
}

const getStatusColor = (status: string) => {
  switch (status) {
    case 'normal':
      return 'var(--success-color)'
    case 'warning':
      return 'var(--warning-color)'
    case 'error':
      return 'var(--error-color)'
    default:
      return 'var(--error-color)'
  }
}

// 计算完成进度
const completionProgress = computed(() => {
  const total = stepsStatus.value.length
  const completed = stepsStatus.value.filter((step) => step.completed).length
  return Math.round((completed / total) * 100)
})

// 获取状态图标
const getStatusIcon = (status: string) => {
  switch (status) {
    case 'normal':
      return CheckmarkCircleOutline
    case 'warning':
      return AlertCircleOutline
    default:
      return CloseCircleOutline
  }
}

// 添加缺失的工具函数
const getCPUColor = (usage: number) => {
  if (usage >= 90) return 'var(--error-color)'
  if (usage >= 70) return 'var(--warning-color)'
  return 'var(--success-color)'
}

const getMemoryColor = (usage: number) => {
  if (usage >= 90) return 'var(--error-color)'
  if (usage >= 70) return 'var(--warning-color)'
  return 'var(--success-color)'
}

const getRailColor = () => {
  return 'rgba(0, 0, 0, 0.04)'
}

// 计算是否所有步骤都已完成
const isAllCompleted = computed(() => {
  return stepsStatus.value.every((step) => step.completed)
})

// 解析运行时间
const startTime = ref(Date.now() - appStore.systemStatus.uptime * 1000)
const currentTime = ref(Date.now())
const parseUptime = computed(() => {
  // 计算当前的运行时间（秒）
  const uptimeSeconds = Math.floor((currentTime.value - startTime.value) / 1000)
  const days = Math.floor(uptimeSeconds / 86400)
  const hours = Math.floor((uptimeSeconds % 86400) / 3600)
  const minutes = Math.floor((uptimeSeconds % 3600) / 60)
  const seconds = uptimeSeconds % 60
  if (days > 0) {
    return `${days}天${hours}小时${minutes}分钟${seconds}秒`
  } else if (hours > 0) {
    return `${hours}小时${minutes}分钟${seconds}秒`
  } else if (minutes > 0) {
    return `${minutes}分钟${seconds}秒`
  }
  return `${seconds}秒`
})

const timer = ref(0)
// 每秒更新当前时间
onMounted(() => {
  timer.value = setInterval(() => {
    currentTime.value = Date.now()
  }, 1000)
  // 就绪状态在进入页面时读一次。不做轮询：它是一份「你还缺什么」的清单，
  // 用户按提示改完配置后会自己回到这一页，而每秒刷新只会给一个诊断接口
  // 制造持续负载。
  void loadReadiness()
})

onUnmounted(() => {
  clearInterval(timer.value)
})

// 当系统状态更新时，重新计算启动时间
watch(
  () => appStore.systemStatus.uptime,
  (newUptime) => {
    startTime.value = Date.now() - newUptime * 1000
  }
)
</script>

<template>
  <div class="guide-container">
    <n-space vertical :size="16">
      <!-- 快速开始引导 -->
      <n-card v-if="showGuide" title="快速开始" :bordered="false" class="guide-card">
        <template #header-extra>
          <n-space align="center" :size="8">
            <n-popconfirm
              v-if="isAllCompleted"
              positive-text="重置"
              negative-text="取消"
              @positive-click="resetGuideProgress"
            >
              <template #trigger>
                <n-button secondary size="small">
                  <template #icon>
                    <n-icon><RefreshOutline /></n-icon>
                  </template>
                  重置进度
                </n-button>
              </template>
              重新开始快速引导？这不会更改已经保存的系统配置。
            </n-popconfirm>
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-button
                  circle
                  tertiary
                  type="error"
                  size="small"
                  aria-label="隐藏快速开始引导"
                  @click="hideGuide"
                >
                  <template #icon>
                    <n-icon><CloseCircleOutline /></n-icon>
                  </template>
                </n-button>
              </template>
              隐藏引导
            </n-tooltip>
          </n-space>
        </template>
        <n-steps :current="currentStep" class="guide-steps">
          <n-step
            v-for="(step, index) in stepsStatus"
            :key="index"
            :title="step.title"
            :description="step.description"
            :status="step.completed ? 'finish' : index === currentStep ? 'process' : 'wait'"
          >
            <template #default>
              <div class="step-content">
                <div class="step-description">{{ step.description }}</div>
                <!--
                  「已核实」只在就绪检查确认配好时出现，与「点过这一步」区分开。
                  没有它的话，一个只是被点过的步骤和一个真的配好了的步骤长得一样。
                -->
                <span
                  v-if="step.verified"
                  class="step-verified"
                  :data-test="`step-${step.key}-verified`"
                >
                  已核实
                </span>
                <n-button
                  text
                  type="primary"
                  @click="handleStepClick(index, step.path)"
                  :disabled="index !== currentStep && !step.completed"
                  v-if="index === currentStep"
                  class="step-button"
                >
                  立刻前往
                  <template #icon>
                    <n-icon>
                      <ArrowForwardOutline />
                    </n-icon>
                  </template>
                </n-button>
              </div>
            </template>
          </n-step>
        </n-steps>
      </n-card>

      <n-card
        v-else-if="shouldShowQuickStartRestore(showGuide)"
        :bordered="false"
        class="guide-restore-card"
      >
        <div class="guide-restore-content">
          <div>
            <div class="guide-restore-title">快速开始引导已隐藏</div>
            <div class="guide-restore-description">
              {{
                isAllCompleted
                  ? '引导已经完成。重新显示后可以回顾步骤或重置进度。'
                  : '重新显示后可继续完成插件、聊天平台、模型和工作流配置。'
              }}
            </div>
          </div>
          <n-button type="primary" secondary @click="restoreGuide">
            <template #icon>
              <n-icon><CheckmarkCircleOutline /></n-icon>
            </template>
            重新显示引导
          </n-button>
        </div>
      </n-card>

      <!-- 系统状态概览卡片 -->
      <!--
        就绪面板：新部署第一次打开时唯一能回答「我还缺什么」的地方。
        后端的 remediation 就是「下一步做什么」，这里原样呈现，不另写一套说法——
        两处说法一旦不一致，用户就得先判断该信哪个。
      -->
      <n-card
        v-if="readinessActions.length > 0"
        data-test="readiness-panel"
        :bordered="false"
        title="还需要处理"
        class="readiness-card"
      >
        <n-space vertical :size="10">
          <div
            v-for="item in readinessActions"
            :key="item.id"
            class="readiness-row"
            :data-test="`readiness-${item.id}`"
          >
            <n-tag :type="item.status === 'fail' ? 'error' : 'warning'" size="small">
              {{ item.status === 'fail' ? '阻塞' : '注意' }}
            </n-tag>
            <div class="readiness-text">
              <div class="readiness-title">{{ readinessLabel(item.id) }} · {{ item.summary }}</div>
              <!-- remediation 是这块面板存在的全部理由 -->
              <div class="readiness-remediation">{{ item.remediation }}</div>
            </div>
          </div>
        </n-space>
      </n-card>

      <n-card
        v-else-if="readiness && !readinessUnavailable"
        data-test="readiness-all-clear"
        :bordered="false"
        class="readiness-card"
      >
        <!-- 全绿时不逐项列「无需处理」：那是噪声，不是信息。 -->
        <n-alert type="success">所有就绪检查都已通过。</n-alert>
      </n-card>

      <n-card
        v-else-if="readinessUnavailable"
        data-test="readiness-unknown"
        :bordered="false"
        class="readiness-card"
      >
        <!--
          「读不到」与「没就绪」是两件事。把前者显示成后者会让人去修一个
          不存在的问题，而真正的问题只是这一个诊断接口没响应。
        -->
        <n-alert type="warning">
          暂时读不到自检结果（诊断接口未响应），下面的步骤只反映本机的点击记录。
          这不代表部署有问题，稍后刷新即可重试。
        </n-alert>
      </n-card>

      <n-card :bordered="false" class="status-overview-card">
        <n-grid
          cols="1 s:2 m:4"
          :x-gap="16"
          :y-gap="16"
          responsive="screen"
          :item-responsive="true"
        >
          <!-- 运行时长 -->
          <n-gi>
            <div class="status-item">
              <div class="status-icon uptime">
                <n-icon size="24">
                  <TimeOutline />
                </n-icon>
              </div>
              <div class="status-info">
                <div class="status-label">运行时长</div>
                <div class="status-value">{{ parseUptime }}</div>
              </div>
            </div>
          </n-gi>

          <!-- 已接入 IM -->
          <n-gi>
            <div class="status-item">
              <div class="status-icon chat">
                <n-icon size="24">
                  <ChatbubblesOutline />
                </n-icon>
              </div>
              <div class="status-info">
                <div class="status-label">已接入 IM</div>
                <div class="status-value">{{ appStore.systemStatus.activeAdapters }}</div>
              </div>
            </div>
          </n-gi>

          <!-- 已接入 LLM -->
          <n-gi>
            <div class="status-item">
              <div class="status-icon brain">
                <n-icon size="24">
                  <AppsOutline />
                </n-icon>
              </div>
              <div class="status-info">
                <div class="status-label">已接入 LLM</div>
                <div class="status-value">{{ appStore.systemStatus.activeBackends }}</div>
              </div>
            </div>
          </n-gi>

          <!-- 已安装插件 -->
          <n-gi>
            <div class="status-item">
              <div class="status-icon plugin">
                <n-icon size="24">
                  <ExtensionPuzzleOutline />
                </n-icon>
              </div>
              <div class="status-info">
                <div class="status-label">已安装插件</div>
                <div class="status-value">{{ appStore.systemStatus.loadedPlugins }}</div>
              </div>
            </div>
          </n-gi>
        </n-grid>
      </n-card>

      <!-- 系统负载和系统信息 -->
      <n-grid
        cols="1 s:1 m:2 l:2"
        :x-gap="16"
        :y-gap="16"
        responsive="screen"
        :item-responsive="true"
      >
        <!-- 系统负载卡片 -->
        <n-gi>
          <n-card :bordered="false" class="system-load-card" title="系统负载">
            <div class="load-container">
              <!-- CPU 使用率 -->
              <div class="load-item">
                <div class="load-info">
                  <div class="load-title">
                    <n-icon size="18"><HardwareChipOutline /></n-icon>
                    <span>CPU 使用率</span>
                  </div>
                  <div class="load-value">{{ Math.round(appStore.systemStatus.cpuUsage) }}%</div>
                  <div class="load-progress">
                    <n-progress
                      type="line"
                      :percentage="Math.round(appStore.systemStatus.cpuUsage)"
                      :color="getCPUColor(appStore.systemStatus.cpuUsage)"
                      :rail-color="getRailColor()"
                      :height="8"
                      border-radius="var(--radius-pill)"
                      :show-indicator="false"
                      class="resource-progress"
                    />
                  </div>
                </div>
              </div>

              <n-divider vertical />

              <!-- 内存使用率 -->
              <div class="load-item">
                <div class="load-info">
                  <div class="load-title">
                    <n-icon size="18"><ServerOutline /></n-icon>
                    <span>内存可用率</span>
                  </div>
                  <div class="load-value">
                    {{ Math.round(appStore.systemStatus.memoryUsage.percent * 100) }}%
                  </div>
                  <div class="load-progress">
                    <n-progress
                      type="line"
                      :percentage="Math.round(appStore.systemStatus.memoryUsage.percent * 100)"
                      :color="getMemoryColor(appStore.systemStatus.memoryUsage.percent * 100)"
                      :rail-color="getRailColor()"
                      :height="8"
                      border-radius="var(--radius-pill)"
                      :show-indicator="false"
                      class="resource-progress"
                    />
                  </div>
                  <div class="load-detail">
                    <div>
                      Kirara 占用: {{ appStore.systemStatus?.memoryUsage?.used?.toFixed(2) }}MB
                    </div>
                    <div>
                      系统可用: {{ appStore.systemStatus?.memoryUsage?.free?.toFixed(2) }}MB
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </n-card>
        </n-gi>

        <!-- 系统信息卡片 -->
        <n-gi>
          <n-card :bordered="false" class="system-info-card" title="系统信息">
            <n-grid
              cols="1 s:2 m:2 l:2"
              :x-gap="16"
              :y-gap="16"
              responsive="screen"
              :item-responsive="true"
            >
              <n-gi>
                <div class="info-item">
                  <div class="info-label">Kirara AI</div>
                  <div class="info-value">{{ appStore.systemStatus.version }}</div>
                </div>
              </n-gi>
              <n-gi>
                <div class="info-item">
                  <div class="info-label">操作系统</div>
                  <div class="info-value">{{ appStore.systemStatus.platform || '-' }}</div>
                </div>
              </n-gi>
              <n-gi>
                <div class="info-item">
                  <div class="info-label">CPU 型号</div>
                  <div class="info-value">{{ appStore.systemStatus.cpuInfo || '-' }}</div>
                </div>
              </n-gi>
              <n-gi>
                <div class="info-item">
                  <div class="info-label">Python</div>
                  <div class="info-value">{{ appStore.systemStatus.pythonVersion || '-' }}</div>
                </div>
              </n-gi>
            </n-grid>
          </n-card>
        </n-gi>
      </n-grid>

      <!--
        LLM 统计概览。
        完整的统计页在「系统记录 → 使用统计」（/tracing/statistics），
        那里带 Provider / 模型 / 时间范围 / 时区筛选与导出。这里只做一个
        无筛选的概览入口，避免首页承担对账职责。
      -->
      <div class="llm-stats-container">
        <div class="llm-stats-header">
          <div class="llm-stats-title">LLM 统计</div>
          <n-button
            text
            type="primary"
            data-test="open-usage-statistics"
            @click="router.push('/tracing/statistics')"
          >
            打开完整统计 →
          </n-button>
        </div>
        <LLMStatistics />
      </div>
    </n-space>
  </div>
</template>

<style scoped>
.guide-container {
  padding: 24px;
  min-height: 100vh;
  background: linear-gradient(
    135deg,
    var(--bg-color) 0%,
    rgba(var(--primary-color-rgb), 0.05) 100%
  );
}

.guide-card,
.guide-restore-card,
.status-overview-card,
.system-load-card,
.system-info-card {
  background: rgba(var(--card-bg-color-rgb), 0.8);
  backdrop-filter: blur(20px);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.05);
  overflow: hidden;
  border: 1px solid rgba(var(--primary-color-rgb), 0.1);
  transition: all 0.3s ease;
}

.guide-card:hover,
.guide-restore-card:hover,
.status-overview-card:hover,
.system-load-card:hover,
.system-info-card:hover {
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.guide-restore-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 4px 0;
}

.guide-restore-title {
  color: var(--text-color);
  font-size: 1rem;
  font-weight: 600;
  line-height: 1.5;
}

.guide-restore-description {
  max-width: 720px;
  margin-top: 4px;
  color: var(--text-color-secondary);
  line-height: 1.5;
}

@media (max-width: 768px) {
  .guide-container {
    padding: 16px;
  }

  .guide-restore-content {
    align-items: flex-start;
    flex-direction: column;
  }

  .guide-restore-content :deep(.n-button) {
    align-self: flex-start;
  }
}

.guide-steps {
  margin: 16px 0;
  padding: 0 12px;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 8px 0;
}

.step-description {
  color: var(--text-color-secondary);
  font-size: 0.9rem;
  line-height: 1.5;
}

.step-button {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: rgba(var(--primary-color-rgb), 0.1);
  color: var(--primary-color);
  transition: all 0.3s ease;
}

.step-button:hover {
  background: rgba(var(--primary-color-rgb), 0.15);
}

:deep(.n-step.n-step--finish) {
  .n-step-indicator {
    background: linear-gradient(
      135deg,
      var(--success-color) 0%,
      rgba(var(--success-color-rgb), 0.8) 100%
    );
    border: none;
    box-shadow: 0 4px 12px rgba(var(--success-color-rgb), 0.2);
  }
}

:deep(.n-step.n-step--process) {
  .n-step-indicator {
    background: linear-gradient(
      135deg,
      var(--primary-color) 0%,
      rgba(var(--primary-color-rgb), 0.8) 100%
    );
    border: none;
    box-shadow: 0 4px 12px rgba(var(--primary-color-rgb), 0.2);
  }
}

:deep(.n-step.n-step--wait) {
  opacity: 0.5;
}

/* 状态概览卡片样式 */
.status-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  border-radius: var(--radius-md);
  background: rgba(var(--primary-color-rgb), 0.03);
  transition: all 0.3s ease;
}

.status-item:hover {
  background: rgba(var(--primary-color-rgb), 0.06);
  transform: translateY(-2px);
}

.status-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  /* 图标底板嵌在 .status-item（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  background: linear-gradient(
    135deg,
    rgba(var(--primary-color-rgb), 0.1) 0%,
    rgba(var(--primary-color-rgb), 0.2) 100%
  );
  color: var(--primary-color);
}

.status-icon.uptime {
  background: linear-gradient(
    135deg,
    rgba(var(--primary-color-rgb), 0.1) 0%,
    rgba(var(--primary-color-rgb), 0.2) 100%
  );
  color: var(--primary-color);
}

.status-icon.chat {
  background: linear-gradient(
    135deg,
    rgba(var(--info-color-rgb), 0.1) 0%,
    rgba(var(--info-color-rgb), 0.2) 100%
  );
  color: var(--info-color);
}

.status-icon.brain {
  background: linear-gradient(
    135deg,
    rgba(var(--success-color-rgb), 0.1) 0%,
    rgba(var(--success-color-rgb), 0.2) 100%
  );
  color: var(--success-color);
}

.status-icon.plugin {
  background: linear-gradient(
    135deg,
    rgba(var(--warning-color-rgb), 0.1) 0%,
    rgba(var(--warning-color-rgb), 0.2) 100%
  );
  color: var(--warning-color);
}

.status-info {
  flex: 1;
}

.status-label {
  font-size: 0.9rem;
  color: var(--text-color-secondary);
  margin-bottom: 4px;
}

.status-value {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-color);
}

/* 系统负载卡片样式 */
.load-container {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 16px 0;
}

.load-item {
  display: flex;
  flex-direction: column;
  flex: 1;
  padding: 0 16px;
}

.load-progress {
  margin-top: 12px;
  width: 100%;
}

.load-info {
  width: 100%;
}

.load-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-color-secondary);
  font-size: 0.9rem;
  margin-bottom: 8px;
}

.load-value {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-color);
  margin-bottom: 4px;
}

.load-detail {
  font-size: 0.85rem;
  color: var(--text-color-secondary);
  margin-bottom: 8px;
  display: flex;
  justify-content: space-between;
}

/* 系统信息卡片样式 */
.info-item {
  padding: 16px;
  border-radius: var(--radius-md);
  background: rgba(var(--primary-color-rgb), 0.03);
  transition: all 0.3s ease;
}

.info-item:hover {
  background: rgba(var(--primary-color-rgb), 0.06);
}

.info-label {
  font-size: 0.9rem;
  color: var(--text-color-secondary);
  margin-bottom: 8px;
}

.info-value {
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-color);
  word-break: break-word;
}

/* LLM 统计样式 */
/*
  标题与「打开完整统计」并排：标题原先自带 margin-bottom，
  改为由容器统一控制间距，避免两侧基线不齐。
*/
.llm-stats-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.llm-stats-header .llm-stats-title {
  margin-bottom: 0;
}

.llm-stats-title {
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--text-color);
  margin-bottom: 16px;
}
</style>
