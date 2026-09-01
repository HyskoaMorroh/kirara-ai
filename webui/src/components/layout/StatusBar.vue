<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { NSpace, NText, NBadge, NTooltip, NButton, NIcon } from 'naive-ui'
import { SunnyOutline, MoonOutline } from '@vicons/ionicons5'
import { useAppStore } from '@/stores/app'
import { useThemeStore } from '@/stores/theme'
import { useUpdateViewModel } from '@/views/system/update.vm'
import UpdateChecker from '@/components/UpdateChecker.vue'
import { http } from '@/utils/http'

/**
 * 后端 `/system/status` 真正发出的形状,全字段 snake_case。
 *
 * 不能借用 store 里的 `SystemStatus`：那个是转换之后的 camelCase 内部形状,
 * 拿它标注响应会让下面每一处 `data.status.xxx` 都落在类型之外。
 */
interface SystemStatusPayload {
  version: string
  uptime: number
  active_adapters: number
  active_backends: number
  loaded_plugins: number
  workflow_count: number
  memory_usage: {
    percent: number
    total: number
    used: number
    free: number
  }
  cpu_usage: number
  cpu_info: string
  python_version: string
  platform: string
  has_proxy: boolean
}

const updateCheckerRef = ref<InstanceType<typeof UpdateChecker> | null>(null)
const appStore = useAppStore()
const themeStore = useThemeStore()
// 连接状态
const connecting = ref(false)

// 从环境变量获取版本号
const webUIVersion = import.meta.env.VITE_APP_VERSION || 'unknown'

const fetchStatus = () => {
  http
    .get<{ status: SystemStatusPayload }>('/system/status')
    .then((data) => {
      connecting.value = false
      appStore.updateSystemStatus({
        status: 'normal',
        apiConnected: true,
        memoryUsage: {
          percent: data.status.memory_usage.percent,
          total: data.status.memory_usage.total,
          used: data.status.memory_usage.used,
          free: data.status.memory_usage.free
        },
        cpuUsage: data.status.cpu_usage,
        uptime: data.status.uptime,
        activeAdapters: data.status.active_adapters,
        activeBackends: data.status.active_backends,
        loadedPlugins: data.status.loaded_plugins,
        workflowCount: data.status.workflow_count,
        version: data.status.version,
        platform: data.status.platform,
        cpuInfo: data.status.cpu_info,
        pythonVersion: data.status.python_version,
        hasProxy: data.status.has_proxy
      })
    })
    .catch((error) => {
      console.error('获取系统状态失败:', error)
      connecting.value = false
      appStore.updateSystemStatus({
        status: 'error',
        apiConnected: false,
        memoryUsage: {
          percent: 0,
          total: 0,
          used: 0,
          free: 0
        },
        cpuUsage: 0,
        uptime: 0,
        activeAdapters: 0,
        activeBackends: 0,
        loadedPlugins: 0,
        workflowCount: 0,
        version: 'unknown',
        platform: 'unknown',
        cpuInfo: 'unknown',
        pythonVersion: 'unknown',
        hasProxy: false
      })
    })
}
// 模拟状态更新
let timer: number

onMounted(() => {
  updateCheckerRef.value?.checkUpdate()
  appStore.updateSystemStatus({
    status: 'warning',
    apiConnected: false,
    memoryUsage: {
      percent: 0,
      total: 0,
      used: 0,
      free: 0
    },
    cpuUsage: 0,
    uptime: 0,
    activeAdapters: 0,
    activeBackends: 0,
    loadedPlugins: 0,
    workflowCount: 0,
    version: 'unknown',
    platform: 'unknown',
    cpuInfo: 'unknown',
    pythonVersion: 'unknown',
    hasProxy: false
  })
  connecting.value = true
  timer = setInterval(() => {
    fetchStatus()
  }, 10000)
  fetchStatus()
})

onUnmounted(() => {
  clearInterval(timer)
})
</script>

