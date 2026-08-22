import { ref, computed } from 'vue'
import type { BlockInstance, Wire, WorkflowConfig } from '@/api/workflow'
import type { BlockType } from '@/api/block'
import { deepClone } from '@/utils/deep-clone'

// 定义意图（Intent）
export interface WorkflowEditorIntent {
  initialize: (data: {
    blocks: BlockInstance[]
    wires: Wire[]
    blockTypes: BlockType[]
    name?: string
    description?: string
    workflowId?: string
    config: WorkflowConfig
  }) => void
  updateBlocks: (blocks: BlockInstance[]) => void
  updateWires: (wires: Wire[]) => void
  updateName: (name: string) => void
  updateDescription: (description: string) => void
  updateWorkflowId: (workflowId: string) => void
  updateConfig: (config: WorkflowConfig) => void
  saveToHistory: () => void
  undo: () => void
  redo: () => void
  reset: () => void
}

// 定义视图状态（ViewState）
export interface WorkflowEditorViewState {
  blocks: BlockInstance[]
  wires: Wire[]
  blockTypes: BlockType[]
  name: string
  description: string
  workflowId: string
  config: WorkflowConfig
  canUndo: boolean
  canRedo: boolean
  hasClipboard: boolean
  skipSavingHistory: boolean
}

// 定义模型（Model）
export interface WorkflowGraphSnapshot {
  blocks: BlockInstance[]
  wires: Wire[]
  name: string
  description: string
  workflowId: string
  config: WorkflowConfig
}

type HistoryState = WorkflowGraphSnapshot

/**
 * 工作流数据最终会被序列化为 JSON/YAML。历史记录必须拥有自己的副本，
 * 否则节点配置或坐标的原地修改会反向污染已经保存的快照。
 *
 * 深拷贝由 `@/utils/deep-clone` 唯一实现：它在每一层都 `toRaw`，因此不会踩到
 * `structuredClone` 遇 Vue 响应式代理抛 `DataCloneError` 的坑，也不像 JSON 克隆
 * 那样丢失 Date / Map / Set 或在循环引用时抛错。
 */
const cloneHistoryValue = <T>(value: T): T => deepClone(value)

const stableSerialize = (value: unknown, seen = new Map<object, number>()): string => {
  if (value === null) return 'null'
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return 'number:NaN'
    if (!Number.isFinite(value)) return `number:${String(value)}`
    return `number:${value}`
  }
  if (typeof value !== 'object') return `${typeof value}:${String(value)}`
  if (seen.has(value)) return `reference:${seen.get(value)}`
  seen.set(value, seen.size)
  if (Array.isArray(value)) return `[${value.map((item) => stableSerialize(item, seen)).join(',')}]`
  if (value instanceof Date) return `date:${value.toISOString()}`
  if (value instanceof RegExp) return `regexp:${value.source}/${value.flags}:${value.lastIndex}`
  if (value instanceof Map) {
    return `map:{${[...value.entries()]
      .map(([key, item]) => `${stableSerialize(key, seen)}=${stableSerialize(item, seen)}`)
      .sort()
      .join(',')}}`
  }
  if (value instanceof Set) {
    return `set:{${[...value].map((item) => stableSerialize(item, seen)).sort().join(',')}}`
  }
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key], seen)}`)
    .join(',')}}`
}

const freezeSnapshotValue = <T>(value: T, seen = new WeakSet<object>()): T => {
  if (value === null || typeof value !== 'object' || seen.has(value)) return value
  seen.add(value)
  if (value instanceof Map || value instanceof Set) return Object.freeze(value)
  for (const child of Object.values(value as Record<string, unknown>)) {
    freezeSnapshotValue(child, seen)
  }
  return Object.freeze(value)
}

const buildReusableRecords = <T>(records: T[], keyOf: (record: T) => string) => {
  const reusable = new Map<string, T[]>()
  for (const record of records) {
    const key = `${keyOf(record)}\u0000${stableSerialize(record)}`
    const matches = reusable.get(key) || []
    matches.push(record)
    reusable.set(key, matches)
  }
  return reusable
}

