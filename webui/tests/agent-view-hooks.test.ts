// @vitest-environment happy-dom

/**
 * Hooks previously had no UI at all: a declaration could only be validated by
 * installing it and waiting for a real request, so a wrong event name, an invalid
 * matcher or a `command` in a forbidden place all surfaced on the production
 * path. This panel lists what each hook declares and lets the operator dry-run
 * one event against a tool name without executing anything.
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
    NTag: passthrough('NTag', 'span')
  }
})

const hook = (overrides: Record<string, unknown> = {}) => ({
  resource_id: 'hook.ai-debug',
  version: '1.0.0',
  enabled: true,
  events: [
    {
      event: 'PreToolUse',
      enabled: true,
      kind: 'handler',
      matcher: 'Bash',
      requires_process_execution: false
    }
  ],
  ...overrides
})

describe('AgentView hook inspection', () => {
  beforeEach(() => {
    Object.values(agentApi).forEach((mock) => mock.mockReset())
    resourceApi.listResources.mockReset()
    agentApi.listAgents.mockResolvedValue([])
    resourceApi.listResources.mockResolvedValue([])
    agentApi.listSessions.mockResolvedValue({ items: [] })
    agentApi.listPendingConfirmations.mockResolvedValue({ items: [] })
    agentApi.listHookDeclarations.mockResolvedValue({ items: [hook()] })
    agentApi.previewHookEvent.mockResolvedValue({ would_run: true, matcher: 'Bash' })
  })

  it('loads hook declarations on mount', async () => {
    mount(AgentView)
    await flushPromises()

    expect(agentApi.listHookDeclarations).toHaveBeenCalledTimes(1)
  })

  it('shows the declared event and its matcher', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('PreToolUse')
    expect(text).toContain('限定工具：Bash')
  })

  it('says so when an event applies to every call', async () => {
    agentApi.listHookDeclarations.mockResolvedValue({
      items: [hook({ events: [{ event: 'PreToolUse', enabled: true, matcher: null }] })]
    })
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('适用于全部调用')
  })

  it('flags a command hook as needing process execution', async () => {
    agentApi.listHookDeclarations.mockResolvedValue({
      items: [
        hook({
          events: [
            {
              event: 'PreToolUse',
              enabled: true,
              kind: 'command',
              requires_process_execution: true
            }
          ]
        })
      ]
    })
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('需要进程执行权限')
  })

  it('surfaces a per-event declaration error instead of hiding it', async () => {
    agentApi.listHookDeclarations.mockResolvedValue({
      items: [
        hook({
          events: [{ event: 'PreToolUse', error: 'hook event matcher is not a valid pattern' }]
        })
      ]
    })
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('matcher is not a valid pattern')
  })

  it('dry-runs one event and reports that it would fire', async () => {
    const wrapper = mount(AgentView)
    await flushPromises()

    const button = wrapper.findAll('button').find((item) => item.text() === '预演')
    await button!.trigger('click')
    await flushPromises()

    expect(agentApi.previewHookEvent).toHaveBeenCalledWith(
      'hook.ai-debug',
      'PreToolUse',
      undefined
    )
    expect(wrapper.text()).toContain('会触发')
  })

  it('reports a non-matching dry-run with its reason', async () => {
    agentApi.previewHookEvent.mockResolvedValue({
      would_run: false,
      reason: 'matcher_not_matched'
    })
    const wrapper = mount(AgentView)
    await flushPromises()

    const button = wrapper.findAll('button').find((item) => item.text() === '预演')
    await button!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('matcher_not_matched')
  })

  it('shows an empty state when no hooks are installed', async () => {
    agentApi.listHookDeclarations.mockResolvedValue({ items: [] })
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('尚未安装 Hook')
  })

  it('reports a missing agent runtime instead of failing silently', async () => {
    agentApi.listHookDeclarations.mockRejectedValue(
      new Error('agent hook runtime is not configured')
    )
    const wrapper = mount(AgentView)
    await flushPromises()

    expect(wrapper.text()).toContain('agent hook runtime is not configured')
  })
})
