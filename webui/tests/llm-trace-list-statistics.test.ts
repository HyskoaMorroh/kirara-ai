// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import LLMTraceList from '../src/views/tracing/llm/LLMTraceList.vue'

const { vm } = vi.hoisted(() => ({
  vm: {
    traces: [],
    formattedStatistics: null,
    statisticsStatus: 'error',
    statisticsError: '统计信息加载失败，请稍后重试。',
    isConnected: false,
    isLoading: false,
    totalTraces: 0,
    currentPage: 1,
    pageSize: 20,
    totalPages: 1,
    filterParams: {
      correlationId: null,
      modelId: null,
      backendName: null,
      status: null,
      query: ''
    },
    filterOptions: { modelId: [], backendName: [] },
    statusOptions: [],
    columns: [],
    fetchTraces: vi.fn(),
    fetchStatistics: vi.fn(),
    resetFilter: vi.fn(),
    applyFilter: vi.fn(),
    handlePageChange: vi.fn(),
    handlePageSizeChange: vi.fn(),
    refreshData: vi.fn(),
    initialize: vi.fn(),
    disconnectWebSocket: vi.fn()
  }
}))

vi.mock('../src/views/tracing/llm/llm-tracing.vm', () => ({
  useLLMTracingViewModel: () => vm
}))

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'section') => ({
    name,
    template: `<${tag} v-bind="$attrs"><slot name="header" /><slot name="header-extra" /><slot name="icon" /><slot /></${tag}>`
  })
  return {
    NAlert: passthrough('NAlert'),
    NBadge: passthrough('NBadge'),
    NButton: passthrough('NButton', 'button'),
    NCard: passthrough('NCard'),
    NDataTable: passthrough('NDataTable'),
    NEmpty: passthrough('NEmpty'),
    NGrid: passthrough('NGrid'),
    NGridItem: passthrough('NGridItem'),
    NIcon: passthrough('NIcon', 'span'),
    NInput: passthrough('NInput', 'input'),
    NSelect: {
      name: 'NSelect',
      inheritAttrs: false,
      props: ['value', 'options'],
      template: '<select :value="value"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>'
    },
    NSkeleton: passthrough('NSkeleton'),
    NSpace: passthrough('NSpace'),
    NText: passthrough('NText', 'span')
  }
})

describe('LLMTraceList statistics feedback', () => {
  it('renders a failed statistics request as an error instead of an empty grid', () => {
    const wrapper = mount(LLMTraceList)

    expect(wrapper.text()).toContain('统计信息加载失败，请稍后重试。')
    expect(wrapper.find('[data-test="statistics-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="statistics-empty"]').exists()).toBe(false)
  })
})
