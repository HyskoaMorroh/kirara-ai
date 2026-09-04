<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, useDialog } from 'naive-ui'

import {
  clearSessionHistory,
  createAgentConfiguration,
  deleteAgent,
  deleteSession,
  listAgents,
  listHookDeclarations,
  listPendingConfirmations,
  listSessions,
  previewHookEvent,
  updateAgentConfiguration,
  type AgentChannel,
  type AgentConfigurationRequest,
  type AgentRelations,
  type AgentResourceBindingInput,
  type AgentResourceType,
  type AgentSummary,
  type HookDeclarationSummary,
  type HookPreviewResult,
  type PendingConfirmation,
  type SessionSummary
} from '@/api/agent'
import { llmApi } from '@/api/llm'
import type { LLMBackend } from '@/api/llm'
import { listResources } from '@/api/resource'
import type { ManagedResource } from '@/api/resource'
import {
  collectModelChoices,
  collectProviderChoices,
  unknownModels
} from './agentModelChoices'

type BindingKey = 'prompt_bindings' | 'skill_bindings' | 'memory_bindings' | 'mcp_bindings' | 'hook_bindings'

interface ResourceSection {
  key: BindingKey
  type: AgentResourceType
  label: string
  description: string
}

const resourceSections: ResourceSection[] = [
  { key: 'prompt_bindings', type: 'prompt', label: 'Prompt', description: '定义 Agent 的回答边界和表达方式。' },
  { key: 'skill_bindings', type: 'skill', label: 'Skill', description: '挂载可复用的领域能力和操作流程。' },
  { key: 'memory_bindings', type: 'memory', label: 'Memory', description: '指定可参与当前 Agent 的记忆范围。' },
  { key: 'mcp_bindings', type: 'mcp', label: 'MCP', description: '选择服务器边界，再用工具白名单进一步收窄。' },
  { key: 'hook_bindings', type: 'hook', label: 'Hook', description: '配置会话、工具和审计生命周期事件。' }
]

const channelOptions: Array<{ label: string; value: AgentChannel }> = [
  { label: 'WebUI', value: 'webui' },
  { label: 'OneBot', value: 'onebot' },
  { label: 'QQ', value: 'qqbot' },
  { label: 'Telegram', value: 'telegram' },
  { label: 'WeCom', value: 'wecom' }
]

const policyOptions = [
  { label: '跟随当前版本', value: 'current' },
  { label: '固定版本', value: 'fixed' }
]

/**
 * 回复取回方式的四个档位。
 *
 * `inherit` 必须在列表里、而且排第一：它是默认值，也是唯一能把一个显式设过档位的
 * Agent 改回「跟随上层」的途径。少了它，运维只能靠猜上层是什么再手填同一个值，
 * 而那份手填的副本此后不会随上层改变。
 */
const replyStreamModeOptions = [
  { label: '跟随上层（渠道 / 进程默认）', value: 'inherit' },
  { label: '非流式：一次拿到完整回复', value: 'off' },
  { label: '流式聚合：流式取回，整段投递', value: 'aggregate' },
  { label: '逐步推送：边生成边改写同一条消息', value: 'incremental' }
]

const agentOptions = ref<AgentSummary[]>([])
const dialog = useDialog()
const resources = ref<ManagedResource[]>([])
const form = ref<AgentConfigurationRequest>(emptyForm())
const selectedAgentId = ref('')
const agentsLoaded = ref(false)
const agentsLoading = ref(true)
const resourcesLoading = ref(true)
const saving = ref(false)
const agentLoadError = ref('')
const resourceLoadError = ref('')
const errorMessage = ref('')
const savedMessage = ref('')

/**
 * 会话与待确认队列。
 *
 * 会话此前只有一个手填的绑定输入框：既列不出实际存在的会话，也看不到
 * 有没有操作卡在待确认上。这里补上只读列表与清理动作；后端只返回
 * 计数与时间戳，不含任何对话正文或工具参数。
 */
const sessions = ref<SessionSummary[]>([])
const confirmations = ref<PendingConfirmation[]>([])
const sessionsLoading = ref(false)
const sessionsError = ref('')
const sessionBusyId = ref('')

/**
 * Hook 声明与 dry-run。
 *
 * Hook 此前只能「装上再看它会不会跑」：事件名写错、matcher 写成非法正则、
 * 把 command 写进不该写的位置，都只能在真实请求里暴露——而那时它已经在
 * 生产路径上了。这里列出每个 Hook 声明的事件，并允许输入一个工具名做预演。
 */
const hooks = ref<HookDeclarationSummary[]>([])
const hooksLoading = ref(false)
const hooksError = ref('')
const previewToolName = ref('')
const previewResults = ref<Record<string, HookPreviewResult>>({})
const capabilitiesText = ref('')
const mcpAllowlistText = ref('')
const persistedAgentId = ref<string | null>(null)

const isCreating = computed(() => persistedAgentId.value === null)
const selectedAgent = computed(() =>
  agentOptions.value.find((agent) => agent.agent_id === selectedAgentId.value) || null
)

/**
 * 可选队友：所有**已启用**且**不是自己**的 Agent。
 *
 * 停用的 Agent 不列出——选中它只会得到一个调用时必定失败的工具，
 * 让模型撞墙一次再重试，白花一轮 token。
 */
/**
 * 模型优先链与 Provider 白名单的候选项，来自 `GET /llm/backends`。
 *
 * 那两格原来是纯文本框，而这些名字就在「模型配置」页上。手打的后果不是
 * 「多打几个字」：模型 ID 拼错不会当场报错，Agent 保存成功，直到某次真实
 * 对话解析不到那个模型才失败——而那时的报错与拼写无关。
 *
 * 拉取失败只让候选为空，不阻断编辑：选择器仍可自由输入（`tag`），
 * 因此拿不到后端列表时这一页照样可用。
 */
const backends = ref<LLMBackend[]>([])
const backendLoadError = ref('')
const modelOptions = computed(() => collectModelChoices(backends.value))
const providerOptions = computed(() => collectProviderChoices(backends.value))

/** 填了但当前配置里找不到的模型。只提示，不阻止保存。 */
const unknownModelNames = computed(() =>
  unknownModels(form.value.model_priority, modelOptions.value)
)

async function loadBackends() {
  backendLoadError.value = ''
  try {
    const response = await llmApi.getBackends()
    backends.value = response.data.backends || []
  } catch (error) {
    // 拿不到候选不该让这一页不可用——选择器允许自由输入。
    backends.value = []
    backendLoadError.value =
      error instanceof Error ? error.message : '供应商列表加载失败，模型候选暂不可用'
  }
}

