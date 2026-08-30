// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 使用统计页的三处收尾（需求 9 / 22.2）。
 *
 * 1. **「未标注」Provider 筛选是个空操作。** `null` provider 被映射成 `value: ''`，
 *    而空串会被筛选构造器当成「没填」丢掉——选了「未标注」却看到全量数据，
 *    比没有这个选项更糟：它给出一个错误的答案而不是拒绝回答。
 * 2. **统计页没有导出。** 22.2 把导出列在统计页能力里；后端与 CSV 都在，
 *    但唯一入口在请求日志页。
 * 3. **时区只能自动检测。** 后端接受任意 IANA 名，界面却无法查看别的时区——
 *    跨时区对账时看不到对方眼里的「今天」。
 */

const { httpGet, httpFetch } = vi.hoisted(() => ({
  httpGet: vi.fn(),
  httpFetch: vi.fn()
}))
const { pushed } = vi.hoisted(() => ({ pushed: vi.fn() }))

vi.mock('../src/utils/http', () => ({
  http: { get: httpGet, fetch: httpFetch }
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushed }),
  useRoute: () => ({ path: '/tracing/statistics', query: {} })
}))
vi.mock('../src/components/LLMStatistics.vue', () => ({
  default: {
    name: 'LLMStatistics',
    props: ['filters'],
    template: '<div data-test="statistics-charts" />'
  }
}))

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'section') => ({
    name,
    template: `<${tag} v-bind="$attrs"><slot name="header-extra" /><slot /></${tag}>`
  })
  return {
    NAlert: passthrough('NAlert'),
    NButton: passthrough('NButton', 'button'),
    NCard: passthrough('NCard'),
    NDatePicker: {
      name: 'NDatePicker',
      props: ['value'],
      emits: ['update:value'],
      template: '<input data-test="range-picker" />'
    },
    NGrid: passthrough('NGrid'),
    NGridItem: passthrough('NGridItem'),
    NIcon: passthrough('NIcon', 'span'),
    NSelect: {
      name: 'NSelect',
      props: ['value', 'options'],
      emits: ['update:value'],
      template: '<select v-bind="$attrs" />'
    },
    NSpace: passthrough('NSpace'),
    NText: passthrough('NText', 'span'),
    useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() })
  }
})

const statistics = {
  overview: {
    total_tokens: 900,
    total_requests: 10,
    pending_requests: 0,
    success_requests: 8,
    failed_requests: 2,
    total_cost: '3.5000',
    cost_currency: 'USD',
    unpriced_requests: 2
  },
  daily_stats: [],
  hourly_stats: [],
  models: [{ model_id: 'gpt-4o', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0', unpriced_requests: 0 }],
  backends: [],
  providers: [
    { provider: 'openai', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0', unpriced_requests: 0 },
    { provider: null, count: 4, tokens: 300, avg_duration: 800, cost: '1.5', unpriced_requests: 2 }
  ],
  usage_sources: [],
  error_categories: []
}

async function mountView() {
  const module = await import('../src/views/tracing/UsageStatisticsView.vue')
  return mount(module.default)
}

describe('unlabelled provider filter', () => {
  beforeEach(() => {
    httpGet.mockReset()
    httpGet.mockResolvedValue(statistics)
    httpFetch.mockReset()
    pushed.mockReset()
  })

  it('uses a sentinel rather than an empty string for a null provider', async () => {
    // 空串会被筛选构造器当成「没填」丢掉，于是「未标注」变成「全部」。
    const wrapper = await mountView()
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerOptions: Array<{ label: string; value: string }>
    }
    const unlabelled = component.providerOptions.find((item) => item.label === '未标注')
    expect(unlabelled, '未标注选项缺失').toBeTruthy()
    expect(unlabelled!.value).not.toBe('')

    wrapper.unmount()
  })

  it('translates the sentinel into a filter the backend understands', async () => {
    const wrapper = await mountView()
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerOptions: Array<{ label: string; value: string }>
      provider: string | null
      filters: Record<string, string>
    }
    const sentinel = component.providerOptions.find((item) => item.label === '未标注')!.value
    ;(wrapper.vm as unknown as { provider: string | null }).provider = sentinel
    await flushPromises()

    // 必须落到一个真实的筛选键上，而不是被丢掉变成「全量」。
    expect(Object.keys(component.filters)).toContain('provider_unset')
    expect(component.filters.provider).toBeUndefined()

    wrapper.unmount()
  })
})

describe('statistics page export', () => {
  beforeEach(() => {
    httpGet.mockReset()
    httpGet.mockResolvedValue(statistics)
    httpFetch.mockReset()
    httpFetch.mockResolvedValue(
      new Response('a,b\n1,2', { status: 200, headers: { 'Content-Type': 'text/csv' } })
    )
    pushed.mockReset()
  })

  it('offers an export control on the statistics page', async () => {
    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.find('[data-test="export-statistics"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('exports with the same filters currently shown', async () => {
    const wrapper = await mountView()
    await flushPromises()
    ;(wrapper.vm as unknown as { provider: string | null }).provider = 'openai'
    await flushPromises()

    await wrapper.get('[data-test="export-statistics"]').trigger('click')
    await flushPromises()

    expect(httpFetch).toHaveBeenCalled()
    const [path, init] = httpFetch.mock.calls.at(-1) as [string, RequestInit]
    expect(path).toBe('/tracing/llm/export')
    const body = JSON.parse(String(init.body))
    expect(body.provider).toBe('openai')
    expect(body.format).toBe('csv')
    // 导出必须带时区，否则 CSV 里的时间戳与页面上看到的不是同一套。
    expect(body.timezone).toBeTruthy()

    wrapper.unmount()
  })
})

describe('timezone selection', () => {
  beforeEach(() => {
    httpGet.mockReset()
    httpGet.mockResolvedValue(statistics)
    httpFetch.mockReset()
    pushed.mockReset()
  })

  it('defaults to the browser timezone', async () => {
    const wrapper = await mountView()
    await flushPromises()

    const component = wrapper.vm as unknown as { timezone: string }
    expect(component.timezone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)

    wrapper.unmount()
  })

  it('sends the chosen timezone instead of the browser one', async () => {
    const wrapper = await mountView()
    await flushPromises()
    ;(wrapper.vm as unknown as { timezone: string }).timezone = 'UTC'
    await flushPromises()

    const component = wrapper.vm as unknown as { filters: Record<string, string> }
    expect(component.filters.timezone).toBe('UTC')

    wrapper.unmount()
  })
})
