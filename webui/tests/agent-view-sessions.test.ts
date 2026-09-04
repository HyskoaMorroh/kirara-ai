// @vitest-environment happy-dom

/**
 * Session management had no UI at all: the only session-related control was a
 * free-text binding input, so an operator could not see which sessions existed,
 * how long their history was, or whether an operation was stuck awaiting
 * confirmation — the store held all of it.
 *
 * These tests pin the contract that matters: the panel lists sessions, exposes
 * clear/delete, surfaces the pending queue, and never renders conversation text
 * (the API deliberately does not return any).
 */

import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentView from '../src/views/llm/AgentView.vue'

const { agentApi, resourceApi } = vi.hoisted(() => ({
  agentApi: {
    listAgents: vi.fn(),
    createAgentConfiguration: vi.fn(),
    updateAgentConfiguration: vi.fn(),
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    clearSessionHistory: vi.fn(),
    listPendingConfirmations: vi.fn(),
    listHookDeclarations: vi.fn(),
    previewHookEvent: vi.fn()
  },
  resourceApi: { listResources: vi.fn() }
}))

vi.mock('@/api/agent', () => agentApi)
vi.mock('@/api/resource', () => resourceApi)

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'div') => ({
    name,
    inheritAttrs: false,
    template: `<${tag} v-bind="$attrs"><slot /></${tag}>`
  })
  return {
    // 布局容器，只透传插槽。手写的 naive-ui mock 漏一个导出就整页崩，
    // 而报错（No "NSpace" export is defined）与被测行为完全无关。
    NSpace: { template: '<div class="n-space"><slot /></div>' },
    NAlert: passthrough('NAlert', 'section'),
    NButton: {
      name: 'NButton',
      inheritAttrs: false,
      emits: ['click'],
      template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>'
    },
    NInput: passthrough('NInput', 'span'),
    NInputNumber: passthrough('NInputNumber', 'span'),
    NSelect: passthrough('NSelect', 'span'),
    NSwitch: passthrough('NSwitch', 'span'),
    NTag: passthrough('NTag', 'span'),
    // 见 `agent-view.test.ts` 里的同一条：删除 Agent 需要 `useDialog`。
    useDialog: () => ({ warning: () => {} })
  }
})

const session = (overrides: Record<string, unknown> = {}) => ({
  session_id: 'a'.repeat(64),
  agent_id: 'agent-1',
  message_count: 12,
  updated_at: '2026-08-28T06:00:00+00:00',
  pending_confirmations: 0,
  ...overrides
})

describe('AgentView session management', () => {
  beforeEach(() => {
    Object.values(agentApi).forEach((mock) => mock.mockReset())
    resourceApi.listResources.mockReset()
    agentApi.listAgents.mockResolvedValue([])
    resourceApi.listResources.mockResolvedValue([])
    agentApi.listSessions.mockResolvedValue({ items: [session()] })
    agentApi.listPendingConfirmations.mockResolvedValue({ items: [] })
    agentApi.listHookDeclarations.mockResolvedValue({ items: [] })
    agentApi.previewHookEvent.mockResolvedValue({ would_run: true })
    agentApi.deleteSession.mockResolvedValue({ deleted: true })
    agentApi.clearSessionHistory.mockResolvedValue({ cleared: true })
  })

  it('loads sessions and the pending queue on mount', async () => {
    mount(AgentView)
    await flushPromises()

    expect(agentApi.listSessions).toHaveBeenCalledTimes(1)
    expect(agentApi.listPendingConfirmations).toHaveBeenCalledTimes(1)
  })

  it('renders one row per persisted session with its counts', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('12')
    expect(text).toContain('agent-1')
    // Only the digest prefix is shown; the full 64-char id would break the layout.
    expect(text).toContain('a'.repeat(12))
  })

  it('shows an empty state instead of a broken table when nothing is stored', async () => {
    agentApi.listSessions.mockResolvedValue({ items: [] })
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无持久化会话')
  })

  it('surfaces a pending confirmation with its tool name and status', async () => {
    agentApi.listPendingConfirmations.mockResolvedValue({
      items: [
        {
          confirmation_id: 'c'.repeat(32),
          agent_id: 'agent-1',
          status: 'awaiting_confirmation',
          created_at: null,
          updated_at: null,
          expires_at: '2026-08-28T07:00:00+00:00',
          correlation_id: null,
          tool_name: 'Bash'
        }
      ]
    })
    const wrapper = mount(AgentView)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('等待确认（1）')
    expect(text).toContain('Bash')
  })

  it('clears one session history and refreshes the listing', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()
    agentApi.listSessions.mockClear()

    const button = wrapper
      .findAll('button')
      .find((item) => item.text() === '清空历史')
    await button!.trigger('click')
    await flushPromises()

    expect(agentApi.clearSessionHistory).toHaveBeenCalledWith('a'.repeat(64))
    expect(agentApi.listSessions).toHaveBeenCalledTimes(1)
  })

  it('deletes one session and refreshes the listing', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()
    agentApi.listSessions.mockClear()

    const button = wrapper.findAll('button').find((item) => item.text() === '删除')
    await button!.trigger('click')
    await flushPromises()

    expect(agentApi.deleteSession).toHaveBeenCalledWith('a'.repeat(64))
    expect(agentApi.listSessions).toHaveBeenCalledTimes(1)
  })

  it('reports a session store that is not deployed instead of failing silently', async () => {
    agentApi.listSessions.mockRejectedValue(new Error('session store is not configured'))
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('session store is not configured')
  })

  it('keeps the agent editor usable when session loading fails', async () => {
    agentApi.listSessions.mockRejectedValue(new Error('boom'))
    const wrapper = mount(AgentView)
    await flushPromises()

    // The Agent list panel must still render; sessions are an adjacent concern.
    expect(wrapper.text()).toContain('Agent 管理')
  })
})