const teammateOptions = computed(() =>
  agentOptions.value
    .filter((agent) => agent.enabled && agent.agent_id !== form.value.agent_id.trim())
    .map((agent) => ({
      label: agent.display_name ? `${agent.display_name}（${agent.agent_id}）` : agent.agent_id,
      value: agent.agent_id
    }))
)

function emptyRelations(): AgentRelations {
  return { channels: [], accounts: [], sessions: [], is_default: false }
}

function emptyForm(): AgentConfigurationRequest {
  return {
    agent_id: '',
    display_name: null,
    enabled: true,
    workflow_id: null,
    model_priority: [''],
    provider_allowlist: [],
    capabilities: [],
    prompt_bindings: [],
    skill_bindings: [],
    memory_bindings: [],
    mcp_bindings: [],
    hook_bindings: [],
    mcp_allowlist: [],
    allow_tools: true,
    max_tool_iterations: 8,
    teammate_agent_ids: [],
    reply_stream_mode: 'inherit',
    relations: emptyRelations()
  }
}

function bindingInput(binding: AgentSummary['prompt_bindings'][number]): AgentResourceBindingInput {
  const result: AgentResourceBindingInput = {
    resource_id: binding.resource_id,
    resource_type: binding.resource_type,
    version_policy: binding.version_policy,
    enabled: binding.enabled
  }
  if (binding.version_policy === 'fixed') result.version = binding.version
  return result
}

function formFromAgent(agent: AgentSummary): AgentConfigurationRequest {
  const next: AgentConfigurationRequest = {
    agent_id: agent.agent_id,
    display_name: agent.display_name || null,
    enabled: agent.enabled,
    workflow_id: agent.workflow_id || null,
    model_priority: [...agent.model_priority],
    provider_allowlist: [...agent.provider_allowlist],
    capabilities: [...agent.capabilities],
    prompt_bindings: agent.prompt_bindings.map(bindingInput),
    skill_bindings: agent.skill_bindings.map(bindingInput),
    memory_bindings: agent.memory_bindings.map(bindingInput),
    mcp_bindings: agent.mcp_bindings.map(bindingInput),
    hook_bindings: agent.hook_bindings.map(bindingInput),
    mcp_allowlist: [...agent.mcp_allowlist],
    allow_tools: agent.allow_tools,
    max_tool_iterations: agent.max_tool_iterations,
    // 旧后端可能不返回该字段：缺省为空即「不启用」，而不是让表单变成 undefined。
    teammate_agent_ids: [...(agent.teammate_agent_ids ?? [])],
    // 同理：早于分档流式的后端不返回它，缺省 `inherit` 即「跟随上层」，
    // 与不声明这一层完全等价。缺省成 `off` 会让保存一次就把上层配置覆盖掉。
    reply_stream_mode: agent.reply_stream_mode ?? 'inherit',
    relations: {
      channels: [...agent.relations.channels] as AgentChannel[],
      accounts: agent.relations.accounts.map((account) => ({ ...account })),
      sessions: [...agent.relations.sessions],
      is_default: agent.relations.is_default
    }
  }
  return next
}

function syncTextFields(next: AgentConfigurationRequest) {
  // Provider 白名单已改为多选（直接绑 `form.provider_allowlist`），
  // 不再经过文本中转——留着这一行会让「选完又被文本覆盖」。
  capabilitiesText.value = next.capabilities.join(', ')
  mcpAllowlistText.value = next.mcp_allowlist.join(', ')
}

function selectAgent(agent: AgentSummary) {
  selectedAgentId.value = agent.agent_id
  persistedAgentId.value = agent.agent_id
  form.value = formFromAgent(agent)
  syncTextFields(form.value)
  errorMessage.value = ''
  savedMessage.value = ''
}

function startNewAgent() {
  selectedAgentId.value = ''
  persistedAgentId.value = null
  form.value = emptyForm()
  syncTextFields(form.value)
  errorMessage.value = ''
  savedMessage.value = ''
}

function chooseInitialAgent(items: AgentSummary[]) {
  return items.find((agent) => agent.relations.is_default)?.agent_id || items[0]?.agent_id || ''
}

async function loadAgents() {
  agentsLoading.value = true
  agentLoadError.value = ''
  try {
    const agents = await listAgents()
    agentOptions.value = agents
    agentsLoaded.value = true
    const initial = agents.find((agent) => agent.agent_id === selectedAgentId.value) ||
      agents.find((agent) => agent.agent_id === chooseInitialAgent(agents))
    if (initial) selectAgent(initial)
    else startNewAgent()
  } catch {
    agentLoadError.value = 'Agent 配置加载失败，请检查服务状态后重试。'
  } finally {
    agentsLoading.value = false
  }
}

async function loadResourceCatalog() {
  resourcesLoading.value = true
  resourceLoadError.value = ''
  try {
    const installedResources = await listResources()
    resources.value = installedResources
  } catch {
    resourceLoadError.value = '资源目录加载失败，当前 Agent 配置和已有绑定已保留。'
  } finally {
    resourcesLoading.value = false
  }
}

async function load() {
  // 三者并行：候选项与 Agent 列表互不依赖，串起来只会让首屏更慢。
  await Promise.all([loadAgents(), loadResourceCatalog(), loadBackends()])
}

function isBindableResource(resource: ManagedResource) {
  return resource.enabled && !resource.confirmation_required
}

function resourceOptions(type: AgentResourceType, currentResourceId = '') {
  return resources.value
    .filter((resource) => resource.type === type && (isBindableResource(resource) || resource.resource_id === currentResourceId))
    .map((resource) => ({
      label: `${resource.resource_id} · ${resource.current_version}${isBindableResource(resource) ? '' : ' · 未启用'}`,
      value: resource.resource_id
    }))
}

function resourceLabel(resourceId: string) {
  const resource = resources.value.find((item) => item.resource_id === resourceId)
  return resource ? `${resource.resource_id} · ${resource.current_version}` : resourceId || '选择已安装资源'
}

function addModel() {
  form.value.model_priority.push('')
}

function removeModel(index: number) {
  if (form.value.model_priority.length > 1) form.value.model_priority.splice(index, 1)
}

