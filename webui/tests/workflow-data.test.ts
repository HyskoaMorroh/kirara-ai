import { describe, expect, it } from 'vitest'
import {
  connectionToWorkflowWire,
  createWorkflowConnectionPortIndex,
  createWorkflowEdgeId,
  getCanvasBlockPorts,
  getCanvasBlockType,
  filterWiresForBlocks,
  getUnknownBlockTypes,
  getWorkflowGraphIssues,
  mergeWorkflowConfig,
  parseWorkflowTransferPayload,
  getRenderableNodePosition,
  validateWorkflowConnection,
  workflowWireToConnection,
  WORKFLOW_TRANSFER_SCHEMA_VERSION
} from '../src/components/workflow/workflow-data'

describe('workflow import data', () => {
  it('keeps wires whose endpoints are both in the imported blocks', () => {
    const importedBlocks = [{ name: 'source_a' }, { name: 'target_b' }]
    const importedWires = [
      {
        source_block: 'source_a',
        source_output: 'result',
        target_block: 'target_b',
        target_input: 'value'
      }
    ]

    expect(filterWiresForBlocks(importedWires, importedBlocks)).toEqual(importedWires)
  })

  it('reports missing block types before an import can discard them', () => {
    expect(
      getUnknownBlockTypes(
        [{ type_name: 'core:known' }, { type_name: 'plugin:missing' }, { type_name: 'plugin:missing' }],
        [{ type_name: 'core:known' }]
      )
    ).toEqual(['plugin:missing'])
  })

  it('preserves the saved workflow config instead of applying the new-workflow default', () => {
    const importedConfig = mergeWorkflowConfig(
      { max_execution_time: 3600, retry_count: 2 },
      { max_execution_time: 36000 }
    )

    expect(importedConfig.max_execution_time).toBe(3600)
    expect(importedConfig.retry_count).toBe(2)
  })

  it('accepts legacy exports and rejects unsupported or ambiguous workflow payloads', () => {
    expect(
      parseWorkflowTransferPayload({
        blocks: [{ name: 'source', type_name: 'core:source', config: {} }],
        wires: []
      }).schema_version
    ).toBeUndefined()

    expect(() =>
      parseWorkflowTransferPayload({
        schema_version: WORKFLOW_TRANSFER_SCHEMA_VERSION + 1,
        blocks: [],
        wires: []
      })
    ).toThrow('不支持的工作流导入版本')

    expect(() =>
      parseWorkflowTransferPayload({
        schema_version: WORKFLOW_TRANSFER_SCHEMA_VERSION,
        blocks: [
          { name: 'same', type_name: 'core:first', config: {} },
          { name: 'same', type_name: 'core:second', config: {} }
        ],
        wires: []
      })
    ).toThrow('节点名称重复')
  })

  it('distinguishes an explicit origin from a missing saved node position', () => {
    expect(getRenderableNodePosition({ x: 0, y: 0 })).toEqual({ x: 0, y: 0 })
    expect(getRenderableNodePosition(null)).toEqual({ x: 0, y: 0 })
    expect(getRenderableNodePosition(undefined)).toEqual({ x: 0, y: 0 })
  })

  it('keeps an unknown node renderable and preserves its original type name', () => {
    const block = {
      name: 'script',
      type_name: 'plugin:missing',
      config: {
        inputs: [{ name: 'prompt', label: 'Prompt', type: 'str', required: true }],
        outputs: [{ name: 'answer', label: 'Answer', type: 'str' }]
      }
    }

    const blockType = getCanvasBlockType(block, [])

    expect(blockType.type_name).toBe('plugin:missing')
    expect(blockType.label).toContain('plugin:missing')
    expect(getCanvasBlockPorts(block, blockType)).toEqual({
      inputs: [expect.objectContaining({ name: 'prompt', type: 'str' })],
      outputs: [expect.objectContaining({ name: 'answer', type: 'str' })]
    })
  })

  it('reports unknown block types and ports instead of silently dropping them', () => {
    const blocks = [
      { name: 'source', type_name: 'plugin:missing', config: {} },
      { name: 'target', type_name: 'core:known', config: {} }
    ]
    const wires = [
      {
        source_block: 'source',
        source_output: 'answer',
        target_block: 'target',
        target_input: 'missing_input'
      }
    ]
    const blockTypes = [
      {
        type_name: 'core:known',
        name: 'Known',
        label: 'Known',
        description: '',
        inputs: [],
        outputs: [],
        configs: []
      }
    ]

    const issues = getWorkflowGraphIssues(blocks, wires, blockTypes)

    expect(issues.map((issue) => issue.code)).toEqual([
      'unknown_block_type',
      'unknown_source_port',
      'unknown_target_port'
    ])
    expect(issues[0].nodeId).toBe('source')
  })

  it('creates visible placeholder handles for ports referenced by historical wires', () => {
    const block = { name: 'known', type_name: 'core:known', config: {} }
    const blockType = {
      type_name: 'core:known',
      name: 'Known',
      label: 'Known',
      description: '',
      inputs: [],
      outputs: [],
      configs: []
    }
    const wires = [
      {
        source_block: 'known',
        source_output: 'legacy_output',
        target_block: 'known',
        target_input: 'legacy_input'
      }
    ]

    expect(getCanvasBlockPorts(block, blockType, wires)).toEqual({
      inputs: [expect.objectContaining({ name: 'legacy_input', type: 'unknown' })],
      outputs: [expect.objectContaining({ name: 'legacy_output', type: 'unknown' })]
    })
  })

  it('reports missing wire endpoints separately from unavailable ports', () => {
    const blocks = [{ name: 'present', type_name: 'core:known', config: {} }]
    const wires = [
      {
        source_block: 'missing_source',
        source_output: 'value',
        target_block: 'present',
        target_input: 'input'
      },
      {
        source_block: 'present',
        source_output: 'value',
        target_block: 'missing_target',
        target_input: 'input'
      }
    ]
    const blockTypes = [
      {
        type_name: 'core:known',
        name: 'Known',
        label: 'Known',
        description: '',
        inputs: [{ name: 'input', label: 'Input', description: '', type: 'str', required: false }],
        outputs: [{ name: 'value', label: 'Value', description: '', type: 'str' }],
        configs: []
      }
    ]

    expect(getWorkflowGraphIssues(blocks, wires, blockTypes).map((issue) => issue.code)).toEqual([
      'missing_source_block',
      'missing_target_block'
    ])
  })

  it('validates dynamic code ports by direction and type, then preserves handles in a wire round trip', () => {
    const blocks = [
      {
        name: 'script',
        type_name: 'internal:code',
        config: {
          inputs: [{ name: 'prompt', label: 'Prompt', type: 'str', required: true }],
          outputs: [{ name: 'answer', label: 'Answer', type: 'str' }]
        }
      },
      {
        name: 'consumer',
        type_name: 'core:consumer',
        config: {}
      }
    ]
    const blockTypes = [
      {
        type_name: 'internal:code',
        name: 'Code',
        label: 'Code',
        description: '',
        inputs: [],
        outputs: [],
        configs: []
      },
      {
        type_name: 'core:consumer',
        name: 'Consumer',
        label: 'Consumer',
        description: '',
        inputs: [{ name: 'value', label: 'Value', description: '', type: 'str', required: false }],
        outputs: [],
        configs: []
      }
    ]
    const connection = {
      source: 'script',
      sourceHandle: 'answer',
      target: 'consumer',
      targetHandle: 'value'
    }

    expect(validateWorkflowConnection(connection, blocks, blockTypes, { str: { str: true } })).toEqual({
      valid: true
    })
    expect(
      validateWorkflowConnection(
        { ...connection, sourceHandle: 'prompt' },
        blocks,
        blockTypes,
        { str: { str: true } }
      ).valid
    ).toBe(false)
    expect(
      validateWorkflowConnection(
        { ...connection, targetHandle: 'missing' },
        blocks,
        blockTypes,
        { str: { str: true } }
      ).valid
    ).toBe(false)

    const wire = connectionToWorkflowWire(connection)
    expect(workflowWireToConnection(wire)).toEqual(connection)
    expect(createWorkflowEdgeId(connection)).toBe('script-answer-consumer-value')
  })

  it('keeps an unresolved saved wire inspectable without treating it as a new valid connection', () => {
    const connection = {
      source: 'old-node',
      sourceHandle: 'removed-output',
      target: 'new-node',
      targetHandle: 'input'
    }
    const wire = connectionToWorkflowWire(connection)

    expect(workflowWireToConnection(wire)).toEqual(connection)
    expect(
      validateWorkflowConnection(connection, [], [], {}).valid
    ).toBe(false)
  })

  it('uses a reusable port index without changing dynamic connection validation', () => {
    const blocks = [
      {
        name: 'script',
        type_name: 'internal:code',
        config: {
          inputs: [],
          outputs: [{ name: 'answer', type: 'str' }]
        }
      },
      {
        name: 'consumer',
        type_name: 'core:consumer',
        config: {}
      }
    ]
    const blockTypes = [
      {
        type_name: 'internal:code',
        name: 'Code',
        label: 'Code',
        description: '',
        inputs: [],
        outputs: [],
        configs: []
      },
      {
        type_name: 'core:consumer',
        name: 'Consumer',
        label: 'Consumer',
        description: '',
        inputs: [{ name: 'value', label: 'Value', description: '', type: 'str', required: false }],
        outputs: [],
        configs: []
      }
    ]
    const index = createWorkflowConnectionPortIndex(blocks, blockTypes)

    expect(
      validateWorkflowConnection(
        {
          source: 'script',
          sourceHandle: 'answer',
          target: 'consumer',
          targetHandle: 'value'
        },
        blocks,
        blockTypes,
        { str: { str: true } },
        index
      )
    ).toEqual({ valid: true })
    expect(
      validateWorkflowConnection(
        {
          source: 'script',
          sourceHandle: 'missing',
          target: 'consumer',
          targetHandle: 'value'
        },
        blocks,
        blockTypes,
        { str: { str: true } },
        index
      )
    ).toEqual({ valid: false, reason: 'unknown_source_port' })
  })

  it('keeps new connections strict while preserving an incomplete historical handle', () => {
    const incomplete = {
      source: 'source',
      sourceHandle: null,
      target: 'target',
      targetHandle: 'value'
    }

    expect(() => connectionToWorkflowWire(incomplete)).toThrow('连线必须包含')
    expect(connectionToWorkflowWire(incomplete, { preserveIncompleteHandles: true })).toEqual({
      source_block: 'source',
      source_output: '',
      target_block: 'target',
      target_input: 'value'
    })
  })
})
