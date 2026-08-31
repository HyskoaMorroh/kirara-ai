// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 9：上游限额余量必须在容错面板上看得到。
 *
 * 桌面端参考实现的额度面板回答「这个上游还剩多少可用」（进度条 + 重置倒计时）。
 * 后端已按本项目的落点实现——从每个响应的限额头采集余量，随
 * `GET /llm/resilience/status` 的行返回（见
 * `tests/llm/test_rate_limit_integration.py`）。
 *
 * 界面这一跳不能省：余量在 API 里而面板不显示，等于「离上限还有多远」只能 curl。
 * 而这个数的全部价值就在于**撞上之前**看到它——事后从 429 日志里反推没有意义。
 *
 * 两个必须区分开的显示：
 * - `null`（上游不报限额头，很多兼容端点如此）→「未上报」；
 * - `0`（余量真的用完）→ 要显眼。
 * 两者在界面上长得一样时，第一种会被当成紧急情况，第二种会被忽略。
 */

const here = dirname(fileURLToPath(import.meta.url))
const apiSource = readFileSync(resolve(here, '../src/api/llm.ts'), 'utf-8')
const viewSource = readFileSync(
  resolve(here, '../src/views/llm/ResilienceView.vue'),
  'utf-8'
)

describe('rate limit headroom in the resilience panel', () => {
  it('declares the shape on ProviderResilienceRow', () => {
    const row = apiSource.match(/interface ProviderResilienceRow \{[\s\S]*?\n\}/)
    expect(row).not.toBeNull()
    expect(row?.[0]).toContain('rate_limit')
  })

  it('types the headroom fields as nullable', () => {
    // 非空类型会让「上游不报」这个状态无法表达，前端只能编一个 0。
    const block = apiSource.match(/interface RateLimitHeadroom \{[\s\S]*?\n\}/)
    expect(block).not.toBeNull()
    expect(block?.[0]).toMatch(/remaining_requests\??:\s*number \| null/)
    expect(block?.[0]).toMatch(/request_headroom\??:\s*number \| null/)
  })

  it('renders the headroom in the panel', () => {
    expect(viewSource).toContain('data-test="rate-limit-headroom"')
  })

  it('distinguishes "not reported" from "none left"', () => {
    // 0% 与「未上报」在界面上长得一样时，前者会被忽略、后者会被当成紧急情况。
    expect(viewSource).toMatch(/未上报/)
    const fn = viewSource.match(/function headroomText[\s\S]*?\n\}/)
    expect(fn, 'headroomText 未定义').not.toBeNull()
    expect(fn?.[0]).toMatch(/null|undefined/)
  })

  it('shows the reset countdown when the upstream reports one', () => {
    // 「还剩 12 次」与「还剩 12 次、8 秒后重置」是完全不同的处置：
    // 后者等一会儿就好，前者要立刻降频。
    expect(viewSource).toMatch(/reset|重置/)
  })
})