function parseList(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function addBinding(section: ResourceSection) {
  const options = resourceOptions(section.type)
  if (!options.length) return
  const binding: AgentResourceBindingInput = {
    resource_id: options[0].value,
    resource_type: section.type,
    version_policy: 'current',
    enabled: true
  }
  bindingsFor(section.key).push(binding)
}

function removeBinding(key: BindingKey, index: number) {
  bindingsFor(key).splice(index, 1)
}

function bindingsFor(key: BindingKey): AgentResourceBindingInput[] {
  return form.value[key] as AgentResourceBindingInput[]
}

function addAccount() {
  form.value.relations.accounts.push({
    channel_type: 'telegram',
    adapter_instance: '',
    account_scope: ''
  })
}

function removeAccount(index: number) {
  form.value.relations.accounts.splice(index, 1)
}

function addSession() {
  form.value.relations.sessions.push('')
}

function removeSession(index: number) {
  form.value.relations.sessions.splice(index, 1)
}

function validate() {
  if (!form.value.agent_id.trim()) return 'Agent ID 不能为空'
  if (!form.value.model_priority.some((model) => model.trim())) return '至少填写一个模型候选'
  if (form.value.max_tool_iterations < 0) return '最大工具轮数不能小于 0'
  // 自委派是最短的无限递归：每一层都是一次真实的模型调用。
  // 后端也会拒绝，但在这里先拦住能给出更清楚的说明。
  if (form.value.teammate_agent_ids.includes(form.value.agent_id.trim())) {
    return '队友列表不能包含自己：那会形成无限委派'
  }
  for (const section of resourceSections) {
    if (form.value[section.key].some((binding) => !binding.resource_id.trim())) {
      return `${section.label} 绑定必须选择已安装资源`
    }
    if (form.value[section.key].some((binding) => binding.version_policy === 'fixed' && !binding.version?.trim())) {
      return `${section.label} 的固定版本不能为空`
    }
  }
  if (form.value.relations.accounts.some((account) => !account.adapter_instance.trim() || !account.account_scope.trim())) {
    return '账号关系需要填写适配器实例和账号范围'
  }
  if (form.value.relations.sessions.some((session) => !session.trim())) return '会话关系不能为空'
  return ''
}

function buildPayload(): AgentConfigurationRequest {
  const payload: AgentConfigurationRequest = {
    ...form.value,
    agent_id: form.value.agent_id.trim(),
    display_name: form.value.display_name?.trim() || null,
    workflow_id: form.value.workflow_id?.trim() || null,
    model_priority: form.value.model_priority.map((model) => model.trim()).filter(Boolean),
    // 多选组件已经给出数组；仍然 trim + 去空，因为 `tag` 允许自由输入。
    provider_allowlist: form.value.provider_allowlist
      .map((name) => String(name).trim())
      .filter(Boolean),
    capabilities: parseList(capabilitiesText.value),
    mcp_allowlist: parseList(mcpAllowlistText.value),
    relations: {
      channels: [...form.value.relations.channels],
      accounts: form.value.relations.accounts.map((account) => ({
        channel_type: account.channel_type.trim(),
        adapter_instance: account.adapter_instance.trim(),
        account_scope: account.account_scope.trim()
      })),
      sessions: form.value.relations.sessions.map((session) => session.trim()).filter(Boolean),
      is_default: form.value.relations.is_default
    }
  }
  return payload
}

function replaceAgent(updated: AgentSummary) {
  const index = agentOptions.value.findIndex((agent) => agent.agent_id === updated.agent_id)
  if (index === -1) agentOptions.value.push(updated)
  else agentOptions.value[index] = updated
  selectAgent(updated)
}

/**
 * 删除当前选中的 Agent。
 *
 * 此前 `DELETE /agents/<id>` **没有任何前端调用点**：建错一个 Agent 就永久留在
 * 列表里，而它仍然参与「渠道身份 → Agent」的解析。
 *
 * 后端 `AgentRegistry.remove()` 有三道拒绝（默认 Agent、还有渠道绑定、
 * 还有账号或会话绑定），每一条都给出可照做的原因。因此这里把后端那句话
 * **原样显示**，不换成「删除失败」——那三种情况用户都能自己解决
 * （先改默认、先解绑），而通用文案会把可解的问题变成死胡同。
 */
const deleting = ref(false)

async function removeAgent() {
  const agent = selectedAgent.value
  if (!agent || isCreating.value) return
  const label = form.value.display_name || form.value.agent_id
  dialog.warning({
    title: `确认删除 Agent ${label}`,
    // 写出名字而不是「确定删除吗」：列表里的行看起来很像，
    // 而这个操作不可逆（配置与绑定一起消失）。
    content:
      `删除后「${label}」的模型链、资源绑定与渠道关系都会消失，且不可恢复。` +
      '被它服务的渠道会回落到默认 Agent。',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      deleting.value = true
      errorMessage.value = ''
      savedMessage.value = ''
      try {
        await deleteAgent(agent.agent_id)
        savedMessage.value = `已删除 Agent ${label}`
        // 先清空编辑器再重取：留着一个已经不存在的 Agent，
        // 下一次「保存配置」会把它重新建出来。
        startNewAgent()
        await loadAgents()
      } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : 'Agent 删除失败'
      } finally {
        deleting.value = false
      }
    }
  })
}

async function save() {
  errorMessage.value = ''
  savedMessage.value = ''
  const validationError = validate()
  if (validationError) {
    errorMessage.value = validationError
    return
  }
  const payload = buildPayload()
  saving.value = true
  try {
    const updated = isCreating.value
      ? await createAgentConfiguration(payload)
      : await updateAgentConfiguration(payload.agent_id, payload)
    replaceAgent(updated)
    savedMessage.value = 'Agent 配置已保存'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Agent 配置保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
  void loadSessions()
  void loadHooks()
})

/** 读取所有 Hook 的声明摘要；未部署 Agent 运行时返回 503。 */
async function loadHooks() {
  hooksLoading.value = true
  hooksError.value = ''
  try {
    const response = await listHookDeclarations()
    hooks.value = response.items
  } catch (error) {
    hooks.value = []
    hooksError.value =
      error instanceof Error ? error.message : 'Hook 声明加载失败，请稍后重试。'
  } finally {
    hooksLoading.value = false
  }
}

/** 对一个 Hook 的某个事件做预演；不会执行 handler，也不会启动进程。 */
async function runHookPreview(hook: HookDeclarationSummary, event: string) {
  const key = `${hook.resource_id}::${event}`
  try {
    previewResults.value = {
      ...previewResults.value,
      [key]: await previewHookEvent(
        hook.resource_id,
        event,
        previewToolName.value.trim() || undefined
      )
    }
  } catch (error) {
    previewResults.value = {
      ...previewResults.value,
      [key]: {
        would_run: false,
        reason: 'request_failed',
        error: error instanceof Error ? error.message : '预演失败'
      }
    }
  }
}

function previewFor(hook: HookDeclarationSummary, event: string) {
  return previewResults.value[`${hook.resource_id}::${event}`]
}