const cloneRecordsWithSharing = <T>(
  records: T[],
  previous: T[],
  keyOf: (record: T) => string
): T[] => {
  const reusable = buildReusableRecords(previous, keyOf)
  return records.map((record) => {
    const key = `${keyOf(record)}\u0000${stableSerialize(record)}`
    const match = reusable.get(key)?.shift()
    return match || freezeSnapshotValue(cloneHistoryValue(record))
  })
}

/** Build an immutable workflow snapshot while reusing unchanged records. */
export function createWorkflowGraphSnapshot(
  current: Omit<WorkflowGraphSnapshot, never>,
  previous?: WorkflowGraphSnapshot
): WorkflowGraphSnapshot {
  const previousBlocks = previous?.blocks || []
  const previousWires = previous?.wires || []
  const blocks = cloneRecordsWithSharing(
    current.blocks,
    previousBlocks,
    (block) => `${block.type_name}\u0000${block.name}`
  )
  const wires = cloneRecordsWithSharing(
    current.wires,
    previousWires,
    (wire) =>
      `${wire.source_block}\u0000${wire.source_output}\u0000${wire.target_block}\u0000${wire.target_input}`
  )
  const config =
    previous && stableSerialize(current.config) === stableSerialize(previous.config)
      ? previous.config
      : freezeSnapshotValue(cloneHistoryValue(current.config))
  return freezeSnapshotValue({
    blocks,
    wires,
    name: current.name,
    description: current.description,
    workflowId: current.workflowId,
    config
  })
}

const snapshotsEqual = (left: WorkflowGraphSnapshot, right: WorkflowGraphSnapshot) =>
  left.name === right.name &&
  left.description === right.description &&
  left.workflowId === right.workflowId &&
  stableSerialize(left.blocks) === stableSerialize(right.blocks) &&
  stableSerialize(left.wires) === stableSerialize(right.wires) &&
  stableSerialize(left.config) === stableSerialize(right.config)

/**
 * 撤销栈上限。
 *
 * 每个快照都是 blocks / wires / config 的深拷贝，节点多的工作流单个快照
 * 就有几十 KB。编辑器是长驻页面，不设上限意味着内存只增不减，所以按
 * 先进先出丢弃最早的快照——用户实际用到的都是最近几十步。
 */
const MAX_HISTORY_DEPTH = 100

class WorkflowEditorModel {
  private state = ref({
    blocks: [] as BlockInstance[],
    wires: [] as Wire[],
    blockTypes: [] as BlockType[],
    name: '',
    description: '',
    workflowId: '',
    undoStack: [] as HistoryState[],
    redoStack: [] as HistoryState[],
    clipboard: null as any,
    config: {
      max_execution_time: 0
    }
  })

  // 用深度计数而不是布尔值，保证嵌套及并行异步操作都在结束前抑制历史记录。
  private historySuppressionDepth = ref(0)

  // 计算属性
  private readonly viewState = computed<WorkflowEditorViewState>(() => ({
    blocks: this.state.value.blocks,
    wires: this.state.value.wires,
    blockTypes: this.state.value.blockTypes,
    name: this.state.value.name,
    description: this.state.value.description,
    workflowId: this.state.value.workflowId,
    canUndo: this.state.value.undoStack.length > 0,
    canRedo: this.state.value.redoStack.length > 0,
    hasClipboard: this.state.value.clipboard !== null,
    skipSavingHistory: this.historySuppressionDepth.value > 0,
    config: this.state.value.config
  }))

  private endHistorySuppression() {
    this.historySuppressionDepth.value = Math.max(0, this.historySuppressionDepth.value - 1)
  }

  // 执行操作但不保存历史记录；Promise 完成前保持抑制，且支持嵌套调用。
  performActionWithoutHistory<T>(action: () => T): T {
    this.historySuppressionDepth.value += 1
    let result: T
    try {
      result = action()
    } catch (error) {
      this.endHistorySuppression()
      throw error
    }

    if (result && typeof (result as any).then === 'function') {
      return Promise.resolve(result).finally(() => {
        this.endHistorySuppression()
      }) as T
    }

    this.endHistorySuppression()
    return result
  }

  // 提取：保存当前状态到 undo 栈
  private createHistoryState(): HistoryState {
    return createWorkflowGraphSnapshot({
      blocks: this.state.value.blocks,
      wires: this.state.value.wires,
      name: this.state.value.name,
      description: this.state.value.description,
      workflowId: this.state.value.workflowId,
      config: this.state.value.config
    }, this.state.value.undoStack.at(-1))
  }

