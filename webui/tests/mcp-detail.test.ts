// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MCPDetail from '../src/views/mcp/MCPDetail.vue'

const {
  getServerById,
  fetchMCPServerTools,
  startMCPServer,
  stopMCPServer,
  httpGet,
  httpPost,
  listAgents,
  messageError,
  auditTableRows
} = vi.hoisted(() => ({
  getServerById: vi.fn(),
  fetchMCPServerTools: vi.fn(),
  startMCPServer: vi.fn(),
  stopMCPServer: vi.fn(),
  httpGet: vi.fn(),
  httpPost: vi.fn(),
  listAgents: vi.fn(),
  messageError: vi.fn(),
  auditTableRows: vi.fn()
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'context7' } }),
  useRouter: () => ({ push: vi.fn() })
}))

vi.mock('../src/views/mcp/mcp.vm', () => ({
  useMCPViewModel: () => ({
    getServerById,
    fetchServerTools: fetchMCPServerTools,
    startServer: startMCPServer,
    stopServer: stopMCPServer,
    openEditModal: vi.fn()
  })
}))

vi.mock('../src/utils/http', () => ({
  http: {
    get: httpGet,
    post: httpPost
  },
  HttpRequestError: class HttpRequestError extends Error {
    status: number
    data: unknown
    constructor(message: string, status: number, data: unknown) {
      super(message)
      this.status = status
      this.data = data
    }
  }
}))

vi.mock('../src/api/agent', () => ({
  listAgents
}))

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'section') => ({
    name,
    template: `<${tag} v-bind="$attrs"><slot name="header" /><slot name="header-extra" /><slot /></${tag}>`
  })
  return {
    NAlert: passthrough('NAlert'),
    NButton: {
      name: 'NButton',
      emits: ['click'],
      template: '<button type="button" v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'
    },
    NCard: passthrough('NCard'),
    NCode: passthrough('NCode', 'pre'),
    NDataTable: {
      name: 'NDataTable',
      props: ['columns', 'data'],
      setup(props: { columns: Array<{ render?: (row: unknown) => unknown }>; data: unknown[] }) {
        return () => h('div', { 'data-test': 'audit-table' }, props.data.map((row) =>
          h('div', { class: 'audit-row' }, props.columns.map((column) =>
            h('span', { class: 'audit-cell' }, column.render ? String(column.render(row) ?? '') : '')
          ))
        ))
      }
    },
    NDescriptions: passthrough('NDescriptions'),
    NDescriptionsItem: passthrough('NDescriptionsItem'),
    NDivider: passthrough('NDivider'),
    NEmpty: passthrough('NEmpty'),
    NForm: passthrough('NForm', 'form'),
    NFormItem: {
      name: 'NFormItem',
      props: ['label'],
      template: '<label><span class="form-label">{{ label }}</span><slot /></label>'
    },
    NGrid: passthrough('NGrid'),
    NGridItem: passthrough('NGridItem'),
    NIcon: passthrough('NIcon', 'span'),
    NInput: {
      name: 'NInput',
      inheritAttrs: false,
      props: ['value', 'type', 'inputProps'],
      emits: ['update:value'],
      template: '<textarea v-if="type === \'textarea\'" v-bind="inputProps" :value="value" @input="$emit(\'update:value\', $event.target.value)" /><input v-else v-bind="inputProps" type="text" :value="value" @input="$emit(\'update:value\', $event.target.value)" />'
    },
    NInputNumber: {
      name: 'NInputNumber',
      inheritAttrs: false,
      props: ['value', 'inputProps'],
      emits: ['update:value'],
      template: '<input v-bind="inputProps" type="number" :value="value" @input="$emit(\'update:value\', Number($event.target.value))" />'
    },
    NModal: {
      name: 'NModal',
      props: ['show'],
      emits: ['update:show'],
      template: '<section v-if="show" role="dialog"><slot /><slot name="footer" /></section>'
    },
    NPagination: {
      name: 'NPagination',
      props: ['page', 'pageCount', 'pageSize', 'pageSizes'],
      emits: ['update:page', 'update:page-size'],
      template: '<div data-test="audit-pagination"></div>'
    },
    NSelect: {
      name: 'NSelect',
      props: ['value', 'options', 'inputProps'],
      emits: ['update:value'],
      template: '<select v-bind="inputProps" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option value="">请选择</option><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>'
    },
    NSlider: {
      name: 'NSlider',
      props: ['value'],
      emits: ['update:value'],
      template: '<input type="range" :value="value" @input="$emit(\'update:value\', Number($event.target.value))" />'
    },
    NSpace: passthrough('NSpace'),
    NSpin: passthrough('NSpin'),
    NSwitch: {
      name: 'NSwitch',
      inheritAttrs: false,
      props: ['value'],
      emits: ['update:value'],
      template: '<input type="checkbox" v-bind="$attrs" :checked="value" @change="$emit(\'update:value\', $event.target.checked)" />'
    },
    NTabPane: passthrough('NTabPane'),
    NTabs: passthrough('NTabs'),
    NTag: passthrough('NTag', 'span'),
    NTimeline: passthrough('NTimeline'),
    NTimelineItem: passthrough('NTimelineItem'),
    useDialog: () => ({}),
    useMessage: () => ({ success: vi.fn(), error: messageError })
  }
})