/** 读取会话列表与待确认队列；未部署 Agent 运行时会返回 503，此时给出可读说明。 */
async function loadSessions() {
  sessionsLoading.value = true
  sessionsError.value = ''
  try {
    const [sessionResponse, confirmationResponse] = await Promise.all([
      listSessions(),
      listPendingConfirmations()
    ])
    sessions.value = sessionResponse.items
    confirmations.value = confirmationResponse.items
  } catch (error) {
    sessions.value = []
    confirmations.value = []
    sessionsError.value =
      error instanceof Error ? error.message : '会话列表加载失败，请稍后重试。'
  } finally {
    sessionsLoading.value = false
  }
}

async function handleClearHistory(session: SessionSummary) {
  sessionBusyId.value = session.session_id
  try {
    await clearSessionHistory(session.session_id)
    await loadSessions()
  } catch (error) {
    sessionsError.value = error instanceof Error ? error.message : '清空会话历史失败'
  } finally {
    sessionBusyId.value = ''
  }
}

async function handleDeleteSession(session: SessionSummary) {
  sessionBusyId.value = session.session_id
  try {
    await deleteSession(session.session_id)
    await loadSessions()
  } catch (error) {
    sessionsError.value = error instanceof Error ? error.message : '删除会话失败'
  } finally {
    sessionBusyId.value = ''
  }
}

/** 会话 ID 是 64 位摘要，界面上只显示前 12 位，完整值放到 title 里。 */
function shortSessionId(sessionId: string) {
  return sessionId.slice(0, 12)
}

/**
 * 会话来自谁：渠道类型 + 发送者标识。
 *
 * 只显示渠道类型回答不了「是谁」——同一个渠道上有几十个会话；只显示发送者
 * 标识回答不了「同一个人在私聊和群里的两个会话」。两者一起才构成一个能用来
 * 找行的身份。
 *
 * `null` 显示成「未记录」而不是空白或 `null` 字样：前者会被读成「渠道身份丢了」，
 * 后者是把内部表示漏给用户。真正的含义是「这个会话建于渠道身份落盘之前」，
 * 而它仍然可以被清空与删除。
 *
 * 收成一个函数而不是在模板里拼五个字段：那会让「`null` 怎么显示」这个判断
 * 散落在多处，而那正是最容易漏的一处。
 */
function channelIdentityText(session: SessionSummary): string {
  const identity = session.channel_identity
  if (!identity) return '未记录'
  return `${identity.channel_type} · ${identity.sender_scope}`
}

/** 悬浮时给出完整五元组：会话身份的其余三项在排查时才需要。 */
function channelIdentityTitle(session: SessionSummary): string {
  const identity = session.channel_identity
  if (!identity) return '该会话建于渠道身份落盘之前，因此没有记录来源'
  return [
    `渠道：${identity.channel_type}`,
    `适配器实例：${identity.adapter_instance}`,
    `账号：${identity.account_scope}`,
    `会话范围：${identity.conversation_scope}`,
    `发送者：${identity.sender_scope}`
  ].join('\n')
}
</script>

