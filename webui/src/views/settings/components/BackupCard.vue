<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NDataTable, NDescriptions, NDescriptionsItem, NIcon, NSpace, NSpin, NText, NUpload, useDialog, useMessage, type DataTableColumns, type UploadFileInfo } from 'naive-ui'
import { CloudDownloadOutline, RefreshOutline } from '@vicons/ionicons5'
import { http } from '@/utils/http'

interface BackupInspection { format_version: number; created_at: string; application_version: string; components: string[]; file_count: number; uncompressed_size: number }
interface RollbackBackup { name: string; size: number; modified_at: number }

const message = useMessage()
const dialog = useDialog()
const selectedFile = ref<File | null>(null)
const inspection = ref<BackupInspection | null>(null)
const rollbacks = ref<RollbackBackup[]>([])
const loading = ref(false)
const exporting = ref(false)
const refreshing = ref(false)
const canRestore = computed(() => Boolean(selectedFile.value && inspection.value && !loading.value))

const formatBytes = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(2)} MB`
const formatDate = (seconds: number) => new Date(seconds * 1000).toLocaleString()
const getAuthHeaders = () => { const token = http.getAuthToken(); return token ? { Authorization: `Bearer ${token}` } : {} }
const readError = async (response: Response) => { const payload = await response.json().catch(() => null); return payload?.error || payload?.message || `HTTP ${response.status}` }
const saveFile = (blob: Blob, filename: string) => { const anchor = document.createElement('a'); const url = URL.createObjectURL(blob); anchor.href = url; anchor.download = filename; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0) }

const exportBackup = async () => {
  exporting.value = true
  try {
    const response = await fetch(http.url('/system/backups/export'), { headers: getAuthHeaders() })
    if (!response.ok) throw new Error(await readError(response))
    const filename = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/i)?.[1] || 'kirara-export.kirara-backup.zip'
    saveFile(await response.blob(), filename)
    message.success('备份已开始下载，请保存到可信位置。')
  } catch (error: any) { message.error(`导出备份失败：${error.message || error}`) } finally { exporting.value = false }
}

const updateFileList = (files: UploadFileInfo[]) => { selectedFile.value = (files[0]?.file as File | undefined) || null; inspection.value = null }
const sendBackup = async (path: '/system/backups/inspect' | '/system/backups/import') => {
  if (!selectedFile.value) throw new Error('请先选择备份文件')
  const body = new FormData()
  body.append('backup', selectedFile.value)
  const response = await fetch(http.url(path), { method: 'POST', headers: getAuthHeaders(), body })
  if (!response.ok) throw new Error(await readError(response))
  return response.json()
}

const inspectBackup = async () => {
  loading.value = true
  try { inspection.value = await sendBackup('/system/backups/inspect'); message.success('备份包校验通过，可以恢复。') }
  catch (error: any) { inspection.value = null; message.error(`校验备份失败：${error.message || error}`) }
  finally { loading.value = false }
}

const restoreBackup = () => dialog.warning({
  title: '确认恢复数据',
  content: '恢复会覆盖当前实例数据。系统会先创建回滚包，但仍建议你已导出一份备份。恢复成功后必须重启服务。',
  positiveText: '确认恢复', negativeText: '取消',
  onPositiveClick: async () => {
    loading.value = true
    try { const result = await sendBackup('/system/backups/import'); message.success(`恢复完成，已创建回滚包：${result.rollback_backup}。请立即重启服务。`); inspection.value = null; await loadRollbacks() }
    catch (error: any) { message.error(`恢复备份失败：${error.message || error}`) }
    finally { loading.value = false }
  }
})

const loadRollbacks = async () => {
  refreshing.value = true
  try { rollbacks.value = (await http.get<{ rollbacks: RollbackBackup[] }>('/system/backups/rollbacks')).rollbacks || [] }
  catch (error: any) { message.error(`读取回滚包失败：${error.message || error}`) }
  finally { refreshing.value = false }
}

const downloadRollback = async (rollback: RollbackBackup) => {
  try {
    const response = await fetch(http.url(`/system/backups/rollbacks/${encodeURIComponent(rollback.name)}`), { headers: getAuthHeaders() })
    if (!response.ok) throw new Error(await readError(response))
    saveFile(await response.blob(), rollback.name)
  } catch (error: any) { message.error(`下载回滚包失败：${error.message || error}`) }
}

const rollbackColumns: DataTableColumns<RollbackBackup> = [
  { title: '回滚包', key: 'name', ellipsis: { tooltip: true } },
  { title: '大小', key: 'size', render: (row) => formatBytes(row.size) },
  { title: '创建时间', key: 'modified_at', render: (row) => formatDate(row.modified_at) },
  { title: '操作', key: 'actions', render: (row) => h(NButton, { text: true, type: 'primary', onClick: () => downloadRollback(row) }, { default: () => '下载' }) }
]

onMounted(loadRollbacks)
</script>

<template>
  <n-space vertical :size="24">
    <n-card title="完整备份与恢复" class="settings-card">
      <n-alert type="warning" :show-icon="true" style="margin-bottom: 16px">备份包包含模型 API Key、机器人令牌、Web 密钥和密码哈希。请仅保存到可信位置，勿提交到 GitHub、Docker 镜像或发送给他人。</n-alert>
      <n-space vertical :size="16">
        <n-text>导出会生成当前实例的完整可迁移备份，包含配置、工作流、规则、数据库、记忆、媒体、插件和字体。导入前会校验包结构和文件完整性，写入前会自动创建回滚包。</n-text>
        <n-button type="primary" :loading="exporting" @click="exportBackup"><template #icon><n-icon><cloud-download-outline /></n-icon></template>导出完整备份</n-button>
      </n-space>
    </n-card>

    <n-card title="导入与恢复" class="settings-card">
      <n-spin :show="loading">
        <n-space vertical :size="16">
          <n-upload accept=".zip,.kirara-backup.zip" :default-upload="false" :max="1" @update:file-list="updateFileList"><n-button>选择备份包</n-button></n-upload>
          <n-space><n-button :disabled="!selectedFile || loading" @click="inspectBackup">检查备份包</n-button><n-button type="error" :disabled="!canRestore" @click="restoreBackup">恢复数据</n-button></n-space>
          <n-alert v-if="selectedFile && !inspection" type="info" :show-icon="true">请先检查备份包，校验通过后才可执行恢复。</n-alert>
          <n-descriptions v-if="inspection" bordered label-placement="left" :column="1">
            <n-descriptions-item label="格式版本">{{ inspection.format_version }}</n-descriptions-item>
            <n-descriptions-item label="来源版本">{{ inspection.application_version }}</n-descriptions-item>
            <n-descriptions-item label="创建时间">{{ inspection.created_at }}</n-descriptions-item>
            <n-descriptions-item label="包含组件">{{ inspection.components.join('、') }}</n-descriptions-item>
            <n-descriptions-item label="文件数量与大小">{{ inspection.file_count }} 个文件，{{ formatBytes(inspection.uncompressed_size) }}</n-descriptions-item>
          </n-descriptions>
          <n-alert type="warning" :show-icon="true">恢复成功后必须重启服务，配置、工作流与 MCP 连接才会重新加载。</n-alert>
        </n-space>
      </n-spin>
    </n-card>

    <n-card title="自动回滚包" class="settings-card">
      <template #header-extra><n-button text :loading="refreshing" @click="loadRollbacks"><template #icon><n-icon><refresh-outline /></n-icon></template>刷新</n-button></template>
      <n-data-table :columns="rollbackColumns" :data="rollbacks" :pagination="false" :bordered="false" :single-line="false" />
    </n-card>
  </n-space>
</template>

<style scoped>
.settings-card { max-width: 800px; margin: 0 auto; }
</style>
