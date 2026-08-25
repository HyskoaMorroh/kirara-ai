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
    NInput: { name: 'NInput', inheritAttrs: false, props: ['value'], emits: ['update:value'], template: '<input v-bind="$attrs" :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
    NInputNumber: { name: 'NInputNumber', props: ['value'], emits: ['update:value'], template: '<input type="number" v-bind="$attrs" :value="value" @input="$emit(\'update:value\', Number($event.target.value))" />' },
    NSelect: { name: 'NSelect', inheritAttrs: false, props: ['value', 'options'], emits: ['update:value'], template: '<select v-bind="$attrs" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>' },
    NSwitch: { name: 'NSwitch', props: ['value'], emits: ['update:value'], template: '<input type="checkbox" v-bind="$attrs" :checked="value" @change="$emit(\'update:value\', $event.target.checked)" />' },
    NTag: { template: '<span><slot /></span>' }
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

describe('AgentView', () => {
  beforeEach(() => {
    listAgents.mockReset().mockResolvedValue([agent])
    listResources.mockReset().mockResolvedValue([
      { resource_id: 'prompt.office-research', type: 'prompt', current_version: '1.0.0' },
      { resource_id: 'mcp.context7', type: 'mcp', current_version: '1.0.0' }
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

    await wrapper.get('[data-test="agent-display-name"]').setValue('Updated Office Research')
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

  it('starts a new configuration with a stable empty editor', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    await wrapper.get('[data-test="new-agent"]').trigger('click')

    expect((wrapper.get('[data-test="agent-id"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('新建 Agent')
  })

  it('creates instead of updating after a new agent id is entered', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    await wrapper.get('[data-test="new-agent"]').trigger('click')
    await wrapper.get('[data-test="agent-id"]').setValue('new-research-agent')
    await wrapper.get('[aria-label="1号模型"]').setValue('primary-model')
    await wrapper.get('[data-test="save-agent"]').trigger('click')
    await flushPromises()

    expect(createAgentConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ agent_id: 'new-research-agent' })
    )
    expect(updateAgentConfiguration).not.toHaveBeenCalled()
  })
})
