import { describe, expect, it } from 'vitest'
import {
  filterWiresForBlocks,
  getUnknownBlockTypes,
  mergeWorkflowConfig,
  parseWorkflowTransferPayload,
  getRenderableNodePosition,
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
})
