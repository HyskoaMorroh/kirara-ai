// 需求 22.1：每一次上游尝试都要能看到，而不只是一个次数。
//
// `attempts` 一直在 `to_dict()` 里返回，每条都带 provider、retry_index、
// success、error_category、时间戳与 partial_output——那是「这条请求为什么花了
// 3 次尝试、是哪一家失败的」的唯一证据。但全仓库没有任何界面消费它：
// `webui/` 里 grep `attempts` 零命中。
//
// 只给「重试 2 次、转移 1 次」这两个数字回答不了运维真正要问的问题：
// **哪一家在失败、失败的类型是什么、换到哪一家之后成功了**。而这三件事决定
// 完全不同的动作（调超时 / 查那家的配额 / 把它从故障转移池里摘掉）。
//
// 这些用例钉住：明细能被读出、顺序稳定、失败与成功可分、以及缺字段时不编值。

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

import { summarizeTraceAttempts } from '../src/views/tracing/llm/trace-attempts'

const attempt = (over: Record<string, unknown> = {}) => ({
  trace_id: 't1',
  model: 'claude-sonnet',
  provider: 'p1',
  attempt: 1,
  retry_index: 0,
  success: true,
  error_category: null,
  error_summary: null,
  started_at: 100,
  first_byte_at: 100.5,
  completed_at: 101,
  partial_output: false,
  ...over
})

describe('summarizeTraceAttempts', () => {
  it('returns one row per attempt in upstream order', () => {
    const rows = summarizeTraceAttempts([
      attempt({ provider: 'p1', attempt: 1, success: false, error_category: 'timeout' }),
      attempt({ provider: 'p2', attempt: 2, success: true })
    ])

    // 顺序就是发生顺序：乱序展示会让「换到哪一家之后成功了」这个问题不可回答。
    expect(rows.map((row) => row.provider)).toEqual(['p1', 'p2'])
    expect(rows[0].succeeded).toBe(false)
    expect(rows[1].succeeded).toBe(true)
  })

  it('distinguishes a retry on the same provider from a failover to another', () => {
    const rows = summarizeTraceAttempts([
      attempt({ provider: 'p1', attempt: 1, success: false, error_category: 'timeout' }),
      attempt({ provider: 'p1', attempt: 2, success: false, error_category: 'timeout' }),
      attempt({ provider: 'p2', attempt: 3, success: true })
    ])

    // 这一列是这张表存在的理由：同一家再试与换一家的处置完全相反。
    expect(rows.map((row) => row.kind)).toEqual(['initial', 'retry', 'failover'])
  })

  it('carries the error category so the failure type is visible per attempt', () => {
    const rows = summarizeTraceAttempts([
      attempt({ success: false, error_category: 'rate_limited' })
    ])

    expect(rows[0].errorCategory).toBe('rate_limited')
  })

  it('reports time to first byte only when the upstream actually sent one', () => {
    const rows = summarizeTraceAttempts([
      attempt({ started_at: 10, first_byte_at: 10.25 }),
      // 非流式请求没有首字节时刻：这里必须是 null 而不是 0。
      // 0 会被读成「零延迟」，那是一个我们没有依据的论断。
      attempt({ started_at: 20, first_byte_at: null, attempt: 2 })
    ])

    expect(rows[0].ttftSeconds).toBeCloseTo(0.25, 5)
    expect(rows[1].ttftSeconds).toBeNull()
  })

  it('marks an attempt that produced partial output', () => {
    // 产出过部分内容的失败与「什么都没发生」的失败不同：前者用户可能已经
    // 看到半句话，重发会造成重复。
    const rows = summarizeTraceAttempts([
      attempt({ success: false, partial_output: true, error_category: 'stream_broken' })
    ])

    expect(rows[0].partialOutput).toBe(true)
  })

  it('returns an empty list rather than inventing a row when there is no data', () => {
    // 旧记录、第三方调用方、以及从未走过故障转移路径的请求都没有 attempts。
    // 「没有数据」不能被显示成「尝试过 0 次」。
    expect(summarizeTraceAttempts(null)).toEqual([])
    expect(summarizeTraceAttempts(undefined)).toEqual([])
    expect(summarizeTraceAttempts([])).toEqual([])
  })

  it('survives an attempt object missing optional fields', () => {
    // 明细来自 JSON 列，schema 可能早于当前字段集。少一个字段不该让整块面板打不开。
    const rows = summarizeTraceAttempts([{ provider: 'p1' } as any])

    expect(rows).toHaveLength(1)
    expect(rows[0].provider).toBe('p1')
    expect(rows[0].ttftSeconds).toBeNull()
    expect(rows[0].errorCategory).toBeNull()
  })

  it('does not claim a failover when the provider is unknown', () => {
    // provider 缺失时无法判断是否换了家。标成 failover 是一个论断，
    // 而这里我们不知道——不知道就不要说。
    const rows = summarizeTraceAttempts([
      attempt({ provider: 'p1' }),
      { attempt: 2 } as any
    ])

    expect(rows[1].kind).toBe('unknown')
  })
})

// 源码级断言。纯函数正确不代表界面真的会展示：这个缺陷的形态恰恰是
// 「后端返回了、封装了、界面零消费者」，而那一跳只能在接线处校验。
describe('detail view wiring', () => {
  const detail = readFileSync(
    new URL('../src/views/tracing/llm/LLMTraceDetail.vue', import.meta.url),
    'utf-8'
  )

  it('consumes the attempts array from the trace detail', () => {
    expect(detail).toContain('summarizeTraceAttempts')
    expect(detail).toContain('attemptRows')
  })

  it('renders one row per attempt rather than a single count', () => {
    expect(detail).toContain('data-test="attempt-row"')
    expect(detail).toContain('v-for="(row, index) in attemptRows"')
  })

  it('hides the whole block when there is no attempt data', () => {
    // 「没有明细」不能显示成一张空表：空表看起来像「加载失败」。
    expect(detail).toContain('v-if="attemptRows.length > 0"')
  })
})
