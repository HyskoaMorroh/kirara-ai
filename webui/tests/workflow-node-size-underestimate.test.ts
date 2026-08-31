import { describe, expect, it } from 'vitest'
import { estimateBlockSize } from '../src/components/workflow/useLayout'

/**
 * 尺寸估算偏小会让「已排好」的布局渲染出来仍然重叠（需求 2 的现场报障）。
 *
 * 载入既有工作流时，缺坐标的节点走 `layoutMissingNodes`，而那一刻 DOM 还没渲染，
 * 因此 100% 用 `estimateBlockSize` 的估算值。`useLayout.ts` 自己写下的判据是
 * 「估算值宁可略大也不能偏小：dagre 与去重叠扫描都按这个尺寸留空隙，一旦小于
 * 真实渲染高度，节点就会重新压在一起」。
 *
 * 三处偏小，都在高度上：
 *
 * 1. **`.custom-node-body` 的 padding 只在有配置项时才算。** 那个容器是无条件
 *    渲染的 `padding: 12px`（`CustomNode.vue:332-337`），因此没有配置项的节点
 *    （`GetIMMessage` / `SendIMMessage` / `IMMessageToText` 等）真实高度比估算多
 *    24px。这类节点恰恰是每个工作流的头尾。
 * 2. **代码节点主体按定值 132px 计。** 真实高度随预览行数变化：`codePreview`
 *    取前 5 行加一行 `# ...`（`CodeNode.vue:97-102`），而 `.code-preview-content`
 *    是 `white-space: pre-wrap`（`CodeNode.vue:319`），长行在 300px 宽度内还会折行。
 * 3. **零端口代码节点的空态完全没算。** 那是三行中文提示
 *    （`CodeNode.vue:128-130`，约 70px），而 `portRows === 0` 时端口区贡献 0。
 *    刚拖进画布的自定义脚本节点一定是这个形态。
 *
 * 分隔是 60px，能吸收单侧误差；两侧叠加（尤其含代码节点）就会突破它，于是布局
 * 算出「不重叠」而渲染出来重叠——用户只能看到角标提示「建议点击自动排布」，
 * 而他刚刚点过。
 */

const HEADER = 40
const PORT_ROW = 28
const PORTS_PADDING = 12
const BODY_PADDING = 24
const BORDER_AND_MARGIN = 8

describe('estimateBlockSize 的高度不能偏小', () => {
  it('没有配置项的节点也要算上节点主体的 padding', () => {
    // `.custom-node-body` 无条件渲染，padding: 12px 上下共 24px。
    const size = estimateBlockSize({
      id: 'n1',
      type: 'internal:im_message',
      label: '发送消息',
      inputs: [{ name: 'msg', label: '消息', type: 'str' }],
      outputs: [],
      configs: []
    })

    const withoutBody = HEADER + 1 * PORT_ROW + PORTS_PADDING + BORDER_AND_MARGIN
    expect(size.height).toBeGreaterThanOrEqual(withoutBody + BODY_PADDING)
  })

  it('配置项节点的高度不因这条修正而变小', () => {
    // 既有部署的布局不能被改坏：有配置项时本来就算了 BODY_PADDING。
    const size = estimateBlockSize({
      id: 'n2',
      type: 'internal:chat',
      label: '执行对话',
      inputs: [{ name: 'prompt', label: '提示词', type: 'str' }],
      outputs: [{ name: 'reply', label: '回复', type: 'str' }],
      configs: [
        { name: 'model_name', label: '模型', type: 'str' },
        { name: 'fallback', label: '备用模型', type: 'str' }
      ]
    })

    expect(size.height).toBeGreaterThanOrEqual(
      HEADER + PORT_ROW + PORTS_PADDING + 2 * 30 + 4 + BODY_PADDING + BORDER_AND_MARGIN
    )
  })
})

describe('代码节点', () => {
  it('代码越长，估算高度越高', () => {
    const short = estimateBlockSize({
      id: 'c1',
      type: 'code',
      label: '自定义脚本',
      inputs: [{ name: 'a', label: 'a', type: 'Any' }],
      outputs: [{ name: 'b', label: 'b', type: 'Any' }],
      configs: [],
      code: 'x = 1'
    })
    const long = estimateBlockSize({
      id: 'c2',
      type: 'code',
      label: '自定义脚本',
      inputs: [{ name: 'a', label: 'a', type: 'Any' }],
      outputs: [{ name: 'b', label: 'b', type: 'Any' }],
      configs: [],
      // 预览取前 5 行再加一行 `# ...`，共 6 行。
      code: Array.from({ length: 40 }, (_, index) => `value_${index} = compute(${index})`).join(
        '\n'
      )
    })

    expect(long.height).toBeGreaterThan(short.height)
  })

  it('预览行数封顶在 6 行，不随代码无限增长', () => {
    // `codePreview` 只取前 5 行 + `# ...`；估算跟着无限增长会把画布拉散。
    const six = estimateBlockSize({
      id: 'c3',
      type: 'code',
      label: '脚本',
      inputs: [],
      outputs: [],
      configs: [],
      code: Array.from({ length: 6 }, (_, index) => `line_${index}`).join('\n')
    })
    const sixHundred = estimateBlockSize({
      id: 'c4',
      type: 'code',
      label: '脚本',
      inputs: [],
      outputs: [],
      configs: [],
      code: Array.from({ length: 600 }, (_, index) => `line_${index}`).join('\n')
    })

    expect(sixHundred.height).toBe(six.height)
  })

  it('零端口的脚本节点要算上那段三行空态提示', () => {
    // `portRows === 0` 时端口区贡献 0，而界面上是一段三行中文提示。
    const noPorts = estimateBlockSize({
      id: 'c5',
      type: 'code',
      label: '自定义脚本',
      inputs: [],
      outputs: [],
      configs: [],
      code: ''
    })
    const onePort = estimateBlockSize({
      id: 'c6',
      type: 'code',
      label: '自定义脚本',
      inputs: [{ name: 'a', label: 'a', type: 'Any' }],
      outputs: [],
      configs: [],
      code: ''
    })

    expect(noPorts.height).toBeGreaterThan(onePort.height)
  })

  it('空代码的预览也占一行，而不是零行', () => {
    // 空代码时预览区显示 `# 请在配置面板编写代码`。
    const empty = estimateBlockSize({
      id: 'c7',
      type: 'code',
      label: '脚本',
      inputs: [{ name: 'a', label: 'a', type: 'Any' }],
      outputs: [],
      configs: [],
      code: ''
    })

    expect(empty.height).toBeGreaterThan(HEADER + PORT_ROW + PORTS_PADDING + BORDER_AND_MARGIN)
  })
})

describe('宽度不受影响', () => {
  it('这条修正只动高度，不动宽度', () => {
    const size = estimateBlockSize({
      id: 'n3',
      type: 'internal:im_message',
      label: '发送消息',
      inputs: [{ name: 'msg', label: '消息', type: 'str' }],
      outputs: [],
      configs: []
    })

    // 宽度上限与 CSS 的 max-width 同源，由 workflow-node-width-source.test.ts 钉住。
    expect(size.width).toBeLessThanOrEqual(360)
    expect(size.width).toBeGreaterThanOrEqual(220)
  })
})
