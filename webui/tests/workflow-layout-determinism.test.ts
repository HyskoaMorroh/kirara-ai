import { describe, expect, it } from 'vitest'
import {
  computeWorkflowLayout,
  findOverlappingNodes,
  resolveNodeOverlaps,
  type LayoutBox
} from '../src/components/workflow/useLayout'

/**
 * 「自动排布」按钮真正调用的入口是 `computeWorkflowLayout`，此前它在
 * `tests/` 下没有任何引用——被测的是旁边的 `layoutMissingNodes`。
 *
 * 需求 20.2 要求自动布局**确定性**，并在布局完成后依据真实尺寸做重叠检测。
 * 确定性的具体含义是：同一张图，节点/连线的**数组顺序**不同也必须得到同一份
 * 坐标。dagre 的 greedy-FAS 与层内重心排序都依赖插入顺序，因此不排序就把
 * `blocks`/`edges` 原样喂进去，会让「撤销后恢复」「删掉一个节点再排布」
 * 这类顺序变化产生不同的版式——用户看到的是「同样的图排出来不一样」。
 */

const block = (id: string, width = 120, height = 80) => ({
  id,
  position: null,
  size: { width, height }
})

const boxes = (result: Record<string, LayoutBox>) =>
  Object.entries(result).map(([id, box]) => ({ id, ...box }))

describe('computeWorkflowLayout determinism', () => {
  it('is invariant to block array order for a connected graph', () => {
    const blocks = [block('a'), block('b'), block('c'), block('d'), block('e')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'a', target: 'c' },
      { source: 'b', target: 'd' },
      { source: 'c', target: 'd' },
      { source: 'd', target: 'e' }
    ]

    const forward = computeWorkflowLayout(blocks, edges)
    const reversed = computeWorkflowLayout([...blocks].reverse(), edges)

    expect(reversed).toEqual(forward)
  })

  it('is invariant to edge array order', () => {
    const blocks = [block('a'), block('b'), block('c'), block('d')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'd' }
    ]

    const forward = computeWorkflowLayout(blocks, edges)
    const shuffled = computeWorkflowLayout(blocks, [...edges].reverse())

    expect(shuffled).toEqual(forward)
  })

  it('terminates and stays deterministic on a cyclic graph', () => {
    // 循环图交给 dagre 的 greedy-FAS 打破，但仓库里此前没有任何用例证明
    // 它既能终止又与输入顺序无关。
    const blocks = [block('a'), block('b'), block('c')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' }
    ]

    const forward = computeWorkflowLayout(blocks, edges)
    const reversed = computeWorkflowLayout([...blocks].reverse(), [...edges].reverse())

    expect(Object.keys(forward).sort()).toEqual(['a', 'b', 'c'])
    expect(reversed).toEqual(forward)
  })

  it('leaves no overlapping boxes after layout with mixed node sizes', () => {
    const blocks = [
      block('tall', 120, 400),
      block('wide', 400, 80),
      block('small', 100, 60),
      block('plain'),
      block('another')
    ]
    const edges = [
      { source: 'tall', target: 'wide' },
      { source: 'wide', target: 'small' },
      { source: 'tall', target: 'plain' }
    ]

    const result = computeWorkflowLayout(blocks, edges)

    expect(findOverlappingNodes(boxes(result))).toEqual(new Set())
  })

  it('leaves no overlap for fully disconnected nodes', () => {
    const blocks = Array.from({ length: 24 }, (_, index) => block(`node-${index}`))

    const result = computeWorkflowLayout(blocks)

    expect(findOverlappingNodes(boxes(result))).toEqual(new Set())
  })
})

describe('resolveNodeOverlaps guarantees separation', () => {
  it('separates a dense pile that needs many pushes to converge', () => {
    // 收敛守卫是 `placed.length * 2 + 2`；一堆完全重合的盒子是最坏情况，
    // 守卫耗尽时函数会安静地留下残余重叠，因此必须直接断言结果无重叠。
    const pile = new Map<string, LayoutBox>(
      Array.from({ length: 30 }, (_, index) => [
        `n-${index}`,
        { x: 0, y: 0, width: 120, height: 80 }
      ])
    )

    const resolved = resolveNodeOverlaps(pile, true)

    expect(
      findOverlappingNodes([...resolved.entries()].map(([id, box]) => ({ id, ...box })))
    ).toEqual(new Set())
  })

  it('is order-independent for the same set of boxes', () => {
    const make = (): Array<[string, LayoutBox]> => [
      ['a', { x: 0, y: 0, width: 120, height: 80 }],
      ['b', { x: 10, y: 10, width: 120, height: 80 }],
      ['c', { x: 20, y: 20, width: 120, height: 80 }]
    ]

    const forward = resolveNodeOverlaps(new Map(make()), true)
    const reversed = resolveNodeOverlaps(new Map(make().reverse()), true)

    expect([...reversed.entries()].sort()).toEqual([...forward.entries()].sort())
  })
})
