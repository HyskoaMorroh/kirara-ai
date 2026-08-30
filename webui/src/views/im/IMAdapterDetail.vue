<script setup lang="ts">
import { imApi } from '@/api/im'
import type { IMAdapter, IMAdapterInfo, UserProfile } from '@/api/im'
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard,
  NSpace,
  NButton,
  NForm,
  NFormItem,
  NInput,
  NSpin,
  useMessage,
  NIcon,
  NTag,
  NDivider,
  NSwitch,
  NEmpty,
  NPopconfirm,
  NAlert,
  NText,
  NThing,
  NAvatar,
  NScrollbar
} from 'naive-ui'
import DynamicConfigForm from '@/components/form/DynamicConfigForm.vue'
import { AddOutline, ArrowBackOutline, SaveOutline } from '@vicons/ionicons5'
import type { FormInst } from 'naive-ui'
import MarkdownIt from 'markdown-it'
const route = useRoute()
const router = useRouter()
const message = useMessage()

// 获取路由参数中的适配器类型
const adapterType = computed(() => route.params.adapterType as string)
const loading = ref(false)
const processing = ref(false)
const configSchema = ref<any>(null)
const adapters = ref<IMAdapter[]>([])
const currentAdapter = ref<IMAdapter | null>(null)
const formRef = ref<FormInst | null>(null)
const isEdit = ref<string | null>(null)

type StatusTagType = 'default' | 'success' | 'warning' | 'error'

/**
 * 断开原因码到可读文案。
 *
 * 后端只回固定原因码（不含凭据与账号信息），所以文案在前端组装；
 * 每条都直说「下一步该查什么」，因为这里就是用户排查 QQ 未连接时唯一能看到的信息。
 */
const DISCONNECT_REASON_TEXT: Record<string, string> = {
  access_token_missing: '上游未携带访问令牌',
  access_token_mismatch: '上游访问令牌与本适配器配置不一致',
  invalid_client_role: '上游握手缺少或使用了不支持的客户端角色',
  missing_self_id: '上游握手缺少账号标识',
  heartbeat_timeout: '曾经连上但心跳超时',
  upstream_lifecycle_disconnect: '上游主动上报断开',
  adapter_stopped: '适配器已停止',
  data_directory_unwritable: '持久化目录不可写：检查数据卷是否被只读重挂或磁盘写满'
}

const disconnectReasonText = (adapter: IMAdapter): string | null => {
  const reason = adapter.health?.last_disconnect_reason
  if (!reason) return null
  return DISCONNECT_REASON_TEXT[reason] || null
}

/**
 * 上游扫码登录状态的展示文案。
 *
 * 与连接状态严格分开：`waiting` 说的是「上游还没接进来」，扫码状态说的是
 * 「上游接进来了，但它自己还没登录 QQ」。两者的处置一个是查地址与 Token，
 * 一个是去扫码——放在同一个标签里会让用户查错方向。
 */
const QR_STATE_TEXT: Record<string, { label: string; type: StatusTagType }> = {
  pending: { label: '等待二维码', type: 'default' },
  waiting_scan: { label: '待扫码', type: 'warning' },
  scanned: { label: '已扫码待确认', type: 'info' },
  expired: { label: '二维码已过期', type: 'error' },
  succeeded: { label: 'QQ 已登录', type: 'success' },
  failed: { label: '登录失败', type: 'error' },
  unavailable: { label: '二维码暂不可用', type: 'default' },
  quick_login: { label: '免扫码登录', type: 'success' }
}

