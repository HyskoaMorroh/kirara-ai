<script setup lang="ts">
import { NCard, NSpace, NText, NButton } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { useUpdateViewModel } from '@/views/system/update.vm'
import { version } from '@/utils/version'

const appStore = useAppStore()
const { checkUpdate } = useUpdateViewModel()

// 从环境变量获取版本号
const webUIVersion = import.meta.env.VITE_APP_VERSION || 'unknown'

const handleCheckUpdate = () => {
  // 这颗按钮是用户主动点的，所以传 `true`：后端把不带 `manual` 的调用当作
  // 自动检查，在「禁用自动检查更新」打开时直接返回不外呼，会把手动点击一起挡掉。
  checkUpdate(true)
}
</script>

<template>
  <n-card title="版本信息" :bordered="false" class="version-card">
    <n-space vertical>
      <div class="version-row">
        <span class="version-label">WebUI 版本：</span>
        <n-text>{{ webUIVersion }}</n-text>
        <n-text
          v-if="appStore.updateInfo?.webui_update_available"
          type="success"
          style="margin-left: 8px"
        >
          (有更新: {{ appStore.updateInfo?.latest_webui_version }})
        </n-text>
      </div>

      <div class="version-row">
        <span class="version-label">后端版本：</span>
        <n-text>{{ appStore.systemStatus.version }}</n-text>
        <n-text
          v-if="appStore.updateInfo?.backend_update_available"
          type="success"
          style="margin-left: 8px"
        >
          (有更新: {{ appStore.updateInfo?.latest_backend_version }})
        </n-text>
      </div>

      <div style="text-align: right; margin-top: 12px">
        <n-button type="primary" size="small" @click="handleCheckUpdate"> 检查更新 </n-button>
      </div>
    </n-space>
  </n-card>
</template>

<style scoped>
.version-card {
  background: var(--panel-bg-color, rgba(255, 255, 255, 0.8));
  backdrop-filter: blur(10px);
  border-radius: var(--radius-lg);
}

.version-row {
  display: flex;
  align-items: center;
  margin-bottom: 8px;
}

.version-label {
  width: 100px;
  color: var(--text-color-secondary, #666);
}
</style>
