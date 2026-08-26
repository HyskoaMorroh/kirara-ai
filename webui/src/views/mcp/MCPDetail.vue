<template>
  <div class="mcp-detail-container">
    <n-card class="main-card" :bordered="false">
      <!-- 顶部导航和标题 -->
      <div class="top-actions">
        <div class="back-button">
          <n-button quaternary @click="goBack">
            <template #icon>
              <n-icon><arrow-back-outline /></n-icon>
            </template>
            返回服务器列表
          </n-button>
        </div>
        <div class="server-title">
          <n-icon size="24" :color="getStateColor(serverInfo?.connection_state || 'disconnected')">
            <component
              :is="serverInfo?.server.type === 'stdio' ? TerminalOutline : GlobeOutline"
            />
          </n-icon>
          <span>{{ serverInfo?.id }}</span>
          <n-tag size="small" :type="getStateType(serverInfo?.connection_state || 'disconnected')">
            {{ formatConnectionState(serverInfo?.connection_state || 'disconnected') }}
          </n-tag>
        </div>
        <div class="top-right-actions">
          <n-space>
            <n-button
              :type="serverInfo?.connection_state === 'connected' ? 'warning' : 'success'"
              @click="serverInfo?.connection_state === 'connected' ? stopServer() : startServer()"
              :loading="isConnecting || isDisconnecting"
              :disabled="
                ['connecting', 'disconnecting'].includes(serverInfo?.connection_state || '')
              "
            >
              <template #icon>
                <n-icon>
                  <component
                    :is="serverInfo?.connection_state === 'connected' ? StopOutline : PlayOutline"
                  />
                </n-icon>
              </template>
              {{ serverInfo?.connection_state === 'connected' ? '断开连接' : '连接服务器' }}
            </n-button>
            <n-button @click="refreshDetails" :loading="isLoading">
              <template #icon>
                <n-icon><refresh-outline /></n-icon>
              </template>
              刷新
            </n-button>
          </n-space>
        </div>
      </div>

      <!-- 服务器信息卡片 -->
      <n-grid
        cols="24"
        :x-gap="16"
        :y-gap="16"
        class="detail-grid"
        item-responsive
        responsive="screen"
      >
        <!-- 基本信息卡片 -->
        <n-grid-item span="24 m:12">
          <n-card title="基本信息" class="detail-card" :bordered="false">
            <n-descriptions bordered :column="1" label-placement="left">
              <n-descriptions-item label="服务器ID">
                {{ serverInfo?.id }}
              </n-descriptions-item>
              <n-descriptions-item label="连接类型">
                <n-tag :type="serverInfo?.server.type === 'stdio' ? 'success' : 'info'">
                  {{ serverInfo?.server.type }}
                </n-tag>
              </n-descriptions-item>
              <n-descriptions-item v-if="serverInfo?.server.type === 'stdio'" label="命令">
                <div class="code-container">
                  <code>{{ formatCommand }}</code>
                  <n-button text size="tiny" @click="copyCommand" class="copy-button">
                    <template #icon>
                      <n-icon><copy-outline /></n-icon>
                    </template>
                  </n-button>
                </div>
              </n-descriptions-item>
              <n-descriptions-item v-if="serverInfo?.server.type !== 'stdio'" label="URL">
                <div class="code-container">
                  <code>{{ serverInfo?.server.url }}</code>
                  <n-button text size="tiny" @click="copyUrl" class="copy-button">
                    <template #icon>
                      <n-icon><copy-outline /></n-icon>
                    </template>
                  </n-button>
                </div>
              </n-descriptions-item>
              <n-descriptions-item v-if="serverInfo?.description" label="描述">
                {{ serverInfo?.description }}
              </n-descriptions-item>
            </n-descriptions>
          </n-card>
        </n-grid-item>

        <!-- 环境变量或Headers卡片 -->
        <n-grid-item span="24 m:12">
          <n-card
            :title="serverInfo?.server.type === 'stdio' ? '环境变量' : 'Headers'"
            class="detail-card"
            :bordered="false"
          >
            <div v-if="!hasEnvOrHeaders" class="empty-box">
              <n-empty
                :description="`未设置${
                  serverInfo?.server.type === 'stdio' ? '环境变量' : 'Headers'
                }`"
                size="small"
              />
            </div>
            <n-descriptions v-else bordered :column="1" label-placement="left">
              <n-descriptions-item v-for="(value, key) in envOrHeaders" :key="key" :label="key">
                {{ value }}
              </n-descriptions-item>
            </n-descriptions>
          </n-card>
        </n-grid-item>
      </n-grid>

      <!-- 工具和资源标签页 -->
      <n-card class="tabs-card" :bordered="false">
        <n-tabs type="line" animated>
          <!-- 工具标签页 -->
          <n-tab-pane name="tools" tab="工具">
            <div class="section-header">
              <n-button
                size="small"
                @click="loadTools"
                :loading="loadingTools"
                :disabled="!isConnected"
              >
                <template #icon>
                  <n-icon><refresh-outline /></n-icon>
                </template>
                刷新工具
              </n-button>
            </div>

            <n-alert v-if="!isConnected" type="warning" :show-icon="true" class="section-alert">
              请先连接到 MCP 服务器以查看可用的工具
            </n-alert>

            <div v-else-if="loadingTools" class="loading-container">
              <n-spin size="medium" />
              <p>加载工具中...</p>
            </div>

            <n-empty v-else-if="tools.length === 0" description="没有可用的工具" />

            <div v-else class="tools-list">
              <n-card v-for="tool in tools" :key="tool.name" class="tool-card" :bordered="false">
                <div class="tool-header">
                  <div class="tool-title">
                    <n-icon size="20"><terminal-outline /></n-icon>
                    <span>{{ tool.name }}</span>
                  </div>
                  <n-button size="small" @click="callTool(tool)" :disabled="!isConnected">
                    <template #icon>
                      <n-icon><play-outline /></n-icon>
                    </template>
                    执行工具
                  </n-button>
                </div>
                <div class="tool-description">
                  {{ tool.description || '没有描述' }}
                </div>
              </n-card>
            </div>
          </n-tab-pane>

          <!-- 资源标签页 -->
          <n-tab-pane name="resources" tab="资源">
            <div class="section-header">
              <n-button
                size="small"
                @click="loadResources"
                :loading="loadingResources"
                :disabled="!isConnected"
              >
                <template #icon>
                  <n-icon><refresh-outline /></n-icon>
                </template>
                刷新资源
              </n-button>
            </div>

            <n-alert v-if="!isConnected" type="warning" :show-icon="true" class="section-alert">
              请先连接到 MCP 服务器以查看可用的资源
            </n-alert>

            <div v-else-if="loadingResources" class="loading-container">
              <n-spin size="medium" />
              <p>加载资源中...</p>
            </div>

            <n-empty v-else-if="resources.length === 0" description="没有可用的资源" />

            <div v-else class="resources-list">
              <n-card
                v-for="resource in resources"
                :key="resource.id"
                class="resource-card"
                :bordered="false"
              >
                <div class="resource-header">
                  <div class="resource-title">
                    <n-icon size="20"><book-outline /></n-icon>
                    <span>{{ resource.id }}</span>
                  </div>
                  <n-button size="small" @click="viewResource(resource)" :disabled="!isConnected">
                    <template #icon>
                      <n-icon><eye-outline /></n-icon>
                    </template>
                    查看资源
                  </n-button>
                </div>
                <div class="resource-description">
                  {{ resource.description || '没有描述' }}
                </div>
              </n-card>
            </div>
          </n-tab-pane>

          <!-- 提示标签页 -->
          <n-tab-pane name="prompts" tab="提示">
            <div class="section-header">
              <n-button
                size="small"
                @click="loadPrompts"
                :loading="loadingPrompts"
                :disabled="!isConnected"
              >
                <template #icon>
                  <n-icon><refresh-outline /></n-icon>
                </template>
                刷新提示
              </n-button>
            </div>

            <n-alert v-if="!isConnected" type="warning" :show-icon="true" class="section-alert">
              请先连接到 MCP 服务器以查看可用的提示
            </n-alert>

            <div v-else-if="loadingPrompts" class="loading-container">
              <n-spin size="medium" />
              <p>加载提示中...</p>
            </div>

            <n-empty v-else-if="prompts.length === 0" description="没有可用的提示" />

            <div v-else class="prompts-list">
              <n-card
                v-for="prompt in prompts"
                :key="prompt.id"
                class="prompt-card"
                :bordered="false"
              >
                <div class="prompt-header">
                  <div class="prompt-title">
                    <n-icon size="20"><chatbubble-outline /></n-icon>
                    <span>{{ prompt.id }}</span>
                  </div>
                  <n-button size="small" @click="samplePrompt(prompt)" :disabled="!isConnected">
                    <template #icon>
                      <n-icon><play-outline /></n-icon>
                    </template>
                    采样提示
                  </n-button>
                </div>
                <div class="prompt-description">
                  {{ prompt.description || '没有描述' }}
                </div>
              </n-card>
            </div>
          </n-tab-pane>

          <!-- 运行审计标签页 -->
          <n-tab-pane name="audit" tab="运行审计">
            <n-alert :type="auditPersistent ? 'success' : 'warning'" :show-icon="true" class="section-alert">
              <template v-if="auditPersistent">
                最近 {{ auditRetentionLimit }} 条记录已持久化到服务器，服务重启后仍可查询。
              </template>
              <template v-else>
                持久审计存储当前不可用，仅保留本次后端进程中的最近 {{ auditRetentionLimit }} 条记录，重启后清空。
              </template>
              命令、URL、参数、环境变量和请求头不会在此展示。
            </n-alert>

            <div class="audit-toolbar">
              <n-space wrap>
                <n-select
                  v-model:value="auditOperation"
                  :options="auditOperationOptions"
                  clearable
                  placeholder="全部操作"
                  class="audit-filter"
                  data-test="audit-operation"
                  @update:value="resetAuditPage"
                />
                <n-select
                  v-model:value="auditOutcome"
                  :options="auditOutcomeOptions"
                  clearable
                  placeholder="全部结果"
                  class="audit-filter"
                  data-test="audit-outcome"
                  @update:value="resetAuditPage"
                />
                <n-button size="small" @click="loadAudit" :loading="auditLoading">
                  <template #icon>
                    <n-icon><refresh-outline /></n-icon>
                  </template>
                  刷新审计
                </n-button>
              </n-space>
            </div>

            <n-data-table
              :columns="auditColumns"
              :data="auditRecords"
              :loading="auditLoading"
              :pagination="false"
              :bordered="false"
              class="audit-table"
            />
            <div v-if="!auditLoading && auditRecords.length === 0" class="audit-empty">
              <n-empty description="当前筛选条件下没有运行记录" />
            </div>
            <div class="audit-pagination">
              <span class="audit-count">共 {{ auditTotal }} 条记录</span>
              <n-pagination
                v-model:page="auditPage"
                :page-count="auditPageCount"
                :page-size="auditPageSize"
                :page-sizes="[20, 50, 100]"
                show-size-picker
                @update:page="handleAuditPageChange"
                @update:page-size="handleAuditPageSizeChange"
              />
            </div>
          </n-tab-pane>
        </n-tabs>
      </n-card>
    </n-card>

    <!-- 工具调用模态框 -->
    <n-modal
      v-model:show="showToolModal"
      class="tool-modal"
      preset="card"
      title="执行工具"
      :style="{ width: '650px' }"
    >
      <div v-if="selectedTool">
        <div class="modal-description">
          {{ selectedTool.description || '没有描述' }}
        </div>

        <n-form-item label="执行 Agent">
          <n-select
            v-model:value="selectedAgentId"
            :options="agentOptions"
            placeholder="请选择已绑定此 MCP 服务器的 Agent"
            data-test="tool-agent"
          />
        </n-form-item>

        <n-alert v-if="pendingConfirmationId" type="warning" :show-icon="false">
          该工具需要明确确认。确认令牌仅对当前 Agent、参数和工具版本有效，且只能使用一次。
          <n-button size="small" type="warning" data-test="confirm-tool-call" @click="executeToolCall(true)">
            确认执行
          </n-button>
        </n-alert>

        <n-form
          :model="toolForm"
          ref="toolFormRef"
          label-placement="left"
          label-width="100px"
          class="tool-form"
        >
          <n-form-item
            v-for="param in getToolParams(selectedTool)"
            :key="param.name"
            :label="param.name + (param.required ? ' *' : '')"
          >
            <n-switch
              v-if="param.type === 'boolean'"
              v-model:value="toolForm[param.name]"
              :data-test="`tool-param-${param.name}`"
              :aria-label="param.name"
            />
            <n-input-number
              v-else-if="param.type === 'integer' || param.type === 'number'"
              v-model:value="toolForm[param.name]"
              :step="param.type === 'integer' ? 1 : undefined"
              :input-props="{
                'data-test': `tool-param-${param.name}`,
                'aria-label': param.name
              }"
            />
            <n-select
              v-else-if="param.options"
              v-model:value="toolForm[param.name]"
              :options="param.options"
              :input-props="{
                'data-test': `tool-param-${param.name}`,
                'aria-label': param.name
              }"
              clearable
            />
            <n-input
              v-else-if="param.type === 'object' || param.type === 'array'"
              v-model:value="toolForm[param.name]"
              type="textarea"
              :rows="4"
              :placeholder="param.description || (param.type === 'array' ? '[]' : '{}')"
              :input-props="{
                'data-test': `tool-param-${param.name}`,
                'aria-label': param.name
              }"
            />
            <n-input
              v-else
              v-model:value="toolForm[param.name]"
              :placeholder="param.description || ''"
              :input-props="{
                'data-test': `tool-param-${param.name}`,
                'aria-label': param.name
              }"
            />
          </n-form-item>
        </n-form>

        <div class="tool-response" v-if="toolResponse">
          <n-divider>工具响应</n-divider>
          <pre>{{ JSON.stringify(toolResponse, null, 2) }}</pre>
        </div>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showToolModal = false">关闭</n-button>
          <n-button type="primary" :loading="callingTool" @click="executeToolCall(false)"> 执行 </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 资源查看模态框 -->
    <n-modal
      v-model:show="showResourceModal"
      class="resource-modal"
      preset="card"
      title="资源内容"
      :style="{ width: '800px' }"
    >
      <div v-if="resourceContent">
        <n-code
          :code="
            typeof resourceContent === 'string'
              ? resourceContent
              : JSON.stringify(resourceContent, null, 2)
          "
          language="json"
          show-line-numbers
        />
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showResourceModal = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 采样提示模态框 -->
    <n-modal
      v-model:show="showPromptModal"
      class="prompt-modal"
      preset="card"
      title="采样提示"
      :style="{ width: '800px' }"
    >
      <div v-if="selectedPrompt">
        <n-form
          :model="promptForm"
          ref="promptFormRef"
          label-placement="left"
          label-width="100px"
          class="prompt-form"
        >
          <n-form-item
            v-for="argument in selectedPrompt.arguments"
            :key="argument.name"
            :label="argument.name + (argument.required ? ' *' : '')"
          >
            <n-input
              v-model:value="promptForm[argument.name]"
              :placeholder="argument.description || ''"
              :input-props="{
                'data-test': `prompt-argument-${argument.name}`,
                'aria-label': argument.name
              }"
            />
          </n-form-item>
          <n-empty v-if="selectedPrompt.arguments.length === 0" description="该提示无需参数" />
        </n-form>

        <div class="prompt-response" v-if="promptResponse">
          <n-divider>响应</n-divider>
          <n-card :bordered="false" class="response-card">
            {{ promptResponse }}
          </n-card>
        </div>
      </div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showPromptModal = false">关闭</n-button>
          <n-button type="primary" :loading="samplingPrompt" @click="executeSampling">
            开始采样
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton,
  NCard,
  NInput,
  NInputNumber,
  NSelect,
  NSwitch,
  NSpace,
  NModal,
  NForm,
  NFormItem,
  NIcon,
  NGrid,
  NGridItem,
  NEmpty,
  NSpin,
  NTag,
  NDivider,
  useMessage,
  useDialog,
  NTabs,
  NTabPane,
  NAlert,
  NTimeline,
  NTimelineItem,
  NDescriptions,
  NDescriptionsItem,
  NCode,
  NDataTable,
  NPagination
} from 'naive-ui'
import {
  ArrowBackOutline,
  RefreshOutline,
  PencilOutline,
  PlayOutline,
  StopOutline,
  CopyOutline,
  TerminalOutline,
  GlobeOutline,
  BookOutline,
  OpenOutline,
  EyeOutline,
  ChatbubbleOutline,
  DownloadOutline
} from '@vicons/ionicons5'
import { http, HttpRequestError } from '@/utils/http'
import { listAgents } from '@/api/agent'
import type { AgentSummary } from '@/api/agent'
import { useMCPViewModel } from './mcp.vm'
import type { MCPServer, MCPTool } from './mcp.vm'

// 路由和消息提示
const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

// 从 MCP ViewModel 获取服务器操作方法
const {
  getServerById,
  startServer: startMCPServer,
  stopServer: stopMCPServer,
  fetchServerTools: fetchMCPServerTools,
  openEditModal: openMCPEditModal
} = useMCPViewModel()

// 服务器 ID 和基本信息
const serverId = ref(route.params.id as string)
const serverInfo = ref<MCPServer | null>(null)
const isLoading = ref(false)
const isConnecting = ref(false)
const isDisconnecting = ref(false)

// 连接日志
interface ConnectionLog {
  state: string
  timestamp: number
  message?: string
}
const connectionLogs = ref<ConnectionLog[]>([])

// 工具相关
const tools = ref<MCPTool[]>([])
const loadingTools = ref(false)
const showToolModal = ref(false)
const selectedTool = ref<MCPTool | null>(null)
const toolForm = reactive<Record<string, any>>({})
const toolResponse = ref<any>(null)
const callingTool = ref(false)
const agents = ref<AgentSummary[]>([])
const selectedAgentId = ref('')
const pendingConfirmationId = ref('')

interface MCPJSONSchemaProperty {
  type?: string | string[]
  description?: string
  enum?: unknown[]
  default?: unknown
}

interface MCPToolParameter {
  name: string
  description: string
  required: boolean
  type: string
  schema: MCPJSONSchemaProperty
  options?: Array<{ label: string; value: unknown }>
}

// 资源相关
interface MCPResource {
  id: string
  description?: string
}
const resources = ref<MCPResource[]>([])
const loadingResources = ref(false)
const showResourceModal = ref(false)
const selectedResource = ref<MCPResource | null>(null)
const resourceContent = ref<any>(null)

// 提示相关
interface MCPPrompt {
  id: string
  name: string
  description?: string
  arguments: MCPPromptArgument[]
}
interface MCPPromptArgument {
  name: string
  description?: string
  required?: boolean
}

interface MCPAuditRecord {
  component: string
  timestamp?: string | null
  server: string
  operation: string
  duration_ms: number
  outcome: string
  correlation_id?: string | null
  error?: { type?: string; message?: string } | null
}

interface MCPAuditPage {
  items: MCPAuditRecord[]
  total: number
  offset: number
  limit: number
  has_more: boolean
  persistent: boolean
  retention_limit: number
}

const auditRecords = ref<MCPAuditRecord[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditPageSize = ref(20)
const auditRetentionLimit = ref(1000)
const auditPersistent = ref(false)
const auditLoading = ref(false)
const auditOperation = ref<string | null>(null)
const auditOutcome = ref<string | null>(null)

const auditOperationOptions = [
  { label: '连接', value: 'connect' },
  { label: '断开', value: 'disconnect' },
  { label: '调用工具', value: 'call_tool' },
  { label: '读取提示', value: 'get_prompt' },
  { label: '读取资源', value: 'get_resource' }
]

const auditOutcomeOptions = [
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failure' },
  { label: '错误', value: 'error' },
  { label: '已连接', value: 'already_connected' },
  { label: '已断开', value: 'already_disconnected' },
  { label: '未找到', value: 'not_found' },
  { label: '能力加载失败', value: 'capability_load_failed' }
]

const auditOperationLabels: Record<string, string> = {
  connect: '连接',
  disconnect: '断开',
  call_tool: '调用工具',
  get_prompt: '读取提示',
  get_resource: '读取资源'
}

const auditOutcomeLabels: Record<string, string> = {
  success: '成功',
  failure: '失败',
  error: '错误',
  already_connected: '已连接',
  already_disconnected: '已断开',
  not_found: '未找到',
  capability_load_failed: '能力加载失败',
  server_not_bound: '服务器未绑定',
  denied: '权限拒绝',
  confirmation_required: '需要确认',
  disconnected: '未连接'
}

const formatAuditTime = (timestamp?: string | null) => {
  if (!timestamp) return '未记录'
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString()
}

const formatAuditOperation = (operation: string) => auditOperationLabels[operation] || operation

const formatAuditOutcome = (outcome: string) => auditOutcomeLabels[outcome] || outcome

const auditColumns = [
  { title: '时间', key: 'timestamp', render: (row: MCPAuditRecord) => formatAuditTime(row.timestamp) },
  { title: '操作', key: 'operation', render: (row: MCPAuditRecord) => formatAuditOperation(row.operation) },
  { title: '结果', key: 'outcome', render: (row: MCPAuditRecord) => formatAuditOutcome(row.outcome) },
  { title: '耗时', key: 'duration_ms', render: (row: MCPAuditRecord) => `${row.duration_ms} ms` },
  { title: '关联 ID', key: 'correlation_id', render: (row: MCPAuditRecord) => row.correlation_id || '未记录' },
  { title: '错误', key: 'error', render: (row: MCPAuditRecord) => row.error ? `${row.error.type || '未知错误'}：${row.error.message || '操作失败'}` : '无' }
]

const auditPageCount = computed(() => Math.max(1, Math.ceil(auditTotal.value / auditPageSize.value)))
const prompts = ref<MCPPrompt[]>([])
const loadingPrompts = ref(false)
const showPromptModal = ref(false)
const selectedPrompt = ref<MCPPrompt | null>(null)
const promptForm = reactive<Record<string, string>>({})
const promptResponse = ref<string>('')
const samplingPrompt = ref(false)

// 计算属性
const isConnected = computed(() => serverInfo.value?.connection_state === 'connected')

const envOrHeaders = computed(() => {
  if (!serverInfo.value) return {}
  return serverInfo.value.server.type === 'stdio'
    ? serverInfo.value.server.env || {}
    : serverInfo.value.server.headers || {}
})

const hasEnvOrHeaders = computed(() => {
  return Object.keys(envOrHeaders.value).length > 0
})

const agentOptions = computed(() => agents.value
  .filter((agent) => agent.enabled)
  .map((agent) => ({
    label: agent.display_name ? `${agent.display_name} (${agent.agent_id})` : agent.agent_id,
    value: agent.agent_id
  })))

const formatCommand = computed(() => {
  if (!serverInfo.value) return ''
  return [
    serverInfo.value.server.command || '',
    ...(serverInfo.value.server.args || [])
  ]
    .filter(Boolean)
    .join(' ')
})

// 工具参数计算属性
const schemaType = (property: MCPJSONSchemaProperty) => {
  if (!Array.isArray(property.type)) return property.type || 'string'
  return property.type.find((type) => type !== 'null') || 'string'
}

const getToolParams = (tool: MCPTool): MCPToolParameter[] => {
  if (!tool.input_schema || typeof tool.input_schema !== 'object') return []

  // 假设 input_schema 是 JSON Schema 格式，从中提取属性作为参数
  const schema = tool.input_schema
  const params: MCPToolParameter[] = []

  if (schema.properties) {
    for (const [name, prop] of Object.entries<MCPJSONSchemaProperty>(schema.properties)) {
      params.push({
        name,
        description: prop.description || '',
        required: schema.required?.includes(name) || false,
        type: schemaType(prop),
        schema: prop,
        options: prop.enum?.map((value) => ({ label: String(value), value }))
      })
    }
  }

  return params
}

// 初始化加载
onMounted(async () => {
  await loadAgents()
  await loadServerDetails()
  await loadAudit()
  // 添加初始连接状态到日志
  if (serverInfo.value) {
    addConnectionLog(serverInfo.value.connection_state)
  }
})

const loadAgents = async () => {
  try {
    agents.value = await listAgents()
    if (!selectedAgentId.value) {
      selectedAgentId.value = agents.value.find((agent) => agent.enabled)?.agent_id || ''
    }
  } catch (error) {
    console.error('加载 Agent 列表失败:', error)
    agents.value = []
  }
}

// 加载服务器详情
const loadServerDetails = async () => {
  isLoading.value = true
  try {
    serverInfo.value = await getServerById(serverId.value)
    if (isConnected.value) {
      await loadTools()
      await loadResources()
      await loadPrompts()
    }
  } catch (error) {
    console.error('加载服务器详情失败:', error)
    message.error('加载服务器详情失败')
  } finally {
    isLoading.value = false
  }
}

// 刷新服务器详情
const refreshDetails = async () => {
  await loadServerDetails()
  await loadAudit()
}

const loadAudit = async () => {
  auditLoading.value = true
  try {
    const params = new URLSearchParams({
      server_id: serverId.value,
      offset: String((auditPage.value - 1) * auditPageSize.value),
      limit: String(auditPageSize.value)
    })
    if (auditOperation.value) params.set('operation', auditOperation.value)
    if (auditOutcome.value) params.set('outcome', auditOutcome.value)

    const response = await http.get<MCPAuditPage>(`/mcp/audit?${params.toString()}`)
    auditRecords.value = response.items || []
    auditTotal.value = response.total || 0
    auditRetentionLimit.value = response.retention_limit || 1000
    auditPersistent.value = response.persistent === true
  } catch (error) {
    console.error('加载MCP运行审计失败:', error)
    message.error('加载MCP运行审计失败')
    auditRecords.value = []
    auditTotal.value = 0
    auditPersistent.value = false
  } finally {
    auditLoading.value = false
  }
}

const resetAuditPage = async () => {
  auditPage.value = 1
  await loadAudit()
}

const handleAuditPageChange = async (page: number) => {
  auditPage.value = page
  await loadAudit()
}

const handleAuditPageSizeChange = async (pageSize: number) => {
  auditPageSize.value = pageSize
  auditPage.value = 1
  await loadAudit()
}

// 返回服务器列表
const goBack = () => {
  router.push({ name: 'mcp' })
}

// 启动服务器
const startServer = async () => {
  if (!serverInfo.value) return

  isConnecting.value = true
  try {
    await startMCPServer(serverId.value)
    // 刷新服务器状态
    serverInfo.value = await getServerById(serverId.value)
    message.success('服务器连接成功')
    addConnectionLog('connected', '服务器连接成功')

    // 加载工具和资源
    await loadTools()
    await loadResources()
    await loadPrompts()
    await loadAudit()
  } catch (error: any) {
    console.error('连接服务器失败:', error)
    message.error('连接服务器失败: ' + (error.message || '未知错误'))
    addConnectionLog('error', '连接服务器失败: ' + (error.message || '未知错误'))
    await loadAudit()
  } finally {
    isConnecting.value = false
  }
}

// 停止服务器
const stopServer = async () => {
  if (!serverInfo.value) return

  isDisconnecting.value = true
  try {
    await stopMCPServer(serverId.value)
    // 刷新服务器状态
    serverInfo.value = await getServerById(serverId.value)
    message.success('服务器已断开连接')
    addConnectionLog('disconnected', '服务器已断开连接')
    await loadAudit()
  } catch (error: any) {
    console.error('断开服务器失败:', error)
    message.error('断开服务器失败: ' + (error.message || '未知错误'))
    addConnectionLog('error', '断开服务器失败: ' + (error.message || '未知错误'))
    await loadAudit()
  } finally {
    isDisconnecting.value = false
  }
}

// 打开编辑模态框
const openEditModal = () => {
  if (serverInfo.value) {
    openMCPEditModal(serverInfo.value)
  }
}

// 加载工具列表
const loadTools = async () => {
  if (!isConnected.value) return

  loadingTools.value = true
  try {
    tools.value = await fetchMCPServerTools(serverId.value)
  } catch (error) {
    console.error('加载工具列表失败:', error)
    message.error('加载工具列表失败')
  } finally {
    loadingTools.value = false
  }
}

// 加载资源列表
const loadResources = async () => {
  if (!isConnected.value) return

  loadingResources.value = true
  try {
    const response = await http.get<MCPResource[]>(`/mcp/servers/${serverId.value}/resources`)
    resources.value = response || []
  } catch (error) {
    console.error('加载资源列表失败:', error)
    message.error('加载资源列表失败')
    resources.value = []
  } finally {
    loadingResources.value = false
  }
}

// 加载提示列表
const loadPrompts = async () => {
  if (!isConnected.value) return

  loadingPrompts.value = true
  try {
    const response = await http.get<MCPPrompt[]>(`/mcp/servers/${serverId.value}/prompts`)
    prompts.value = response || []
  } catch (error) {
    console.error('加载提示列表失败:', error)
    message.error('加载提示列表失败')
    prompts.value = []
  } finally {
    loadingPrompts.value = false
  }
}

// 调用工具
const callTool = (tool: MCPTool) => {
  selectedTool.value = tool
  // 重置表单和响应
  Object.keys(toolForm).forEach((key) => delete toolForm[key])
  // 初始化表单字段
  const params = getToolParams(tool)
  params.forEach((param) => {
    const defaultValue = param.schema.default
    if (param.type === 'object' || param.type === 'array') {
      toolForm[param.name] = defaultValue === undefined
        ? ''
        : JSON.stringify(defaultValue, null, 2)
    } else if (defaultValue !== undefined) {
      toolForm[param.name] = defaultValue
    } else if (param.type === 'boolean') {
      toolForm[param.name] = false
    } else {
      toolForm[param.name] = null
    }
  })
  toolResponse.value = null
  pendingConfirmationId.value = ''
  showToolModal.value = true
}

// 执行工具调用
const executeToolCall = async (usePendingConfirmation = false) => {
  if (!selectedTool.value) return

  if (!selectedAgentId.value) {
    message.error('请先选择执行 Agent')
    return
  }

  const params: Record<string, unknown> = {}
  for (const param of getToolParams(selectedTool.value)) {
    const value = toolForm[param.name]
    const isEmpty = value === null || value === undefined || value === ''
    if (isEmpty) {
      if (param.required) {
        message.error(`参数 ${param.name} 为必填项`)
        return
      }
      continue
    }
    if (param.type === 'object' || param.type === 'array') {
      try {
        const parsed = JSON.parse(String(value))
        const validType = param.type === 'array'
          ? Array.isArray(parsed)
          : parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
        if (!validType) throw new TypeError('JSON type mismatch')
        params[param.name] = parsed
      } catch {
        message.error(`参数 ${param.name} 必须是有效的 ${param.type === 'array' ? 'JSON 数组' : 'JSON 对象'}`)
        return
      }
    } else if (param.type === 'integer') {
      if (!Number.isInteger(value)) {
        message.error(`参数 ${param.name} 必须是整数`)
        return
      }
      params[param.name] = value
    } else if (param.type === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        message.error(`参数 ${param.name} 必须是数字`)
        return
      }
      params[param.name] = value
    } else {
      params[param.name] = value
    }
  }

  callingTool.value = true
  try {
    const response = await http.post<any>(`/mcp/servers/${serverId.value}/tools/call`, {
      toolName: selectedTool.value.name,
      params,
      agent_id: selectedAgentId.value,
      ...(usePendingConfirmation && pendingConfirmationId.value
        ? { confirmation_id: pendingConfirmationId.value }
        : {})
    })
    toolResponse.value = response
    pendingConfirmationId.value = ''
    message.success('工具执行成功')
    await loadAudit()
  } catch (error: any) {
    console.error('工具执行失败:', error)
    if (
      error instanceof HttpRequestError &&
      error.status === 409 &&
      error.data &&
      typeof error.data === 'object' &&
      typeof (error.data as Record<string, unknown>).confirmation_id === 'string'
    ) {
      pendingConfirmationId.value = (error.data as Record<string, string>).confirmation_id
      message.error('该工具需要明确确认，请检查参数后确认执行')
      await loadAudit()
      return
    }
    message.error('工具执行失败: ' + (error.message || '未知错误'))
    toolResponse.value = { error: error.message || '未知错误' }
    await loadAudit()
  } finally {
    callingTool.value = false
  }
}

// 查看资源
const viewResource = async (resource: MCPResource) => {
  selectedResource.value = resource
  resourceContent.value = null
  showResourceModal.value = true

  try {
    const response = await http.get<any>(`/mcp/servers/${serverId.value}/resources/${resource.id}`)
    resourceContent.value = response
    await loadAudit()
  } catch (error) {
    console.error('加载资源内容失败:', error)
    message.error('加载资源内容失败')
    resourceContent.value = { error: '加载资源失败' }
    await loadAudit()
  }
}

// 采样提示
const samplePrompt = (prompt: MCPPrompt) => {
  selectedPrompt.value = prompt
  Object.keys(promptForm).forEach((key) => delete promptForm[key])
  prompt.arguments.forEach((argument) => {
    promptForm[argument.name] = ''
  })
  promptResponse.value = ''
  showPromptModal.value = true
}

// 执行采样
const executeSampling = async () => {
  if (!selectedPrompt.value) return

  const missing = selectedPrompt.value.arguments.find(
    (argument) => argument.required && !promptForm[argument.name]?.trim()
  )
  if (missing) {
    message.error(`提示词参数 ${missing.name} 为必填项`)
    return
  }

  const argumentsPayload = Object.fromEntries(
    selectedPrompt.value.arguments
      .map((argument) => [argument.name, promptForm[argument.name]?.trim() || ''])
      .filter(([, value]) => value !== '')
  )

  samplingPrompt.value = true
  try {
    const response = await http.post<{ text: string }>(
      `/mcp/servers/${serverId.value}/prompts/sample`,
      {
        promptId: selectedPrompt.value.id,
        arguments: argumentsPayload
      }
    )
    promptResponse.value = response.text || JSON.stringify(response)
    await loadAudit()
  } catch (error: any) {
    console.error('提示采样失败:', error)
    message.error('提示采样失败: ' + (error.message || '未知错误'))
    promptResponse.value = '错误: ' + (error.message || '未知错误')
    await loadAudit()
  } finally {
    samplingPrompt.value = false
  }
}

// 添加连接日志
const addConnectionLog = (state: string, message?: string) => {
  connectionLogs.value.unshift({
    state,
    timestamp: Date.now(),
    message
  })
}

// 复制命令到剪贴板
const copyCommand = () => {
  if (!serverInfo.value) return

  const command = formatCommand.value
  navigator.clipboard.writeText(command)
  message.success('命令已复制到剪贴板')
}

// 复制URL到剪贴板
const copyUrl = () => {
  if (!serverInfo.value || !serverInfo.value.server.url) return

  navigator.clipboard.writeText(serverInfo.value.server.url)
  message.success('URL已复制到剪贴板')
}

// 格式化连接状态
const formatConnectionState = (state: string) => {
  const stateMap: Record<string, string> = {
    connected: '已连接',
    connecting: '连接中',
    disconnected: '已断开',
    disconnecting: '断开中',
    error: '错误'
  }
  return stateMap[state] || state
}

// 获取状态对应的类型
const getStateType = (
  state: string
): 'success' | 'warning' | 'error' | 'info' | 'default' | undefined => {
  const typeMap: Record<string, 'success' | 'warning' | 'error' | 'info' | 'default'> = {
    connected: 'success',
    connecting: 'info',
    disconnected: 'warning',
    disconnecting: 'warning',
    error: 'error'
  }
  return typeMap[state] || 'default'
}

// 获取状态对应的颜色
const getStateColor = (state: string) => {
  // 返回 CSS 变量表达式，交给主题决定具体色值（内联 style 支持 var()）
  const colorMap: Record<string, string> = {
    connected: 'var(--success-color, #18a058)',
    connecting: 'var(--primary-color, #2080f0)',
    disconnected: 'var(--error-color, #d03050)',
    disconnecting: 'var(--warning-color, #f0a020)',
    error: 'var(--error-color, #d03050)'
  }
  return colorMap[state] || 'var(--error-color, #d03050)'
}

// 格式化时间
const formatTime = (timestamp: number) => {
  return new Date(timestamp).toLocaleString()
}
</script>

<style scoped>
.mcp-detail-container {
  padding: 1.5rem;
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.main-card {
  /* 页面级主卡片，用大型表面档 */
  border-radius: var(--radius-lg);
  box-shadow: var(--box-shadow, 0 4px 16px rgba(0, 0, 0, 0.08));
  overflow: hidden;
}

.top-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--divider-color, rgba(0, 0, 0, 0.06));
}

.server-title {
  display: flex;
  align-items: center;
  font-size: var(--font-size-xl, 20px);
  font-weight: 600;
  gap: 12px;
}

.back-button {
  display: flex;
  align-items: center;
}

.top-right-actions {
  display: flex;
  gap: 8px;
}

.detail-grid {
  margin-bottom: 24px;
}

.detail-card {
  height: 100%;
  border-radius: var(--radius-md);
  transition: all 0.3s ease;
}

.detail-card:hover {
  box-shadow: var(--box-shadow-hover, 0 4px 12px rgba(0, 0, 0, 0.1));
}

.code-container {
  display: flex;
  align-items: center;
}

.code-container code {
  background-color: var(--code-bg-color, #f3f4f6);
  color: var(--code-text-color, #1f2937);
  padding: 4px 8px;
  border-radius: var(--radius-xs);
  font-family: monospace;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
}

.copy-button {
  margin-left: 8px;
}

.tabs-card {
  border-radius: var(--radius-md);
  margin-top: 16px;
}

.section-header {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 16px;
}

.section-header h3 {
  margin: 0;
  font-size: var(--font-size-lg, 16px);
  font-weight: 600;
}

.section-alert {
  margin-bottom: 16px;
}

.audit-toolbar {
  display: flex;
  justify-content: space-between;
  margin-bottom: 16px;
}

.audit-filter {
  min-width: 150px;
}

.audit-table {
  min-height: 120px;
}

.audit-empty {
  padding: 24px 0;
}

.audit-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 16px;
}

.audit-count {
  color: var(--text-color-secondary, rgba(0, 0, 0, 0.65));
  font-size: var(--font-size-sm, 13px);
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 0;
  gap: 16px;
}

.loading-container p {
  color: var(--text-color-tertiary, rgba(0, 0, 0, 0.45));
}

.tools-list,
.resources-list,
.prompts-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.tool-card,
.resource-card,
.prompt-card {
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
}

.tool-card:hover,
.resource-card:hover,
.prompt-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.08));
}

.tool-header,
.resource-header,
.prompt-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.tool-title,
.resource-title,
.prompt-title {
  display: flex;
  align-items: center;
  font-weight: 600;
  gap: 8px;
}

.tool-description,
.resource-description,
.prompt-description {
  color: var(--text-color-secondary, rgba(0, 0, 0, 0.65));
  font-size: var(--font-size-base, 14px);
  margin-top: 8px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tool-modal,
.resource-modal,
.prompt-modal {
  width: 650px;
}

.modal-description {
  margin-bottom: 16px;
  color: var(--text-color-secondary, rgba(0, 0, 0, 0.65));
}

.tool-form,
.prompt-form {
  margin: 16px 0;
}

.tool-response,
.prompt-response {
  margin-top: 16px;
}

.tool-response pre {
  background-color: var(--code-bg-color, #f3f4f6);
  color: var(--code-text-color, #1f2937);
  padding: 12px;
  /* 代码块嵌在 .tool-card（md 档）内部，按嵌套原则降一档到 sm */
  border-radius: var(--radius-sm);
  overflow: auto;
  font-family: monospace;
  font-size: var(--font-size-sm, 13px);
}

.response-card {
  background-color: var(--code-bg-color, #f3f4f6);
  color: var(--code-text-color, #1f2937);
  font-family: monospace;
  white-space: pre-wrap;
}

.slider-value {
  text-align: center;
  margin-top: 4px;
  color: var(--text-color-secondary, rgba(0, 0, 0, 0.65));
}

.empty-box {
  padding: 20px 0;
  display: flex;
  justify-content: center;
}

@media (max-width: 640px) {
  .audit-pagination {
    align-items: flex-start;
    flex-direction: column;
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 768px) {
  .top-actions {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }

  .top-right-actions {
    width: 100%;
    justify-content: space-between;
  }

  .tools-list,
  .resources-list,
  .prompts-list {
    grid-template-columns: 1fr;
  }

  .tool-modal,
  .resource-modal,
  .prompt-modal {
    width: 90vw;
  }
}
</style>
