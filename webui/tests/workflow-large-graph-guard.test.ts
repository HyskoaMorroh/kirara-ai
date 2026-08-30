import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import {
  LARGE_GRAPH_NODE_THRESHOLD,
  computeWorkflowLayout,
  findOverlappingNodes,
  type LayoutBox
} from '../src/components/workflow/useLayout'

/**
 * 超大图必须有受控降级（需求 20.2 明确要求）。
 *
 * `handleTidyLayout` 此前唯一的守卫是「节点数为 0」，其后无条件跑
 * dagre + 去重叠扫描，全部在主线程同步完成，且整段包在一次历史批次里。
 * 去重叠扫描的收敛守卫是 `placed.length * 2 + 2`，随节点数增长；
 * 节点很多时这条路径会把标签页卡住几秒到十几秒，期间用户以为界面死了，
 * 反复点「自动排布」只会排更多次。
 *
 * 需求要的不是「更快的算法」，而是**受控**：达到阈值时先告知代价并让用户决定，
 * 而不是默默把浏览器锁住。这些用例钉住阈值本身与它在画布上的接线。
 */

const here = dirname(fileURLToPath(import.meta.url))
const canvasSource = readFileSync(
  resolve(here, '../src/components/workflow/WorkflowCanvas.vue'),
  'utf-8'
)

const block = (id: string) => ({
  id,
  position: null,
  size: { width: 120, height: 80 }
})

const boxes = (result: Record<string, LayoutBox>) =>
  Object.entries(result).map(([id, box]) => ({ id, ...box }))

describe('large-graph degradation threshold', () => {
  it('exposes a positive node-count threshold', () => {
    expect(LARGE_GRAPH_NODE_THRESHOLD).toBeGreaterThan(0)
  })

  it('is high enough not to nag on ordinary workflows', () => {
    // 内置预设与常见用户工作流都在几十个节点以内；阈值不能低到日常触发。
    expect(LARGE_GRAPH_NODE_THRESHOLD).toBeGreaterThanOrEqual(120)
  })

  it('is low enough to fire before the layout becomes unusable', () => {
    expect(LARGE_GRAPH_NODE_THRESHOLD).toBeLessThanOrEqual(1000)
  })
})

describe('WorkflowCanvas wires the threshold into 自动排布', () => {
  it('imports the shared threshold instead of hardcoding a number', () => {
    expect(canvasSource).toContain('LARGE_GRAPH_NODE_THRESHOLD')
  })

  it('asks for confirmation rather than silently blocking the tab', () => {
    const handler = canvasSource.match(/const handleTidyLayout[\s\S]*?\n}/)
    expect(handler, 'handleTidyLayout 未找到').not.toBeNull()
    const body = handler![0]
    expect(body).toContain('LARGE_GRAPH_NODE_THRESHOLD')
    // 必须是「先问再做」，不是直接拒绝——拒绝等于把功能拿掉。
    expect(body).toMatch(/dialog|confirm|确认/)
  })

  it('still lays out immediately below the threshold', () => {
    const handler = canvasSource.match(/const handleTidyLayout[\s\S]*?\n}/)!
    const body = handler[0]
    // 阈值以下不得多一步点击：确认分支必须由一次与阈值的比较守卫，
    // 且该分支之外仍有一条直接执行的出口。
    expect(body).toMatch(/>=\s*LARGE_GRAPH_NODE_THRESHOLD/)
    // 确认分支 return 之后还要有一次无条件调用，否则小图也被挡住。
    const afterGuard = body.slice(body.lastIndexOf('return'))
    expect(afterGuard).toMatch(/runTidyLayout\(\)/)
  })
})

describe('layout still terminates and stays correct at the threshold', () => {
  it('produces an overlap-free layout for a graph at the threshold size', () => {
    // 阈值处必须仍然算得出正确结果：降级是「先问」，不是「算不对」。
    const blocks = Array.from({ length: LARGE_GRAPH_NODE_THRESHOLD }, (_, index) =>
      block(`node-${index}`)
    )

    const result = computeWorkflowLayout(blocks)

    expect(Object.keys(result)).toHaveLength(LARGE_GRAPH_NODE_THRESHOLD)
    expect(findOverlappingNodes(boxes(result))).toEqual(new Set())
  })

  it('remains deterministic at that size', () => {
    const blocks = Array.from({ length: 200 }, (_, index) => block(`node-${index}`))

    const forward = computeWorkflowLayout(blocks)
    const reversed = computeWorkflowLayout([...blocks].reverse())

    expect(reversed).toEqual(forward)
  })
})
