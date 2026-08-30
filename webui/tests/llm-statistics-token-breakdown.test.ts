// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 9 / 22.1：统计页必须显示输入、输出与缓存 Token，以及缓存命中率。
 *
 * 后端已按四类拆分聚合（见 `tests/tracing/test_statistics_token_breakdown.py`）：
 * `overview` 多了 `total_prompt_tokens` / `total_completion_tokens` /
 * `total_cached_tokens` / `total_cache_write_tokens` / `cache_hit_rate`，
 * 分组与趋势分桶也各自带了四项。
 *
 * 但「返回了」不等于「看得到」——这正是本轮反复在修的那类缺口。少了渲染这一跳：
 *
 * - 缓存命中率在界面上不存在，而它是这批数字的用途所在：输入 Token 单价通常是
 *   缓存读取的 5~10 倍，一份「总 Token 没变」的账单在命中率从 80% 掉到 0% 时
 *   会翻几倍，而只显示总量的页面在这两种情况下**完全一样**。
 * - 趋势图只有一条总量线，看得出「涨了」看不出涨的是输入还是输出，
 *   而两者的处置相反（输入涨查上下文与历史长度，输出涨查 prompt 与 max_tokens）。
 *
 * `null` 与 `0` 的区分必须一路保留到界面：`cache_hit_rate === null` 是
 * 「没有上游报缓存」，显示 0% 会让运维去查一个并不存在的缓存失效问题。
 */

const here = dirname(fileURLToPath(import.meta.url))
const statisticsSource = readFileSync(
  resolve(here, '../src/components/LLMStatistics.vue'),
  'utf-8'
)
const vmSource = readFileSync(
  resolve(here, '../src/views/tracing/llm/llm-tracing.vm.ts'),
  'utf-8'
)

describe('statistics token breakdown types', () => {
  it('declares the four aggregate token totals', () => {
    for (const key of [
      'total_prompt_tokens',
      'total_completion_tokens',
      'total_cached_tokens',
      'total_cache_write_tokens'
    ]) {
      expect(vmSource).toContain(key)
    }
  })

  it('types the cache hit rate as nullable so unknown stays unknown', () => {
    expect(vmSource).toMatch(/cache_hit_rate:\s*number \| null/)
  })

  it('types grouped rows with the split', () => {
    const grouped = vmSource.match(/interface GroupedStat \{[\s\S]*?\n\}/)
    expect(grouped).not.toBeNull()
    expect(grouped?.[0]).toContain('prompt_tokens')
    expect(grouped?.[0]).toContain('completion_tokens')
    // 分组里缓存可空：这一组可能一个上游都没报缓存。
    expect(grouped?.[0]).toMatch(/cached_tokens:\s*number \| null/)
  })
})

describe('statistics token breakdown rendering', () => {
  it('shows input and output tokens, not only the total', () => {
    expect(statisticsSource).toContain('data-test="input-tokens"')
    expect(statisticsSource).toContain('data-test="output-tokens"')
  })

  it('shows the cache hit rate', () => {
    expect(statisticsSource).toContain('data-test="cache-hit-rate"')
  })

  it('says "unknown" rather than 0% when no upstream reported cache', () => {
    // 报 0% 会把「上游不报数」误判成「缓存失效」，那是两个完全不同的排查方向。
    //
    // 断言落在那个 computed 的函数体上，而不是「文件里某处出现过 === null」：
    // 后者会被任何一处不相关的空值判断满足，从而在真正的分支被删掉时仍然通过。
    const computed = statisticsSource.match(/const cacheHitRateText = computed\([\s\S]*?\n\}\)/)
    expect(computed).not.toBeNull()
    // `undefined` 也要挡：WebUI 可独立升级，旧后端不返回这个字段。
    expect(computed?.[0]).toMatch(/===\s*null/)
    expect(computed?.[0]).toMatch(/===\s*undefined/)
    expect(computed?.[0]).toMatch(/未上报/)
    // 未知时另有一句解释，否则「未上报」三个字自己不说明该去查什么。
    expect(statisticsSource).toContain('data-test="cache-hit-rate-unknown"')
    expect(statisticsSource).toMatch(/命中率未知/)
  })

  it('plots the token trend split by category, not just one total line', () => {
    // 一条总量线看得出涨了，看不出涨的是输入还是输出。
    const trend = statisticsSource.match(/dailyTokensOption[\s\S]{0,4000}/)
    expect(trend).not.toBeNull()
    expect(trend?.[0]).toContain('prompt_tokens')
    expect(trend?.[0]).toContain('completion_tokens')
    expect(trend?.[0]).toContain('cached_tokens')
  })

  it('exposes the split in the model and provider tooltips', () => {
    // 分组维度上「输入重」与「输出重」的两家，处置完全不同。
    const tooltipRegion = statisticsSource
    expect(tooltipRegion).toMatch(/输入[^\n]{0,20}Token|输入 Token/)
    expect(tooltipRegion).toMatch(/输出[^\n]{0,20}Token|输出 Token/)
  })
})

