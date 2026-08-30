// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 9：「单次成本」必须在统计页说得出来。
 *
 * 后端的分组聚合已经给了每个维度的 `count` 与 `cost`（`_group_statistics`），
 * 界面也显示了合计成本。缺的是两者之商——**平均每次请求多少钱**。
 *
 * 为什么这个数不能让用户自己算：它是回答「该换模型吗」的那个数，
 * 而合计成本回答不了。请求量最大的模型往往不是最贵的，合计成本高也可能只是
 * 因为调用多；只有单次成本能把「这个模型贵」与「这个模型用得多」分开。
 * 两个模型合计成本相同、单次成本差十倍时，合计视图完全看不出差别。
 *
 * 另一半是**未定价请求**：单次成本 = 成本 / 总请求数 会把未定价的那些算进分母，
 * 得到一个偏低的数字，而它看起来完全正常。分母必须是**已定价**的请求数，
 * 且未定价条数要在旁边标出来。
 */

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(
  resolve(here, '../src/components/LLMStatistics.vue'),
  'utf-8'
)

describe('per-request cost', () => {
  it('is exposed as a helper rather than inlined in one chart', () => {
    // 三个分组维度（模型/后端/供应商）都要用同一口径；各写一份必然漂移。
    expect(source).toMatch(/function formatCostPerRequest|const formatCostPerRequest/)
  })

  it('divides by priced requests, not by all requests', () => {
    // 把未定价请求算进分母会得到一个偏低且看起来正常的数字。
    const fn = source.match(
      /(?:function|const) formatCostPerRequest[\s\S]*?\n(?:\}|\})/
    )
    expect(fn).not.toBeNull()
    expect(fn?.[0]).toMatch(/unpriced/)
  })

  it('says "无数据" rather than 0 when nothing is priced', () => {
    // 0 是「不花钱」这个论断，而这里的事实是「不知道」。
    const fn = source.match(
      /(?:function|const) formatCostPerRequest[\s\S]*?\n(?:\}|\})/
    )
    expect(fn?.[0]).toMatch(/无数据|---|未定价/)
  })

  it('appears in the model tooltip next to total cost', () => {
    const tooltip = source.match(/modelUsageOption[\s\S]*?legend:/)
    expect(tooltip).not.toBeNull()
    expect(tooltip?.[0]).toContain('formatCostPerRequest')
  })
})
