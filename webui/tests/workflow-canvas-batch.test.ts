import { beforeEach, describe, expect, it, vi } from 'vitest'
import { runGraphBatch } from '../src/components/workflow/workflow-canvas-batch'
import { workflowEditorModel } from '../src/store/workflow-editor'

/**
 * 画布侧批次的回归。
 *
 * `tests/workflow-batch-history.test.ts` 从 store 一侧钉住了 `performBatchAction`
 * 的契约，但它在批次内**同步**调用 `intent.updateBlocks`。真实调用方不是这样：
 * `WorkflowCanvas.vue` 里的 `updateBlocks()` 是 500ms 防抖，而 `setNodes()`
 * 连 `nodesChange` 都不触发。批次关闭那一刻 store 尚未变化，检查点不压栈，
 * 改动却在防抖到期后落库——撤销栈栈顶还是上一次编辑的快照。
 *
 * 后果不是「少一个撤销步骤」，而是**数据丢失**：一次 Ctrl+Z 直接跳回上一次
 * 编辑之前，中间那次编辑既撤不回也重做不到。
 *
 * 这里用与画布同源的防抖行为驱动 `runGraphBatch`，断言：
 * 1. 批量动作产生**恰好一个**检查点；
 * 2. 一次 Ctrl+Z 回到**批量动作之前**，而不是更早；
 * 3. 批量动作之前的那次编辑仍然可达。
 */

const block = (name: string, x = 0, y = 0) => ({
  name,
  type_name: 'internal:noop',
  config: {},
  position: { x, y }
})

const intent = workflowEditorModel.getIntent()
const view = workflowEditorModel.getViewState()

/** 与 `WorkflowCanvas.vue` 的 `debounce` 行为一致的前沿防抖。 */
const makeDebounced = (func: () => void, delay: number) => {
  let timer: ReturnType<typeof setTimeout> | null = null
  const debounced = () => {
    if (timer !== null) return
    timer = setTimeout(() => {
      try {
        func()
      } finally {
        timer = null
      }
    }, delay)
  }
  debounced.cancel = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }
  return debounced
}

/** 一个最小画布替身：`setNodes` 只改自己的状态，写回 store 走防抖。 */
const makeCanvas = () => {
  let canvasBlocks = [...view.value.blocks]
  const write = () => intent.updateBlocks(canvasBlocks.map((item) => ({ ...item })))
  const updateBlocks = makeDebounced(write, 500)

  return {
    setNodes(next: ReturnType<typeof block>[]) {
      canvasBlocks = next
      // 与画布一致：批量动作里调的就是这个防抖入口。
      updateBlocks()
    },
    syncFromStore() {
      canvasBlocks = [...view.value.blocks]
    },
    flush() {
      updateBlocks.cancel()
      write()
    }
  }
}

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

describe('runGraphBatch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    reset()
  })

  it('records one checkpoint even though the canvas writes back through a debounce', () => {
    const canvas = makeCanvas()

    runGraphBatch(
      () => {
        canvas.setNodes([block('a'), block('b')])
      },
      { performBatchAction: intent.performBatchAction, flush: canvas.flush }
    )

    expect(view.value.blocks).toHaveLength(2)
    expect(view.value.canUndo).toBe(true)

    intent.undo()
    expect(view.value.blocks).toHaveLength(0)
  })

  it('does not destroy the edit that preceded the batch', () => {
    // 第一次编辑：拖动一个节点。走正常的逐次记录路径。
    intent.saveToHistory()
    intent.updateBlocks([block('a', 100, 100)])
    expect(view.value.blocks[0].position).toEqual({ x: 100, y: 100 })

    // 第二次编辑：一键整理，把节点挪到别处。
    const canvas = makeCanvas()
    runGraphBatch(
      () => {
        canvas.setNodes([block('a', 900, 50)])
      },
      { performBatchAction: intent.performBatchAction, flush: canvas.flush }
    )
    expect(view.value.blocks[0].position).toEqual({ x: 900, y: 50 })

    // 一次 Ctrl+Z 必须退回整理**之前**（拖动后的位置），不是拖动之前。
    intent.undo()
    expect(view.value.blocks[0].position).toEqual({ x: 100, y: 100 })

    // 再按一次才回到最初。
    intent.undo()
    expect(view.value.blocks).toHaveLength(0)
  })

  it('keeps a node added before the batch reachable after one undo', () => {
    intent.saveToHistory()
    intent.updateBlocks([block('a'), block('b')])

    const canvas = makeCanvas()
    runGraphBatch(
      () => {
        canvas.setNodes([block('a'), block('b'), block('b_copy')])
      },
      { performBatchAction: intent.performBatchAction, flush: canvas.flush }
    )

    expect(view.value.blocks).toHaveLength(3)
    intent.undo()
    // 复制之前是 a + b；一次撤销不能把 b 一起带走。
    expect(view.value.blocks.map((item) => item.name)).toEqual(['a', 'b'])
  })

  it('leaves the pending debounce harmless after the batch flushed', () => {
    const canvas = makeCanvas()
    runGraphBatch(
      () => {
        canvas.setNodes([block('a')])
      },
      { performBatchAction: intent.performBatchAction, flush: canvas.flush }
    )

    // 防抖已被取消，到期后不该再写一次而多出一个检查点。
    vi.advanceTimersByTime(1000)
    intent.undo()
    expect(view.value.blocks).toHaveLength(0)
    expect(view.value.canUndo).toBe(false)
  })

  it('records nothing when the batch changed nothing', () => {
    const canvas = makeCanvas()
    const before = view.value.canUndo

    runGraphBatch(() => {}, {
      performBatchAction: intent.performBatchAction,
      flush: canvas.flush
    })

    expect(view.value.canUndo).toBe(before)
  })

  it('returns the action result', () => {
    const canvas = makeCanvas()

    const result = runGraphBatch(() => 42, {
      performBatchAction: intent.performBatchAction,
      flush: canvas.flush
    })

    expect(result).toBe(42)
  })

  it('still closes the batch when the action throws', () => {
    const canvas = makeCanvas()

    expect(() =>
      runGraphBatch(
        () => {
          canvas.setNodes([block('a')])
          throw new Error('boom')
        },
        { performBatchAction: intent.performBatchAction, flush: canvas.flush }
      )
    ).toThrow('boom')

    // 批次已关闭：后续独立编辑自成一步。
    intent.updateBlocks([block('a'), block('b')])
    expect(view.value.blocks).toHaveLength(2)
  })

  it('demonstrates why the flush is load-bearing, not decorative', () => {
    // 这条用例刻意**不**经过 `runGraphBatch`，而是复现修复前的调用形态：
    // 批次里只调防抖入口，不同步写回。它证明去掉 flush 会重新丢历史，
    // 所以任何「这句 flush 看起来多余」的简化都会让 D1 复活。
    intent.saveToHistory()
    intent.updateBlocks([block('a', 100, 100)])

    const canvas = makeCanvas()
    canvas.syncFromStore()
    intent.performBatchAction(() => {
      canvas.setNodes([block('a', 900, 50)])
      // 没有 flush：批次关闭时 store 还是 {100,100}，检查点不压栈。
    })
    // 防抖到期，改动这才落进 store——已经没有对应的检查点了。
    vi.advanceTimersByTime(1000)
    expect(view.value.blocks[0].position).toEqual({ x: 900, y: 50 })

    intent.undo()
    // 一次撤销直接跳过了「拖到 (100,100)」这一步，退回最初的空画布。
    expect(view.value.blocks).toHaveLength(0)
  })
})
