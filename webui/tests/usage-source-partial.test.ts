import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 四类用量来源必须在界面上各有文案（需求 22.1）。
 *
 * `provider_partial` 与 `provider` 分开是有代价差别的：前者的总额是**补出来的**
 * ——上游没回报缓存维度，缺失维度按 0 计价。而缓存读取的单价通常只有输入
 * Token 的 1/5 到 1/10，缓存写入往往更贵。两者显示成同一个词时，一份系统性
 * 偏低的账单看起来与完全可信的账单毫无区别，而这正是这一条需求存在的理由。
 *
 * 后端枚举见 `kirara_ai/llm/format/response.py::UsageSource`。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const vmSource = read('../src/views/tracing/llm/llm-tracing.vm.ts')
const chartSource = read('../src/components/LLMStatistics.vue')

/** 后端 UsageSource 的全部取值。 */
const BACKEND_SOURCES = ['provider', 'provider_partial', 'estimated', 'unknown']

describe('请求日志的来源文案', () => {
  it.each(BACKEND_SOURCES)('%s 有对应文案', (value) => {
    expect(vmSource).toContain(`${value}:`)
  })

  it('部分回报与完整回报不是同一个词', () => {
    expect(vmSource).toContain('供应商部分回报')
    expect(vmSource).toContain("provider: '供应商返回'")
  })
})

describe('统计图的来源文案', () => {
  it.each(BACKEND_SOURCES)('%s 有对应文案', (value) => {
    expect(chartSource).toContain(`${value}:`)
  })

  it('副标题说明部分回报的总额是怎么算出来的', () => {
    // 只把它列成第四个扇形还不够：用户需要知道那个数字为什么不完全可信。
    expect(chartSource).toContain('缺失维度为 0')
  })
})

describe('未知取值仍然可读', () => {
  it('文案表查不到时回落到原始值而不是空白', () => {
    // 后端将来新增一个成员时，界面应显示那个原始字符串，
    // 而不是显示一个空格——空白读起来像「这一行没有数据」。
    expect(vmSource).toMatch(/USAGE_SOURCE_LABELS\[[^\]]+\] \|\|/)
    expect(chartSource).toMatch(/labels\[[^\]]+\] \|\|/)
  })
})
