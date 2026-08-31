import { describe, expect, it } from 'vitest'
import {
  LAYOUT_GRID_SIZE,
  findFreeNodePosition,
  findOverlappingNodes
} from '../src/components/workflow/useLayout'

/**
 * 落点搜索耗尽候选时不能交出一个没检查过的坐标。
 *
 * 原实现的循环体先检查当前 `(x, y)`，命中就返回；否则算出**下一步**的坐标再进入
 * 下一轮。于是最后一轮算出的那对坐标从未被 `collides()` 检查过，却在循环结束后
 * 被直接返回——恰好落在一个既有节点上时，新节点就叠在旧节点上生成。
 *
 * 这类缺陷的表现最难查：拖入节点看起来成功了，画布上却只看到一个节点，
 * 另一个被完全盖住。用户以为拖放没生效，于是再拖一次，叠三层。
 */
describe('findFreeNodePosition 候选耗尽时的兜底', () => {
  const size = { width: 200, height: 120 }

  it('候选全部被占时，返回的落点仍然不与既有节点重叠', () => {
    // 沿搜索走的对角线铺满障碍：步长是 max(grid, snapToGrid(size/2))，
    // 因此每一步的落点都能被一个同尺寸的方块盖住。
    const grid = LAYOUT_GRID_SIZE
    const stepX = Math.max(grid, Math.round(size.width / 2 / grid) * grid)
    const stepY = Math.max(grid, Math.round(size.height / 2 / grid) * grid)
    const maxSteps = 6
    const occupied = Array.from({ length: maxSteps + 2 }, (_, step) => ({
      id: `blocker-${step}`,
      x: stepX * step,
      y: stepY * step,
      width: size.width,
      height: size.height
    }))

    const position = findFreeNodePosition({ x: 0, y: 0 }, size, occupied, { maxSteps })

    const overlapping = findOverlappingNodes([
      ...occupied,
      { id: '__new__', x: position.x, y: position.y, ...size }
    ])
    // 回归点：兜底路径下这里会包含 __new__。
    expect(overlapping.has('__new__')).toBe(false)
  })

  it('期望位置本来就空时原样返回（对齐到网格）', () => {
    const position = findFreeNodePosition({ x: 37, y: 51 }, size, [])

    expect(position.x % LAYOUT_GRID_SIZE).toBe(0)
    expect(position.y % LAYOUT_GRID_SIZE).toBe(0)
  })

  it('期望位置被占时挪开，且挪开后确实不重叠', () => {
    const occupied = [{ id: 'a', x: 0, y: 0, width: 200, height: 120 }]

    const position = findFreeNodePosition({ x: 0, y: 0 }, size, occupied)

    const overlapping = findOverlappingNodes([
      ...occupied,
      { id: '__new__', x: position.x, y: position.y, ...size }
    ])
    expect(overlapping.has('__new__')).toBe(false)
  })

  it('返回值始终对齐网格，兜底路径也不例外', () => {
    const grid = LAYOUT_GRID_SIZE
    const occupied = Array.from({ length: 30 }, (_, step) => ({
      id: `blocker-${step}`,
      x: grid * 5 * step,
      y: grid * 3 * step,
      width: 400,
      height: 300
    }))

    const position = findFreeNodePosition({ x: 0, y: 0 }, size, occupied, { maxSteps: 4 })

    expect(position.x % grid).toBe(0)
    expect(position.y % grid).toBe(0)
  })
})
