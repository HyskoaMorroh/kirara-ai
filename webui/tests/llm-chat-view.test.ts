// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatView from '../src/views/llm/ChatView.vue'

const { chat, listAgents } = vi.hoisted(() => ({
  chat: vi.fn(),
  listAgents: vi.fn()
}))

vi.mock('../src/api/llm', () => ({
  llmApi: { chat }
}))

vi.mock('../src/api/agent', () => ({
  listAgents
}))

vi.mock('naive-ui', () => {
  const passthrough = (tag: string) => ({
    name: tag,
    template: `<section><slot name="header" /><slot /></section>`
  })

  return {
    NAlert: passthrough('NAlert'),
    NButton: {
      name: 'NButton',
      emits: ['click'],
      template: '<button type="button" v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'
    },
    NCard: passthrough('NCard'),
    NIcon: { template: '<i><slot /></i>' },
    NInput: {
      name: 'NInput',
      inheritAttrs: false,
      props: ['value'],
      emits: ['update:value'],
      template: '<textarea v-bind="$attrs" :value="value" @input="$emit(\'update:value\', $event.target.value)" />'
    },
    NSelect: {
      name: 'NSelect',
      inheritAttrs: false,
      props: ['value', 'options'],
      emits: ['update:value'],
      template: '<select v-bind="$attrs" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>'
    },
    NTag: { template: '<span><slot /></span>' }
  }
})

const agent = {
  agent_id: 'research-agent',
  display_name: 'Research Agent',
  enabled: true,
  workflow_id: null,
  model_priority: ['primary-model', 'backup-model'],
  provider_allowlist: ['test-provider'],
  capabilities: ['research'],
  prompt_bindings: [],
  skill_bindings: [],
  memory_bindings: [],
  mcp_bindings: [],
  hook_bindings: [],
  mcp_allowlist: ['context7'],
  allow_tools: true,
  max_tool_iterations: 3,
  relations: {
    channels: ['webui', 'telegram'],
    accounts: [],
    sessions: [],
    is_default: true
  }
}

describe('ChatView', () => {
  beforeEach(() => {
    chat.mockReset()
    listAgents.mockReset()
    listAgents.mockResolvedValue([agent])
    chat.mockResolvedValue({
      status: 'completed',
      text: 'The paper is about reproducible workflows.',
      agent_id: 'research-agent',
      session_id: 'research-1',
      session_key: 'webui/webui/webui/c2c:research-1/research-1',
      confirmation_id: null
    })
  })

  it('loads the unified Agent relation and sends a private chat through the API', async () => {
    const wrapper = mount(ChatView)
    await flushPromises()

    expect(wrapper.text()).toContain('Research Agent')
    expect(wrapper.text()).toContain('primary-model -> backup-model')
    expect(wrapper.text()).toContain('webui')

    await wrapper.get('[data-test="session-id"]').setValue('research-1')
    await wrapper.get('[data-test="message-input"]').setValue('Summarize this paper')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(chat).toHaveBeenCalledWith({
      message: 'Summarize this paper',
      session_id: 'research-1',
      username: 'WebUI user',
      chat_type: 'c2c',
      agent_id: 'research-agent'
    })
    expect(wrapper.text()).toContain('Summarize this paper')
    expect(wrapper.text()).toContain('The paper is about reproducible workflows.')
    expect(wrapper.text()).toContain('主模型链已返回')
  })

  it('requires a group id for group chat and resumes a pending confirmation explicitly', async () => {
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="chat-type-group"]').trigger('click')
    await wrapper.get('[data-test="message-input"]').setValue('Please publish the notes')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    expect(chat).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('群聊必须填写群组 ID')

    await wrapper.get('[data-test="group-id"]').setValue('study-room')
    chat.mockResolvedValueOnce({
      status: 'awaiting_confirmation',
      text: '需要确认后才能继续',
      agent_id: 'research-agent',
      session_id: 'member-7',
      session_key: 'webui/webui/webui/group:study-room/member-7',
      confirmation_id: 'confirm-123'
    })
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('需要确认后才能继续')
    expect(wrapper.text()).toContain('confirm-123')
    await wrapper.get('[aria-label="确认待处理操作"]').trigger('click')
    await flushPromises()

    expect(chat).toHaveBeenLastCalledWith({
      message: '确认 confirm-123',
      session_id: 'member-7',
      username: 'WebUI user',
      chat_type: 'group',
      group_id: 'study-room',
      agent_id: 'research-agent'
    })
  })

  it('keeps a failed request visible without leaking provider details', async () => {
    chat.mockRejectedValueOnce(new Error('Agent runtime failed'))
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('Run diagnostics')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('消息发送失败')
    expect(wrapper.text()).toContain('Agent runtime failed')
  })
})
