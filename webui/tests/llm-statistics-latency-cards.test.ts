// 需求 9：重试与故障转移的平均次数必须在统计页看得到。
//
// 上一轮把这两列一路做到了后端聚合（`llm_tracer.py` 的 `avg_retry_count` /
// `avg_failover_count`）与前端类型，然后**停在类型定义上**：`formatStatistics`
// 只取了 `avg_ttft_ms`，两个字段在 `webui/src` 下各只有 1 处命中，就是类型声明
// 本身。同一形态又出现了一次——SQL 算了、HTTP 传了、类型写了，界面不显示。
//
// 为什么这两个数字值得占卡片位：它们回答的是**两个处置相反的问题**。
// 平均重试次数高 = 单家上游不稳，要调超时与退避；平均转移次数高 = 某家在整体
// 失败，要查它的配额与熔断。`avg_attempt_count` 把两者合成一个数，等于没说。
//
// `null` 与 0 严格区分：`null` 是「没有 attempt 数据」（旧记录、第三方调用方、
// 从未走过故障转移路径的请求），0 是「确实一次成功」。把 null 显示成 0 是一个
// 我们没有依据的论断。

import { describe, expect, it } from 'vitest'

import { llmTracingDelegate } from '../src/views/tracing/llm/llm-tracing.vm'

const stats = (latency: Record<string, number | null>) =>
  ({
    overview: {
      total_requests: 10,
      success_requests: 9,
      failed_requests: 1,
      pending_requests: 0,
      total_tokens: 1000,
      total_cost: '1.0',
      cost_currency: 'USD',
      unpriced_requests: 0
    },
    latency: {
      avg_ttft_ms: 120,
      max_ttft_ms: null,
      avg_duration: null,
      avg_attempt_count: null,
      avg_retry_count: null,
      avg_failover_count: null,
      ...latency
    },
    daily_stats: [],
    hourly_stats: [],
    models: [],
    backends: [],
    providers: [],
    usage_sources: [],
    error_categories: []
  }) as any

const labels = (cards: Array<{ label: string }>) => cards.map((card) => card.label)
const valueOf = (cards: Array<{ label: string; value: string | number }>, label: string) =>
  cards.find((card) => card.label === label)?.value

describe('formatStatistics latency cards', () => {
  it('shows average retry and failover counts as separate cards', () => {
    const cards = llmTracingDelegate.formatStatistics(
      stats({ avg_retry_count: 0.4, avg_failover_count: 1.2 })
    )

    expect(labels(cards)).toContain('平均重试次数')
    expect(labels(cards)).toContain('平均故障转移次数')
    // 两个数字必须能分辨出来，不能都显示成同一个聚合值。
    expect(String(valueOf(cards, '平均重试次数'))).toContain('0.4')
    expect(String(valueOf(cards, '平均故障转移次数'))).toContain('1.2')
  })

  it('omits the cards entirely when there is no attempt data', () => {
    // 没有数据时显示「0 次」等于断言「从来没重试过」，而我们并不知道。
    const cards = llmTracingDelegate.formatStatistics(stats({}))

    expect(labels(cards)).not.toContain('平均重试次数')
    expect(labels(cards)).not.toContain('平均故障转移次数')
  })

  it('keeps a real zero, which is a different statement from missing', () => {
    // 0 是「确实一次成功」，是有信息量的：它说明这段时间没有发生过转移。
    const cards = llmTracingDelegate.formatStatistics(
      stats({ avg_retry_count: 0, avg_failover_count: 0 })
    )

    expect(labels(cards)).toContain('平均重试次数')
    expect(String(valueOf(cards, '平均故障转移次数'))).toContain('0')
  })
})
