import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { LAYOUT_GRID_SIZE } from '../src/components/workflow/useLayout'

/**
 * 拖动落点、算法落点和背景点阵必须用同一个网格。
 *
 * `vue-flow` 的 `snapGrid` 默认值是 `[15, 15]`，只写 `:snap-to-grid="true"`
 * 而不传 `:snap-grid` 就会用这个默认值；而 `useLayout` 的落点计算、拖放新增、
 * 复制节点和自动排布全部按 `LAYOUT_GRID_SIZE` 对齐，`<Background>` 的点阵间距
 * 也用同一个值。三者不一致的表现正是需求 20.1 里的「拖动后位置跳变」和
 * 「视觉错位」：手工拖过的节点既不落在点阵上，也不落在算法认可的格点上，
 * 下一次自动排布又会把它挪走。
 *
 * 这条测试直接钉住模板：`snap-grid` 必须显式绑定，且与 `LAYOUT_GRID_SIZE`
 * 和背景点阵 `gap` 同源。
 */

const here = dirname(fileURLToPath(import.meta.url))
const canvasSource = readFileSync(
  resolve(here, '../src/components/workflow/WorkflowCanvas.vue'),
  'utf-8'
)

describe('canvas snap grid single source of truth', () => {
  it('binds snap-grid explicitly instead of inheriting vue-flow 15px default', () => {
    expect(canvasSource).toMatch(/:snap-to-grid="true"/)
    expect(
      canvasSource,
      'snap-to-grid 打开但没有绑定 snap-grid，会落到 vue-flow 的 [15,15] 默认值'
    ).toMatch(/:snap-grid="/)
  })

  it('feeds snap-grid from LAYOUT_GRID_SIZE rather than a literal', () => {
    const binding = canvasSource.match(/:snap-grid="([^"]+)"/)
    expect(binding, 'snap-grid 绑定未找到').not.toBeNull()
    expect(binding![1]).toContain('LAYOUT_GRID_SIZE')
  })

  it('keeps the background dot gap on the same grid', () => {
    const gap = canvasSource.match(/<Background[^>]*:gap="([^"]+)"/)
    expect(gap, 'Background gap 绑定未找到').not.toBeNull()
    expect(gap![1]).toContain('LAYOUT_GRID_SIZE')
  })

  it('imports LAYOUT_GRID_SIZE from the layout module', () => {
    expect(canvasSource).toMatch(/LAYOUT_GRID_SIZE/)
    expect(LAYOUT_GRID_SIZE).toBeGreaterThan(0)
  })
})
