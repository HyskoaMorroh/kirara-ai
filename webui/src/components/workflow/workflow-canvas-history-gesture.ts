/**
 * 撤销检查点的合并窗口。
 *
 * 一次用户操作应当只产生一个撤销步骤。原实现把这件事挂在写回 store 的
 * 500ms 防抖上：`recordHistoryBeforeCanvasMutation` 用 `graphHistoryPending`
 * 判断「这次操作已经记过了」，而该标记由防抖回调清除。于是**合并窗口等于防抖
 * 间隔，而不是手势时长**——一次持续 3 秒的拖拽跨过 6 个窗口，就压 6 个检查点，
 * 用户得连按 6 次 Ctrl+Z 才能退回拖拽前；在配置面板里连续输入 4 秒同理。
 * 短于 500ms 的拖拽恰好只产生 1 步，所以这个缺陷很难在随手测试中暴露。
 *
 * 这里把窗口改成由**手势边界**界定：手势开始时记一次检查点并保持在手势内，
 * 手势结束时才释放。没有手势时退回原来的防抖行为——删除节点、连线这类
 * 一次性动作没有开始/结束事件，防抖窗口对它们是合适的粒度。
 */
export interface HistoryGestureHooks {
  /** 写入一次操作前快照。 */
  saveToHistory: () => void
  /** 取消防抖并立即把画布状态写回 store。 */
  flush: () => void
}

export class CanvasHistoryGesture {
  /** 是否已在本轮合并窗口内记过检查点。 */
  private pending = false
  /** 当前打开的手势名；``null`` 表示不在手势内。 */
  private gesture: string | null = null

  constructor(private readonly hooks: HistoryGestureHooks) {}

  /** 手势是否正在进行。 */
  get active(): boolean {
    return this.gesture !== null
  }

  /**
   * 打开一个手势。手势内的所有变更共用一个检查点。
   *
   * 同名手势重复打开是幂等的（拖多个节点时 vue-flow 会为每个节点各发一次
   * `node-drag-start`）；不同名手势打开时先结束上一个，避免嵌套后永不释放。
   */
  begin(name: string): void {
    if (this.gesture === name) return
    if (this.gesture !== null) this.end()
    this.record()
    this.gesture = name
  }

  /**
   * 结束当前手势：同步写回一次，并释放合并窗口。
   *
   * 写回必须发生在释放之前，否则手势结束后残留的防抖回调会在下一个窗口里
   * 落库，而那时新的检查点已经可以被记录，就又拆出一步。
   */
  end(): void {
    if (this.gesture === null) return
    this.gesture = null
    this.hooks.flush()
    this.pending = false
  }

  /**
   * 逐次变更的入口：手势内不再记录，手势外按原有的一次一记。
   *
   * @returns 是否真的写入了检查点。
   */
  record(): boolean {
    if (this.pending) return false
    this.hooks.saveToHistory()
    this.pending = true
    return true
  }

  /**
   * 防抖写回完成后释放合并窗口——但手势内不释放。
   *
   * 这正是修复点：原实现无条件清除标记，于是手势时长一旦超过防抖间隔，
   * 下一次变更就会再记一个检查点。
   */
  releaseAfterFlush(): void {
    if (this.gesture !== null) return
    this.pending = false
  }

  /** 供批次逻辑占用合并窗口，避免与逐次记录重复压栈。 */
  hold(): void {
    this.pending = true
  }

  /** 强制释放，供批次结束与图恢复使用。 */
  release(): void {
    this.pending = false
  }
}
