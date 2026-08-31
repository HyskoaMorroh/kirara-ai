/**
 * 把一组画布改动收成**一个**可撤销步骤。
 *
 * 这里存在一个必须显式处理的相位错配：
 *
 * - `performBatchAction` 在批次开始时拍下操作前快照，在批次关闭时与当下状态
 *   比对，只有不同才压栈；
 * - 画布把图形写回 store 的两个入口（`updateBlocks` / `updateWires`）都是
 *   500ms 防抖，而 Vue Flow 的 `setNodes()` 只改它自己的内部 store，
 *   连 `nodesChange` 都不会触发。
 *
 * 于是「一键整理」「复制选中」「粘贴」这类批量动作在批次关闭那一刻，store 里
 * 什么都还没变，比对结果是「无变化」，检查点不被压栈。同时批次期间
 * `graphHistoryPending` 为真，逐次记录那条路也被抑制。两条路都不写历史，
 * 改动却在防抖到期后落进了 store——撤销栈栈顶仍是**上一次**编辑的快照。
 * 用户按一次 Ctrl+Z，退回的不是整理前，而是整理前的**再前一次**，
 * 而且 redo 只能回到整理后：中间那一次编辑无法到达。
 *
 * 修法是让批次内的写回同步发生：在批次关闭之前取消防抖并直接写一次。
 * 这样比对拿到的是真实的批次后状态，检查点必然压栈，且只压一个。
 */
export interface GraphBatchOptions {
  /** store 侧的批次入口，负责捕获操作前快照并在关闭时比对。 */
  performBatchAction: <R>(action: () => R) => R
  /** 取消防抖并立即把画布状态写回 store。 */
  flush: () => void
}

export const runGraphBatch = <T,>(action: () => T, options: GraphBatchOptions): T =>
  options.performBatchAction(() => {
    const result = action()
    // 同步写回必须在批次关闭**之前**发生，否则比对看到的是操作前状态。
    // 放在这里而不是 action 内部，是为了让每个调用方都拿到这个保证，
    // 而不是各自记得补一句 flush。
    options.flush()
    return result
  })
