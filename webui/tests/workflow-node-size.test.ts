import { describe, expect, it } from 'vitest'
import {
  estimateBlockTypeSize,
  findFreeNodePosition,
  NODE_MIN_WIDTH,
  snapToGrid
} from '../src/components/workflow/useLayout'

/**
 * The canvas had two competing fallback sizes for a not-yet-measured node:
 * `useLayout`'s estimate (derived from ports, configs and label width — a plain
 * node is at least 220px wide and usually well over 140px tall) and a hardcoded
 * 240x140 inside `WorkflowCanvas.vue`. Free-slot search, the overlap warning and
 * jump-to-node centering all used the smaller one, so before the first measure
 * they judged geometry the layout pass would disagree with.
 *
 * These tests pin the shared estimate and the invariant that matters: a node
 * placed via the estimate never collides with an existing node.
 */

const blockType = (overrides: Record<string, unknown> = {}) => ({
  type_name: 'internal:chat',
  label: '执行对话',
  inputs: [{ name: 'prompt', label: '用户提示词格式', type: 'str', required: true }],
  outputs: [{ name: 'reply', label: '回复内容', type: 'str' }],
  configs: [
    { name: 'model_name', label: '模型', type: 'str' },
    { name: 'fallback_model_1', label: '备用模型 1', type: 'str' },
    { name: 'fallback_model_2', label: '备用模型 2', type: 'str' }
  ],
  ...overrides
})

describe('estimateBlockTypeSize', () => {
  it('never returns a width below the rendered minimum', () => {
    const size = estimateBlockTypeSize({ type_name: 'internal:noop', label: 'x' })

    expect(size.width).toBeGreaterThanOrEqual(NODE_MIN_WIDTH)
    expect(size.height).toBeGreaterThan(0)
  })

  it('is taller than the previous hardcoded 140px fallback for a config-heavy node', () => {
    // This is the case the old fallback got wrong: a node with several model
    // config rows renders far taller than 140px, so an unmeasured drop could
    // land inside it.
    expect(estimateBlockTypeSize(blockType()).height).toBeGreaterThan(140)
  })

  it('is wider than the previous hardcoded 240px fallback for a wide CJK label', () => {
    const size = estimateBlockTypeSize(
      blockType({ label: '大语言模型执行对话并生成回复内容' })
    )

    expect(size.width).toBeGreaterThan(240)
  })

  it('is deterministic for the same block type', () => {
    expect(estimateBlockTypeSize(blockType())).toEqual(estimateBlockTypeSize(blockType()))
  })

  it('uses the narrower code-node width band', () => {
    const code = estimateBlockTypeSize({
      type_name: 'internal:code',
      label: '自定义脚本',
      inputs: [],
      outputs: [],
      configs: []
    })

    expect(code.width).toBeLessThanOrEqual(300)
  })
})

describe('drop placement with the shared estimate', () => {
  it('moves a node off an existing one instead of stacking it', () => {
    const size = estimateBlockTypeSize(blockType())
    const occupied = [{ id: 'existing', x: 0, y: 0, ...size }]
    const preferred = { x: snapToGrid(10), y: snapToGrid(10) }

    const position = findFreeNodePosition(preferred, size, occupied)

    const collides =
      position.x < size.width && position.y < size.height
    expect(collides).toBe(false)
  })

  it('keeps the preferred position when nothing is in the way', () => {
    const size = estimateBlockTypeSize(blockType())
    const preferred = { x: 400, y: 300 }

    expect(findFreeNodePosition(preferred, size, [])).toEqual(preferred)
  })

  it('returns grid-aligned coordinates', () => {
    const size = estimateBlockTypeSize(blockType())
    const position = findFreeNodePosition({ x: 137, y: 251 }, size, [])

    expect(position.x % 20).toBe(0)
    expect(position.y % 20).toBe(0)
  })
})
