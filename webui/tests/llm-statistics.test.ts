// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import LLMStatistics from '../src/components/LLMStatistics.vue'

const { httpGet } = vi.hoisted(() => ({ httpGet: vi.fn() }))

vi.mock('../src/utils/http', () => ({ http: { get: httpGet } }))
vi.mock('../src/stores/theme', () => ({
  useThemeStore: () => ({
    isDark: false,
    seed: {
      text: '#111111',
      textSecondary: '#555555',
      elevated: '#ffffff',
      border: '#dddddd',
      card: '#ffffff',
      divider: '#eeeeee'
    }
  })
}))
vi.mock('echarts/core', () => ({ use: vi.fn() }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))
vi.mock('echarts/charts', () => ({ LineChart: {}, BarChart: {}, PieChart: {} }))
vi.mock('echarts/components', () => ({
  GridComponent: {},
  TooltipComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  DataZoomComponent: {}
}))
vi.mock('vue-echarts', () => ({ default: { template: '<div data-test="chart" />' } }))

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'section') => ({
    name,
    template: `<${tag} v-bind="$attrs"><slot name="header-extra" /><slot name="trigger" /><slot /><slot name="action" /></${tag}>`
  })
  return {
    NAlert: passthrough('NAlert'),
    NButton: passthrough('NButton', 'button'),
    NCard: passthrough('NCard'),
    NGi: passthrough('NGi'),
    NGrid: passthrough('NGrid'),
    NIcon: passthrough('NIcon', 'span'),
    NNumberAnimation: {
      name: 'NNumberAnimation',
      props: ['to'],
      template: '<span>{{ to }}</span>'
    },
    NSpace: passthrough('NSpace'),
    NSpin: passthrough('NSpin'),
    NTooltip: passthrough('NTooltip')
  }
})

const statistics = {
  overview: {
    total_tokens: 42,
    total_requests: 3,
    pending_requests: 0,
    success_requests: 3,
    failed_requests: 0
  },
  daily_stats: [],
  hourly_stats: [],
  models: [],
  backends: []
}

describe('LLMStatistics request state', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    httpGet.mockReset()
  })

  it('does not render zero-valued metrics when the request fails and can recover', async () => {
    httpGet.mockRejectedValueOnce(new Error('private upstream failure'))
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    expect(wrapper.find('[data-test="statistics-error"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('统计数据加载失败，请稍后重试。')
    expect(wrapper.text()).not.toContain('30天请求数')
    expect(wrapper.text()).not.toContain('private upstream failure')

    httpGet.mockResolvedValueOnce(statistics)
    await wrapper.get('[data-test="retry-statistics"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="statistics-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('30天请求数')
    expect(wrapper.text()).toContain('3')

    wrapper.unmount()
    vi.useRealTimers()
  })
})
