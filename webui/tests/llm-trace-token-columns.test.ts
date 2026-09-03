// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 请求日志必须逐条给出四类 Token 的拆分，不只是一个合计。
 *
 * 需求 9 点名「不同类型上游**真实消耗 Tokens**」，参考界面的请求日志表把
 * 输入与输出分成两列（`docs/superpowers/plans/ccs-ui-notes.md` 里
 * `Image_2026-08-23_032829_455` 那条：「日志表按时间、供应商、计费模型、**输入、
 * 输出**、总成本、用时/首字、状态、来源展示」）。
 *
 * 数据链路是全的：库里四列都有（`prompt_tokens` / `completion_tokens` /
 * `cached_tokens` / `cache_write_tokens`），`to_dict()` 四个都出，
 * 前端 `LLMTrace` 类型四个都声明了。断的只是**列表列**：表里只有一个合计
 * `Tokens`，四个字段取回来又不显示。
 *
 * 「只有合计」在这个页面上不是省略而是歧义：同样 100 万 Token，一家几乎全在读
 * 上下文、另一家几乎全在生成，成本能差 5~10 倍（输出单价通常是输入的数倍，
 * 缓存读取又比输入便宜一个量级）。要判断「这条为什么这么贵」，
 * 合计恰恰是唯一回答不了的那个数字——而详情页已经有拆分，说明拆分本身有价值，
 * 只是要点进去一条一条看，而排查的第一步是横向比较一屏里的几十条。
 *
 * 这组用例钉住行为：四类各自成列、缓存两项的 `null`（没有上游报过缓存）
 * 与 `0`（报了、确实没命中）在显示上必须可区分。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const vmSource = read('../src/views/tracing/llm/llm-tracing.vm.ts')

/** 表格列标题，按声明顺序。 */
function columnTitles(): string[] {
  return [...vmSource.matchAll(/title: '([^']+)'/g)].map((match) => match[1])
}

describe('请求日志的 Token 拆分', () => {
  it('自检：确实解析到了表格列', () => {
    const titles = columnTitles()
    expect(titles.length).toBeGreaterThan(8)
    expect(titles).toContain('供应商')
  })

  it('输入与输出各自成列', () => {
    const titles = columnTitles()
    expect(titles).toContain('输入')
    expect(titles).toContain('输出')
  })

  it('缓存命中与缓存创建各自成列', () => {
    // 两者的处置相反：命中率低要查缓存有没有配好，创建量大要查上下文是不是每轮都在变。
    const titles = columnTitles()
    expect(titles).toContain('缓存命中')
    expect(titles).toContain('缓存创建')
  })

  it('四列各自绑到自己的字段', () => {
    for (const [title, key] of [
      ['输入', 'prompt_tokens'],
      ['输出', 'completion_tokens'],
      ['缓存命中', 'cached_tokens'],
      ['缓存创建', 'cache_write_tokens']
    ] as const) {
      // 列定义里 `title` 与 `key` 相邻声明；绑错字段的后果是四列显示同一个数。
      const pattern = new RegExp(`title: '${title}'[\\s\\S]{0,120}?key: '${key}'`)
      expect(vmSource, `列「${title}」没有绑到 ${key}`).toMatch(pattern)
    }
  })

  it('保留合计列', () => {
    // 拆分是补充而不是替代：合计仍是「这条请求有多大」最快的读法。
    expect(columnTitles()).toContain('Tokens')
  })

  it('缓存两项区分「未上报」与「零」', () => {
    // `null` = 没有任何上游报过缓存（未知），`0` = 报了、确实没命中。
    // 两者显示成同一个东西时，前者会被当成缓存失效去排查一个不存在的问题。
    expect(vmSource).toMatch(/formatOptionalTokens|未上报/)
  })
})

describe('列宽与横向滚动', () => {
  it('表格声明的 scroll-x 不小于列宽之和', () => {
    // 加了四列之后总宽超过常见视口。不给 scroll-x 时 naive-ui 会按容器宽度
    // 压缩每一列，数字被截断成「1,2…」——而这个页面的全部内容就是数字。
    const listSource = read('../src/views/tracing/llm/LLMTraceList.vue')
    const declared = listSource.match(/:scroll-x="(\d+)"/)
    expect(declared, '请求日志表没有声明 scroll-x').not.toBeNull()

    const widths = [...vmSource.matchAll(/width: (\d+)/g)].map((match) => Number(match[1]))
    const total = widths.reduce((sum, value) => sum + value, 0)
    expect(Number(declared![1])).toBeGreaterThanOrEqual(total)
  })
})
