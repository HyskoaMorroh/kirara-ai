<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NInput, NInputNumber, NSelect, NSwitch, NTag } from 'naive-ui'

import {
  createAgentConfiguration,
  listAgents,
  updateAgentConfiguration,
  type AgentChannel,
  type AgentConfigurationRequest,
  type AgentRelations,
  type AgentResourceBindingInput,
  type AgentResourceType,
  type AgentSummary
} from '@/api/agent'
import { listResources } from '@/api/resource'
import type { ManagedResource } from '@/api/resource'

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

const agentOptions = ref<AgentSummary[]>([])
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
const providerText = ref('')
const capabilitiesText = ref('')
const mcpAllowlistText = ref('')
const persistedAgentId = ref<string | null>(null)

const isCreating = computed(() => persistedAgentId.value === null)
const selectedAgent = computed(() =>
  agentOptions.value.find((agent) => agent.agent_id === selectedAgentId.value) || null
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
  providerText.value = next.provider_allowlist.join(', ')
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
  await Promise.all([loadAgents(), loadResourceCatalog()])
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
    provider_allowlist: parseList(providerText.value),
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
})
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
              <n-input v-model:value="form.model_priority[index]" :placeholder="index === 0 ? '主模型 ID' : '备用模型 ID'" :input-props="{ 'aria-label': `${index + 1}号模型` }" />
              <n-button quaternary size="small" :disabled="form.model_priority.length === 1" :aria-label="`移除第 ${index + 1} 个模型`" @click="removeModel(index)">移除</n-button>
            </div>
          </div>
          <div class="form-grid two-columns compact-top">
            <label class="field"><span>Provider 白名单</span><n-input v-model:value="providerText" placeholder="用逗号分隔，留空表示不限制" /></label>
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
.compact-top { margin-top: var(--space-4); }
.ordered-list { display: grid; gap: var(--space-2); }
.ordered-row { gap: var(--space-3); min-width: 0; }
.order-number { display: grid; place-items: center; flex: 0 0 28px; width: 28px; height: 28px; border-radius: var(--radius-pill); color: var(--primary-color-text); background: var(--selection-bg-color); font-size: var(--font-size-sm); font-weight: 650; }
.ordered-row > .n-input, .binding-row > .n-select, .binding-row > .n-input, .relation-row > .n-input, .relation-row > .n-select { min-width: 0; flex: 1; }
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
