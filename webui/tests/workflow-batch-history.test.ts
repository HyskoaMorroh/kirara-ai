import { beforeEach, describe, expect, it } from 'vitest'
import { workflowEditorModel } from '../src/store/workflow-editor'

/**
 * `performBatchAction` existed in the store but had no caller. Compound canvas
 * edits (duplicate several nodes, tidy the whole layout) therefore relied on
 * per-mutation history plus a 500ms debounce window to merge — so any edit that
 * outlived the window was split into several undo steps, and the user had to
 * press Ctrl+Z repeatedly to get back to where they started.
 *
 * These tests pin the batch contract from the store side: one checkpoint per
 * batch, nested batches collapse, a no-op batch records nothing, and a throwing
 * batch still closes cleanly.
 */

const block = (name: string) => ({
  name,
  type_name: 'internal:noop',
  config: {},
  position: { x: 0, y: 0 }
})

const intent = workflowEditorModel.getIntent()
const view = workflowEditorModel.getViewState()

const reset = () => {
  intent.initialize({
    blocks: [],
    wires: [],
    blockTypes: [],
    name: 'wf',
    description: '',
    workflowId: 'group:wf',
    config: { max_execution_time: 100 }
  })
}

describe('performBatchAction', () => {
  beforeEach(() => {
    reset()
  })

  it('records one undo step for a multi-step edit', () => {
    intent.performBatchAction(() => {
      intent.updateBlocks([block('a')])
      intent.updateBlocks([block('a'), block('b')])
      intent.updateBlocks([block('a'), block('b'), block('c')])
    })

    expect(view.value.blocks).toHaveLength(3)
    intent.undo()
    // One Ctrl+Z must return to the pre-batch state, not to an intermediate one.
    expect(view.value.blocks).toHaveLength(0)
  })

  it('returns the action result', () => {
    const result = intent.performBatchAction(() => 42)

    expect(result).toBe(42)
  })

  it('collapses nested batches into a single checkpoint', () => {
    intent.performBatchAction(() => {
      intent.updateBlocks([block('a')])
      intent.performBatchAction(() => {
        intent.updateBlocks([block('a'), block('b')])
      })
    })

    intent.undo()
    expect(view.value.blocks).toHaveLength(0)
  })

  it('records nothing when the batch changed nothing', () => {
    const before = view.value.canUndo

    intent.performBatchAction(() => {
      // Reading state is not a change.
      void view.value.blocks.length
    })

    expect(view.value.canUndo).toBe(before)
  })

  it('still closes the batch when the action throws', () => {
    expect(() =>
      intent.performBatchAction(() => {
        intent.updateBlocks([block('a')])
        throw new Error('boom')
      })
    ).toThrow('boom')

    // The batch is closed, so a later independent edit is its own step.
    intent.updateBlocks([block('a'), block('b')])
    expect(view.value.blocks).toHaveLength(2)
  })

  it('awaits an async batch before closing it', async () => {
    await intent.performBatchAction(async () => {
      intent.updateBlocks([block('a')])
      await Promise.resolve()
      intent.updateBlocks([block('a'), block('b')])
    })

    intent.undo()
    expect(view.value.blocks).toHaveLength(0)
  })

  it('closes an async batch that rejects', async () => {
    await expect(
      intent.performBatchAction(async () => {
        intent.updateBlocks([block('a')])
        throw new Error('async boom')
      })
    ).rejects.toThrow('async boom')

    intent.updateBlocks([block('a'), block('b')])
    expect(view.value.blocks).toHaveLength(2)
  })

  it('redo replays the whole batch', () => {
    intent.performBatchAction(() => {
      intent.updateBlocks([block('a'), block('b')])
    })
    intent.undo()
    expect(view.value.blocks).toHaveLength(0)

    intent.redo()
    expect(view.value.blocks).toHaveLength(2)
  })
})