<template>
  <main class="agent-page" aria-labelledby="agent-page-title">
    <aside class="agent-list-panel" aria-label="Agent 列表">
      <div class="panel-heading">
        <div>
          <span class="eyebrow">统一运行入口</span>
          <h1 id="agent-page-title">Agent 管理</h1>
        </div>
        <n-button size="small" secondary data-test="new-agent" :disabled="!agentsLoaded" @click="startNewAgent">新建</n-button>
      </div>
      <n-button class="refresh-button" secondary :loading="agentsLoading" @click="loadAgents">刷新列表</n-button>
      <div v-if="agentsLoading && !agentsLoaded" class="loading-state" aria-busy="true">正在加载 Agent 配置...</div>
      <n-alert v-else-if="agentLoadError && !agentsLoaded" type="error" :show-icon="true" role="alert">{{ agentLoadError }}</n-alert>
      <div v-else-if="agentsLoaded && !agentOptions.length" class="empty-list" role="status">
        <strong>还没有 Agent</strong>
        <span>创建一个 Agent，把模型链、资源和渠道关系放在同一份策略里。</span>
      </div>
      <nav v-else class="agent-list" aria-label="已配置 Agent">
        <button
          v-for="agent in agentOptions"
          :key="agent.agent_id"
          type="button"
          class="agent-list-item"
          :class="{ selected: selectedAgentId === agent.agent_id }"
          @click="selectAgent(agent)"
        >
          <span class="agent-list-name">{{ agent.display_name || agent.agent_id }}</span>
          <span class="agent-list-meta"><span class="mono">{{ agent.agent_id }}</span><n-tag size="small" :type="agent.enabled ? 'success' : 'default'">{{ agent.enabled ? '启用' : '停用' }}</n-tag></span>
          <span v-if="agent.relations.is_default" class="default-mark">默认 Agent</span>
        </button>
      </nav>
    </aside>

    <section class="editor-panel" aria-label="Agent 配置编辑器">
      <div v-if="agentsLoading && !agentsLoaded" class="editor-loading" aria-busy="true">正在读取 Agent 关系...</div>
      <n-alert v-else-if="agentLoadError && !agentsLoaded" type="error" :show-icon="true" role="alert">{{ agentLoadError }}</n-alert>
      <template v-else-if="agentsLoaded">
      <header class="editor-header">
        <div>
          <span class="eyebrow">{{ isCreating ? '新建配置' : '编辑配置' }}</span>
          <h2>{{ isCreating ? '新建 Agent' : (form.display_name || form.agent_id) }}</h2>
          <p>渠道身份会先解析到 Agent，再按这里的模型链和资源策略运行。</p>
        </div>
        <div class="header-actions">
          <n-tag v-if="selectedAgent" :type="form.enabled ? 'success' : 'default'">{{ form.enabled ? '运行中' : '已停用' }}</n-tag>
          <!--
            新建中不显示删除：那条配置还不存在，按钮会被读成「取消新建」。
            后端对默认 Agent 与仍有绑定的 Agent 会拒绝并说明原因，那句话原样显示。
          -->
          <n-button
            v-if="selectedAgent && !isCreating"
            secondary
            type="error"
            :loading="deleting"
            data-test="delete-agent"
            @click="removeAgent"
          >
            删除
          </n-button>
          <n-button type="primary" :loading="saving" data-test="save-agent" @click="save">保存配置</n-button>
        </div>
      </header>

      <n-alert v-if="errorMessage" type="error" :show-icon="true" role="alert">{{ errorMessage }}</n-alert>
      <n-alert v-if="savedMessage" type="success" :show-icon="true" role="status">{{ savedMessage }}</n-alert>
      <n-alert v-if="agentLoadError" type="error" :show-icon="true" role="alert">{{ agentLoadError }}</n-alert>
      <n-alert v-if="resourceLoadError" type="warning" :show-icon="true" role="alert">{{ resourceLoadError }}</n-alert>

      <form class="editor-form" @submit.prevent="save">
        <section class="editor-section" aria-labelledby="identity-heading">
          <div class="section-heading"><div><h3 id="identity-heading">身份与运行状态</h3><p>Agent ID 保存后作为稳定路由身份，不可通过编辑器修改。</p></div></div>
          <div class="form-grid two-columns">
            <label class="field"><span>Agent ID</span><n-input v-model:value="form.agent_id" data-test="agent-id" :disabled="!isCreating" placeholder="例如 office-research" /></label>
            <label class="field"><span>显示名称</span><n-input v-model:value="form.display_name" data-test="agent-display-name" placeholder="例如 Office Research" /></label>
            <label class="field"><span>工作流 ID</span><n-input v-model:value="form.workflow_id" placeholder="可选，例如 chat:normal" /></label>
            <div class="switch-field"><span>允许 Agent 接收新请求</span><n-switch v-model:value="form.enabled" aria-label="允许 Agent 接收新请求" /></div>
            <div class="switch-field"><span>设为默认 Agent</span><n-switch v-model:value="form.relations.is_default" aria-label="设为默认 Agent" /></div>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="model-heading">
          <div class="section-heading"><div><h3 id="model-heading">模型优先链</h3><p>从上到下依次尝试，前一个模型不可用时自动进入备用链。</p></div><n-button size="small" secondary @click="addModel">添加模型</n-button></div>
          <div class="ordered-list">
            <div v-for="(model, index) in form.model_priority" :key="index" class="ordered-row">
              <span class="order-number">{{ index + 1 }}</span>
              <!--
                可筛选可创建的选择器而不是纯文本框：模型 ID 就在「模型配置」页上，
                手打拼错不会当场报错，Agent 保存成功，直到某次真实对话解析不到
                那个模型才失败——而那时的报错与拼写无关。
                保留 `tag`（可创建）：模型可能来自尚未登记的后端，
                或者用户想先配 Agent 再配供应商。
              -->
              <n-select
                v-model:value="form.model_priority[index]"
                filterable
                tag
                :options="modelOptions"
                :placeholder="index === 0 ? '选择或输入主模型 ID' : '选择或输入备用模型 ID'"
                :input-props="{ 'aria-label': `${index + 1}号模型` }"
                data-test="model-priority-select"
              />
              <n-button quaternary size="small" :disabled="form.model_priority.length === 1" :aria-label="`移除第 ${index + 1} 个模型`" @click="removeModel(index)">移除</n-button>
            </div>
          </div>
          <!--
            填了但当前配置里找不到的模型：只提示不阻止保存——尚未登记的后端上的
            模型是合法取值，拒绝保存会让「先配 Agent 再配供应商」不可能。
            但不提示也不行：拼错一个字母的后果是运行时失败。
          -->
          <n-alert
            v-if="unknownModelNames.length"
            type="warning"
            :show-icon="true"
            class="model-hint"
            data-test="unknown-model-hint"
          >
            这些模型在当前供应商配置里找不到：{{ unknownModelNames.join('、') }}。
            如果它们来自尚未登记的供应商，可以照常保存；否则请检查拼写。
          </n-alert>
          <n-alert v-if="backendLoadError" type="info" :show-icon="true" class="model-hint">
            {{ backendLoadError }}（仍可手动输入模型 ID）
          </n-alert>
          <div class="form-grid two-columns compact-top">
            <!--
              多选而不是逗号分隔的文本：后端名同样在另一个页面上，
              而逗号分隔的输入还要用户自己记住分隔规则。
              `tag` 保留，理由与模型链相同。
            -->
            <label class="field"><span>Provider 白名单</span>
              <n-select
                v-model:value="form.provider_allowlist"
                multiple
                filterable
                tag
                :options="providerOptions"
                placeholder="留空表示不限制"
                :input-props="{ 'aria-label': 'Provider 白名单' }"
                data-test="provider-allowlist-select"
              />
            </label>
            <label class="field"><span>能力标记</span><n-input v-model:value="capabilitiesText" placeholder="例如 research, code" /></label>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="tool-heading">
          <div class="section-heading"><div><h3 id="tool-heading">工具策略</h3><p>MCP 服务器绑定负责边界，白名单只允许更窄的工具集合。</p></div></div>
          <div class="form-grid two-columns">
            <div class="switch-field"><span>允许调用工具</span><n-switch v-model:value="form.allow_tools" aria-label="允许调用工具" /></div>
            <label class="field"><span>最大工具轮数</span><n-input-number v-model:value="form.max_tool_iterations" :min="0" data-test="max-tool-iterations" /></label>
            <label class="field full-width"><span>MCP 工具白名单</span><n-input v-model:value="mcpAllowlistText" placeholder="例如 context7.query-docs, context7.resolve-library-id" /></label>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="stream-heading">
          <div class="section-heading">
            <div>
              <h3 id="stream-heading">回复取回方式</h3>
              <p>
                三层优先级：这里的设置覆盖「系统设置 → Agent 运行时」里的渠道默认与进程默认。
                <strong>跟随上层</strong>等于不在这一层声明。
              </p>
            </div>
          </div>
          <div class="form-grid two-columns">
            <label class="field">
              <span>取回方式</span>
              <n-select
                v-model:value="form.reply_stream_mode"
                :options="replyStreamModeOptions"
                data-test="reply-stream-mode"
              />
            </label>
            <p class="field-hint full-width">
              <strong>逐步推送</strong>需要渠道能改写已交付出去的内容：Telegram 靠编辑已发消息，
              WebUI 在线对话靠 SSE。QQ 与企业微信上它会自动<strong>退化成流式聚合</strong>——仍走
              流式请求并保留首字节超时与故障转移，但用户端仍是一条完整回复。这不是故障，也不会报错。
            </p>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="teammates-heading">
          <div class="section-heading">
            <div>
              <h3 id="teammates-heading">队友（Teammates）</h3>
              <p>
                选中的 Agent 会以 <code>delegate_to_&lt;id&gt;</code> 工具的形式提供给模型，
                供它把子任务交出去。队友用自己的模型链、提示词、技能与工具白名单执行，
                <strong>看不到本次对话历史</strong>，因此模型必须在任务描述里自带背景。
                留空表示不启用。
              </p>
            </div>
          </div>
          <div class="form-grid">
            <label class="field full-width">
              <span>可委派的队友</span>
              <n-select
                v-model:value="form.teammate_agent_ids"
                multiple
                filterable
                clearable
                placeholder="留空表示不启用委派"
                data-test="teammate-agents"
                :options="teammateOptions"
              />
              <small class="field-hint">
                委派最多向下递归 2 层，且不能选择自己——每一层都是一次真实的模型调用。
              </small>
            </label>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="resource-heading">
          <div class="section-heading"><div><h3 id="resource-heading">Prompt / Skill / Memory / MCP / Hook</h3><p>绑定只引用服务器已安装资源；每一项都可独立停用，并选择版本策略。</p></div><n-button size="small" secondary data-test="refresh-resources" :loading="resourcesLoading" @click="loadResourceCatalog">刷新资源</n-button></div>
          <div class="resource-grid">
            <article v-for="section in resourceSections" :key="section.key" class="resource-section" :data-test="`resource-binding-${section.type}`">
              <header class="resource-section-header"><div><h4>{{ section.label }}</h4><p>{{ section.description }}</p></div><n-button size="small" secondary :disabled="!resourceOptions(section.type).length" :title="resourceOptions(section.type).length ? '添加已启用资源' : '请先在资源管理中启用并确认资源'" @click="addBinding(section)">添加</n-button></header>
              <div v-if="!form[section.key].length" class="binding-empty">{{ resourceOptions(section.type).length ? '未绑定' : '没有可绑定的已启用资源，请先在资源管理中启用并确认。' }}</div>
              <div v-for="(binding, index) in form[section.key]" :key="index" class="binding-row">
                <n-select v-model:value="binding.resource_id" :options="resourceOptions(section.type, binding.resource_id)" :input-props="{ 'aria-label': `${section.label}资源` }" placeholder="选择已安装资源" />
                <n-select v-model:value="binding.version_policy" :options="policyOptions" :input-props="{ 'aria-label': `${section.label}版本策略` }" />
                <n-input v-if="binding.version_policy === 'fixed'" v-model:value="binding.version" placeholder="版本" :input-props="{ 'aria-label': `${section.label}固定版本` }" />
                <span v-else class="current-version" :title="resourceLabel(binding.resource_id)">当前版本</span>
                <n-switch v-model:value="binding.enabled" :aria-label="`启用${section.label}绑定`" />
                <n-button quaternary size="small" :aria-label="`移除${section.label}绑定`" @click="removeBinding(section.key, index)">移除</n-button>
              </div>
            </article>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="channel-heading">
          <div class="section-heading"><div><h3 id="channel-heading">渠道与身份关系</h3><p>同一 Agent 可服务多个入口；账号和会话关系比渠道级默认关系更具体。</p></div></div>
          <div class="channel-grid">
            <label v-for="channel in channelOptions" :key="channel.value" class="channel-option">
              <input v-model="form.relations.channels" type="checkbox" :value="channel.value" />
              <span>{{ channel.label }}</span>
            </label>
          </div>

          <div class="relation-subsection">
            <div class="subsection-heading"><h4>账号关系</h4><n-button size="small" secondary @click="addAccount">添加账号</n-button></div>
            <div v-if="!form.relations.accounts.length" class="binding-empty">未绑定特定账号</div>
            <div v-for="(account, index) in form.relations.accounts" :key="index" class="relation-row">
              <n-select v-model:value="account.channel_type" :options="channelOptions" :input-props="{ 'aria-label': '账号渠道' }" />
              <n-input v-model:value="account.adapter_instance" placeholder="适配器实例" :input-props="{ 'aria-label': '适配器实例' }" />
              <n-input v-model:value="account.account_scope" placeholder="账号范围" :input-props="{ 'aria-label': '账号范围' }" />
              <n-button quaternary size="small" aria-label="移除账号关系" @click="removeAccount(index)">移除</n-button>
            </div>
          </div>

          <div class="relation-subsection">
            <div class="subsection-heading"><h4>会话关系</h4><n-button size="small" secondary @click="addSession">添加会话</n-button></div>
            <div v-if="!form.relations.sessions.length" class="binding-empty">未绑定特定会话</div>
            <div v-for="(session, index) in form.relations.sessions" :key="index" class="relation-row session-row">
              <n-input v-model:value="form.relations.sessions[index]" placeholder="例如 telegram/telegram/main/c2c:user/user" :input-props="{ 'aria-label': '会话关系' }" />
              <n-button quaternary size="small" aria-label="移除会话关系" @click="removeSession(index)">移除</n-button>
            </div>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="session-management-title">
          <div class="section-heading">
            <div>
              <h3 id="session-management-title">会话与待确认</h3>
              <p>
                已持久化的会话及其历史长度。这里只显示计数与时间，不展示任何对话内容；
                需要重置某个会话的上下文时，清空历史即可，绑定关系不受影响。
              </p>
            </div>
            <n-button size="small" secondary :loading="sessionsLoading" @click="loadSessions">
              刷新
            </n-button>
          </div>

          <n-alert v-if="sessionsError" type="warning" :show-icon="true" role="alert">
            {{ sessionsError }}
          </n-alert>

          <div v-if="confirmations.length" class="confirmation-list" role="status">
            <h4>等待确认（{{ confirmations.length }}）</h4>
            <div v-for="item in confirmations" :key="item.confirmation_id" class="confirmation-row">
              <span class="mono" :title="item.confirmation_id">
                {{ shortSessionId(item.confirmation_id) }}
              </span>
              <span>{{ item.tool_name || '未记录工具' }}</span>
              <n-tag size="small" type="warning">{{ item.status }}</n-tag>
              <span class="confirmation-expiry">到期 {{ item.expires_at || '未知' }}</span>
            </div>
          </div>

          <div v-if="sessionsLoading && !sessions.length" class="binding-empty" aria-busy="true">
            正在读取会话...
          </div>
          <div v-else-if="!sessions.length" class="binding-empty">暂无持久化会话</div>
          <div v-else class="session-table" role="table" aria-label="持久化会话">
            <div class="session-table-head" role="row">
              <span role="columnheader">会话</span>
              <!--
                渠道身份紧跟会话 ID：ID 是一个 64 位摘要，对人没有含义，
                而清空历史与删除都以它为唯一标识。分不清哪一行属于谁的时候，
                那两个动作只能靠猜。
              -->
              <span role="columnheader">渠道 / 发送者</span>
              <span role="columnheader">Agent</span>
              <span role="columnheader">消息数</span>
              <span role="columnheader">待确认</span>
              <span role="columnheader">最近更新</span>
              <span role="columnheader">操作</span>
            </div>
            <div
              v-for="session in sessions"
              :key="session.session_id"
              class="session-table-row"
              role="row"
            >
              <span class="mono" role="cell" :title="session.session_id">
                {{ shortSessionId(session.session_id) }}
              </span>
              <span
                role="cell"
                class="session-channel"
                data-test="session-channel-identity"
                :title="channelIdentityTitle(session)"
              >
                {{ channelIdentityText(session) }}
              </span>
              <span role="cell">{{ session.agent_id || '未绑定' }}</span>
              <span role="cell">{{ session.message_count }}</span>
              <span role="cell">{{ session.pending_confirmations }}</span>
              <span role="cell">{{ session.updated_at || '未知' }}</span>
              <span class="session-actions" role="cell">
                <n-button
                  quaternary
                  size="small"
                  :loading="sessionBusyId === session.session_id"
                  :aria-label="`清空会话 ${shortSessionId(session.session_id)} 的历史`"
                  @click="handleClearHistory(session)"
                >
                  清空历史
                </n-button>
                <n-button
                  quaternary
                  size="small"
                  :loading="sessionBusyId === session.session_id"
                  :aria-label="`删除会话 ${shortSessionId(session.session_id)}`"
                  @click="handleDeleteSession(session)"
                >
                  删除
                </n-button>
              </span>
            </div>
          </div>
        </section>

        <section class="editor-section" aria-labelledby="hook-inspection-title">
          <div class="section-heading">
            <div>
              <h3 id="hook-inspection-title">Hook 声明与预演</h3>
              <p>
                列出每个已安装 Hook 声明的事件、限定的工具与是否需要进程执行权限。
                填入工具名可以预演它是否会触发——预演不执行 handler，也不启动任何进程。
              </p>
            </div>
            <n-button size="small" secondary :loading="hooksLoading" @click="loadHooks">
              刷新
            </n-button>
          </div>

          <n-alert v-if="hooksError" type="warning" :show-icon="true" role="alert">
            {{ hooksError }}
          </n-alert>

          <div class="field hook-preview-field">
            <label for="hook-preview-tool">预演工具名</label>
            <n-input
              id="hook-preview-tool"
              v-model:value="previewToolName"
              placeholder="例如 Bash；留空表示不带工具名的事件"
              :input-props="{ 'aria-label': '预演工具名' }"
            />
          </div>

          <div v-if="hooksLoading && !hooks.length" class="binding-empty" aria-busy="true">
            正在读取 Hook 声明...
          </div>
          <div v-else-if="!hooks.length" class="binding-empty">尚未安装 Hook</div>
          <div v-else class="hook-list">
            <div v-for="hook in hooks" :key="hook.resource_id" class="hook-card">
              <div class="hook-card-header">
                <span class="mono">{{ hook.resource_id }}</span>
                <n-tag size="small" :type="hook.enabled ? 'success' : 'default'">
                  {{ hook.enabled ? '已启用' : '未启用' }}
                </n-tag>
                <span class="hook-version">v{{ hook.version }}</span>
              </div>
              <n-alert v-if="hook.error" type="error" :show-icon="false" class="hook-error">
                {{ hook.error }}
              </n-alert>
              <div v-else-if="!hook.events.length" class="binding-empty">未声明任何事件</div>
              <div v-for="item in hook.events" :key="item.event" class="hook-event-row">
                <span class="hook-event-name">{{ item.event }}</span>
                <n-tag size="small" :type="item.enabled === false ? 'default' : 'info'">
                  {{ item.enabled === false ? '已关停' : item.kind || '未知' }}
                </n-tag>
                <span class="hook-matcher">
                  {{ item.matcher ? `限定工具：${item.matcher}` : '适用于全部调用' }}
                </span>
                <n-tag v-if="item.requires_process_execution" size="small" type="warning">
                  需要进程执行权限
                </n-tag>
                <span v-if="item.error" class="hook-event-error">{{ item.error }}</span>
                <n-button
                  v-else
                  quaternary
                  size="small"
                  :aria-label="`预演 ${hook.resource_id} 的 ${item.event} 事件`"
                  @click="runHookPreview(hook, item.event)"
                >
                  预演
                </n-button>
                <span
                  v-if="previewFor(hook, item.event)"
                  class="hook-preview-result"
                  :class="{ 'would-run': previewFor(hook, item.event)?.would_run }"
                >
                  {{
                    previewFor(hook, item.event)?.would_run
                      ? '会触发'
                      : `不触发（${previewFor(hook, item.event)?.reason}）`
                  }}
                </span>
              </div>
            </div>
          </div>
        </section>
      </form>
      </template>
    </section>
  </main>
