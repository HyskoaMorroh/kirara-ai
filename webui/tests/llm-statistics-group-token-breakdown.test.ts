// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * Provider 与模型两个分组必须给出**四类** Token，不能漏缓存创建。
 *
 * 需求 9 点名「不同类型上游真实消耗 Tokens」，参考界面的趋势图图例是五条：
 * 成本、**缓存创建**、缓存命中、输入、输出。
 *
 * 后端逐组算齐了四类（`_group_statistics` 里 `prompt_tokens` /
 * `completion_tokens` / `cached_tokens` / `cache_write_tokens` 四个 SUM，
 * 缓存两项还各带一个 `count` 用来区分「没上游报过」与「报了 0」）。
 * 概览卡片有四类、趋势折线有四条，唯独 Provider 与模型两个分组的 tooltip
 * 只列了三类——缓存创建被丢掉。
 *
 * 丢它的后果不是「少一个数」，而是最贵的那种情形看不见：
 * 缓存写入的单价通常**高于**普通输入（Anthropic 是 1.25 倍），
 * 而缓存读取只有输入的十分之一。一家「缓存创建很高、缓存命中接近 0」的上游
 * 正在按溢价写一堆永远不会被读到的缓存——这是账单异常里最值得先查的一种，
 * 而在只有「输入 / 输出 / 缓存读取」三项的 tooltip 里，它与一家正常上游长得一样。
 *
 * 这组用例按行为断言：两个分组各自都要出现缓存创建，且要用能区分
 * `null`（没上游报过）与 `0`（报了、确实没写）的格式化函数——两者显示成同一个
 * 东西时，前者会被当成「缓存没配起来」去排查一个不存在的问题。
 */

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(
  resolve(here, '../src/components/LLMStatistics.vue'),
  'utf-8'
)

/** 取出一个 ECharts option 计算属性的源码段。 */
function optionBlock(name: string): string {
  const start = source.indexOf(`const ${name}`)
  expect(start, `找不到 ${name}`).toBeGreaterThan(-1)
  const rest = source.slice(start + 1)
  const next = rest.search(/\nconst [a-zA-Z]/)
  return rest.slice(0, next === -1 ? rest.length : next)
}

const GROUPS = [
  ['providerUsageOption', 'Provider 统计'],
  ['modelUsageOption', '模型使用分析']
] as const

describe('分组统计的 Token 拆分', () => {
  it('自检：两个分组的 option 都解析到了', () => {
    for (const [name] of GROUPS) {
      const block = optionBlock(name)
      expect(block.length).toBeGreaterThan(200)
      expect(block).toContain('tooltip')
    }
  })

  it.each(GROUPS)('%s 列出四类 Token', (name) => {
    const block = optionBlock(name)

    for (const label of ['输入 Token', '输出 Token', '缓存读取', '缓存创建']) {
      expect(block, `${name} 的 tooltip 缺「${label}」`).toContain(label)
    }
  })

  it.each(GROUPS)('%s 的缓存两项区分「未上报」与「零」', (name) => {
    const block = optionBlock(name)

    // 后端对缓存两项返回 `number | null`：`null` = 这一组里没有任何上游报过缓存。
    // 直接插值会把它渲染成 "null"，而按 0 显示会把「未知」说成「没命中」。
    expect(block).toMatch(/缓存读取：\$\{formatNullableTokens\(/)
    expect(block).toMatch(/缓存创建：\$\{formatNullableTokens\(/)
  })

  it.each(GROUPS)('%s 绑到后端真实字段名', (name) => {
    const block = optionBlock(name)

    expect(block).toContain('row.prompt_tokens')
    expect(block).toContain('row.completion_tokens')
    expect(block).toContain('row.cached_tokens')
    expect(block).toContain('row.cache_write_tokens')
  })

  it('概览与趋势本来就有缓存创建，分组不该是唯一的例外', () => {
    // 这条是对照：三处口径必须一致，否则同一份账单在三个位置给出三种拆分。
    expect(source).toContain('total_cache_write_tokens')
    expect(source).toMatch(/daily_stats[\s\S]{0,200}cache_write_tokens/)
  })
})
