import { describe, expect, it, vi } from 'vitest'
import { CanvasHistoryGesture } from '../src/components/workflow/workflow-canvas-history-gesture'

/**
 * 撤销粒度的回归。
 *
 * 原实现把「一次操作只记一个检查点」挂在写回 store 的 500ms 防抖上：
 * `graphHistoryPending` 由防抖回调清除，于是合并窗口等于防抖间隔而不是手势
 * 时长。一次持续 3 秒的拖拽跨 6 个窗口就压 6 个检查点，用户要连按 6 次
 * Ctrl+Z 才能退回拖拽前。短拖拽恰好只产生 1 步，因此随手测试测不出来。
 */

const makeGesture = () => {
  const saveToHistory = vi.fn()
  const flush = vi.fn()
  return {
    gesture: new CanvasHistoryGesture({ saveToHistory, flush }),
    saveToHistory,
    flush
  }
}

describe('CanvasHistoryGesture', () => {
  it('records exactly one checkpoint for a gesture that outlives many flush windows', () => {
    const { gesture, saveToHistory } = makeGesture()

    gesture.begin('node-drag')
    // 模拟一次 3 秒拖拽：6 个 500ms 防抖窗口，每个窗口里都有坐标变更。
    for (let window = 0; window < 6; window += 1) {
      gesture.record()
      gesture.releaseAfterFlush()
    }
    gesture.end()

    expect(saveToHistory).toHaveBeenCalledTimes(1)
  })

  it('flushes once when the gesture ends, before releasing the window', () => {
    const { gesture, flush } = makeGesture()

    gesture.begin('node-drag')
    gesture.end()

    expect(flush).toHaveBeenCalledTimes(1)
    // 释放之后新的变更才能开一个新检查点。
    expect(gesture.record()).toBe(true)
  })

  it('keeps one checkpoint per gesture across consecutive gestures', () => {
    const { gesture, saveToHistory } = makeGesture()

    for (let round = 0; round < 3; round += 1) {
      gesture.begin('node-drag')
      gesture.record()
      gesture.releaseAfterFlush()
      gesture.end()
    }

    expect(saveToHistory).toHaveBeenCalledTimes(3)
  })

  it('is idempotent when the same gesture starts again', () => {
    const { gesture, saveToHistory } = makeGesture()

    // 拖动多选时 vue-flow 会为每个节点各发一次 node-drag-start。
    gesture.begin('node-drag')
    gesture.begin('node-drag')
    gesture.begin('node-drag')
    gesture.end()

    expect(saveToHistory).toHaveBeenCalledTimes(1)
  })

  it('closes the previous gesture when a different one starts', () => {
    const { gesture, saveToHistory, flush } = makeGesture()

    gesture.begin('node-drag')
    gesture.begin('config-edit')

    // 前一个手势被结束（含写回），新手势自己记一个检查点。
    expect(flush).toHaveBeenCalledTimes(1)
    expect(saveToHistory).toHaveBeenCalledTimes(2)
    expect(gesture.active).toBe(true)
  })

  it('still records one checkpoint per flush window outside a gesture', () => {
    const { gesture, saveToHistory } = makeGesture()

    // 删除节点、连线这类一次性动作没有开始/结束事件，
    // 防抖窗口对它们是合适的粒度，行为必须保持不变。
    gesture.record()
    gesture.record()
    expect(saveToHistory).toHaveBeenCalledTimes(1)

    gesture.releaseAfterFlush()
    gesture.record()
    expect(saveToHistory).toHaveBeenCalledTimes(2)
  })

  it('does not flush or record when ending a gesture that never began', () => {
    const { gesture, saveToHistory, flush } = makeGesture()

    gesture.end()

    expect(saveToHistory).not.toHaveBeenCalled()
    expect(flush).not.toHaveBeenCalled()
    expect(gesture.active).toBe(false)
  })

  it('lets a batch hold the window without pushing a second checkpoint', () => {
    const { gesture, saveToHistory } = makeGesture()

    gesture.hold()
    expect(gesture.record()).toBe(false)
    expect(saveToHistory).not.toHaveBeenCalled()

    gesture.release()
    expect(gesture.record()).toBe(true)
  })
})