</template>

<style scoped>
.agent-page { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: calc(100vh - 28px); color: var(--text-color); background: var(--bg-color); }
.agent-list-panel { box-sizing: border-box; min-width: 0; padding: var(--space-6); border-right: 1px solid var(--border-color); background: var(--card-bg-color); }
.panel-heading, .editor-header, .section-heading, .resource-section-header, .subsection-heading, .header-actions, .agent-list-meta, .ordered-row, .binding-row, .relation-row { display: flex; align-items: center; }
.panel-heading, .editor-header, .section-heading, .resource-section-header, .subsection-heading { justify-content: space-between; gap: var(--space-4); }
.eyebrow { color: var(--primary-color-text); font-size: var(--font-size-sm); font-weight: 650; }
h1, h2, h3, h4, p { letter-spacing: 0; }
h1 { margin: var(--space-2) 0 0; font-size: var(--font-size-xl); }
h2 { margin: 0; font-size: var(--font-size-2xl); line-height: var(--line-height-tight); }
h3 { margin: 0; font-size: var(--font-size-lg); }
h4 { margin: 0; font-size: var(--font-size-base); }
.refresh-button { width: 100%; margin: var(--space-6) 0 var(--space-4); }
.agent-list { display: grid; gap: var(--space-1); }
.agent-list-item { display: grid; gap: var(--space-1); min-width: 0; padding: var(--space-3); border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--text-color); text-align: left; background: transparent; cursor: pointer; }
.agent-list-item:hover, .agent-list-item.selected { border-color: var(--border-color); background: var(--muted-bg-color); }
.agent-list-name { min-width: 0; overflow: hidden; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.agent-list-meta { justify-content: space-between; gap: var(--space-2); color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.default-mark { color: var(--primary-color-text); font-size: var(--font-size-xs); }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.empty-list, .loading-state, .editor-loading { display: grid; gap: var(--space-2); color: var(--text-color-secondary); font-size: var(--font-size-sm); line-height: var(--line-height-relaxed); }
.empty-list { padding: var(--space-4) 0; }
.empty-list strong { color: var(--text-color); }
.editor-panel { min-width: 0; max-width: 1120px; padding: var(--space-7) clamp(var(--space-4), 5vw, var(--space-8)); }
.editor-header { align-items: flex-start; margin-bottom: var(--space-5); }
.editor-header p, .section-heading p, .resource-section-header p { margin: var(--space-2) 0 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); line-height: var(--line-height-relaxed); }
.header-actions { flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-3); }
.editor-form { display: grid; gap: var(--space-4); margin-top: var(--space-4); }
.editor-section { padding: var(--space-6); border: 1px solid var(--border-color); border-radius: var(--radius-md); background: var(--card-bg-color); }
.section-heading { align-items: flex-start; margin-bottom: var(--space-5); }
.form-grid { display: grid; gap: var(--space-4); }
.two-columns { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.field, .switch-field { display: grid; align-content: start; gap: var(--space-2); min-width: 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.switch-field { grid-template-columns: minmax(0, 1fr) auto; align-items: center; min-height: 34px; }
.full-width { grid-column: 1 / -1; }
/* 字段下方的说明文字。与 `.section-heading p` 同一视觉层级（次要色、小字号、
   放松行高），因为它们承担同一件事：解释这一项在什么情况下才生效。 */
.field-hint { margin: 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); line-height: var(--line-height-relaxed); }
.compact-top { margin-top: var(--space-4); }
.ordered-list { display: grid; gap: var(--space-2); }
/* 提示与下面的字段网格之间留一档间距，避免读成同一块。 */
.model-hint { margin-top: var(--space-3); }
.ordered-row { gap: var(--space-3); min-width: 0; }
.order-number { display: grid; place-items: center; flex: 0 0 28px; width: 28px; height: 28px; border-radius: var(--radius-pill); color: var(--primary-color-text); background: var(--selection-bg-color); font-size: var(--font-size-sm); font-weight: 650; }
.ordered-row > .n-select, .ordered-row > .n-input, .binding-row > .n-select, .binding-row > .n-input, .relation-row > .n-input, .relation-row > .n-select { min-width: 0; flex: 1; }
.resource-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.resource-section { min-width: 0; padding: var(--space-4); border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--muted-bg-color); }
.resource-section-header { align-items: flex-start; }
.resource-section-header p { margin-bottom: 0; }
.binding-empty { padding: var(--space-3) 0 0; color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.binding-row { gap: var(--space-2); min-width: 0; padding-top: var(--space-3); }
.binding-row .n-select:first-child { flex: 1.5; }
.current-version { flex: 0 0 62px; color: var(--text-color-secondary); font-size: var(--font-size-xs); text-align: center; }
.channel-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-2); }
.channel-option { display: flex; align-items: center; gap: var(--space-2); min-width: 0; padding: var(--space-3); border: 1px solid var(--border-color); border-radius: var(--radius-sm); color: var(--text-color); background: var(--muted-bg-color); cursor: pointer; }
.channel-option input { flex: 0 0 auto; accent-color: var(--primary-color); }
.relation-subsection { margin-top: var(--space-6); padding-top: var(--space-5); border-top: 1px solid var(--border-color); }
.subsection-heading { margin-bottom: var(--space-3); }
.relation-row { gap: var(--space-2); min-width: 0; margin-top: var(--space-2); }
.session-row > .n-input { flex: 1; }