/**
 * 需求 9 的「Provider 统计」里最有处置价值的一项：成功率。
 *
 * 后端已按分组给出 `success_requests` / `failed_requests` / `pending_requests`
 * 与 `success_rate`（见 `tests/tracing/test_statistics_success_rate.py`）。
 * 界面上必须能看到它，否则「该把哪家供应商在故障转移队列里排后面」
 * 仍然只能靠翻请求日志人工计数。
 *
 * `null`（全是 pending、还没有结论）显示为「未知」而不是 0%：
 * 一家刚配好、只有一条在途请求的供应商不该看起来是最差的那一个。
 */
describe('provider success rate rendering', () => {
  it('types the nullable success rate', () => {
    expect(vmSource).toMatch(/success_rate:\s*number \| null/)
  })

  it('shows the success rate per provider', () => {
    expect(statisticsSource).toMatch(/成功率/)
  })

  it('keeps unknown distinct from zero percent', () => {
    const helper = statisticsSource.match(/const formatSuccessRate[\s\S]*?\n\}/)
    expect(helper).not.toBeNull()
    expect(helper?.[0]).toMatch(/===\s*null/)
    expect(helper?.[0]).toMatch(/===\s*undefined/)
    expect(helper?.[0]).toMatch(/未知/)
  })
})

/**
 * 需求 22.2「统计页面要支持趋势」里唯独缺的那一条：成本。
 *
 * 后端已按日 / 时分桶给出 `cost` / `cost_currency` / `cost_by_currency` 与
 * `unpriced_requests`（见 `tests/tracing/test_statistics_cost_trend.py`）。
 * 界面上必须有这条曲线，否则「这个月贵了三倍，是哪天开始的」只能靠手工
 * 二分时间范围反复改筛选条件重查——而账单异常恰恰最需要快速定位到某一天。
 */
describe('daily cost trend', () => {
  it('types the per-bucket cost fields', () => {
    const bucket = vmSource.match(/interface BucketTokenBreakdown \{[\s\S]*?\n\}/)
    expect(bucket).not.toBeNull()
    expect(bucket?.[0]).toContain('cost_by_currency')
    expect(bucket?.[0]).toMatch(/cost_currency:\s*string \| null/)
    expect(bucket?.[0]).toContain('unpriced_requests')
  })

  it('renders a dedicated cost chart', () => {
    // 与 Token 趋势分开：金额与 Token 数差几个数量级，同框会把一条压成平线。
    expect(statisticsSource).toContain('dailyCostOption')
    expect(statisticsSource).toMatch(/:option="dailyCostOption"/)
  })

  it('plots one line per currency instead of adding them up', () => {
    // 两种货币加进同一条曲线，得到的是一串没有单位的数字，而那不会报错。
    const option = statisticsSource.match(/const dailyCostOption = computed\([\s\S]*?\n\}\)/)
    expect(option).not.toBeNull()
    expect(option?.[0]).toContain('cost_by_currency')
    expect(option?.[0]).toMatch(/currencies\.map/)
  })

  it('flags unpriced requests so a dip is not read as "cheaper"', () => {
    const option = statisticsSource.match(/const dailyCostOption = computed\([\s\S]*?\n\}\)/)
    expect(option?.[0]).toContain('unpriced_requests')
    expect(option?.[0]).toMatch(/未定价/)
  })
})
