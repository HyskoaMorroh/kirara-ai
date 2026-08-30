// 需求 20.2：超大图必须有受控降级——**打开工作流那条路径上也要有**。
//
// `LARGE_GRAPH_NODE_THRESHOLD` 只挡住了工具栏的「自动排布」按钮（先弹确认再执行）。
// 但打开一个旧工作流时，`restoreGraph` 会为缺坐标的节点调用 `layoutMissingNodes`，
// 那条路径上没有任何阈值：几百个缺坐标的节点会在主线程同步跑完碰撞搜索
// （每个节点最多 100 圈、每圈 O(radius) 个候选点），用户看到的是白屏卡顿。
//
// 这条路径不能用确认对话框解决：用户只是打开了一个文件，没有可确认的动作；
// 而拒绝布局会让所有节点叠在 (0,0)，比慢更糟。
// 因此降级方式是**限住工作量**：超过阈值时改用确定性的通道排布，
// 不做碰撞搜索——O(n) 而不是 O(n × 搜索半径)，每个节点仍然拿到唯一有限坐标。

import { describe, expect, it } from 'vitest'

import {
  layoutMissingNodes,
  LARGE_GRAPH_NODE_THRESHOLD
} from '../src/components/workflow/useLayout'

type Block = Parameters<typeof layoutMissingNodes>[0][number]

const block = (id: string, position?: { x: number; y: number }): Block => ({
  id,
  label: id,
  inputs: [{ name: 'in', type: 'Any', required: false }],
  outputs: [{ name: 'out', type: 'Any' }],
  configs: [],
  position
})

/** 一张全部缺坐标的大图：这正是「旧工作流第一次用新版本打开」的形态。 */
const largeGraph = (count: number) =>
  Array.from({ length: count }, (_, index) => block(`node-${String(index).padStart(4, '0')}`))

describe('layoutMissingNodes on a large graph', () => {
  it('gives every node a finite, unique position without a collision search', () => {
    const blocks = largeGraph(LARGE_GRAPH_NODE_THRESHOLD + 50)

    const boxes = layoutMissingNodes(blocks, [])

    // 降级不等于放弃：每个节点都必须有坐标，否则它们会叠在 (0,0)。
    expect(Object.keys(boxes)).toHaveLength(blocks.length)
    const positions = new Set<string>()
    for (const id of Object.keys(boxes)) {
      expect(Number.isFinite(boxes[id].x)).toBe(true)
      expect(Number.isFinite(boxes[id].y)).toBe(true)
      positions.add(`${boxes[id].x},${boxes[id].y}`)
    }
    expect(positions.size).toBe(blocks.length)
  })

  it('completes a large graph fast enough to not freeze the canvas', () => {
    // 这个断言钉住的是「不做逐节点碰撞搜索」这件事本身。
    // 阈值内的图仍然走搜索路径，因此这里只对超大图设界。
    const blocks = largeGraph(LARGE_GRAPH_NODE_THRESHOLD * 3)

    const started = Date.now()
    layoutMissingNodes(blocks, [])
    const elapsed = Date.now() - started

    // 600 个节点走碰撞搜索时是数量级更高的耗时；这里留足余量只判「没有退化」。
    expect(elapsed).toBeLessThan(1000)
  })

  it('still honours positions the user already saved', () => {
    // 降级只影响**缺坐标**的节点。已有坐标是用户的编辑结果，任何情况下都不能动。
    const blocks = [
      block('kept', { x: 700, y: 900 }),
      ...largeGraph(LARGE_GRAPH_NODE_THRESHOLD + 10)
    ]

    const boxes = layoutMissingNodes(blocks, [])

    expect(boxes['kept'].x).toBe(700)
    expect(boxes['kept'].y).toBe(900)
  })

  it('stays deterministic in the degraded path', () => {
    const blocks = largeGraph(LARGE_GRAPH_NODE_THRESHOLD + 10)

    const first = layoutMissingNodes(blocks, [])
    const second = layoutMissingNodes([...blocks].reverse(), [])

    // 「同样的图排出来不一样」在降级路径上同样不可接受。
    expect(second).toEqual(first)
  })

  it('leaves graphs under the threshold on the collision-search path', () => {
    // 小图仍然应该贴着邻居放，而不是退化成通道——降级不能变成默认行为。
    const blocks = [block('anchor', { x: 0, y: 0 }), block('next')]

    const boxes = layoutMissingNodes(blocks, [{ source: 'anchor', target: 'next' }])

    // 贴着 anchor 的右侧放，说明走的是邻居锚定路径。
    expect(boxes['next'].x).toBeGreaterThan(0)
  })
})