/* 会话与待确认列表：用网格保证七列在窄屏下仍能各自换行而不挤成一团。 */
.session-table { display: grid; gap: var(--space-1); margin-top: var(--space-3); min-width: 0; }
.session-table-head, .session-table-row {
  display: grid;
  /* 渠道 / 发送者插在会话 ID 之后，宽度与它相当：两者一起才构成一个能用来
     找行的身份，把它压窄会让发送者标识先被省略号吃掉。 */
  grid-template-columns:
    minmax(0, 1.2fr) minmax(0, 1.2fr) minmax(0, 1fr) 72px 72px minmax(0, 1.4fr) minmax(0, auto);
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-sm);
}
.session-table-head { color: var(--text-color-secondary); font-weight: 650; }
.session-table-row { border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--muted-bg-color); }
.session-table-row > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 未记录的会话把这一格调淡：它是一个「建于升级之前」的事实，不是错误。 */
.session-channel { color: var(--text-color-secondary); }
.session-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-1); }
.confirmation-list { display: grid; gap: var(--space-2); margin-top: var(--space-4); }
.confirmation-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--warning-color, var(--border-color));
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}
.confirmation-expiry { color: var(--text-color-secondary); font-size: var(--font-size-xs); }

/* Hook 声明与预演 */
.hook-preview-field { max-width: 420px; margin-top: var(--space-4); }
.hook-list { display: grid; gap: var(--space-3); margin-top: var(--space-4); }
.hook-card { min-width: 0; padding: var(--space-4); border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--muted-bg-color); }
.hook-card-header { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-bottom: var(--space-3); }
.hook-version { color: var(--text-color-secondary); font-size: var(--font-size-xs); }
.hook-error { margin-bottom: var(--space-2); }
.hook-event-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  border-top: 1px solid var(--border-color);
  font-size: var(--font-size-sm);
}
.hook-event-name { font-weight: 650; }
.hook-matcher { color: var(--text-color-secondary); }
.hook-event-error { color: var(--error-color); }
.hook-preview-result { color: var(--text-color-secondary); }
.hook-preview-result.would-run { color: var(--success-color); font-weight: 650; }

