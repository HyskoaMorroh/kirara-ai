/**
 * 定价自动同步的运行态必须在界面上看得见（需求 9）。
 *
 * 调度器一直汇报 `price_sync`（interval_days / enabled / last_run / last_ok），
 * 但前端类型只声明了 `running` 与 `backends`——后端读得到、前端到不了。
 *
 * 这个缺口在界面上是"看不出来的"：定价页有同步按钮，手工点一次会成功，
 * 于是看起来功能齐全。真正丢掉的是**自动同步是否在跑**这条信息：
 * 间隔设成 7 天之后，用户无法从任何地方确认它到底有没有生效、上次何时跑的、
 * 上次是成功还是失败。价格静止不动时，「上游没调价」和「同步半个月前就失败了」
 * 在界面上完全同形。
 *
 * 特别地，`last_ok` 必须是三态（null = 未同步过，true/false = 上次结果），
 * 不能塌成布尔——把「从没跑过」显示成「失败」会引来无意义的排查。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const api = readFileSync(resolve(root, 'src/api/llm.ts'), 'utf-8')
const view = readFileSync(resolve(root, 'src/views/llm/PricingView.vue'), 'utf-8')

describe('定价自动同步运行态', () => {
  it('调度响应类型声明了 price_sync，否则这段状态在前端根本没有落点', () => {
    expect(api).toMatch(/price_sync\??:/)
  })

  // 四个字段可以内联在调度响应里，也可以抽成具名接口再被它引用——两种写法都算
  // "前端有落点"，所以这里取声明它们的那一段，而不是假定某一种结构。
  const syncBlock = (() => {
    const named = api.indexOf('interface PriceSyncState')
    const start = named >= 0 ? named : api.indexOf('price_sync')
    return api.slice(start, api.indexOf('\n}', start))
  })()

  it('last_ok 是三态，null 表示从未同步过', () => {
    expect(syncBlock).toMatch(/last_ok\??:\s*boolean\s*\|\s*null/)
  })

  it('last_run 可为 null，因为第一次同步之前它本来就没有值', () => {
    expect(syncBlock).toMatch(/last_run\??:\s*string\s*\|\s*null/)
  })

  it('定价页读取这份状态，而不是只保留一个手工同步按钮', () => {
    expect(view).toMatch(/price_sync|priceSync/)
  })

  it('定价页区分「未同步过」与「同步失败」两种取值', () => {
    expect(view).toMatch(/last_ok|lastOk/)
  })
})
