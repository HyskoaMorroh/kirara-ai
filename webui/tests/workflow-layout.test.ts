import { describe, expect, it } from 'vitest'
import { findOverlappingNodes } from '../src/components/workflow/useLayout'

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
