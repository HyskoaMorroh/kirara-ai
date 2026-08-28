import { describe, expect, it } from 'vitest'
import {
  createWorkflowGraphSnapshot,
  workflowEditorModel
} from '../src/store/workflow-editor'

describe('workflow editor history', () => {
  it('exposes model-level undo and redo methods matching the intent contract', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:model-history',
      config: { max_execution_time: 0 }
    })
    intent.saveToHistory()
    intent.updateName('after')

    workflowEditorModel.undo()
    expect(viewState.value.name).toBe('before')
    workflowEditorModel.redo()
    expect(viewState.value.name).toBe('after')
  })

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

  it('does not create a checkpoint while history saving is suppressed', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:suppressed-history',
      config: { max_execution_time: 0 }
    })

    workflowEditorModel.performActionWithoutHistory(() => {
      intent.saveToHistory()
      intent.updateName('after')
    })

    expect(viewState.value.canUndo).toBe(false)
    intent.undo()
    expect(viewState.value.name).toBe('after')
  })

  it('keeps performActionWithoutHistory available through the public intent', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:public-suppression',
      config: { max_execution_time: 0 }
    })

    intent.performActionWithoutHistory(() => {
      intent.saveToHistory()
      intent.updateName('after')
    })

    expect(viewState.value.canUndo).toBe(false)
    expect(viewState.value.name).toBe('after')
  })

  it('restores history saving after a suppressed action throws', () => {
    const viewState = workflowEditorModel.getViewState()

    expect(() =>
      workflowEditorModel.performActionWithoutHistory(() => {
        throw new Error('expected failure')
      })
    ).toThrow('expected failure')
    expect(viewState.value.skipSavingHistory).toBe(false)
  })

  it('restores history saving when thenable detection throws', () => {
    const viewState = workflowEditorModel.getViewState()
    const thenable = Object.defineProperty({}, 'then', {
      get() {
        throw new Error('then getter failure')
      }
    })

    expect(() => workflowEditorModel.performActionWithoutHistory(() => thenable)).toThrow(
      'then getter failure'
    )
    expect(viewState.value.skipSavingHistory).toBe(false)
  })

  it('records and undoes normal edits after a suppressed action throws', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:history-after-error',
      config: { max_execution_time: 0 }
    })

    expect(() =>
      workflowEditorModel.performActionWithoutHistory(() => {
        throw new Error('expected failure')
      })
    ).toThrow('expected failure')
    intent.saveToHistory()
    intent.updateName('after')
    intent.undo()

    expect(viewState.value.name).toBe('before')
  })

  it('keeps history suppression active across nested actions', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:nested-suppression',
      config: { max_execution_time: 0 }
    })

    workflowEditorModel.performActionWithoutHistory(() => {
      workflowEditorModel.performActionWithoutHistory(() => {
        expect(viewState.value.skipSavingHistory).toBe(true)
        intent.saveToHistory()
      })
      expect(viewState.value.skipSavingHistory).toBe(true)
      intent.saveToHistory()
      intent.updateName('after')
    })

    expect(viewState.value.skipSavingHistory).toBe(false)
    expect(viewState.value.canUndo).toBe(false)
  })

  it('records a batch of node and wire mutations at one history point', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [{ type_name: 'core:source', name: 'source', config: {}, position: { x: 0, y: 0 } }],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:batch-history',
      config: { max_execution_time: 0 }
    })

    workflowEditorModel.performBatchAction(() => {
      intent.updateBlocks([
        ...viewState.value.blocks,
        { type_name: 'core:target', name: 'target', config: {}, position: { x: 300, y: 0 } }
      ])
      intent.updateWires([
        {
          source_block: 'source',
          source_output: 'result',
          target_block: 'target',
          target_input: 'value'
        }
      ])
    })

    expect(viewState.value.blocks).toHaveLength(2)
    expect(viewState.value.wires).toHaveLength(1)
    intent.undo()
    expect(viewState.value.blocks).toHaveLength(1)
    expect(viewState.value.wires).toEqual([])
    expect(viewState.value.canUndo).toBe(false)
    intent.redo()
    expect(viewState.value.blocks).toHaveLength(2)
    expect(viewState.value.wires).toHaveLength(1)
  })

  it('does not add a second history point for nested batches and restores suppression after async failure', async () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:nested-batch-history',
      config: { max_execution_time: 0 }
    })

    await expect(
      workflowEditorModel.performBatchAction(async () => {
        workflowEditorModel.performBatchAction(() => {
          intent.updateName('during')
        })
        await Promise.resolve()
        throw new Error('batch failed')
      })
    ).rejects.toThrow('batch failed')

    expect(viewState.value.skipSavingHistory).toBe(false)
    expect(viewState.value.canUndo).toBe(true)
    intent.undo()
    expect(viewState.value.name).toBe('before')
  })

  it('keeps redo intact when a batch makes no observable change', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:no-op-batch',
      config: { max_execution_time: 0 }
    })
    workflowEditorModel.performBatchAction(() => intent.updateName('after'))
    intent.undo()
    expect(viewState.value.name).toBe('before')
    expect(viewState.value.canRedo).toBe(true)

    const result = workflowEditorModel.performBatchAction(() => 42)

    expect(result).toBe(42)
    expect(viewState.value.canRedo).toBe(true)
    intent.redo()
    expect(viewState.value.name).toBe('after')
  })

  it('does not create history for a failed batch that made no change', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:failed-no-op-batch',
      config: { max_execution_time: 0 }
    })

    expect(() =>
      workflowEditorModel.performBatchAction(() => {
        throw new Error('batch failed before mutation')
      })
    ).toThrow('batch failed before mutation')

    expect(viewState.value.skipSavingHistory).toBe(false)
    expect(viewState.value.canUndo).toBe(false)
  })

  it('restores batch state when reading a returned then property throws', () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:batch-then-getter',
      config: { max_execution_time: 0 }
    })
    const thenable = Object.defineProperty({}, 'then', {
      get() {
        throw new Error('batch then getter failure')
      }
    })

    expect(() => workflowEditorModel.performBatchAction(() => thenable)).toThrow(
      'batch then getter failure'
    )
    expect(viewState.value.skipSavingHistory).toBe(false)
    expect(viewState.value.canUndo).toBe(false)

    workflowEditorModel.performBatchAction(() => intent.updateName('after'))
    intent.undo()
    expect(viewState.value.name).toBe('before')
  })

  it('preserves an asynchronous batch return value and records its changes once', async () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:async-batch-result',
      config: { max_execution_time: 0 }
    })

    const result = await workflowEditorModel.performBatchAction(async () => {
      await Promise.resolve()
      intent.updateName('after')
      return 'saved'
    })

    expect(result).toBe('saved')
    intent.undo()
    expect(viewState.value.name).toBe('before')
    expect(viewState.value.canUndo).toBe(false)
  })

  it('keeps history suppression active until an async action settles', async () => {
    const intent = workflowEditorModel.getIntent()
    const viewState = workflowEditorModel.getViewState()
    intent.initialize({
      blocks: [],
      wires: [],
      blockTypes: [],
      name: 'before',
      workflowId: 'user:async-suppression',
      config: { max_execution_time: 0 }
    })

    await workflowEditorModel.performActionWithoutHistory(async () => {
      await Promise.resolve()
      expect(viewState.value.skipSavingHistory).toBe(true)
      intent.saveToHistory()
      intent.updateName('after')
    })

    expect(viewState.value.skipSavingHistory).toBe(false)
    expect(viewState.value.canUndo).toBe(false)
    intent.undo()
    expect(viewState.value.name).toBe('after')
  })

  it('restores suppression after an async action rejects', async () => {
    const viewState = workflowEditorModel.getViewState()

    await expect(
      workflowEditorModel.performActionWithoutHistory(async () => {
        await Promise.resolve()
        throw new Error('expected async failure')
      })
    ).rejects.toThrow('expected async failure')

    expect(viewState.value.skipSavingHistory).toBe(false)
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
