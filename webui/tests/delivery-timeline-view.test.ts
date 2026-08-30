// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 投递时间线视图必须真的消费后端接口，且把「没测到」与「耗时为零」分开。
 *
 * 后端一直提供 `/tracing/delivery/summary` 与 `/recent`（表 `im_delivery_timings`），
 * 但前端从未调用——需求 19.5 要求给出 QQ / Telegram / WeCom 的可比链路耗时，
 * 在产品上此前等于只能 curl。
 *
 * 最容易做错的一点：阶段耗时为 `null` 表示**没测到**（非流式请求没有首字节），
 * 渲染成 `0` 会被读成「极快」，与事实相反。这里把它钉住。
 */

const { httpGet } = vi.hoisted(() => ({ httpGet: vi.fn() }))
vi.mock('../src/utils/http', () => ({ http: { get: httpGet } }))

vi.mock('naive-ui', () => {
  const passthrough = (name: string, tag = 'section') => ({
    name,
    template: `<${tag} v-bind="$attrs"><slot /></${tag}>`
  })
  return {
    NAlert: passthrough('NAlert'),
    NButton: passthrough('NButton', 'button'),
    NCard: passthrough('NCard'),
    NTag: passthrough('NTag', 'span'),
    NSelect: {
      name: 'NSelect',
      props: ['value', 'options'],
      emits: ['update:value'],
      template: '<select v-bind="$attrs" @change="$emit(\'update:value\', $event.target.value)" />'
    }
  }
})

import DeliveryTimelineView from '../src/views/tracing/DeliveryTimelineView.vue'

const phase = (avg: number | null, max: number | null, samples: number) => ({
  avg_seconds: avg,
  max_seconds: max,
  samples
})

const summary = {
  deliveries: 12,
  failed_deliveries: 1,
  channels: ['onebot', 'telegram'],
  phases: {
    queue_seconds: phase(0.12, 0.4, 12),
    // 非流式部署下这两项没有样本：必须显示「未测到」而不是 0。
    llm_first_byte_seconds: phase(null, null, 0),
    llm_generation_seconds: phase(null, null, 0),
    formatting_seconds: phase(0.03, 0.1, 12),
    send_seconds: phase(2.5, 9.0, 12),
    total_seconds: phase(2.7, 9.4, 12)
  },
  counts: {
    segment_count: { avg: 3, max: 7, samples: 12 },
    // 这台部署没有任何投递重试过：必须显示「未测到」而不是 0。
    retry_count: { avg: null, max: null, samples: 0 }
  }
}

const records = {
  items: [
    {
      id: 2,
      channel: 'onebot',
      adapter_instance: 'qq-main',
      recorded_at: '2026-08-28T10:00:00+00:00',
      status: 'succeeded',
      queue_seconds: 0.1,
      llm_first_byte_seconds: null,
      llm_generation_seconds: null,
      formatting_seconds: 0.02,
      send_seconds: 2.4,
      total_seconds: 2.6,
      segment_count: 3,
      retry_count: 1,
      correlation_id: 'corr-1'
    }
  ]
}

function mockOk() {
  httpGet.mockImplementation((url: string) => {
    if (url.startsWith('/tracing/delivery/summary')) return Promise.resolve(summary)
    if (url.startsWith('/tracing/delivery/recent')) return Promise.resolve(records)
    throw new Error(`unexpected url ${url}`)
  })
}

describe('DeliveryTimelineView', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    httpGet.mockReset()
  })

  it('calls both delivery endpoints', async () => {
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const urls = httpGet.mock.calls.map((call) => String(call[0]))
    expect(urls.some((url) => url.startsWith('/tracing/delivery/summary'))).toBe(true)
    expect(urls.some((url) => url.startsWith('/tracing/delivery/recent'))).toBe(true)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('renders an unmeasured phase as 未测到 rather than 0', async () => {
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const rows = wrapper.findAll('[data-test="phase-row"]')
    const firstByteRow = rows.find((row) => row.text().includes('模型首字节'))
    expect(firstByteRow).toBeTruthy()
    expect(firstByteRow!.text()).toContain('未测到')
    expect(firstByteRow!.text()).not.toMatch(/\b0 ms\b/)
    // 并且要解释为什么没测到，而不是让人以为坏了。
    expect(firstByteRow!.text()).toContain('仅流式模式')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('shows sample counts next to every average', async () => {
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const sendRow = wrapper
      .findAll('[data-test="phase-row"]')
      .find((row) => row.text().includes('平台发送'))
    expect(sendRow!.text()).toContain('样本')
    expect(sendRow!.text()).toContain('12')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('excludes the total from the slowest-phase判断', async () => {
    // total_seconds 是各段之和，拿它比较会永远胜出，没有诊断价值。
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const slowest = wrapper.get('[data-test="slowest-phase"]').text()
    expect(slowest).toContain('平台发送')
    expect(slowest).not.toContain('端到端总计')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('renders overview counts and recent rows', async () => {
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    expect(wrapper.get('[data-test="delivery-count"]').text()).toBe('12')
    expect(wrapper.get('[data-test="failed-count"]').text()).toBe('1')
    const rows = wrapper.findAll('[data-test="record-row"]')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('onebot')
    expect(rows[0].text()).toContain('分段')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('treats a missing store as not-enabled rather than an error', async () => {
    // 503 表示这台部署没启用投递计时存储；那不是故障，不该显示成错误。
    httpGet.mockRejectedValue(new Error('delivery timing store is not configured'))
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    expect(wrapper.find('[data-test="store-unavailable"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('不影响消息投递')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('surfaces the segment and retry counts alongside the phase table', async () => {
    // 需求 19.5 九项里的后两项。它们一直被落库，却只出现在逐条记录里——
    // 汇总看不到，于是「这批慢投递是不是因为分了很多页」只能逐条翻。
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const rows = wrapper.findAll('[data-test="count-row"]')
    expect(rows).toHaveLength(2)
    expect(wrapper.text()).toContain('分段数量')
    expect(wrapper.text()).toContain('重试次数')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('shows 未测到 rather than 0 for a count nobody recorded', async () => {
    // `retry_count: 0` 会被读成「都没重试过」——那是一个论断。
    // 没有样本时必须说「没有数据」，否则读者会以为链路一切正常。
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()

    const rows = wrapper.findAll('[data-test="count-row"]')
    const retryRow = rows.find((row) => row.text().includes('重试次数'))
    expect(retryRow).toBeTruthy()
    expect(retryRow!.text()).toContain('未测到')
    expect(retryRow!.text()).toContain('没有记录这项的投递')

    // 有样本的那一行照常显示数字。
    const segmentRow = rows.find((row) => row.text().includes('分段数量'))
    expect(segmentRow!.text()).toContain('3')
    expect(segmentRow!.text()).not.toContain('未测到')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('stops polling on unmount', async () => {
    mockOk()
    const wrapper = mount(DeliveryTimelineView)
    await flushPromises()
    const callsAfterMount = httpGet.mock.calls.length

    wrapper.unmount()
    vi.advanceTimersByTime(120_000)
    await flushPromises()

    expect(httpGet.mock.calls.length).toBe(callsAfterMount)
    vi.useRealTimers()
  })
})
