/**
 * 前端 `resilienceDefaults()` 必须与后端 `LLMBackendConfig` 的字段默认值一致
 * （需求 8）。
 *
 * 这份默认值不是展示用的占位符——`LLMAdapterConfig.vue` 的 `resilienceValue()`
 * 在字段未设置时回落到它，而新建供应商保存时会把这些值**真的写进配置**。
 * 于是两端一旦漂移，后果不是「显示得不一样」，而是：后端把非流式默认放宽到
 * 600s 之后，任何从界面新建的供应商仍然被写回 60s，且面板上显示的就是 60 ——
 * 用户看不出自己从未选择过这个值。改后端默认值而漏改这里，等于让新默认值
 * 只对「手写配置文件的人」生效。
 *
 * 判据：**默认值只能有一个来源。** 前端这份是镜像，必须由测试钉住它跟随后端。
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { resilienceDefaults } from '../src/api/llm'

/** 后端字段名 → 前端同名键。两端命名一致，此处只做存在性与数值比对。 */
const FIELDS = [
  'auto_detect_interval_days',
  'priority',
  'max_retries',
  'retry_backoff_seconds',
  'retry_backoff_max_seconds',
  'request_timeout_seconds',
  'non_stream_timeout_seconds',
  'stream_first_byte_timeout_seconds',
  'stream_idle_timeout_seconds',
  'stream_total_timeout_seconds',
  'circuit_failure_threshold',
  'circuit_error_rate_threshold',
  'circuit_min_requests',
  'circuit_recovery_timeout_seconds',
  'circuit_recovery_success_threshold'
] as const

/**
 * 从 `global_config.py` 抽取字段默认值。
 *
 * 刻意读源码而不是起一个 Python 进程：这条测试要能在纯前端的 CI 作业里跑。
 * 代价是解析得容忍两种写法——单行 `Field(default=X, ...)` 与跨行的
 * `Field(\n default=X,\n ...)`。
 */
const backendDefaults = (): Record<string, number> => {
  const source = readFileSync(
    resolve(__dirname, '../../kirara_ai/config/global_config.py'),
    'utf-8'
  )
  const start = source.indexOf('class LLMBackendConfig')
  expect(start, 'global_config.py 里找不到 LLMBackendConfig').toBeGreaterThan(-1)
  // 归一行尾：源文件是 CRLF，逐行解析时 \r 会混进缩进与数值里。
  const lines = source.slice(start).replace(/\r/g, '').split('\n')

  const out: Record<string, number> = {}
  for (const field of FIELDS) {
    const at = lines.findIndex((line) => line.startsWith(`    ${field}:`))
    expect(at, `后端缺少字段 ${field}`).toBeGreaterThan(-1)
    // 声明可能跨多行（`Field(` 换行写 default=），向后并入若干行再取数值。
    const declaration = lines.slice(at, at + 8).join(' ')
    const value = declaration.match(/default\s*=\s*([0-9_]+(?:\.[0-9]+)?)/)
    expect(value, `字段 ${field} 没有数值默认值`).not.toBeNull()
    out[field] = Number(value![1].replace(/_/g, ''))
  }
  return out
}

describe('resilienceDefaults 与后端配置默认值', () => {
  const backend = backendDefaults()
  const frontend = resilienceDefaults() as unknown as Record<string, number>

  it.each(FIELDS)('%s 两端一致', (field) => {
    expect(frontend[field], `${field} 前端未声明`).toBeTypeOf('number')
    expect(frontend[field]).toBeCloseTo(backend[field], 5)
  })

  it('放宽后的超时默认值确实到达了前端', () => {
    // 单独钉住这三个数字：它们是需求 8 里用户确认过的值，
    // 上面的逐字段比对只保证「跟随」，这里保证「跟随到了正确的那一版」。
    expect(frontend.stream_first_byte_timeout_seconds).toBeCloseTo(60, 5)
    expect(frontend.stream_idle_timeout_seconds).toBeCloseTo(120, 5)
    expect(frontend.non_stream_timeout_seconds).toBeCloseTo(600, 5)
  })

  it('流式活动预算不超过流式总预算', () => {
    // 与后端 `check_resilience_budget` 同一条约束。前端若给出一组自相矛盾的
    // 默认值，新建供应商会在保存时被后端拒绝，而错误指向用户没碰过的字段。
    expect(
      frontend.stream_first_byte_timeout_seconds + frontend.stream_idle_timeout_seconds
    ).toBeLessThanOrEqual(frontend.stream_total_timeout_seconds)
  })
})
