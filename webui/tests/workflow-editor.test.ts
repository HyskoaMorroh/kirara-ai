import { describe, expect, it } from 'vitest'
import {
  createWorkflowGraphSnapshot,
  workflowEditorModel
} from '../src/store/workflow-editor'

describe('workflow editor history', () => {
  it('restores deeply cloned node data and workflow config, then supports redo', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()

    intent.initialize({
      blocks: [
        {
          type_name: 'test:message',
          name: 'message',
          config: { nested: { text: 'before' } },
          position: { x: 12, y: 24 }
        }
      ],
      wires: [],
      blockTypes: [],
      name: 'before',
      description: 'before description',
      workflowId: 'user:history',
      config: { max_execution_time: 120 }
    })
    intent.saveToHistory()

    viewState.value.blocks[0].config.nested.text = 'after'
    viewState.value.config.max_execution_time = 999
    intent.updateName('after')

    intent.undo()

    expect(viewState.value.blocks[0].config.nested.text).toBe('before')
    expect(viewState.value.config.max_execution_time).toBe(120)
    expect(viewState.value.name).toBe('before')

    intent.redo()

    expect(viewState.value.blocks[0].config.nested.text).toBe('after')
    expect(viewState.value.config.max_execution_time).toBe(999)
    expect(viewState.value.name).toBe('after')
  })

  it('retains exactly the latest 100 undo checkpoints', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: '0',
      workflowId: 'user:bounded-history',
      config: { max_execution_time: 0 }
    })

    for (let index = 1; index <= 101; index += 1) {
      intent.saveToHistory()
      intent.updateName(String(index))
    }
    for (let index = 0; index < 100; index += 1) intent.undo()

    expect(viewState.value.name).toBe('1')
    intent.undo()
    expect(viewState.value.name).toBe('1')
  })

  it('does not consume an undo step for a no-op checkpoint', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:no-op-history',
      config: { max_execution_time: 0 }
    })

    intent.saveToHistory()
    intent.updateName('after')
    intent.saveToHistory()
    intent.saveToHistory()
    intent.undo()

    expect(viewState.value.name).toBe('before')
  })

  it('invalidates redo after a new edit following undo', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:redo-invalidation',
      config: { max_execution_time: 0 }
    })
    intent.saveToHistory()
    intent.updateName('first edit')
    intent.undo()
    expect(viewState.value.canRedo).toBe(true)

    intent.saveToHistory()
    intent.updateName('replacement edit')

    expect(viewState.value.canRedo).toBe(false)
    intent.redo()
    expect(viewState.value.name).toBe('replacement edit')
  })

  it('reuses unchanged records between immutable snapshots and clones changed records only', () => {
    const initial = {
      blocks: [
        { type_name: 'test:a', name: 'a', config: { value: 1 } },
        { type_name: 'test:b', name: 'b', config: { value: 2 } }
      ],
      wires: [],
      name: 'workflow',
      description: '',
      workflowId: 'user:sharing',
      config: { max_execution_time: 30 }
    }
    const first = createWorkflowGraphSnapshot(initial)
    const edited = {
      ...initial,
      blocks: [initial.blocks[0], { ...initial.blocks[1], config: { value: 3 } }]
    }
    const second = createWorkflowGraphSnapshot(edited, first)

    expect(second.blocks[0]).toBe(first.blocks[0])
    expect(second.blocks[1]).not.toBe(first.blocks[1])
    edited.blocks[1].config.value = 99
    expect(second.blocks[1].config.value).toBe(3)
  })

  it('distinguishes cyclic reference topology when deciding whether records are reusable', () => {
    const shared = { value: 1 }
    const initialConfig: Record<string, unknown> = {
      first: shared,
      second: shared
    }
    const initial = {
      blocks: [{ type_name: 'test:cycle', name: 'cycle', config: initialConfig }],
      wires: [],
      name: 'workflow',
      description: '',
      workflowId: 'user:cyclic-sharing',
      config: { max_execution_time: 30 }
    }
    const first = createWorkflowGraphSnapshot(initial)

    const cyclicConfig: Record<string, unknown> = {
      first: { value: 1 }
    }
    cyclicConfig.second = cyclicConfig
    const second = createWorkflowGraphSnapshot(
      {
        ...initial,
        blocks: [{ ...initial.blocks[0], config: cyclicConfig }]
      },
      first
    )

    expect(second.blocks[0] !== first.blocks[0]).toBe(true)
    expect(second.blocks[0].config.second).toBe(second.blocks[0].config)
  })
})