const qrLoginTag = (
  adapter: IMAdapter
): { label: string; type: StatusTagType; title: string } | null => {
  const qr = adapter.health?.qr_login
  // 未配置上游日志路径时后端返回 null，此时不该显示任何扫码信息——
  // 显示「未知」会让用户以为出了问题。
  if (!qr || qr.state === 'unknown') return null
  const preset = QR_STATE_TEXT[qr.state]
  if (!preset) return null

  // 剩余时间只在还能扫的时候有意义，且必须是「还剩多久」而不是绝对时刻：
  // 用户要判断的是「现在扫还来不来得及」。
  const remaining =
    qr.state === 'waiting_scan' && typeof qr.remaining_seconds === 'number'
      ? `（剩 ${Math.max(0, Math.round(qr.remaining_seconds))} 秒）`
      : ''

  const details: string[] = []
  if (qr.remediation) details.push(qr.remediation)
  if (qr.latest_qr_path) details.push(`最新二维码：${qr.latest_qr_path}`)
  if (qr.refresh_count > 0) details.push(`已刷新 ${qr.refresh_count} 次`)

  return {
    label: `${preset.label}${remaining}`,
    type: preset.type,
    title: details.join('\n')
  }
}

const adapterStatus = (
  adapter: IMAdapter
): { label: string; type: StatusTagType; className: string } => {
  if (!adapter.enable) return { label: '未启用', type: 'default', className: 'disabled' }
  if (!adapter.is_running) return { label: '已停止', type: 'warning', className: 'stopped' }

  switch (adapter.health?.status) {
    case 'connected': {
      const accounts = adapter.health.connected_account_count
      return {
        label: accounts > 0 ? `已连接 · ${accounts} 个账号` : '已连接',
        type: 'success',
        className: 'connected'
      }
    }
    case 'initializing':
      return { label: '正在启动', type: 'default', className: 'initializing' }
    case 'waiting':
      return { label: '等待连接', type: 'warning', className: 'waiting' }
    case 'credential_rejected':
      return { label: '凭据被拒', type: 'error', className: 'credential-rejected' }
    case 'upstream_refused':
      return { label: '握手被拒', type: 'error', className: 'upstream-refused' }
    // 链路是通的，坏的是磁盘。标签必须说「存储」而不是「断开」，
    // 否则操作者会去查网络与 Token，而要修的是数据卷。
    case 'storage_unavailable':
      return { label: '存储不可写', type: 'error', className: 'storage-unavailable' }
    case 'disconnected':
      return { label: '已断开', type: 'error', className: 'disconnected' }
    case 'stale':
      return { label: '心跳超时', type: 'error', className: 'stale' }
    default:
      return { label: '运行中', type: 'success', className: 'running' }
  }
}

const md = new MarkdownIt()
const defaultRender =
  md.renderer.rules.link_open ||
  function (tokens, idx, options, env, self) {
    return self.renderToken(tokens, idx, options)
  }

md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  // Add a new `target` attribute, or replace the value of the existing one.
  tokens[idx].attrSet('target', '_blank')

  // Pass the token to the default renderer.
  return defaultRender(tokens, idx, options, env, self)
}

const adapterInfo = ref<IMAdapterInfo | null>(null)

// 获取适配器配置模式
const fetchAdapterConfigSchema = async () => {
  try {
    loading.value = true
    const { configSchema: schema } = await imApi.getAdapterConfigSchema(adapterType.value)
    configSchema.value = schema
  } catch (error) {
    message.error('获取适配器配置模式失败: ' + error)
    console.error('获取适配器配置模式失败:', error)
  } finally {
    loading.value = false
  }
}

// 获取适配器列表
const fetchAdapters = async () => {
  try {
    loading.value = true
    const { adapters: adapterList } = await imApi.getAdapters()
    // 过滤出当前类型的适配器
    // bot_profile 的查询是异步过程，所以这里先备份当前 adapters 的 bot_profile 历史数据，等 adpters 查询完毕后恢复，最后再刷新真实的 bot_profile
    let cachedbot_profileMap = new Map<string, UserProfile | null>()
    if (adapters.value.length > 0) {
      adapters.value.forEach((a) => {
        cachedbot_profileMap.set(a.name, a.bot_profile)
      })
    }
    adapters.value = adapterList.filter((a) => a.adapter === adapterType.value)
    // 恢复 bot_profile
    if (adapters.value.length > 0) {
      adapters.value.forEach((a) => {
        a.bot_profile = cachedbot_profileMap.get(a.name) || null
      })
    }
    await Promise.all(
      adapters.value.map(async (adapter) => {
        try {
          const { adapter: adapterDetail } = await imApi.getAdapterDetail(adapter.name)
          adapter.bot_profile = adapterDetail.bot_profile
          adapter.health = adapterDetail.health
          adapter.is_running = adapterDetail.is_running
        } catch (error) {
          console.error(`获取适配器 ${adapter.name} 的连接详情失败:`, error)
        }
      })
    )
  } catch (error) {
    message.error('获取适配器列表失败: ' + error)
    console.error('获取适配器列表失败:', error)
  } finally {
    loading.value = false
  }
}

