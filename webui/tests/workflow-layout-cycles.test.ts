// 需求 20.2：循环图必须有受控降级，且自动布局要确定性。
//
// `computeWorkflowLayout` 的注释写着「dagre 的 greedy-FAS（打破环）… 依赖插入顺序」，
// 但 `setGraph` 里从未设过 `acyclicer`。dagre 默认不打破环——`ranker` 只决定
// 分层算法。注释描述的机制没有被启用：这属于「注释与实现不符」，
// 而它保护的恰恰是最容易出事的一类输入（工作流允许回边，比如重试与循环审阅）。
//
// 这些用例不假设某个具体版式，只钉住三件事：环不会让布局崩、每个节点都有坐标、
// 同一张图两次布局结果完全一致。

import { describe, expect, it } from 'vitest'

import { computeWorkflowLayout } from '../src/components/workflow/useLayout'

type Block = Parameters<typeof computeWorkflowLayout>[0][number]

const block = (id: string): Block => ({
  id,
  label: id,
  inputs: [{ name: 'in', type: 'Any', required: false }],
  outputs: [{ name: 'out', type: 'Any' }],
  configs: []
})

describe('computeWorkflowLayout with cyclic graphs', () => {
  it('lays out a two-node cycle without dropping either node', () => {
    const blocks = [block('a'), block('b')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'a' }
    ]

    const boxes = computeWorkflowLayout(blocks, edges)

    // 环不能让任何节点失去坐标：丢坐标的节点在画布上会叠到 (0,0)。
    expect(Object.keys(boxes).sort()).toEqual(['a', 'b'])
    for (const id of ['a', 'b']) {
      expect(Number.isFinite(boxes[id].x)).toBe(true)
      expect(Number.isFinite(boxes[id].y)).toBe(true)
    }
  })

  it('lays out a longer cycle and a self-loop', () => {
    const blocks = [block('a'), block('b'), block('c'), block('d')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' },
      // 自环是最短的环，最容易让分层算法卡住。
      { source: 'd', target: 'd' }
    ]

    const boxes = computeWorkflowLayout(blocks, edges)

    expect(Object.keys(boxes).sort()).toEqual(['a', 'b', 'c', 'd'])
  })

  it('produces identical results for the same cyclic graph', () => {
    const blocks = [block('a'), block('b'), block('c')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' }
    ]

    const first = computeWorkflowLayout(blocks, edges)
    const second = computeWorkflowLayout(blocks, edges)

    expect(second).toEqual(first)
  })

  it('is insensitive to the input order of blocks and edges', () => {
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' }
    ]
    const forward = computeWorkflowLayout([block('a'), block('b'), block('c')], edges)
    const reversed = computeWorkflowLayout(
      [block('c'), block('b'), block('a')],
      [...edges].reverse()
    )

    // 「同样的图排出来不一样」是用户能直接看到的不确定性。
    expect(reversed).toEqual(forward)
  })

  it('keeps cyclic nodes from stacking on one another', () => {
    const blocks = [block('a'), block('b'), block('c')]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
      { source: 'c', target: 'a' }
    ]

    const boxes = computeWorkflowLayout(blocks, edges)
    const positions = Object.values(boxes).map((box) => `${box.x},${box.y}`)

    // 环被打破后 dagre 才能分层；不打破时多个节点会落在同一个 rank 的同一点上。
    expect(new Set(positions).size).toBe(positions.length)
  })
})
