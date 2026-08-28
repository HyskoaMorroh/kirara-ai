// @vitest-environment happy-dom

/**
 * The statistics endpoint accepts provider / usage_source / error_category /
 * time-range / timezone filters, but the view model called it with no query at
 * all. The list and the stat cards therefore disagreed the moment a filter was
 * applied, and every bucket was computed in the *server's* timezone, so a
 * cross-timezone user's "today" was wrong.
 *
 * These tests pin the request contract, not the layout.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

const { httpGet, httpPost } = vi.hoisted(() => ({
  httpGet: vi.fn(),
  httpPost: vi.fn()
}))

vi.mock('@/utils/http', () => ({
  http: { get: httpGet, post: httpPost, fetch: vi.fn() }
}))

vi.mock('naive-ui', () => ({
  useMessage: () => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }),
  useDialog: () => ({}),
  NTag: {},
  NButton: {}
}))

const routerPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ params: {}, query: {} })
}))

import { useTracingViewModel } from '../src/views/tracing/tracing.vm'

const delegate = {
  getFilterOptions: () => ({}),
  getTableColumns: () => [],
  formatStatistics: () => [],
  getDetailFields: () => []
}

const emptyStats = {
  overview: {
    total_requests: 0,
    success_requests: 0,
    failed_requests: 0,
    pending_requests: 0,
    total_tokens: 0,
    total_cost: '0',
    cost_currency: null,
    unpriced_requests: 0
  },
  latency: { avg_ttft_ms: null, max_ttft_ms: null, avg_duration: null, avg_attempt_count: null },
  daily_stats: [],
  hourly_stats: [],
  models: [],
  backends: [],
  providers: [],
  usage_sources: [],
  error_categories: []
}

const statisticsUrl = () => {
  const call = httpGet.mock.calls.find(([path]) => String(path).includes('/statistics'))
  return call ? String(call[0]) : ''
}

describe('tracing statistics request', () => {
  beforeEach(() => {
    httpGet.mockReset()
    httpPost.mockReset()
    httpGet.mockResolvedValue(emptyStats)
    httpPost.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, total_pages: 1 })
  })

  it('always sends the browser timezone so buckets match the user calendar', async () => {
    const vm = useTracingViewModel('llm', delegate)

    await vm.fetchStatistics()

    const url = statisticsUrl()
    expect(url).toContain('timezone=')
  })

  it('forwards every active filter to the statistics endpoint', async () => {
    const vm = useTracingViewModel('llm', delegate)
    Object.assign(vm.filterParams.value, {
      modelId: 'model-a',
      backendName: 'backend-a',
      provider: 'provider-a',
      status: 'failed',
      usageSource: 'estimated',
      errorCategory: 'rate_limit',
      correlationId: 'corr-1',
      startTime: '2026-08-01T00:00:00.000Z',
      endTime: '2026-08-02T00:00:00.000Z'
    })

    await vm.fetchStatistics()
    const url = statisticsUrl()

    expect(url).toContain('model=model-a')
    expect(url).toContain('backend=backend-a')
    expect(url).toContain('provider=provider-a')
    expect(url).toContain('status=failed')
    expect(url).toContain('usage_source=estimated')
    expect(url).toContain('error_category=rate_limit')
    expect(url).toContain('correlation_id=corr-1')
    expect(url).toContain('start_time=')
    expect(url).toContain('end_time=')
  })

  it('omits empty filters instead of sending blank values', async () => {
    const vm = useTracingViewModel('llm', delegate)

    await vm.fetchStatistics()
    const url = statisticsUrl()

    expect(url).not.toContain('provider=')
    expect(url).not.toContain('status=')
  })

  it('forwards the new filters to the trace list too', async () => {
    const vm = useTracingViewModel('llm', delegate)
    Object.assign(vm.filterParams.value, {
      provider: 'provider-b',
      usageSource: 'unknown',
      errorCategory: 'authentication',
      startTime: '2026-08-01T00:00:00.000Z',
      endTime: '2026-08-02T00:00:00.000Z'
    })

    await vm.fetchTraces()

    const [, body] = httpPost.mock.calls[0] as [string, Record<string, unknown>]
    expect(body.provider).toBe('provider-b')
    expect(body.usage_source).toBe('unknown')
    expect(body.error_category).toBe('authentication')
    expect(body.start_time).toBe('2026-08-01T00:00:00.000Z')
    expect(body.end_time).toBe('2026-08-02T00:00:00.000Z')
  })

  it('refreshes the statistics when a filter is applied', async () => {
    const vm = useTracingViewModel('llm', delegate)
    httpGet.mockClear()

    vm.applyFilter()
    await Promise.resolve()

    expect(statisticsUrl()).toContain('/statistics')
  })

  it('refreshes the statistics when filters are reset', async () => {
    const vm = useTracingViewModel('llm', delegate)
    vm.filterParams.value.provider = 'provider-a'
    httpGet.mockClear()

    vm.resetFilter()
    await Promise.resolve()

    expect(vm.filterParams.value.provider).toBeNull()
    expect(statisticsUrl()).toContain('/statistics')
  })
})
