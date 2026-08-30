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
    failed_requests: 0,
    total_cost: '1.2345',
    cost_currency: 'USD',
    cost_by_currency: { USD: '1.2345' },
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

/** 带 Provider / 用量来源 / 成本的完整统计，用于验证这三个维度真的被渲染。 */
const richStatistics = {
  overview: {
    total_tokens: 900,
    total_requests: 10,
    pending_requests: 0,
    success_requests: 8,
    failed_requests: 2,
    total_cost: '3.5000',
    cost_currency: 'USD',
    cost_by_currency: { USD: '3.5000' },
    unpriced_requests: 2
  },
  daily_stats: [],
  hourly_stats: [],
  models: [{ model_id: 'gpt-4o', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0', unpriced_requests: 0 }],
  backends: [],
  providers: [
    { provider: 'openai', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0000', unpriced_requests: 0 },
    { provider: null, count: 4, tokens: 300, avg_duration: 800, cost: '1.5000', unpriced_requests: 2 }
  ],
  usage_sources: [
    { usage_source: 'provider', count: 7, tokens: 700, avg_duration: 1100, cost: '3.0', unpriced_requests: 0 },
    { usage_source: 'estimated', count: 3, tokens: 200, avg_duration: 900, cost: '0.5', unpriced_requests: 2 }
  ],
  error_categories: []
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

describe('LLMStatistics provider dimension', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    httpGet.mockReset()
  })

  it('renders a provider chart, not just a filter dropdown', async () => {
    // 需求 9 要的是「Provider 统计」。后端一直返回 providers 分组（还建了索引），
    // 但此前前端只把它当筛选项数据源——一个下拉框不是统计。
    httpGet.mockResolvedValueOnce(richStatistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerUsageOption: { xAxis: { data: string[] }; series: Array<{ data: number[] }> }
    }
    expect(component.providerUsageOption.xAxis.data).toEqual(['openai', '未标注'])
    expect(component.providerUsageOption.series[0].data).toEqual([6, 4])

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('labels a null provider instead of dropping the row', async () => {
    // 丢弃 null 会让各 Provider 请求数之和小于总请求数，读起来像数据缺失。
    httpGet.mockResolvedValueOnce(richStatistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerUsageOption: { xAxis: { data: string[] }; series: Array<{ data: number[] }> }
    }
    const total = component.providerUsageOption.series[0].data.reduce((a, b) => a + b, 0)
    expect(total).toBe(richStatistics.overview.total_requests)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('renders the usage-source split so estimates are distinguishable', async () => {
    httpGet.mockResolvedValueOnce(richStatistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      usageSourceOption: { series: Array<{ data: Array<{ name: string; value: number }> }> }
    }
    const slices = component.usageSourceOption.series[0].data
    expect(slices.map((slice) => slice.name)).toEqual(['供应商返回', '本地估算'])
    // 这张图叫「Token 来源构成」，副标题写着「估算与未知不能当作实测消耗」——
    // 那就必须画 token 而不是请求数。fixture 里 7 条实测/3 条估算，
    // 但 token 是 700/200；两个比例不同，而标题只对其中一个成立。
    // 按请求数画会在「少量估算请求各自很大」时把图显示成「基本都是实测」。
    expect(slices.map((slice) => slice.value)).toEqual([700, 200])

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('surfaces total cost and unpriced requests side by side', async () => {
    // 未定价请求按 0 元并入成本会让账单看起来更便宜，因此两者必须并列展示。
    httpGet.mockResolvedValueOnce(richStatistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    expect(wrapper.get('[data-test="total-cost"]').text()).toBe('3.5000')
    expect(wrapper.get('[data-test="unpriced-requests"]').text()).toBe('2')
    expect(wrapper.text()).toContain('未计入上面的成本')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('keeps working when the provider group is empty', async () => {
    httpGet.mockResolvedValueOnce(statistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerUsageOption: { xAxis: { data: string[] } }
    }
    expect(component.providerUsageOption.xAxis.data).toEqual([])
    expect(wrapper.find('[data-test="statistics-error"]').exists()).toBe(false)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('says so when a second currency exists instead of showing one number as the total', async () => {
    // 后端刻意不把不同货币相加——那会得到一个没有单位的数字且不报错。
    // 于是「30天成本」只是主币种的合计，界面必须把其余币种说出来，
    // 否则用户会把一个偏小的数字当成全部花费。
    httpGet.mockResolvedValueOnce({
      ...statistics,
      overview: {
        ...statistics.overview,
        total_cost: '10.0',
        cost_currency: 'EUR',
        cost_by_currency: { EUR: '10.0', USD: '3.0' }
      }
    })
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const note = wrapper.get('[data-test="other-currency-totals"]').text()
    expect(note).toContain('3.0 USD')
    expect(note).toContain('不同货币不相加')
    // 主币种不该在「另有」里重复出现。
    expect(note).not.toContain('EUR')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('shows no currency note when everything is in one currency', async () => {
    httpGet.mockResolvedValueOnce(statistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    expect(wrapper.find('[data-test="other-currency-totals"]').exists()).toBe(false)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('exposes tokens and cost per model, not only request count', async () => {
    // `models[]` 一直带 tokens / cost / unpriced_requests，但图上只画了
    // 请求数与平均响应时间，tooltip 又是默认的 `trigger: 'axis'`（无 formatter）。
    // 于是「哪个模型最贵」在界面上无从回答——而那是按模型看统计的首要问题：
    // 请求数最多的模型往往不是花钱最多的那个。
    httpGet.mockResolvedValueOnce(richStatistics)
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      modelUsageOption: { tooltip: { formatter?: (params: any) => string } }
    }
    const formatter = component.modelUsageOption.tooltip.formatter
    expect(typeof formatter).toBe('function')

    const text = formatter!([{ name: 'gpt-4o', dataIndex: 0 }])
    // 三项都必须出现：只给请求数等于把「贵不贵」这个问题留在数据库里。
    expect(text).toContain('600')
    expect(text).toContain('2.0')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('marks unpriced requests per model so cost is not read as complete', async () => {
    // 未定价请求按 0 元并入合计会让某个模型看起来很便宜。
    // 这个标注是「这个数字不完整」的唯一提示。
    httpGet.mockResolvedValueOnce({
      ...richStatistics,
      models: [
        { model_id: 'gpt-4o', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0', unpriced_requests: 4 }
      ]
    })
    const wrapper = mount(LLMStatistics)
    await flushPromises()

    const component = wrapper.vm as unknown as {
      modelUsageOption: { tooltip: { formatter?: (params: any) => string } }
    }
    const text = component.modelUsageOption.tooltip.formatter!([{ name: 'gpt-4o', dataIndex: 0 }])
    expect(text).toContain('未定价')
    expect(text).toContain('4')

    wrapper.unmount()
    vi.useRealTimers()
  })
})
