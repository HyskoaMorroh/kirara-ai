import { describe, expect, it } from 'vitest'
import { workflowEditorModel } from '../src/store/workflow-editor'

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
})