const schemaTool = {
  name: 'query-docs',
  description: 'Query documentation',
  input_schema: {
    type: 'object',
    required: ['query'],
    properties: {
      query: { type: 'string', description: 'Search text' },
      includeDrafts: { type: 'boolean', default: true },
      limit: { type: 'integer', default: 3 },
      threshold: { type: 'number', default: 0.5 },
      mode: { type: 'string', enum: ['fast', 'thorough'], default: 'fast' },
      filters: { type: 'object', default: { language: 'en' } },
      tags: { type: 'array', default: ['research'] }
    }
  }
}

function buttonWithText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().trim() === text)
  if (!button) throw new Error(`Button not found: ${text}`)
  return button
}

describe('MCPDetail schema forms', () => {
  beforeEach(() => {
    listAgents.mockReset().mockResolvedValue([
      {
        agent_id: 'research-agent',
        display_name: 'Research Agent',
        enabled: true,
        relations: { channels: [], accounts: [], sessions: [], is_default: false }
      }
    ])
    getServerById.mockReset().mockResolvedValue({
      id: 'context7',
      name: 'Context7',
      server: { type: 'stdio', command: 'npx', args: ['-y', '@upstash/context7-mcp'], env: {}, headers: {} },
      connection_state: 'connected'
    })
    fetchMCPServerTools.mockReset().mockResolvedValue([schemaTool])
    auditTableRows.mockReset()
    httpGet.mockReset().mockImplementation((path: string) => {
      if (path.endsWith('/resources')) return Promise.resolve([])
      if (path.startsWith('/mcp/audit?')) {
        const query = new URLSearchParams(path.slice(path.indexOf('?') + 1))
        auditTableRows(query)
        return Promise.resolve({
          items: [{
            component: 'mcp',
            timestamp: '2026-08-27T01:02:03Z',
            server: query.get('server_id'),
            operation: query.get('operation') || 'connect',
            duration_ms: 12.5,
            outcome: query.get('outcome') || 'success',
            correlation_id: null,
            error: null
          }],
          total: 1,
          offset: Number(query.get('offset') || 0),
          limit: Number(query.get('limit') || 50),
          has_more: false,
          persistent: true,
          retention_limit: 1000
        })
      }
      if (path.endsWith('/prompts')) {
        return Promise.resolve([
          {
            id: 'research-brief',
            name: 'research-brief',
            description: 'Build a brief',
            arguments: [
              { name: 'topic', description: 'Research topic', required: true },
              { name: 'audience', description: 'Target audience', required: false },
              { name: 'tone', description: 'Writing tone', required: false }
            ]
          }
        ])
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`))
    })
    httpPost.mockReset().mockResolvedValue({ text: 'ready' })
    messageError.mockReset()
  })

  it('renders JSON Schema controls and submits typed values with defaults', async () => {
    const wrapper = mount(MCPDetail)
    await flushPromises()
    await buttonWithText(wrapper, '执行工具').trigger('click')

    expect((wrapper.get('[data-test="tool-param-includeDrafts"]').element as HTMLInputElement).type).toBe('checkbox')
    expect((wrapper.get('[data-test="tool-param-includeDrafts"]').element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.get('[data-test="tool-param-limit"]').element as HTMLInputElement).type).toBe('number')
    expect((wrapper.get('[data-test="tool-param-limit"]').element as HTMLInputElement).value).toBe('3')
    expect((wrapper.get('[data-test="tool-param-threshold"]').element as HTMLInputElement).value).toBe('0.5')
    expect(wrapper.get('[data-test="tool-param-mode"]').element.tagName).toBe('SELECT')
    expect((wrapper.get('[data-test="tool-param-mode"]').element as HTMLSelectElement).value).toBe('fast')
    expect(wrapper.get('[data-test="tool-param-filters"]').element.tagName).toBe('TEXTAREA')
    expect(wrapper.get('[data-test="tool-param-tags"]').element.tagName).toBe('TEXTAREA')

    await wrapper.get('[data-test="tool-param-query"]').setValue('MCP prompt arguments')
    await wrapper.get('[data-test="tool-param-filters"]').setValue('{"language":"zh"}')
    await wrapper.get('[data-test="tool-param-tags"]').setValue('["mcp","prompt"]')
    await buttonWithText(wrapper, '执行').trigger('click')
    await flushPromises()

    expect(httpPost).toHaveBeenCalledWith('/mcp/servers/context7/tools/call', {
      toolName: 'query-docs',
      params: {
        query: 'MCP prompt arguments',
        includeDrafts: true,
        limit: 3,
        threshold: 0.5,
        mode: 'fast',
        filters: { language: 'zh' },
        tags: ['mcp', 'prompt']
      },
      agent_id: 'research-agent'
    })
  })

  it('loads redacted runtime audit for this server and sends filter/pagination query values', async () => {
    const wrapper = mount(MCPDetail)
    await flushPromises()

    expect(auditTableRows).toHaveBeenCalledWith(expect.any(URLSearchParams))
    const initialQuery = auditTableRows.mock.calls[0][0] as URLSearchParams
    expect(initialQuery.get('server_id')).toBe('context7')
    expect(initialQuery.get('offset')).toBe('0')
    expect(initialQuery.get('limit')).toBe('20')
    expect(wrapper.text()).toContain('已持久化到服务器')
    expect(wrapper.text()).toContain('连接')

    await wrapper.get('[data-test="audit-operation"]').setValue('call_tool')
    await flushPromises()
    const filteredQuery = auditTableRows.mock.calls.at(-1)?.[0] as URLSearchParams
    expect(filteredQuery.get('operation')).toBe('call_tool')
    expect(filteredQuery.get('server_id')).toBe('context7')
    expect(filteredQuery.get('offset')).toBe('0')

    wrapper.findComponent({ name: 'NPagination' }).vm.$emit('update:page', 2)
    await flushPromises()
    const secondPageQuery = auditTableRows.mock.calls.at(-1)?.[0] as URLSearchParams
    expect(secondPageQuery.get('offset')).toBe('20')

    wrapper.findComponent({ name: 'NPagination' }).vm.$emit('update:page-size', 50)
    await flushPromises()
    const resizedQuery = auditTableRows.mock.calls.at(-1)?.[0] as URLSearchParams
    expect(resizedQuery.get('offset')).toBe('0')
    expect(resizedQuery.get('limit')).toBe('50')
  })

  it('blocks a tool call when required or structured values are invalid', async () => {
    const wrapper = mount(MCPDetail)
    await flushPromises()
    await buttonWithText(wrapper, '执行工具').trigger('click')
    await wrapper.get('[data-test="tool-param-filters"]').setValue('{bad json')
    await buttonWithText(wrapper, '执行').trigger('click')
    await flushPromises()

    expect(httpPost).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalledWith(expect.stringContaining('query'))
  })

  it('builds the prompt form from declared arguments and submits only that argument map', async () => {
    const wrapper = mount(MCPDetail)
    await flushPromises()
    await buttonWithText(wrapper, '采样提示').trigger('click')

    expect(wrapper.text()).toContain('topic *')
    expect(wrapper.find('[data-test="prompt-text"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="prompt-temperature"]').exists()).toBe(false)

    await wrapper.get('[data-test="prompt-argument-topic"]').setValue('retrieval')
    await wrapper.get('[data-test="prompt-argument-audience"]').setValue('researchers')
    await wrapper.get('[data-test="prompt-argument-tone"]').setValue('concise')
    await buttonWithText(wrapper, '开始采样').trigger('click')
    await flushPromises()

    expect(httpPost).toHaveBeenLastCalledWith('/mcp/servers/context7/prompts/sample', {
      promptId: 'research-brief',
      arguments: {
        topic: 'retrieval',
        audience: 'researchers',
        tone: 'concise'
      }
    })
  })
})