// 添加适配器
const addAdapter = async () => {
  isEdit.value = null
  await fetchAdapterConfigSchema()
  currentAdapter.value = {
    name: '',
    adapter: adapterType.value,
    config: {},
    is_running: false,
    enable: true,
    bot_profile: null,
    health: null
  }
}

// 编辑适配器
const editAdapter = (adapter: IMAdapter) => {
  isEdit.value = adapter.name
  currentAdapter.value = { ...adapter }
}

// 保存适配器
const saveAdapter = async () => {
  if (!currentAdapter.value) return

  try {
    processing.value = true
    try {
      const errors = await formRef.value?.validate()
      if (errors?.warnings?.length) return
    } catch (error) {
      message.error('保存适配器失败: 请检查输入内容')
      return
    }

    if (isEdit.value) {
      await imApi.updateAdapter(isEdit.value, currentAdapter.value)
      isEdit.value = currentAdapter.value.name
    } else {
      await imApi.createAdapter(currentAdapter.value)
      isEdit.value = currentAdapter.value.name
    }
    message.success('保存适配器成功')
  } catch (error) {
    message.error('保存适配器失败: ' + error)
    console.error('保存适配器失败:', error)
  } finally {
    processing.value = false
    await fetchAdapters()
  }
}

// 删除适配器
const deleteAdapter = async (adapterName: string) => {
  try {
    processing.value = true
    await imApi.deleteAdapter(adapterName)
    if (currentAdapter.value?.name === adapterName || isEdit.value === adapterName) {
      currentAdapter.value = null
      isEdit.value = null
    }
    message.success('删除适配器成功')
  } catch (error) {
    message.error('删除适配器失败: ' + error)
    console.error('删除适配器失败:', error)
  } finally {
    processing.value = false
    await fetchAdapters()
  }
}

// 返回列表页
const goBack = () => {
  router.push('/im')
}

// 表单规则
const formRules = {
  name: [
    { required: true, message: '请输入适配器名称', trigger: 'blur' },
    {
      validator: (rule: any, value: string) => {
        if (!currentAdapter.value) return true

        const exists = adapters.value.some((a) => a.name === value && a.name !== isEdit.value)

        if (exists) {
          return new Error('适配器名称已存在')
        }
        return true
      },
      trigger: 'blur'
    }
  ]
}

// 获取适配器信息
const fetchAdapterInfo = async () => {
  const { adapters } = await imApi.getAdapterTypes()
  if (adapters) {
    adapterInfo.value = adapters[adapterType.value] || null
  } else {
    adapterInfo.value = null
  }
}

onMounted(async () => {
  await fetchAdapterInfo()
  await fetchAdapterConfigSchema()
  await fetchAdapters()
})

defineExpose({
  fetchAdapters,
  currentAdapter,
  processing,
  formRef
})
</script>

