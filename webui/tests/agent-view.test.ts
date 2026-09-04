// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentView from '../src/views/llm/AgentView.vue'

const { listAgents, listResources, createAgentConfiguration, updateAgentConfiguration } = vi.hoisted(() => ({
  listAgents: vi.fn(),
  listResources: vi.fn(),
  createAgentConfiguration: vi.fn(),
  updateAgentConfiguration: vi.fn()
}))

vi.mock('../src/api/agent', () => ({
  listAgents,
  createAgentConfiguration,
  updateAgentConfiguration
}))

vi.mock('../src/api/resource', () => ({
  listResources
}))

// 模型优先链与 Provider 白名单的候选来自 `GET /llm/backends`。
// 不 stub 它，`loadBackends()` 会真的去 fetch，在测试环境里得到
// 「Failed to parse URL」——那条错误会渲染进页面并污染这一页的断言。
vi.mock('../src/api/llm', () => ({
  llmApi: {
    // 返回一个真实形状的后端：模型优先链的候选来自这里，
    // 空列表会让「从候选里选一个模型」这条路径无法测到。
    // `ability: 14` 是 `LLMAbility.TextChat`（Chat|TextInput|TextOutput）。
    getBackends: vi.fn(async () => ({
      data: {
        backends: [
          {
            name: 'openai-main',
            adapter: 'openai',
            config: {},
            enable: true,
            models: [{ id: 'primary-model', type: 'llm', ability: 14 }]
          }
        ]
      }
    }))
  }
}))

