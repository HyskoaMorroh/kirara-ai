import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 脚本节点必须在**它自己身上**说明「为什么连不上」。
 *
 * `getWorkflowGraphIssues` 已经产出 `code_node_without_ports` 警告，画布也把
 * 问题汇总 provide 成 `workflowNodeIssues`，但 `CodeNode.vue` 从未 inject 过它：
 * 于是一个零端口的脚本节点在工具栏问题列表里有一条警告，在画布上却只是一个
 * 光秃秃、连不上任何线的方框——用户看到的正是需求 2 里问的「自定义脚本不同框
 * 是断开的吗？是故意这样设计的吗？」。
 *
 * 需求 20.4 要求把「有意的输入/输出边界」在**节点定义、端口状态和 UI 反馈**
 * 三处都表达清楚。角标（与 CustomNode 同一套）解决「哪个节点有问题」，
 * 空态提示解决「现在该做什么」，缺一不可。
 */

const here = dirname(fileURLToPath(import.meta.url))
const codeNodeSource = readFileSync(
  resolve(here, '../src/components/workflow/nodes/CodeNode.vue'),
  'utf-8'
)
const customNodeSource = readFileSync(
  resolve(here, '../src/components/workflow/nodes/CustomNode.vue'),
  'utf-8'
)

describe('CodeNode surfaces its own port boundary', () => {
  it('injects the canvas issue summary the way CustomNode does', () => {
    expect(customNodeSource).toMatch(/inject<.*>\('workflowNodeIssues'/)
    expect(
      codeNodeSource,
      'CodeNode 未 inject workflowNodeIssues，零端口警告不会显示在节点上'
    ).toMatch(/inject<.*>\('workflowNodeIssues'/)
  })

  it('renders the same severity-coded issue badge', () => {
    expect(codeNodeSource).toMatch(/v-if="nodeIssue"/)
    expect(codeNodeSource).toMatch(/node-issue-badge/)
    expect(codeNodeSource).toMatch(/node-issue-error/)
    expect(codeNodeSource).toMatch(/node-issue-warning/)
  })

  it('positions the badge against the node itself', () => {
    const rootRule = codeNodeSource
      .slice(codeNodeSource.indexOf('<style'))
      .match(/\.code-node\s*\{[^}]*\}/)
    expect(rootRule, '.code-node 根规则未找到').not.toBeNull()
    expect(
      rootRule![0],
      '角标是绝对定位，节点自身必须是定位上下文，否则会飘到画布角上'
    ).toMatch(/position:\s*relative/)
  })

  it('shows an actionable empty state when the node has no ports at all', () => {
    expect(codeNodeSource).toMatch(/hasNoPorts/)
    expect(
      codeNodeSource,
      '零端口时必须给出下一步动作，而不是只留一个连不上的空框'
    ).toMatch(/配置面板/)
  })

  it('keeps the ports container hidden only when both sides are empty', () => {
    // 有任意一侧端口时仍要正常渲染端口列，空态不能顶掉真实端口。
    expect(codeNodeSource).toMatch(/v-if="!hasNoPorts"|v-else/)
  })
})