<template>
  <div class="status-bar-content">
    <!-- 更新检查组件 -->
    <update-checker ref="updateCheckerRef" />

    <!-- 桌面版布局 -->
    <n-space align="center" :size="20" class="desktop-view">
      <n-space align="center" :size="4">
        <n-badge
          dot
          :type="
            connecting ? 'warning' : appStore.systemStatus.status === 'normal' ? 'success' : 'error'
          "
        />
        <n-text>
          系统状态:
          <span v-if="connecting">连接中...</span>
          <span v-else>
            {{ appStore.systemStatus.status === 'normal' ? '正常' : '异常' }}
          </span>
        </n-text>
      </n-space>

      <n-space align="center" :size="4">
        <n-badge dot :type="appStore.systemStatus.apiConnected ? 'success' : 'error'" />
        <n-text>API: {{ appStore.systemStatus.apiConnected ? '已连接' : '未连接' }}</n-text>
      </n-space>

      <n-space align="center">
        <n-text> WebUI 版本: {{ webUIVersion }} </n-text>
        <n-text v-if="appStore.systemStatus.status === 'normal'">
          后端版本: {{ appStore.systemStatus.version }}
        </n-text>
        <n-text
          @click="updateCheckerRef!!.showUpdateModal = true"
          v-if="
            appStore.updateInfo?.backend_update_available ||
            appStore.updateInfo?.webui_update_available
          "
          type="success"
          class="version-text"
          style="margin-left: 4px"
        >
          有更新
        </n-text>
      </n-space>

      <n-space v-if="appStore.systemStatus.status === 'normal'">
        <n-text>内存使用: {{ appStore.systemStatus.memoryUsage.used.toFixed(2) }} MB</n-text>
        <n-text>CPU: {{ appStore.systemStatus.cpuUsage }}%</n-text>
        <n-text>IM: {{ appStore.systemStatus.activeAdapters }}</n-text>
        <n-text>LLM: {{ appStore.systemStatus.activeBackends }}</n-text>
        <n-text>插件: {{ appStore.systemStatus.loadedPlugins }}</n-text>
        <n-text>工作流: {{ appStore.systemStatus.workflowCount }}</n-text>
      </n-space>
    </n-space>

    <!-- 主题快速切换，工作流画布等全屏页面也能直接切换明暗 -->
    <n-tooltip placement="top" trigger="hover">
      <template #trigger>
        <n-button
          quaternary
          size="tiny"
          class="theme-toggle"
          :aria-label="themeStore.isDark ? '切换到浅色主题' : '切换到深色主题'"
          @click="themeStore.toggleScheme"
        >
          <template #icon>
            <n-icon aria-hidden="true">
              <MoonOutline v-if="themeStore.isDark" />
              <SunnyOutline v-else />
            </n-icon>
          </template>
        </n-button>
      </template>
      <span>{{ themeStore.isDark ? '切换到浅色主题' : '切换到深色主题' }}</span>
    </n-tooltip>

    <!-- 移动版布局 - 只显示关键信息 -->
    <div class="mobile-view" role="status" aria-label="系统状态">
      <n-space align="center" :size="8">
        <n-space align="center" :size="4">
          <n-badge dot :type="appStore.systemStatus.apiConnected ? 'success' : 'error'" />
          <n-text>API: {{ appStore.systemStatus.apiConnected ? '已连接' : '未连接' }}</n-text>
        </n-space>
        <n-space align="center">
          <n-text> 版本: {{ webUIVersion }} </n-text>
          <n-text
            @click="updateCheckerRef!!.showUpdateModal = true"
            v-if="
              appStore.updateInfo?.backend_update_available ||
              appStore.updateInfo?.webui_update_available
            "
            type="success"
            class="version-text"
            style="margin-left: 4px"
          >
            有更新
          </n-text>
        </n-space>
      </n-space>
    </div>
  </div>
</template>

<style scoped>
.status-bar-content {
  height: 100%;
  display: flex;
  align-items: center;
}

.theme-toggle {
  margin-left: auto;
  flex-shrink: 0;
}

.version-text {
  cursor: pointer;
  animation: blink 3s infinite;
}

/* 可点击的「有更新」文字需要可见的键盘聚焦环 */
.version-text:focus-visible {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 2px;
  /* 行内文字的聚焦环属于内联小件，用 xs 档 */
  border-radius: var(--radius-xs);
}

/* 尊重系统的「减少动态效果」偏好，闪烁对前庭敏感用户不友好 */
@media (prefers-reduced-motion: reduce) {
  .version-text {
    animation: none;
  }
}

@keyframes blink {
  0% {
    opacity: 1;
  }

  55% {
    opacity: 0;
  }

  75% {
    opacity: 1;
  }
}

/* 响应式布局样式 */
.mobile-view {
  display: none;
}

/* 在小屏幕设备上显示移动版布局，隐藏桌面版布局 */
@media (max-width: 768px) {
  .desktop-view {
    display: none !important;
  }

  /* 桌面区隐藏后主题按钮会变成第一个可见子元素，margin-left:auto 反而把它顶到
     移动版状态左侧。这里用 order 把移动版信息排在前、按钮排在最右 */
  .mobile-view {
    display: flex;
    order: 1;
    margin-right: auto;
    min-width: 0;
  }

  .theme-toggle {
    order: 2;
    margin-left: var(--space-2, 8px);
  }
}
</style>
