import dagre from '@dagrejs/dagre'
import { describe, expect, it, vi } from 'vitest'
import {
  findOverlappingNodes,
  layoutMissingNodes
} from '../src/components/workflow/useLayout'

describe('findOverlappingNodes', () => {
  it('reports all and only nodes whose rendered boxes intersect', () => {
    const overlapping = findOverlappingNodes([
      { id: 'a', x: 0, y: 0, width: 100, height: 80 },
      { id: 'b', x: 60, y: 20, width: 100, height: 80 },
      { id: 'c', x: 240, y: 0, width: 80, height: 80 },
      { id: 'd', x: 60, y: 140, width: 100, height: 80 }
    ])

    expect([...overlapping].sort()).toEqual(['a', 'b'])
  })
})

describe('layoutMissingNodes', () => {
  const sizedBlock = (id: string, position?: { x: number; y: number } | null) => ({
    id,
    position,
    size: { width: 120, height: 80 }
  })

  it('does not invoke full dagre layout when every saved coordinate is valid', () => {
    const dagreLayout = vi.spyOn(dagre, 'layout')

    const result = layoutMissingNodes([
      sizedBlock('a', { x: 0, y: 0 }),
      sizedBlock('b', { x: 300, y: 120 })
    ])

    expect(dagreLayout).not.toHaveBeenCalled()
    expect(result.a).toMatchObject({ x: 0, y: 0 })
    expect(result.b).toMatchObject({ x: 300, y: 120 })
    dagreLayout.mockRestore()
  })

  it('preserves finite user positions and repairs only missing or invalid positions', () => {
    const result = layoutMissingNodes(
      [
        sizedBlock('source', { x: 40, y: 60 }),
        sizedBlock('missing', null),
        sizedBlock('nan', { x: Number.NaN, y: 10 }),
        sizedBlock('infinite', { x: 10, y: Number.POSITIVE_INFINITY })
      ],
      [{ source: 'source', target: 'missing' }]
    )

    expect(result.source).toMatchObject({ x: 40, y: 60 })
    expect(result.missing.x).toBeGreaterThan(result.source.x + result.source.width)
    expect(Number.isFinite(result.nan.x)).toBe(true)
    expect(Number.isFinite(result.nan.y)).toBe(true)
    expect(Number.isFinite(result.infinite.x)).toBe(true)
    expect(Number.isFinite(result.infinite.y)).toBe(true)
    expect(findOverlappingNodes(Object.entries(result).map(([id, box]) => ({ id, ...box })))).toEqual(
      new Set()
    )
  })

  it('is deterministic for disconnected nodes and keeps every generated box separate', () => {
    const blocks = Array.from({ length: 40 }, (_, index) => sizedBlock(`node-${index}`, null))

    const first = layoutMissingNodes(blocks)
    const second = layoutMissingNodes([...blocks].reverse())

    expect(second).toEqual(first)
    expect(findOverlappingNodes(Object.entries(first).map(([id, box]) => ({ id, ...box })))).toEqual(
      new Set()
    )
  })
})
