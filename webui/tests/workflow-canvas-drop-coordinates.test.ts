import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * 拖放落点的坐标换算必须减掉画布元素在视口里的偏移。
 *
 * Vue Flow 提供两个换算函数，只差这一步：
 *
 * - `project(position)` = `pointToRendererPoint(position, viewport)`，
 *   **假设画布原点就是浏览器视口的 (0, 0)**；
 * - `screenToFlowCoordinate(position)` 先做 `position.x - domX`，
 *   再走同一套换算。
 *
 * `onDrop` 拿到的是 `event.clientX/clientY`——那是**视口坐标**。把它直接喂给
 * `project()`，只有在画布正好铺满整个视口时才对。今天的样式恰好是
 * `position: fixed; top: 0; left: 0`，于是偏移为 0，缺陷被掩盖着；
 * 任何一次把画布放进带侧栏/顶栏的容器的改动，都会让拖入的节点整体偏移，
 * 偏移量正好等于画布左上角的位置。
 *
 * 这类缺陷不会报错：节点确实生成了，只是位置不对，而用户会以为是自己没拖准。
 *
 * 这条用例做源码级断言而不是行为断言：换算发生在 Vue Flow 内部，
 * 要在单元测试里复现偏移就得把整个 viewport store 立起来；
 * 而真正要守住的规格是「`onDrop` 里不使用不带偏移修正的那个函数」。
 */
describe('画布拖放坐标换算', () => {
  const source = readFileSync(
    resolve(__dirname, '../src/components/workflow/WorkflowCanvas.vue'),
    'utf-8'
  )

  it('onDrop 使用带偏移修正的 screenToFlowCoordinate', () => {
    // 回归点：修之前这里是 `project({ x: event.clientX, y: event.clientY })`。
    expect(source).toContain('screenToFlowCoordinate({ x: event.clientX, y: event.clientY })')
  })

  it('不再从 useVueFlow 取用无偏移修正的 project', () => {
    const destructuring = source.slice(
      source.indexOf('} = useVueFlow()') - 1200,
      source.indexOf('} = useVueFlow()')
    )
    // 留着它会让下一个人顺手用上——两个换算函数的名字看不出这个差别。
    expect(destructuring).not.toMatch(/^\s*project,\s*$/m)
    expect(destructuring).toContain('screenToFlowCoordinate')
  })

  it('clientX/clientY 只喂给带偏移修正的那个函数', () => {
    const clientCoordinateUses = source.match(/\w+\(\{\s*x:\s*event\.clientX/g) || []
    expect(clientCoordinateUses.length).toBeGreaterThan(0)
    for (const use of clientCoordinateUses) {
      expect(use).toContain('screenToFlowCoordinate')
    }
  })
})

/**
 * 零节点时的空状态。
 *
 * 一张只有点阵的空画布无法回答「现在该做什么」：左侧节点面板可能是收起的，
 * 拖放这个交互本身没有任何视觉暗示。新建工作流是每个用户的第一屏，
 * 把它留白等于让第一步全靠猜。
 *
 * 这里同样做源码级断言：把整个 vue-flow 画布挂起来需要真实 DOM 尺寸与
 * ResizeObserver，而要守住的规格是「零节点时渲染提示，且提示不拦交互」。
 */
describe('画布空状态', () => {
  const source = readFileSync(
    resolve(__dirname, '../src/components/workflow/WorkflowCanvas.vue'),
    'utf-8'
  )

  it('零节点时渲染空状态', () => {
    expect(source).toContain('v-if="!nodes.length"')
    expect(source).toContain('data-test="canvas-empty-state"')
  })

  it('空状态不拦截拖放', () => {
    const block = source.slice(
      source.indexOf('.canvas-empty-state {'),
      source.indexOf('.canvas-empty-state strong')
    )
    // 提示自己变成障碍，比没有提示更糟：用户照着提示去拖，却拖不进去。
    expect(block).toContain('pointer-events: none')
  })

  it('空状态给出拖放、连线与撤销三条可执行动作', () => {
    const block = source.slice(
      source.indexOf('data-test="canvas-empty-state"'),
      source.indexOf('<Controls>')
    )
    expect(block).toContain('拖进画布')
    expect(block).toContain('输入端口')
    // 撤销粒度是本轮改过的行为，第一屏说清能省掉一次「为什么撤销得太多」的排查。
    expect(block).toContain('一次连续手势算一步')
  })
})
