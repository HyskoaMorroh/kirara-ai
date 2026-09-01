/**
 * 收窄检查后不许再回落到未收窄的引用。
 *
 * `getConfiguredPorts` 的判断链是这样的：
 *
 *     Array.isArray(block.config?.[key])   ← 用可选链，容忍 config 缺失
 *       ? block.config[key].map(...)       ← 却用了非可选的 block.config
 *
 * `config` 在 `ConfiguredBlock` 上是可选的。三元真分支里 TS 不认为它已被收窄
 * （可选链检查的是 `block.config?.[key]` 的结果，不是 `block.config` 本身），
 * 所以 `block.config[key]` 报 TS18048。
 *
 * 运行时它今天不炸，纯粹因为 `Array.isArray(undefined?.[key])` 恒为 false，
 * 真分支进不去。但这是**靠巧合成立**：一旦哪天判断改成 `block.config?.[key]?.length`
 * 之类的写法，真分支就可能在 `config` 为 undefined 时进入，直接抛
 * `Cannot read properties of undefined`。而它位于工作流图的端口解析路径上，
 * 抛错就是整张画布渲染不出来。
 *
 * 这里锁两件事：判断与取值必须用同一个表达式（都可选或都非可选），
 * 以及 map 回调参数要有显式类型（`asPort` 收 `any`，隐式 any 会让端口结构错配
 * 一路静默传到渲染层）。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  resolve(__dirname, '../src/components/workflow/workflow-data.ts'),
  'utf-8'
)

/** 取 `getConfiguredPorts` 的函数体。 */
function portsFnBody(): string {
  const at = source.indexOf('const getConfiguredPorts')
  expect(at, '找不到 getConfiguredPorts').toBeGreaterThan(-1)
  return source.slice(at, source.indexOf('\n}', at))
}

describe('getConfiguredPorts 的可选链一致性', () => {
  it('自检：确实读到了这个函数', () => {
    expect(portsFnBody()).toContain('Array.isArray')
    expect(portsFnBody()).toContain('asPort')
  })

  it('判断用了可选链，取值就不能用非可选写法', () => {
    const body = portsFnBody()
    const guardsOptionally = /Array\.isArray\(\s*block\.config\?\./.test(body)
    if (!guardsOptionally) return

    // `block.config[` —— 前面没有 `?`，就是那处未收窄的回落。
    expect(
      /block\.config\[/.test(body),
      '判断用 block.config?.[key] 但取值用 block.config[key]：TS18048，且真分支一旦可达就会抛 TypeError'
    ).toBe(false)
  })

  it('map 回调的参数有显式类型', () => {
    const body = portsFnBody()
    const mapCall = body.match(/\.map\(\(([^)]*)\)/)
    expect(mapCall, '找不到 map 回调').toBeTruthy()
    expect(
      mapCall![1],
      'map 回调参数没写类型，隐式 any 会让端口结构错配静默传到渲染层'
    ).toMatch(/:/)
  })
})
