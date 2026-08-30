// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 需求 9 要的是一个把「不同类型上游真实消耗 Tokens、使用趋势、请求日志、
 * Provider 统计、模型统计、成本定价」统筹在一起的页面。
 *
 * 现状是三个互不相连的位置：图表挂在**引导页**、请求日志在 `/tracing/llm`、
 * 成本定价在 `/llm/pricing`。`/tracing` 的侧栏里也没有「使用统计」这一项。
 * 换句话说，能力都在，但没有一个地方把它们放在一起看——
 * 用户要对一次账单，得在三个页面之间来回跳。
 *
 * 这些用例钉住统一页面的三件事：图表带筛选、筛选下发到图表组件、
 * 以及通往请求日志与成本定价的入口真实存在（而不是把功能重做一遍）。
 */

const { httpGet } = vi.hoisted(() => ({ httpGet: vi.fn() }))
const { pushed } = vi.hoisted(() => ({ pushed: vi.fn() }))

vi.mock('../src/utils/http', () => ({ http: { get: httpGet } }))
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
  providers: [{ provider: 'openai', count: 6, tokens: 600, avg_duration: 1200, cost: '2.0', unpriced_requests: 0 }],
  usage_sources: [],
  error_categories: []
}

async function mountView() {
  const module = await import('../src/views/tracing/UsageStatisticsView.vue')
  return mount(module.default)
}

describe('UsageStatisticsView', () => {
  beforeEach(() => {
    httpGet.mockReset()
    httpGet.mockResolvedValue(statistics)
    pushed.mockReset()
  })

  it('hosts the shared chart component rather than reimplementing charts', async () => {
    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.find('[data-test="statistics-charts"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('passes its filter state down to the chart component', async () => {
    const wrapper = await mountView()
    await flushPromises()

    const charts = wrapper.getComponent({ name: 'LLMStatistics' })
    expect(charts.props('filters')).toBeTruthy()

    wrapper.unmount()
  })

  it('offers provider and model filters sourced from the statistics response', async () => {
    const wrapper = await mountView()
    await flushPromises()

    const component = wrapper.vm as unknown as {
      providerOptions: Array<{ value: string }>
      modelOptions: Array<{ value: string }>
    }
    expect(component.providerOptions.some((option) => option.value === 'openai')).toBe(true)
    expect(component.modelOptions.some((option) => option.value === 'gpt-4o')).toBe(true)

    wrapper.unmount()
  })

  it('links to the request log and the cost pricing page instead of duplicating them', async () => {
    const wrapper = await mountView()
    await flushPromises()

    await wrapper.get('[data-test="open-request-log"]').trigger('click')
    expect(pushed).toHaveBeenCalledWith('/tracing/llm')

    await wrapper.get('[data-test="open-pricing"]').trigger('click')
    expect(pushed).toHaveBeenCalledWith('/llm/pricing')

    wrapper.unmount()
  })

  it('is reachable from the tracing sidebar', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, resolve } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))

    const sidebar = readFileSync(
      resolve(here, '../src/components/layout/SecondarySidebar.vue'),
      'utf-8'
    )
    const router = readFileSync(resolve(here, '../src/router/index.ts'), 'utf-8')

    expect(sidebar).toContain('/tracing/statistics')
    expect(router).toContain('/tracing/statistics'.slice(1))
    expect(router).toContain('UsageStatisticsView.vue')
  })
})
