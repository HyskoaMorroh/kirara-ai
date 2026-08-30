// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import LLMStatistics from '../src/components/LLMStatistics.vue'

/**
 * 需求 9 要的是一个「精湛精美的统一使用统计」页面。图表组件本身早就存在，
 * 但它此前只挂在**引导页**上，并且用一个**裸 GET**取数据：
 *
 * ```ts
 * await http.get('/tracing/llm/statistics')   // 无筛选、无时区
 * ```
 *
 * 两个后果：
 *
 * 1. 同一份统计接口在 `/tracing/llm` 上是带筛选和浏览器时区调用的
 *    （`tracing.vm.ts` 的 `statisticsQueryParams`），在引导页却不带。
 *    跨时区用户在两个地方看到的「今天」不一样，而两处显示的是同一批数据。
 * 2. 想按 Provider / 模型 / 时间范围看趋势的用户，在图表这一侧完全没有筛选。
 *
 * 这些用例钉住组件的取数契约：必须始终带时区，且必须接受外部传入的筛选条件。
 */

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

const emptyStatistics = {
  overview: {
    total_tokens: 0,
    total_requests: 0,
    pending_requests: 0,
    success_requests: 0,
    failed_requests: 0,
    total_cost: '0',
    cost_currency: null,
    unpriced_requests: 0
  },
  daily_stats: [],
  hourly_stats: [],
  models: [],
  backends: [],
  providers: [],
  usage_sources: [],
  error_categories: []
}

const lastQuery = () => {
  const call = httpGet.mock.calls.at(-1)
  expect(call, '统计接口未被调用').toBeTruthy()
  const path = String((call as unknown[])[0])
  const [, search = ''] = path.split('?')
  const params: Record<string, string> = {}
  for (const [key, value] of new URLSearchParams(search).entries()) {
    params[key] = value
  }
  return { path, params }
}

describe('LLMStatistics query contract', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    httpGet.mockReset()
    httpGet.mockResolvedValue(emptyStatistics)
  })

  it('always sends the browser timezone so day buckets match the viewer', async () => {
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const expected = Intl.DateTimeFormat().resolvedOptions().timeZone
    expect(lastQuery().params.timezone).toBe(expected)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('forwards externally supplied filters to the statistics endpoint', async () => {
    const wrapper = mount(LLMStatistics, {
      props: {
        filters: {
          provider: 'openai',
          model: 'gpt-4o',
          status: 'failed',
          start_time: '2026-08-01T00:00:00+08:00',
          end_time: '2026-08-29T00:00:00+08:00'
        }
      }
    })
    await flushPromises()

    const { params } = lastQuery()
    expect(params.provider).toBe('openai')
    expect(params.model).toBe('gpt-4o')
    expect(params.status).toBe('failed')
    expect(params.start_time).toBe('2026-08-01T00:00:00+08:00')
    expect(params.end_time).toBe('2026-08-29T00:00:00+08:00')
    // 时区不能被筛选条件挤掉。
    expect(params.timezone).toBeTruthy()

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('refetches when the filters change instead of showing stale numbers', async () => {
    const wrapper = mount(LLMStatistics, {
      props: { filters: { provider: 'openai' } }
    })
    await flushPromises()
    const callsAfterMount = httpGet.mock.calls.length

    await wrapper.setProps({ filters: { provider: 'anthropic' } })
    await flushPromises()

    expect(httpGet.mock.calls.length).toBeGreaterThan(callsAfterMount)
    expect(lastQuery().params.provider).toBe('anthropic')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('omits empty filter values rather than sending blank strings', async () => {
    // 空串会被后端当成一个真实的筛选值，导致「筛了但筛不到」。
    const wrapper = mount(LLMStatistics, {
      props: { filters: { provider: '', model: null, status: undefined } }
    })
    await flushPromises()

    const { params } = lastQuery()
    expect('provider' in params).toBe(false)
    expect('model' in params).toBe(false)
    expect('status' in params).toBe(false)

    wrapper.unmount()
    vi.useRealTimers()
  })
})
