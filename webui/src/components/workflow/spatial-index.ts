/**
 * Small uniform-grid index for the workflow canvas.
 *
 * The canvas usually contains only a few dozen nodes, but overlap checks run
 * for every drag/change event.  Keeping the index independent from Vue Flow
 * makes it usable by layout code, tests, and future canvas tools alike.
 */
export interface SpatialBox {
  id: string
  x: number
  y: number
  width: number
  height: number
}

const intersects = (left: SpatialBox, right: SpatialBox) =>
  left.x < right.x + right.width &&
  right.x < left.x + left.width &&
  left.y < right.y + right.height &&
  right.y < left.y + left.height

export class GridSpatialIndex {
  private readonly buckets = new Map<string, Set<string>>()
  private readonly boxes = new Map<string, SpatialBox>()

  constructor(private readonly cellSize = 256) {
    if (!Number.isFinite(cellSize) || cellSize <= 0) {
      throw new RangeError('GridSpatialIndex cellSize must be a positive finite number')
    }
  }

  insert(box: SpatialBox) {
    if (this.boxes.has(box.id)) this.remove(box.id)
    const normalized = { ...box }
    this.boxes.set(normalized.id, normalized)
    for (const key of this.getCellKeys(normalized)) {
      let bucket = this.buckets.get(key)
      if (!bucket) {
        bucket = new Set<string>()
        this.buckets.set(key, bucket)
      }
      bucket.add(normalized.id)
    }
  }

  update(box: SpatialBox) {
    this.insert(box)
  }

  remove(id: string) {
    const existing = this.boxes.get(id)
    if (!existing) return
    this.boxes.delete(id)
    for (const key of this.getCellKeys(existing)) {
      const bucket = this.buckets.get(key)
      if (!bucket) continue
      bucket.delete(id)
      if (bucket.size === 0) this.buckets.delete(key)
    }
  }

  clear() {
    this.buckets.clear()
    this.boxes.clear()
  }

  query(probe: SpatialBox): SpatialBox[] {
    const candidateIds = new Set<string>()
    for (const key of this.getCellKeys(probe)) {
      for (const id of this.buckets.get(key) || []) candidateIds.add(id)
    }

    const matches: SpatialBox[] = []
    for (const id of candidateIds) {
      const box = this.boxes.get(id)
      if (box && intersects(probe, box)) matches.push({ ...box })
    }
    return matches.sort((left, right) => left.id.localeCompare(right.id))
  }

  private getCellKeys(box: SpatialBox): string[] {
    const minX = Math.floor(box.x / this.cellSize)
    const minY = Math.floor(box.y / this.cellSize)
    // Subtract a tiny epsilon so a box ending exactly on a cell boundary does
    // not occupy the next cell.  Touching edges are not intersections.
    const maxX = Math.floor((box.x + Math.max(0, box.width) - Number.EPSILON) / this.cellSize)
    const maxY = Math.floor((box.y + Math.max(0, box.height) - Number.EPSILON) / this.cellSize)
    const keys: string[] = []
    for (let x = minX; x <= maxX; x += 1) {
      for (let y = minY; y <= maxY; y += 1) keys.push(`${x}:${y}`)
    }
    return keys
  }
}
