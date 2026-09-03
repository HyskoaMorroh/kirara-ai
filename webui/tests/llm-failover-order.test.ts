// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  canMoveDown,
  canMoveUp,
  swappedOrder
} from '../src/views/llm/failoverQueueOrder'

/**
 * 故障转移队列的次序计算，按**行为**验证。
 *
 * 替换的是 `llm-failover-queue-reorder.test.ts` 里的
 * `toMatch(/queue-move-up[\s\S]{0,300}index === 0/)`——那检查模板里有没有那个
 * 禁用条件，不检查交换出来的次序对不对。
 *
 * 这个次序会提交给后端并写进 `config.yaml`，决定真实请求先打哪一家。
 * 交换错一位，故障转移就按一个用户没选的顺序跑，而界面显示的是他选的那个。
 */

describe('相邻交换', () => {
  const queue = ['a', 'b', 'c', 'd']

  it('上移把该项与它前一位互换', () => {
    expect(swappedOrder(queue, 2, -1)).toEqual(['a', 'c', 'b', 'd'])
  })

  it('下移把该项与它后一位互换', () => {
    expect(swappedOrder(queue, 1, 1)).toEqual(['a', 'c', 'b', 'd'])
  })

  it('只动两位，其余保持原序', () => {
    // 一次提交整条队列，所以未参与交换的项必须逐位不变——
    // 差一位就是一个用户没做过的改动被写进了 config.yaml。
    const result = swappedOrder(queue, 0, 1)!
    expect(result).toEqual(['b', 'a', 'c', 'd'])
    expect(result.slice(2)).toEqual(queue.slice(2))
  })

  it('不修改原数组', () => {
    // `rows` 是响应式数据；原地交换会让界面在请求还没回来时就跳动，
    // 而这个页面唯一的论断就是「当前生效的次序」。
    const original = [...queue]
    swappedOrder(queue, 1, 1)
    expect(queue).toEqual(original)
  })

  it('长度不变', () => {
    expect(swappedOrder(queue, 1, 1)).toHaveLength(queue.length)
  })

  it('上移第一项返回 null——调用方据此不发请求', () => {
    // 返回原数组会提交一次内容相同的写操作，在 config.yaml 上留下
    // 一条无意义的备份。
    expect(swappedOrder(queue, 0, -1)).toBeNull()
  })

  it('下移最后一项返回 null', () => {
    expect(swappedOrder(queue, queue.length - 1, 1)).toBeNull()
  })

  it('索引本身越界时返回 null', () => {
    expect(swappedOrder(queue, -1, 1)).toBeNull()
    expect(swappedOrder(queue, queue.length, -1)).toBeNull()
  })

  it('单项队列两个方向都返回 null', () => {
    expect(swappedOrder(['only'], 0, -1)).toBeNull()
    expect(swappedOrder(['only'], 0, 1)).toBeNull()
  })

  it('空队列返回 null 而不抛错', () => {
    expect(swappedOrder([], 0, 1)).toBeNull()
  })

  it('两项队列可以互换', () => {
    expect(swappedOrder(['a', 'b'], 0, 1)).toEqual(['b', 'a'])
    expect(swappedOrder(['a', 'b'], 1, -1)).toEqual(['b', 'a'])
  })

  it('连续两次相反的移动回到原序', () => {
    const once = swappedOrder(queue, 1, 1)!
    expect(swappedOrder(once, 2, -1)).toEqual(queue)
  })
})

describe('按钮禁用条件', () => {
  it('第一项不能上移，最后一项不能下移', () => {
    expect(canMoveUp(0, 3)).toBe(false)
    expect(canMoveDown(2, 3)).toBe(false)
  })

  it('中间项两个方向都可以', () => {
    expect(canMoveUp(1, 3)).toBe(true)
    expect(canMoveDown(1, 3)).toBe(true)
  })

  it('只有一家时两个方向都禁用', () => {
    // 一条只有一家的队列没有「顺序」可言，给出可点的按钮是误导。
    expect(canMoveUp(0, 1)).toBe(false)
    expect(canMoveDown(0, 1)).toBe(false)
  })

  it('禁用条件与 swappedOrder 的边界一致', () => {
    // 两处各自判断会漂移：按钮可点而提交被拒，或者按钮禁用而其实可以移。
    const queue = ['a', 'b', 'c']
    for (let index = 0; index < queue.length; index += 1) {
      expect(canMoveUp(index, queue.length)).toBe(swappedOrder(queue, index, -1) !== null)
      expect(canMoveDown(index, queue.length)).toBe(swappedOrder(queue, index, 1) !== null)
    }
  })
})
