import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 容错状态面板必须真的消费后端接口，并且类型与后端响应形状一致。
 *
 * 需求 21.2 要求「优先级、失败原因、尝试顺序、trace id 都要可查」。后端一直提供
 * `GET /llm/resilience/status`，但前端此前**零调用点**——在产品上等于只能 curl。
 * 更隐蔽的是：正因为没有调用点，`ProviderResilienceStatus` 声明成了嵌套的
 * `circuit: {...}` 且带了后端不返回的 `error_summary` / `ttft_seconds`，
 * 类型与实际响应长期不符也没人发现。照那个类型写面板会读到 undefined，
 * 渲染出空白的熔断状态。
 *
 * 这些用例钉住三件事：接口被调用、类型与后端字段对齐、面板呈现了需求点名的信息。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/ResilienceView.vue')
const routerSource = read('../src/router/index.ts')
const sidebarSource = read('../src/components/layout/SecondarySidebar.vue')

/** 后端 `LLMManager.get_resilience_status()` 平铺返回的字段。 */
const BACKEND_ROW_FIELDS = [
  'model',
  'provider',
  'priority',
  'state',
  'failure_count',
  'error_rate',
  'requests',
  'recovery_successes',
  'recovery_success_threshold',
  'next_recovery_time',
  'recent_error_category',
  'recent_attempts',
  'recent_transitions'
]

const BACKEND_ATTEMPT_FIELDS = [
  'trace_id',
  'provider',
  'model',
  'attempt',
  'retry_index',
  'success',
  'error_category',
  'started_at',
  'completed_at',
  'first_byte_at',
  'partial_output'
]

describe('resilience status API binding', () => {
  it('exposes a call site for the endpoint', () => {
    expect(apiSource).toContain("'/llm/resilience/status'")
    expect(apiSource).toMatch(/getResilienceStatus\s*\(/)
  })

  it('types every field the backend actually returns', () => {
    for (const field of BACKEND_ROW_FIELDS) {
      expect(apiSource, `ProviderResilienceRow 缺少字段 ${field}`).toContain(field)
    }
    for (const field of BACKEND_ATTEMPT_FIELDS) {
      expect(apiSource, `ProviderResilienceAttempt 缺少字段 ${field}`).toContain(field)
    }
  })

  it('no longer declares the nested circuit shape the backend never sends', () => {
    // 后端是 `**breaker.snapshot()` 平铺，不是嵌套对象。
    // 只检查 ProviderResilienceRow / Attempt 那一段：`circuit_failure_threshold`
    // 等字段属于后端配置类型，与这里无关；注释里提到旧字段名也不算声明。
    const typeBlock = apiSource.slice(
      apiSource.indexOf('export interface ProviderResilienceAttempt'),
      apiSource.indexOf('export type ProviderResilienceStatus')
    )
    expect(typeBlock).not.toMatch(/\bcircuit:\s*\{/)
    // 这两个字段后端从未返回过，不得作为类型成员出现。
    expect(typeBlock).not.toMatch(/^\s*ttft_seconds\s*:/m)
    expect(typeBlock).not.toMatch(/^\s*error_summary\s*:/m)
  })

  it('keeps the old exported name working for any external reference', () => {
    expect(apiSource).toMatch(
      /export type ProviderResilienceStatus = ProviderResilienceRow/
    )
  })
})

describe('resilience panel content', () => {
  it('is reachable from the router and the sidebar', () => {
    expect(routerSource).toContain("'/llm/resilience'")
    expect(routerSource).toContain('ResilienceView.vue')
    expect(sidebarSource).toContain("'llm-resilience'")
  })

  it('orders the queue by priority so P1 is visibly first', () => {
    // 队列顺序是这条需求的核心：不排序就看不出「按优先级依次尝试」。
    expect(viewSource).toMatch(/a\.priority - b\.priority/)
    expect(viewSource).toContain('P{{ index + 1 }}')
  })

  it('labels all three circuit states', () => {
    // `half-open` 带连字符，在对象字面量里必须加引号，因此断言时也要带上引号形态。
    expect(viewSource).toContain('closed:')
    expect(viewSource).toContain('open:')
    expect(viewSource).toContain("'half-open':")
    for (const label of ['正常', '半开试探', '已熔断']) {
      expect(viewSource, `缺少熔断状态文案 ${label}`).toContain(label)
    }
  })

  it('always shows the sample count next to the error rate', () => {
    // 3 次里错 1 次和 300 次里错 100 次都是 33%，处置完全不同。
    expect(viewSource).toMatch(/次采样/)
    expect(viewSource).toMatch(/errorRateText/)
  })

  it('surfaces trace id and the partial-output flag on each attempt', () => {
    expect(viewSource).toContain('attempt.trace_id')
    expect(viewSource).toContain('attempt.partial_output')
    expect(viewSource).toContain('已产出内容')
  })

  it('shows why and when the breaker changed state, not only its current state', () => {
    // 需求 21.3 要「记录触发与恢复证据」。当前快照回答不了「昨天下午被隔离过吗、
    // 隔了多久」——轮询间隔内的 open → half-open → closed 在快照里完全不可见。
    expect(viewSource).toContain('recent_transitions')
    expect(viewSource).toContain('transitionReasonText')
    for (const reason of [
      'failure_threshold',
      'error_rate',
      'recovery_timeout',
      'recovery_success',
      'half_open_probe_failed'
    ]) {
      expect(viewSource, `缺少迁移原因 ${reason} 的文案`).toContain(reason)
    }
    // 两种「打开」必须能分辨：一个调阈值，一个查上游稳定性。
    expect(viewSource).toContain('连续失败达到阈值')
    expect(viewSource).toContain('错误率达到阈值')
  })

  it('does not format the monotonic transition clock as wall time', () => {
    // `at` 与 `next_recovery_time` 同为单调时钟，`new Date(at)` 会给出 1970 年。
    expect(viewSource).not.toMatch(/new Date\(\s*transition\.at/)
    expect(viewSource).toContain('latestTransitionAt')
  })

  it('translates error categories instead of showing raw enum values', () => {    for (const category of [
      'authentication',
      'rate_limit',
      'policy_rejection',
      'circuit_open'
    ]) {
      expect(viewSource, `缺少错误类型 ${category} 的文案`).toContain(category)
    }
  })

  it('stops its polling timer on unmount', () => {
    // 一个诊断面板不该在用户离开后继续打接口。
    expect(viewSource).toContain('onBeforeUnmount')
    expect(viewSource).toMatch(/clearInterval\(timer\)/)
  })

  it('does not format the monotonic recovery clock as a wall time', () => {
    // next_recovery_time 是单调时钟秒数，当成时间戳格式化会显示 1970 年。
    expect(viewSource).not.toMatch(/next_recovery_time[^\n]*toLocale/)
  })
})