<template>
  <div class="adapter-detail">
    <n-spin :show="loading || processing">
      <n-card class="adapter-card" style="min-height: var(--n-window-height)">
        <template #header>
          <div class="adapter-header">
            <div class="adapter-title">
              <n-button quaternary circle @click="goBack" class="back-button">
                <template #icon>
                  <n-icon>
                    <ArrowBackOutline />
                  </n-icon>
                </template>
              </n-button>
              <span class="title-text">{{ adapterInfo?.localized_name || adapterType }}</span>
            </div>
          </div>
        </template>

        <n-alert
          type="info"
          class="adapter-info"
          v-if="adapterInfo?.detail_info_markdown"
          :show-icon="false"
        >
          <div v-html="md.render(adapterInfo?.detail_info_markdown)" class="markdown-content" />
        </n-alert>

        <div class="adapter-content">
          <!-- 左侧配置列表 -->
          <div class="instances-panel">
            <div class="panel-header">
              <n-space justify="space-between" align="center">
                <h3 class="panel-title">实例列表</h3>
                <n-button
                  type="primary"
                  @click="addAdapter"
                  size="small"
                  v-if="adapters.length > 0"
                  class="add-button"
                >
                  <template #icon>
                    <n-icon>
                      <AddOutline />
                    </n-icon>
                  </template>
                  添加配置
                </n-button>
              </n-space>
            </div>

            <div class="instances-list">
              <n-scrollbar style="max-height: 600px">
                <n-empty v-if="adapters.length === 0" description="暂无配置" class="empty-state" />
                <n-card
                  v-for="adapter in adapters"
                  :key="adapter.name"
                  hoverable
                  @click="editAdapter(adapter)"
                  class="instance-card"
                  :class="{ active: currentAdapter?.name === adapter.name }"
                >
                  <n-thing
                    :title="adapter.name"
                    :description="adapter.bot_profile ? adapter.bot_profile.display_name : ''"
                    class="instance-thing"
                  >
                    <template #avatar>
                      <n-avatar
                        v-if="adapter.bot_profile && adapter.bot_profile?.avatar_url"
                        round
                        :src="adapter.bot_profile?.avatar_url"
                        class="avatar"
                      >
                      </n-avatar>
                      <n-avatar v-else round class="avatar default-avatar">
                        {{
                          (adapter.bot_profile ? adapter.bot_profile?.username : adapter.name)
                            .slice(0, 1)
                            .toUpperCase()
                        }}
                      </n-avatar>
                    </template>
                    <template #header-extra>
                      <n-space :size="6" align="center">
                        <n-tag
                          :type="adapterStatus(adapter).type"
                          :class="['status-tag', adapterStatus(adapter).className]"
                          :title="disconnectReasonText(adapter) || undefined"
                        >
                          {{ adapterStatus(adapter).label }}
                        </n-tag>
                        <!-- 上游 QQ 登录状态单独一枚标签：它与「适配器连接状态」
                             是两件事，合并显示会让「只差扫码」被误读成「连不上」。 -->
                        <n-tag
                          v-if="qrLoginTag(adapter)"
                          :type="qrLoginTag(adapter)!.type"
                          class="status-tag qr-login"
                          :title="qrLoginTag(adapter)!.title || undefined"
                        >
                          {{ qrLoginTag(adapter)!.label }}
                        </n-tag>
                      </n-space>
                    </template>
                    <template #description>
                      <!-- 原因码在这里落地成一行可读文案：QQ 未连接时，
                           这是用户不进服务器就能拿到的唯一线索。 -->
                      <span v-if="disconnectReasonText(adapter)" class="disconnect-reason">
                        {{ disconnectReasonText(adapter) }}
                      </span>
                      <!-- 扫码处置建议同样直接落地：告诉用户下一步做什么，
                           而不是让他自己去猜「待扫码」意味着要干什么。 -->
                      <span
                        v-if="qrLoginTag(adapter)?.title"
                        class="disconnect-reason qr-login-hint"
                      >
                        {{ adapter.health?.qr_login?.remediation }}
                      </span>
                    </template>
                    <template #action>
                      <n-space class="action-buttons">
                        <n-button
                          @click.stop="editAdapter(adapter)"
                          size="small"
                          class="edit-button"
                        >
                          编辑
                        </n-button>

                        <n-popconfirm
                          @positive-click="deleteAdapter(adapter.name)"
                          positive-text="确定"
                          negative-text="取消"
                          class="delete-confirm"
                        >
                          <template #trigger>
                            <n-button size="small" class="delete-button"> 删除 </n-button>
                          </template>
                          确定要删除配置吗？
                        </n-popconfirm>
                      </n-space>
                    </template>
                  </n-thing>
                </n-card>
              </n-scrollbar>
            </div>
          </div>

          <!-- 右侧配置表单 -->
          <div class="config-panel">
            <div class="panel-header">
              <n-space justify="space-between" align="center">
                <h3 class="panel-title">配置详情</h3>
                <n-button
                  type="primary"
                  size="small"
                  @click="saveAdapter"
                  :loading="processing"
                  v-if="currentAdapter"
                  class="save-button"
                >
                  <template #icon>
                    <n-icon>
                      <SaveOutline />
                    </n-icon>
                  </template>
                  保存配置
                </n-button>
              </n-space>
            </div>

            <div v-if="currentAdapter" class="config-form">
              <n-form
                ref="formRef"
                :model="currentAdapter"
                label-placement="left"
                label-width="150"
                :rules="formRules"
                class="form"
              >
                <n-form-item label="名称" path="name" class="form-item">
                  <n-input
                    v-if="currentAdapter"
                    v-model:value="currentAdapter!!.name"
                    placeholder="配置名称"
                    class="input"
                  />
                  <template #feedback>
                    <n-text depth="3" class="form-hint">用于区分不同的配置，必须唯一</n-text>
                  </template>
                </n-form-item>

                <n-form-item label="开启" class="form-item">
                  <n-switch
                    v-if="currentAdapter"
                    v-model:value="currentAdapter!!.enable"
                    class="switch"
                  />
                </n-form-item>

                <n-divider class="divider" />

                <div v-if="configSchema && currentAdapter" class="dynamic-config">
                  <dynamic-config-form :schema="configSchema" v-model="currentAdapter!!.config" />
                </div>
              </n-form>
            </div>

            <div v-else class="empty-config">
              <n-empty description="请选择或添加一个配置" class="empty-state">
                <template #extra v-if="adapters.length == 0">
                  <n-button type="primary" @click="addAdapter" class="add-button-large">
                    <template #icon>
                      <n-icon>
                        <AddOutline />
                      </n-icon>
                    </template>
                    添加配置
                  </n-button>
                </template>
              </n-empty>
            </div>
          </div>
        </div>
      </n-card>
    </n-spin>
  </div>
