import { describe, expect, it } from 'vitest'
import {
  createWorkflowConnectionPortIndex,
  getCanvasBlockPorts,
  getCanvasBlockType,
  getWorkflowGraphIssues,
  validateWorkflowConnection
} from '../src/components/workflow/workflow-data'
import type { BlockType } from '../src/api/block'

/**
 * `internal:code` declares its ports per instance (see
 * `kirara_ai/workflow/implementations/blocks/system/basic.py` — the class-level
 * `inputs`/`outputs` are empty and are built in `__init__` from the config
 * lists, always typed `Any`). `/block/types` reads the class, so its metadata
 * carries zero ports.
 *
 * Two consequences were reachable from the UI:
 *
 * 1. A newly added code node inherited that empty port list, rendered with no
 *    handle at all and could not be wired — and nothing in the validation pass
 *    said why. That is what "the custom script boxes are disconnected" is.
 * 2. The panel lets the user pick a port `type` (default `str`) and the
 *    connection check compared those literals, while the runtime port type is
 *    `Any`, which the backend treats as universally compatible. The UI
 *    therefore refused connections the runtime would have accepted.
 */

const codeBlockType = (): BlockType => ({
  type_name: 'internal:code',
  name: 'code_block',
  label: '自定义脚本',
  description: '运行自定义 Python 代码',
  color: '',
  // Mirrors what /block/types actually returns for this type.
  inputs: [],
  outputs: [],
  configs: []
})

const chatBlockType = (): BlockType => ({
  type_name: 'internal:chat',
  name: 'chat',
  label: '对话',
  description: '',
  color: '',
  inputs: [{ name: 'prompt', label: '提示词', description: '', type: 'str', required: true }],
  outputs: [{ name: 'reply', label: '回复', description: '', type: 'str' }],
  configs: []
})

/** A saved code node, as it appears in data/workflows/chat/custom_script.yaml. */
const savedCodeBlock = () => ({
  name: 'code',
  type_name: 'internal:code',
  config: {
    inputs: [{ name: 'text', label: 'text', type: 'str', required: true }],
    outputs: [{ name: 'reply', label: 'reply', type: 'str' }]
  }
})

/** A code node just dropped on the canvas: config is still empty. */
const freshCodeBlock = () => ({
  name: 'code_1',
  type_name: 'internal:code',
  config: {}
})

describe('custom script node ports', () => {
  it('derives ports from the saved instance config rather than the empty class metadata', () => {
    const block = savedCodeBlock()
    const ports = getCanvasBlockPorts(block, getCanvasBlockType(block, [codeBlockType()]))

    expect(ports.inputs.map((port) => port.name)).toEqual(['text'])
    expect(ports.outputs.map((port) => port.name)).toEqual(['reply'])
  })

  it('reports a code node with no ports as an actionable issue instead of a silent dead end', () => {
    const issues = getWorkflowGraphIssues(
      [freshCodeBlock()],
      [],
      [codeBlockType()]
    )

    const portIssue = issues.find((issue) => issue.code === 'code_node_without_ports')
    expect(portIssue).toBeDefined()
    expect(portIssue?.nodeId).toBe('code_1')
    expect(portIssue?.severity).toBe('warning')
    expect(portIssue?.message).toContain('端口')
  })

  it('does not raise the no-port issue for a code node that has ports', () => {
    const issues = getWorkflowGraphIssues([savedCodeBlock()], [], [codeBlockType()])

    expect(issues.some((issue) => issue.code === 'code_node_without_ports')).toBe(false)
  })

  it('does not raise the no-port issue for an ordinary block type', () => {
    const issues = getWorkflowGraphIssues(
      [{ name: 'chat', type_name: 'internal:chat', config: {} }],
      [],
      [chatBlockType()]
    )

    expect(issues.some((issue) => issue.code === 'code_node_without_ports')).toBe(false)
  })

  it('treats a code port as runtime Any so the UI cannot reject a connection the runtime accepts', () => {
    const blocks = [
      { name: 'chat', type_name: 'internal:chat', config: {} },
      savedCodeBlock()
    ]
    const blockTypes = [chatBlockType(), codeBlockType()]
    // The panel stored 'int' on the code port, but the backend port is `Any`.
    blocks[1].config.inputs = [{ name: 'text', label: 'text', type: 'int', required: true }]

    // A deliberately narrow table: only str->str is declared compatible.
    const compatibility = { str: { str: true }, Any: { Any: true } }
    const result = validateWorkflowConnection(
      { source: 'chat', sourceHandle: 'reply', target: 'code', targetHandle: 'text' },
      blocks,
      blockTypes,
      compatibility
    )

    expect(result.valid).toBe(true)
  })

  it('still rejects a connection to a port that does not exist', () => {
    const blocks = [
      { name: 'chat', type_name: 'internal:chat', config: {} },
      savedCodeBlock()
    ]
    const result = validateWorkflowConnection(
      { source: 'chat', sourceHandle: 'reply', target: 'code', targetHandle: 'absent' },
      blocks,
      [chatBlockType(), codeBlockType()],
      { str: { str: true } }
    )

    expect(result).toMatchObject({ valid: false, reason: 'unknown_target_port' })
  })

  it('still enforces type compatibility between two ordinary blocks', () => {
    const strictType: BlockType = {
      ...chatBlockType(),
      type_name: 'internal:number',
      inputs: [{ name: 'value', label: '值', description: '', type: 'int', required: true }],
      outputs: []
    }
    const blocks = [
      { name: 'chat', type_name: 'internal:chat', config: {} },
      { name: 'number', type_name: 'internal:number', config: {} }
    ]

    const result = validateWorkflowConnection(
      { source: 'chat', sourceHandle: 'reply', target: 'number', targetHandle: 'value' },
      blocks,
      [chatBlockType(), strictType],
      { str: { str: true }, int: { int: true } }
    )

    expect(result).toMatchObject({ valid: false, reason: 'incompatible_types' })
  })

  it('exposes code ports as Any in the shared port index', () => {
    const index = createWorkflowConnectionPortIndex(
      [savedCodeBlock()],
      [codeBlockType()]
    )

    expect(index.inputs.get('code')?.get('text')).toBe('Any')
    expect(index.outputs.get('code')?.get('reply')).toBe('Any')
  })
})
