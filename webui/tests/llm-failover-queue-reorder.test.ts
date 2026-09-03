// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 故障转移队列的顺序必须能在**看到队列的地方**改。
 *
 * 需求 8 的队列语义是「按队列优先级选择供应商（P1 优先）」。此前 `priority` 只能
 * 在供应商编辑表单里逐个填数字：想把 P3 提到 P1，得先记住另外两家各是多少、再算
 * 一个中间值填进去，而这三次编辑分散在三个不同的表单里。队列页能看到实际次序却
 * 只读——看的地方和改的地方分离。
 *
 * 这组用例钉住的是行为，不是某一种写法：
 *
 * - API 客户端有一次提交整条队列的调用点（不是「把某一家改成某个数字」——
 *   后者会经过两家同优先级的中间态，而那时的相对次序由后端列表下标决定）；
 * - 面板上有可键盘操作的移动入口，两端各自禁掉越界方向；
 * - 用后端返回的新状态刷新，而不是本地改序后等轮询；
 * - 单元素队列不显示移动按钮（没有可移动的位置）。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/ResilienceView.vue')

describe('API 客户端', () => {
  it('声明了整条队列的重排调用', () => {
    expect(apiSource).toContain('reorderFailoverQueue')
  })

  it('打到后端的专用路由', () => {
    expect(apiSource).toContain("'/llm/resilience/queue'")
  })

  it('用 PUT 而不是 POST', () => {
    // 重排是幂等的：同一组次序提交两次结果相同。用 POST 会读成「新建一条队列」。
    expect(apiSource).toMatch(/reorderFailoverQueue[\s\S]{0,400}http\.put/)
  })

  it('一次提交模型与整条队列，而不是单家的数字', () => {
    expect(apiSource).toMatch(/reorderFailoverQueue\(model: string, providers: string\[\]\)/)
    expect(apiSource).toMatch(/reorderFailoverQueue[\s\S]{0,500}\{\s*model,\s*providers\s*\}/)
  })
})

describe('面板上的排序入口', () => {
  it('提供上移与下移按钮', () => {
    expect(viewSource).toContain('data-test="queue-move-up"')
    expect(viewSource).toContain('data-test="queue-move-down"')
  })

  it('用按钮而不是拖拽，天然可键盘操作', () => {
    // 拖拽要额外补一套键盘操作才等价；两套交互对同一件事，
    // 出错时很难判断是哪一套没生效。
    expect(viewSource).not.toContain('draggable')
    expect(viewSource).toMatch(/queue-move-up[\s\S]{0,400}aria-label/)
  })

  it('首行禁上移、末行禁下移', () => {
    // 边界判断（首项不能上移、末项不能下移、单项队列全禁用）由
    // `llm-failover-order.test.ts` 调用函数验证。这里只确认模板用的是
    // 那对函数而不是自己写一份 index 比较——两处各写一份会漂移成
    // 「按钮可点而提交被拒」。
    expect(viewSource).toMatch(/canMoveUp\(index, queue\.providers\.length\)/)
    expect(viewSource).toMatch(/canMoveDown\(index, queue\.providers\.length\)/)
  })

  it('单元素队列不显示移动按钮', () => {
    // 只有一家时没有可移动的位置，两个永远禁用的按钮会让人以为功能坏了。
    expect(viewSource).toMatch(/queue\.providers\.length > 1/)
  })

  it('提交期间按模型禁用，两条队列不会一起转圈', () => {
    expect(viewSource).toContain('reorderingModel')
    expect(viewSource).toMatch(/reorderingModel === queue\.model/)
  })

  it('用后端返回的新状态刷新，而不是本地改序', () => {
    // 本地改完再等轮询会让「界面上的次序」与「实际生效的次序」不一致几秒，
    // 而排队顺序恰恰是这个页面唯一的论断。
    expect(viewSource).toMatch(/reorderFailoverQueue[\s\S]{0,400}rows\.value = response\.data/)
  })

  it('失败时给出提示而不是静默回退', () => {
    expect(viewSource).toMatch(/reorderFailoverQueue[\s\S]{0,900}message\.error/)
  })
})