  private pushToUndoStack(clearRedo = true) {
    const currentState = this.createHistoryState()
    const previousState = this.state.value.undoStack.at(-1)
    if (!previousState || !snapshotsEqual(previousState, currentState)) {
      this.state.value.undoStack.push(currentState)
    }
    // 超出上限时丢弃最早的快照，保证内存占用有界
    if (this.state.value.undoStack.length > MAX_HISTORY_DEPTH) {
      this.state.value.undoStack.splice(
        0,
        this.state.value.undoStack.length - MAX_HISTORY_DEPTH
      )
    }
    if (clearRedo) {
      this.state.value.redoStack = []
    }
  }

  // 提取：保存当前状态到 redo 栈
  private pushToRedoStack() {
    const currentState = this.createHistoryState()
    const previousState = this.state.value.redoStack.at(-1)
    if (!previousState || !snapshotsEqual(previousState, currentState)) {
      this.state.value.redoStack.push(currentState)
    }
    if (this.state.value.redoStack.length > MAX_HISTORY_DEPTH) {
      this.state.value.redoStack.splice(
        0,
        this.state.value.redoStack.length - MAX_HISTORY_DEPTH
      )
    }
  }

  // 提取：恢复状态
  private restoreState(state: HistoryState) {
    Object.assign(this.state.value, {
      blocks: cloneHistoryValue(state.blocks),
      wires: cloneHistoryValue(state.wires),
      name: state.name,
      description: state.description,
      workflowId: state.workflowId,
      config: cloneHistoryValue(state.config)
    })
  }

  undo() {
    if (this.state.value.undoStack.length === 0) return

    const currentState = this.createHistoryState()
    while (
      this.state.value.undoStack.length > 0 &&
      snapshotsEqual(this.state.value.undoStack.at(-1)!, currentState)
    ) {
      this.state.value.undoStack.pop()
    }
    if (this.state.value.undoStack.length === 0) return
    this.pushToRedoStack()
    const prevState = this.state.value.undoStack.pop()!
    this.restoreState(prevState)
  }

  redo() {
    if (this.state.value.redoStack.length === 0) return

    const nextState = this.state.value.redoStack.pop()!
    // 重做时要保留剩余的 redo 历史；普通编辑才会清空 redo 栈。
    this.pushToUndoStack(false)
    this.restoreState(nextState)
  }

  // Intent 处理方法
  private readonly intent: WorkflowEditorIntent = {
    initialize: (data) => {
      // 使用 performActionWithoutHistory 包裹
      this.performActionWithoutHistory(() => {
        Object.assign(this.state.value, {
          blocks: data.blocks,
          wires: data.wires,
          blockTypes: data.blockTypes,
          name: data.name || '',
          description: data.description || '',
          workflowId: data.workflowId || '',
          config: data.config || {},
          // 这是一个单例 store，切换工作流时会再次 initialize。若不清空历史，
          // Ctrl+Z 会把另一张工作流的节点恢复到当前画布上。
          undoStack: [],
          redoStack: []
        })
      })
    },

    updateBlocks: (blocks) => {
      this.state.value.blocks = blocks
    },

    updateWires: (wires) => {
      this.state.value.wires = wires
    },

    updateName: (name) => {
      this.state.value.name = name
    },

    updateDescription: (description) => {
      this.state.value.description = description
    },

    updateWorkflowId: (workflowId) => {
      this.state.value.workflowId = workflowId
    },

    updateConfig: (config) => {
      this.state.value.config = config
    },

    saveToHistory: () => {
      // 只有在 skipSavingHistory 为 false 时才保存历史记录
      if (this.historySuppressionDepth.value > 0) return
      this.pushToUndoStack()
    },

    undo: () => this.undo(),

    redo: () => this.redo(),

    reset: () => {
      this.intent.saveToHistory()
      this.state.value.blocks = []
      this.state.value.wires = []
    }
  }

  getViewState() {
    return this.viewState
  }

  getIntent() {
    return this.intent
  }
}

// 创建单例实例
export const workflowEditorModel = new WorkflowEditorModel()