vi.mock('naive-ui', () => {
  const passthrough = (tag: string) => ({ name: tag, template: `<section><slot /></section>` })
  return {
    NAlert: passthrough('NAlert'),
    NButton: { name: 'NButton', emits: ['click'], template: '<button type="button" v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
    NCard: passthrough('NCard'),
    NCheckbox: { name: 'NCheckbox', props: ['checked', 'value'], emits: ['update:checked'], template: '<label><input type="checkbox" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked)" /><slot /></label>' },
    NCheckboxGroup: passthrough('NCheckboxGroup'),
    NForm: passthrough('NForm'),
    NFormItem: passthrough('NFormItem'),
    NInput: { name: 'NInput', inheritAttrs: false, props: ['value', 'inputProps'], emits: ['update:value'], template: '<div class="n-input" v-bind="$attrs"><input v-bind="inputProps" :value="value" @input="$emit(\'update:value\', $event.target.value)" /></div>' },
    NInputNumber: { name: 'NInputNumber', props: ['value'], emits: ['update:value'], template: '<input type="number" v-bind="$attrs" :value="value" @input="$emit(\'update:value\', Number($event.target.value))" />' },
    // 布局容器，只透传插槽。手写的 naive-ui mock 漏一个导出就整页崩，
    // 而报错（No "NSpace" export is defined）与被测行为完全无关。
    NSpace: { template: '<div class="n-space"><slot /></div>' },
    NSelect: { name: 'NSelect', inheritAttrs: false, props: ['value', 'options', 'inputProps'], emits: ['update:value'], template: '<div class="n-select" v-bind="$attrs"><select v-bind="inputProps" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select></div>' },
    NSwitch: { name: 'NSwitch', props: ['value'], emits: ['update:value'], template: '<input type="checkbox" v-bind="$attrs" :checked="value" @change="$emit(\'update:value\', $event.target.checked)" />' },
    NTag: { template: '<span><slot /></span>' },
    // 删除 Agent 走确认对话框（`DELETE /agents/<id>` 不可逆）。
    // 不 stub 它，组件在 setup 里就抛「No "useDialog" export」，
    // 整个文件一条都跑不起来。
    useDialog: () => ({ warning: () => {} })
  }
})

const agent = {
  agent_id: 'office-research',
  display_name: 'Office Research',
  enabled: true,
  workflow_id: null,
  model_priority: ['primary', 'backup'],
  provider_allowlist: ['openai'],
  capabilities: ['research'],
  prompt_bindings: [{ resource_id: 'prompt.office-research', resource_type: 'prompt', version: 'current', version_policy: 'current', enabled: true, content_sha256: 'hash', permissions: [], source: 'builtin' }],
  skill_bindings: [],
  memory_bindings: [],
  mcp_bindings: [{ resource_id: 'mcp.context7', resource_type: 'mcp', version: '1.0.0', version_policy: 'fixed', enabled: true, content_sha256: 'hash', permissions: [], source: 'builtin' }],
  hook_bindings: [],
  mcp_allowlist: ['mcp.context7:query-docs'],
  allow_tools: true,
  max_tool_iterations: 4,
  relations: {
    channels: ['webui', 'telegram'],
    accounts: [{ channel_type: 'telegram', adapter_instance: 'main', account_scope: 'default' }],
    sessions: ['webui/webui/main/c2c:research-1/research-1'],
    is_default: true
  }
}

const managedResource = (
  resource_id: string,
  type: 'prompt' | 'skill' | 'memory' | 'mcp' | 'hook',
  enabled = true,
  confirmation_required = false
) => ({
  resource_id,
  type,
  current_version: '1.0.0',
  source: 'builtin',
  entry: 'entry.md',
  permissions: [],
  content_sha256: 'hash',
  enabled,
  confirmation_required,
  workflow_id: null,
  installed_at: '2026-08-25T00:00:00Z',
  updated_at: '2026-08-25T00:00:00Z',
  versions: []
})

describe('AgentView', () => {
  beforeEach(() => {
    listAgents.mockReset().mockResolvedValue([agent])
    listResources.mockReset().mockResolvedValue([
      managedResource('prompt.office-research', 'prompt'),
      managedResource('mcp.context7', 'mcp')
    ])
    createAgentConfiguration.mockReset().mockResolvedValue(agent)
    updateAgentConfiguration.mockReset().mockResolvedValue(agent)
  })

  it('loads an editable agent workspace with resource bindings and channel relations', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('Office Research')
    expect(wrapper.text()).toContain('prompt.office-research')
    expect(wrapper.text()).toContain('mcp.context7')
    expect(wrapper.text()).toContain('Telegram')
    expect((wrapper.find('input[type="checkbox"][value="telegram"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.find('[data-test="agent-display-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="resource-binding-prompt"]').exists()).toBe(true)
  })

  it('submits the complete existing configuration through the update endpoint', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    await wrapper.get('[data-test="agent-display-name"] input').setValue('Updated Office Research')
    await wrapper.get('[data-test="save-agent"]').trigger('click')
    await flushPromises()

    expect(updateAgentConfiguration).toHaveBeenCalledWith(
      'office-research',
      expect.objectContaining({
        display_name: 'Updated Office Research',
        prompt_bindings: expect.arrayContaining([
          expect.objectContaining({ resource_id: 'prompt.office-research' })
        ]),
        mcp_bindings: expect.arrayContaining([
          expect.objectContaining({ resource_id: 'mcp.context7' })
        ])
      })
    )
  })

  it('restores Memory bindings from the API and keeps them in the update payload', async () => {
    const memoryBinding = {
      resource_id: 'memory.research-context',
      resource_type: 'memory',
      version: '1.0.0',
      version_policy: 'current',
      enabled: true,
      content_sha256: 'memory-hash',
      permissions: [],
      source: 'builtin'
    }
    listAgents.mockResolvedValue([{ ...agent, memory_bindings: [memoryBinding] }])
    listResources.mockResolvedValue([
      managedResource('prompt.office-research', 'prompt'),
      managedResource('memory.research-context', 'memory'),
      managedResource('mcp.context7', 'mcp')
    ])

    const wrapper = mount(AgentView)
    await flushPromises()

    expect((wrapper.get('[aria-label="Memory资源"]').element as HTMLSelectElement).value).toBe('memory.research-context')

    await wrapper.get('[data-test="save-agent"]').trigger('click')
    await flushPromises()

    expect(updateAgentConfiguration).toHaveBeenCalledWith(
      'office-research',
      expect.objectContaining({
        memory_bindings: [
          expect.objectContaining({
            resource_id: 'memory.research-context',
            resource_type: 'memory',
            version_policy: 'current',
            enabled: true
          })
        ]
      })
    )
  })

  it('does not offer globally disabled or confirmation-pending resources for new bindings', async () => {
    listResources.mockResolvedValue([
      managedResource('prompt.office-research', 'prompt'),
      managedResource('memory.disabled', 'memory', false),
      managedResource('memory.pending-confirmation', 'memory', false, true),
      managedResource('mcp.context7', 'mcp')
    ])

    const wrapper = mount(AgentView)
    await flushPromises()

    const memorySection = wrapper.get('[data-test="resource-binding-memory"]')
    const addButton = memorySection.get('button')
    expect((addButton.element as HTMLButtonElement).disabled).toBe(true)
    expect(memorySection.text()).toContain('没有可绑定的已启用资源')

    await addButton.trigger('click')
    expect(memorySection.find('[aria-label="Memory资源"]').exists()).toBe(false)
  })

  it('gives every top-level switch an accessible name', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.get('[aria-label="允许 Agent 接收新请求"]').attributes('type')).toBe('checkbox')
    expect(wrapper.get('[aria-label="设为默认 Agent"]').attributes('type')).toBe('checkbox')
    expect(wrapper.get('[aria-label="允许调用工具"]').attributes('type')).toBe('checkbox')
    expect(wrapper.find('[aria-label="允许 Agent 接收新请求"][aria-labelledby]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="设为默认 Agent"][aria-labelledby]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="允许调用工具"][aria-labelledby]').exists()).toBe(false)
  })

  it('forwards input and select labels to their semantic controls', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    // 模型链改成了可筛选可创建的选择器：模型 ID 就在「模型配置」页上，
    // 手打拼错的后果是运行时失败，而那时的报错与拼写无关。
    const modelSelect = wrapper.get('select[aria-label="1号模型"]')
    const promptSelect = wrapper.get('select[aria-label="Prompt资源"]')
    expect(modelSelect.element.tagName).toBe('SELECT')
    expect(promptSelect.element.tagName).toBe('SELECT')
    expect(wrapper.find('.n-input[aria-label]').exists()).toBe(false)
    expect(wrapper.find('.n-select[aria-label]').exists()).toBe(false)
  })

  it('starts a new configuration with a stable empty editor', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    await wrapper.get('[data-test="new-agent"]').trigger('click')

    expect((wrapper.get('[data-test="agent-id"] input').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('新建 Agent')
  })

  it('creates instead of updating after a new agent id is entered', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    await wrapper.get('[data-test="new-agent"]').trigger('click')
    await wrapper.get('[data-test="agent-id"] input').setValue('new-research-agent')
    // 从候选里选（mock 里那个后端提供了 `primary-model`），
    // 这与用户真实操作一致：不再手打模型 ID。
    await wrapper.get('select[aria-label="1号模型"]').setValue('primary-model')
    await wrapper.get('[data-test="save-agent"]').trigger('click')
    await flushPromises()

    expect(createAgentConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ agent_id: 'new-research-agent' })
    )
    expect(updateAgentConfiguration).not.toHaveBeenCalled()
  })

  it('keeps a successfully loaded Agent editable when the resource catalog fails', async () => {
    listResources.mockRejectedValue(new Error('resource catalog unavailable'))

    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('Office Research')
    expect(wrapper.text()).toContain('资源目录加载失败')
    expect(wrapper.find('[data-test="agent-display-name"] input').exists()).toBe(true)

    await wrapper.get('[data-test="agent-display-name"] input').setValue('Still Editable')
    await wrapper.get('[data-test="save-agent"]').trigger('click')
    await flushPromises()

    expect(updateAgentConfiguration).toHaveBeenCalledWith(
      'office-research',
      expect.objectContaining({
        display_name: 'Still Editable',
        prompt_bindings: [expect.objectContaining({ resource_id: 'prompt.office-research' })]
      })
    )
  })

  it('shows an Agent loading error instead of an empty list or new configuration', async () => {
    listAgents.mockRejectedValue(new Error('agent registry unavailable'))

    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('Agent 配置加载失败')
    expect(wrapper.text()).not.toContain('还没有 Agent')
    expect(wrapper.text()).not.toContain('新建 Agent')
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('does not overwrite unsaved Agent edits when a resource refresh fails', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()
    await wrapper.get('[data-test="agent-display-name"] input').setValue('Unsaved Draft')

    listResources.mockRejectedValueOnce(new Error('refresh unavailable'))
    await wrapper.get('[data-test="refresh-resources"]').trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-test="agent-display-name"] input').element as HTMLInputElement).value).toBe('Unsaved Draft')
    expect(wrapper.text()).toContain('资源目录加载失败')
    expect(wrapper.find('[aria-label="Prompt资源"]').exists()).toBe(true)
  })
})
