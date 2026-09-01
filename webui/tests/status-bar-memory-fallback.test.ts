/**
 * 状态栏的兜底值必须和模板读法对得上。
 *
 * `memoryUsage` 在 store 里是 `{ percent, total, used, free }`,模板按
 * `memoryUsage.used.toFixed(2)` 渲染。但 `onMounted` 的初始赋值和请求失败的 catch
 * 分支都写成 `memoryUsage: 0` —— 数字没有 `.used`,取到 `undefined` 再调 `.toFixed`
 * 直接抛 TypeError,Vue 的渲染在这里中断,整条状态栏消失。
 *
 * 挂载即赋值,所以后端不可达时这是必然路径,不是边角情况。这里锁住:所有兜底赋值都
 * 得给出完整的四字段对象。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const statusBar = readFileSync(
  resolve(__dirname, '../src/components/layout/StatusBar.vue'),
  'utf-8'
)

describe('状态栏内存兜底', () => {
  it('自检:模板确实按对象字段读 memoryUsage', () => {
    expect(statusBar).toMatch(/memoryUsage\.used\.toFixed/)
  })

  it('自检:确实存在多处 updateSystemStatus 兜底赋值', () => {
    const calls = statusBar.match(/updateSystemStatus\(\{/g) ?? []
    expect(calls.length).toBeGreaterThanOrEqual(3)
  })

  it('没有任何一处把 memoryUsage 赋成标量', () => {
    const scalars = [...statusBar.matchAll(/memoryUsage:\s*([^\n{]*)/g)]
      .map((m) => m[1].trim().replace(/,$/, ''))
      .filter((value) => value !== '')
    expect(scalars, `memoryUsage 被赋成标量：${scalars.join(' | ')}`).toEqual([])
  })

  it('每一处 memoryUsage 都给出完整四字段', () => {
    const blocks = [...statusBar.matchAll(/memoryUsage:\s*\{([^}]*)\}/g)].map((m) => m[1])
    expect(blocks.length, '找不到任何对象形式的 memoryUsage').toBeGreaterThanOrEqual(3)

    for (const block of blocks) {
      for (const field of ['percent', 'total', 'used', 'free']) {
        expect(block, `memoryUsage 缺字段 ${field}`).toMatch(new RegExp(`\\b${field}\\s*:`))
      }
    }
  })
})
