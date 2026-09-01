/**
 * 手工同步成功后必须真的刷新目录，且不能引用不存在的函数（需求 9）。
 *
 * `syncFromUpstream()` 末尾调的是 `load()`，而这个组件里只有 `loadCatalog()`——
 * 没有 `load` 这个定义。它的表现方式最坏：同步请求**已经成功**、价格**已经落盘**，
 * 但紧接着的 `ReferenceError` 被 catch 接住，界面弹出「定价同步失败」。
 *
 * 于是用户看到失败提示，刷新页面却发现价格更新了，会以为同步是"半成功"或数据有脏写，
 * 而真实情况是同步完全成功、只有刷新那一步崩了。类型检查也抓不到：`load` 在模板
 * 作用域之外是普通标识符，`vue-tsc` 对 `<script setup>` 里的未定义引用只在编译期
 * 报错——而它确实会报，说明这一处从未被跑到过。
 *
 * 这里用静态断言而不是挂载组件：缺陷是「引用了不存在的标识符」，读源码就能判定，
 * 挂载反而要先造出一整套 API mock 才能走到那一行。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const source = readFileSync(
  resolve(__dirname, '../src/views/llm/PricingView.vue'),
  'utf-8'
)

describe('PricingView 同步后的刷新', () => {
  it('不存在裸 load() 调用，因为这个组件没有定义 load', () => {
    const bareLoad = [...source.matchAll(/(?<![A-Za-z_$.])load\s*\(/g)]
    expect(
      bareLoad.map((match) => source.slice(Math.max(0, match.index! - 40), match.index! + 10)),
      '调用了未定义的 load()：同步会成功但界面报失败'
    ).toEqual([])
  })

  it('同步成功后刷新目录，否则表格上还是同步前的旧价格', () => {
    const start = source.indexOf('async function syncFromUpstream')
    const body = source.slice(start, source.indexOf('\n}', start))
    expect(body).toMatch(/loadCatalog\(\)/)
  })
})
