import { describe, expect, it } from 'vitest'

import { GridSpatialIndex, type SpatialBox } from '../src/components/workflow/spatial-index'

const intersects = (a: SpatialBox, b: SpatialBox) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

describe('GridSpatialIndex', () => {
  it('matches a brute-force overlap oracle for 1,000 deterministic boxes', () => {
    let seed = 0x5f3759df
    const random = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
      return seed / 0x100000000
    }
    const boxes = Array.from({ length: 1000 }, (_, index) => ({
      id: `box-${index}`,
      x: Math.floor(random() * 8000) - 1000,
      y: Math.floor(random() * 6000) - 1000,
      width: 40 + Math.floor(random() * 180),
      height: 30 + Math.floor(random() * 140)
    }))

    const expected = new Set<string>()
    for (let left = 0; left < boxes.length; left += 1) {
      for (let right = left + 1; right < boxes.length; right += 1) {
        if (intersects(boxes[left], boxes[right])) {
          expected.add(`${boxes[left].id}|${boxes[right].id}`)
        }
      }
    }

    const index = new GridSpatialIndex(256)
    boxes.forEach((box) => index.insert(box))
    const actual = new Set<string>()
    boxes.forEach((box, boxIndex) => {
      index.query(box).forEach((candidate) => {
        const candidateIndex = Number(candidate.id.slice('box-'.length))
        if (candidateIndex > boxIndex) actual.add(`${box.id}|${candidate.id}`)
      })
    })

    expect(actual).toEqual(expected)
  })

  it('updates and removes boxes without leaving stale bucket entries', () => {
    const index = new GridSpatialIndex(100)
    index.insert({ id: 'moving', x: 0, y: 0, width: 80, height: 80 })
    index.insert({ id: 'fixed', x: 40, y: 40, width: 80, height: 80 })
    expect(index.query({ id: 'probe', x: 0, y: 0, width: 100, height: 100 }).map((box) => box.id).sort()).toEqual([
      'fixed',
      'moving'
    ])

    index.update({ id: 'moving', x: 500, y: 500, width: 80, height: 80 })
    index.remove('fixed')

    expect(index.query({ id: 'probe', x: 0, y: 0, width: 100, height: 100 })).toEqual([])
    expect(index.query({ id: 'probe', x: 490, y: 490, width: 100, height: 100 }).map((box) => box.id)).toEqual([
      'moving'
    ])
  })
})
