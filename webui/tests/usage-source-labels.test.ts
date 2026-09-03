// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { USAGE_SOURCE_LABELS, usageSourceLabel } from '../src/views/tracing/usageSource'

/**
 * 四类用量来源的文案按**行为**验证，并且只有一份。
 *
 * 替换的是 `usage-source-partial.test.ts` 的做法：它在两个文件里分别
 * `expect(vmSource).toContain('provider_partial:')` 与
 * `expect(chartSource).toContain('provider_partial:')`。两处各有一份表时，
 * 每一份自己都「包含那个字符串」，于是两者漂移了测试照样全绿——
 * 而漂移只显形在用户眼里：请求日志说「供应商部分回报」，统计图说别的。
 *
 * `provider_partial` 与 `provider` 必须是两个词（需求 22.1）：前者的总额是
 * 补出来的（上游没回报缓存维度，缺失维度按 0 计价），而缓存读取单价通常只有
 * 输入 Token 的 1/5 到 1/10、缓存写入往往更贵。显示成同一个词时，
 * 一份系统性偏低的账单看起来与完全可信的账单毫无区别。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

/** 后端 `UsageSource` 的全部取值。 */
const BACKEND_SOURCES = ['provider', 'provider_partial', 'estimated', 'unknown']

describe('文案表', () => {
  it.each(BACKEND_SOURCES)('%s 有中文文案', (value) => {
    expect(usageSourceLabel(value)).toBeTruthy()
    expect(usageSourceLabel(value)).not.toBe(value)
  })

  it('部分回报与完整回报是两个不同的词', () => {
    // 这是这条需求的实质。相同就等于把一份偏低的账单说成完全可信。
    expect(usageSourceLabel('provider_partial')).not.toBe(usageSourceLabel('provider'))
    expect(usageSourceLabel('provider_partial')).toBe('供应商部分回报')
    expect(usageSourceLabel('provider')).toBe('供应商返回')
  })

  it('四个文案互不相同', () => {
    const labels = BACKEND_SOURCES.map(usageSourceLabel)
    expect(new Set(labels).size).toBe(BACKEND_SOURCES.length)
  })

  it('表里没有多余成员——多出来的说明后端已经改了枚举', () => {
    expect(Object.keys(USAGE_SOURCE_LABELS).sort()).toEqual([...BACKEND_SOURCES].sort())
  })
})

describe('未知取值', () => {
  it('查不到时回落到原始值，而不是空白', () => {
    // 后端将来新增一个成员时，界面显示那个原始字符串仍然可读；
    // 空白读起来像「这一行没有数据」。
    expect(usageSourceLabel('brand_new_source')).toBe('brand_new_source')
  })

  it('null 与 undefined 不抛错', () => {
    expect(usageSourceLabel(null)).toBe('')
    expect(usageSourceLabel(undefined)).toBe('')
  })
})

describe('两个消费点共用同一份表', () => {
  const vmSource = read('../src/views/tracing/llm/llm-tracing.vm.ts')
  const chartSource = read('../src/components/LLMStatistics.vue')

  it('请求日志侧不再自带一份文案表', () => {
    // 自带一份就会漂移，而漂移没有任何测试能发现（各自都「包含那个字符串」）。
    expect(vmSource).not.toMatch(/provider_partial:\s*'/)
    expect(vmSource).toMatch(/from '\.\.\/usageSource'/)
  })

  it('统计图侧不再自带一份文案表', () => {
    expect(chartSource).not.toMatch(/provider_partial:\s*'/)
    expect(chartSource).toMatch(/from '@\/views\/tracing\/usageSource'/)
  })

  it('三处渲染点都走同一个函数', () => {
    // 表格列、导出列、扇形名称。漏掉一处就是那一处会漂移。
    expect((vmSource.match(/usageSourceLabel\(/g) || []).length).toBeGreaterThanOrEqual(3)
    expect(chartSource).toMatch(/usageSourceLabel\(/)
  })

  it('统计图副标题说明部分回报的总额是怎么算出来的', () => {
    // 只把它列成第四个扇形还不够：用户需要知道那个数字为什么不完全可信。
    expect(chartSource).toContain('缺失维度为 0')
  })
})
