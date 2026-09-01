import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 故障转移队列必须给出运行态汇总（需求 8）。
 *
 * 参考界面在队列旁汇总**活跃连接、总请求数、成功率、运行时间**，理由是修改策略时
 * 要同步观察服务表现——逐行健康状态回答不了「刚把 P1 换掉之后整体好了没」。
 * 此前后端 `get_resilience_status()` 不返回这四项，前端只能显示
 * 「N 个供应商不处于正常状态」，那是一个计数，不是运行表现。
 *
 * 这些用例钉住类型对齐与三条呈现纪律，每条都对应一种会误导读者的写法。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/ResilienceView.vue')

/** 后端 `ResilienceSummary.to_dict()` 的九个键。 */
const BACKEND_FIELDS = [
  'active_connections',
  'total_requests',
  'success_rate',
  'uptime_seconds',
  'total_providers',
  'healthy_providers',
  'probing_providers',
  'tripped_providers',
  'sample_window'
]

describe('resilience summary type binding', () => {
  it('types every field the backend returns', () => {
    for (const field of BACKEND_FIELDS) {
      expect(apiSource, `ResilienceSummary 缺少字段 ${field}`).toContain(field)
    }
  })

  it('reads the summary from the same response as the rows', () => {
    // 分两次请求会让页面上出现「队列有 3 行、汇总说 2 家」这种自相矛盾的瞬间，
    // 而那个矛盾只在极短的时间窗里成立，最难复现也最难解释。
    expect(apiSource).toMatch(/data:\s*ProviderResilienceRow\[\]/)
    expect(apiSource).toMatch(/summary:\s*ResilienceSummary/)
    const call = apiSource.slice(apiSource.indexOf('getResilienceStatus'))
    expect(call.slice(0, 300)).toContain("'/llm/resilience/status'")
  })

  it('types success_rate as nullable', () => {
    // 没有样本时后端给 null。类型写成 number 会让前端把它当 0 显示，
    // 而 0% 看起来像全线故障。
    expect(apiSource).toMatch(/success_rate:\s*number \| null/)
  })
})

describe('resilience summary presentation', () => {
  it('renders the four figures the requirement names', () => {
    expect(viewSource).toContain('data-test="resilience-summary"')
    expect(viewSource).toContain('data-test="summary-active"')
    expect(viewSource).toContain('data-test="summary-requests"')
    expect(viewSource).toContain('data-test="summary-success-rate"')
    expect(viewSource).toContain('data-test="summary-uptime"')
  })

  it('shows 暂无样本 rather than a fabricated percentage', () => {
    // 刚启动时显示 100% 会让人以为链路已经验证过；显示 0% 更糟。
    const helper = viewSource.slice(viewSource.indexOf('const successRateText'))
    expect(helper.slice(0, 400)).toContain('暂无样本')
    expect(helper.slice(0, 400)).toMatch(/rate === null \|\| rate === undefined/)
  })

  it('states that total_requests is a bounded window, not a lifetime total', () => {
    // 不说明的话，读者会拿它与 LLM 追踪页的请求总数对比，然后认为其中一个是错的。
    expect(viewSource).toContain('非历史总量')
    expect(viewSource).toContain('sample_window')
  })

  it('breaks the three circuit states out separately', () => {
    // 半开是「正在试探、仍在服务」，熔断是「被跳过」，处置相反。
    expect(viewSource).toContain('data-test="summary-states"')
    expect(viewSource).toContain('healthy_providers')
    expect(viewSource).toContain('probing_providers')
    expect(viewSource).toContain('tripped_providers')
  })

  it('keeps null summary distinct from an all-zero summary', () => {
    // `null` 是「还没读到」，全 0 是「读到了，确实没有请求」。
    expect(viewSource).toMatch(/summary = ref<ResilienceSummary \| null>\(null\)/)
    expect(viewSource).toContain('v-if="summary && !loading"')
  })

  it('formats uptime into a readable unit', () => {
    // 「10920 秒」读不出量级。
    const helper = viewSource.slice(viewSource.indexOf('const uptimeText'))
    expect(helper.slice(0, 400)).toContain('小时')
  })
})