@media (max-width: 1024px) {
  .agent-page { grid-template-columns: 230px minmax(0, 1fr); }
  .editor-panel { padding: var(--space-6) var(--space-4); }
  .resource-grid { grid-template-columns: 1fr; }
  .channel-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}

@media (max-width: 768px) {
  .agent-page { display: block; min-height: 0; }
  .agent-list-panel { padding: var(--space-4); border-right: 0; border-bottom: 1px solid var(--border-color); }
  .agent-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .editor-panel { padding: var(--space-5) var(--space-4) var(--space-8); }
  .editor-section { padding: var(--space-4); }
  /* 六列表格在窄屏上改为纵向字段布局，避免列宽被挤到不可读。 */
  .session-table-head { display: none; }
  .session-table-row { grid-template-columns: minmax(0, 1fr); gap: var(--space-1); }
  .session-actions { justify-content: flex-start; }
}

@media (max-width: 520px) {
  .panel-heading, .editor-header { align-items: flex-start; flex-direction: column; }
  .header-actions { width: 100%; justify-content: flex-start; }
  .header-actions .n-button { flex: 1; }
  .two-columns, .channel-grid { grid-template-columns: 1fr; }
  .full-width { grid-column: auto; }
  .agent-list { grid-template-columns: 1fr; }
  .binding-row, .relation-row { align-items: stretch; flex-wrap: wrap; }
  .binding-row > .n-select, .binding-row > .n-input, .relation-row > .n-input, .relation-row > .n-select { flex: 1 1 100%; }
  .current-version { flex: 1; text-align: left; }
}
</style>
