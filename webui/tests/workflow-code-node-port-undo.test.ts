import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 端口增删必须能撤销（需求 2「自定义脚本不同框是断开的」的连带缺陷）。
 *
 * 自定义脚本节点的端口由用户自己在配置面板声明——这是刚拖进画布时两侧都没有
 * 端口的原因，也是「连不上线」的真正解释。既然端口全靠手工添加，那么加错一个
 * 之后能不能 Ctrl+Z 撤回，就直接决定这个交互好不好用。
 *
 * 四个处理函数里只有 `addInputPort` 复制了 config：
 *
 *     // addInputPort
 *     config: { ...(props.selectedNode.data.config || {}) }
 *     updatedData.config.inputs = updatedData.inputs   // 写在副本上
 *
 *     // addOutputPort / removeInputPort / removeOutputPort
 *     updatedData.config.outputs = updatedData.outputs // 写在**原对象**上
 *
 * 后三者是原地写入。而 `updateSelectedNodeData` 是先 `emit('before-node-mutation')`
 * 让画布拍历史快照、再 `updateNode`——原地写入发生在拍快照**之前**，于是快照里
 * 已经是新值。撤销拿回旧的 `inputs`/`outputs` 数组，但 `config.inputs`/
 * `config.outputs` 停在新值上。而后端 `CodeBlock` 的端口正是从 `config` 读的
 * （`basic.py:200-202`），于是撤销之后画布显示旧端口、保存下去是新端口。
 *
 * 判据：四个函数都必须复制 config 之后再写。写法一致本身就是判据——四处里有一处
 * 不一样，那一处就是缺陷，无论它现在有没有被别的代码兜住。
 */

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(
  resolve(here, '../src/components/workflow/NodeConfigPanel.vue'),
  'utf-8'
)

/** 取出一个函数从声明到下一个 `const xxx = ` 之前的正文。 */
const bodyOf = (name: string): string => {
  const start = source.indexOf(`const ${name} = `)
  expect(start, `找不到 ${name}`).toBeGreaterThan(-1)
  const rest = source.slice(start + 1)
  const end = rest.search(/\nconst \w+ = /)
  return end === -1 ? rest : rest.slice(0, end)
}

const HANDLERS = ['addInputPort', 'removeInputPort', 'addOutputPort', 'removeOutputPort']

describe('端口增删不能原地改 config', () => {
  it.each(HANDLERS)('%s 先复制 config 再写入', (name) => {
    const body = bodyOf(name)

    // 原地写入的形态是「没有复制就直接给 config.xxx 赋值」。
    expect(body).toMatch(/config:\s*\{\s*\.\.\./)
  })

  it.each(HANDLERS)('%s 通过 updateSelectedNodeData 提交', (name) => {
    // 绕过它就绕过了 `before-node-mutation`，那一步是历史快照的唯一触发点。
    expect(bodyOf(name)).toContain('updateSelectedNodeData(')
  })
})

describe('历史快照的顺序', () => {
  it('快照在写入节点之前拍', () => {
    expect(source).toMatch(/emit\('before-node-mutation'\)[\s\S]{0,80}updateNode\(/)
  })
})