</template>

<style scoped>
.adapter-detail {
  padding: var(--n-padding-md);
  transition: all var(--transition-duration) var(--transition-timing-function);
  animation: fade-in 0.8s cubic-bezier(0.4, 0, 0.2, 1);
}

.adapter-card {
  border-radius: var(--radius-md);
  background-color: var(--card-bg-color);
  border: 1px solid var(--border-color);
  box-shadow: var(--box-shadow);
  overflow: hidden;
}

.adapter-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.5rem 0;
}

.adapter-title {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.title-text {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--text-color);
}

.back-button {
  transition: transform 0.3s ease;
}

.back-button:hover {
  transform: translateX(-3px);
}

.adapter-info {
  margin-bottom: 1.5rem;
  /* 该说明块位于 .adapter-card（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--info-color), 0.2);
  background-color: rgba(var(--info-color), 0.05);
}

.markdown-content {
  font-size: 0.95rem;
  line-height: 1.6;
}

.markdown-content a {
  color: var(--primary-color);
  text-decoration: none;
  transition: all 0.3s ease;
  border-bottom: 1px dashed var(--primary-color);
}

.markdown-content a:hover {
  color: var(--primary-color-hover);
  border-bottom: 1px solid var(--primary-color-hover);
}

.adapter-content {
  display: flex;
  gap: 2rem;
  margin-top: 1.5rem;
  height: 100%;
}

.instances-panel {
  flex: 0 0 400px;
  border-right: 1px solid var(--border-color);
  padding-right: 1.5rem;
}

.config-panel {
  flex: 1;
  padding-left: 1rem;
}

.panel-header {
  margin-bottom: 1.5rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border-color);
}

.panel-title {
  margin: 0;
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--text-color);
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.instances-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.instance-card.active {
  border: 1px solid var(--primary-color);
  background-color: rgba(var(--primary-color), 0.05);
}

.instance-thing {
  padding: 0.5rem 0;
}

.avatar {
  transition: transform 0.3s ease;
}

.default-avatar {
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-color-hover) 100%);
  color: white;
  font-weight: bold;
}

.status-tag {
  border-radius: var(--radius-pill);
  font-size: 0.8rem;
  padding: 0 0.8rem;
  transition: all 0.3s ease;
}

.status-tag.running,
.status-tag.connected {
  background-color: var(--success-color);
  color: white;
}

.status-tag.stopped,
.status-tag.waiting {
  background-color: var(--warning-color);
  color: white;
}

.status-tag.disconnected,
.status-tag.credential-rejected,
.status-tag.upstream-refused,
.status-tag.storage-unavailable,
.status-tag.stale {
  background-color: var(--error-color);
  color: white;
}

/* 正在启动是中性态：既不是成功也不是失败，用与「未启用」一致的弱化配色。 */
.status-tag.initializing {
  background-color: var(--text-color-tertiary);
  color: white;
}

.disconnect-reason {
  display: block;
  margin-top: 0.25rem;
  color: var(--text-color-secondary);
  font-size: 0.8rem;
  line-height: var(--line-height-normal, 1.5);
}

/* 扫码标签配色由 n-tag 的 type 决定，这里只保留与连接状态一致的字重与圆角，
   让两枚标签并排时看起来属于同一套语言，而不是两种控件。 */
.status-tag.qr-login {
  font-weight: 500;
}

/* 处置建议比原因码更弱一档：原因是「发生了什么」，建议是「接下来做什么」，
   两行并排时需要能一眼分出主次。 */
.qr-login-hint {
  color: var(--text-color-tertiary);
}

.status-tag.disabled {
  background-color: var(--text-color-tertiary);
  color: white;
}

.action-buttons {
  opacity: 0.7;
  transition: opacity 0.3s ease;
}

.instance-card:hover .action-buttons {
  opacity: 1;
}

.config-form {
  padding: 1.5rem;
  background-color: var(--card-bg-color);
  /* 表单容器嵌在 .adapter-card（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  transition: all 0.3s ease;
  animation: slide-up 0.5s ease forwards;
}

.form-item {
  margin-bottom: 1.5rem;
  transition: all 0.3s ease;
}

.form-hint {
  font-size: 0.85rem;
  color: var(--text-color-tertiary-text);
}

.input {
  transition: all 0.3s ease;
}

.input:hover,
.input:focus {
  border-color: var(--primary-color);
}

.switch {
  transition: all 0.3s ease;
}

.divider {
  margin: 1.5rem 0;
  border-color: var(--border-color);
}

.dynamic-config {
  animation: fade-in 0.5s ease forwards;
}

.empty-config {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 300px;
  /* 空状态占位与 .config-form 同层，故取同一档 sm */
  border-radius: var(--radius-sm);
  border: 1px dashed var(--border-color);
  background-color: rgba(0, 0, 0, 0.02);
}

.empty-state {
  padding: 2rem;
}

.add-button-large {
  padding: 0.5rem 1.5rem;
  font-size: 1rem;
  transition: all 0.3s ease;
}

.add-button-large:hover {
  transform: translateY(-3px);
  box-shadow: var(--box-shadow-hover);
}

/* 响应式布局 */
@media (max-width: 1200px) {
  .adapter-content {
    gap: 1.5rem;
  }

  .instances-panel {
    flex: 0 0 350px;
  }
}

@media (max-width: 992px) {
  .adapter-content {
    flex-direction: column;
  }

  .instances-panel {
    flex: none;
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--border-color);
    padding-right: 0;
    padding-bottom: 1.5rem;
    margin-bottom: 1.5rem;
  }

  .config-panel {
    padding-left: 0;
  }

  .instances-list {
    max-height: 400px;
  }
}

@media (max-width: 768px) {
  .adapter-detail {
    padding: 1rem;
  }

  .title-text {
    font-size: 1.3rem;
  }

  .panel-title {
    font-size: 1.1rem;
  }

  .config-form {
    padding: 1rem;
  }
}

@media (max-width: 480px) {
  .adapter-detail {
    padding: 0.5rem;
  }

  .instances-list {
    max-height: 300px;
  }

  .action-buttons {
    flex-direction: column;
    gap: 0.5rem;
  }
}
</style>
