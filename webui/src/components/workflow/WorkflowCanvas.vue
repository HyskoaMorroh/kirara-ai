<script setup lang="ts">
import { ref, onMounted, watch, computed, onBeforeUnmount, nextTick, provide } from 'vue'
import {
  NScrollbar,
  NButton,
  NModal,
  NForm,
  NFormItem,
  NInput,
  useMessage,
  NSpin,
  NSpace,
  NIcon,
  useLoadingBar,
  NTooltip,
  NDivider,
  NList,
  NListItem,
  NTag,
  NText,
  NInputNumber,
  NSelect
} from 'naive-ui'
import {
  SaveOutline,
  RefreshOutline,
  DownloadOutline,
  SettingsOutline,
  CloseOutline,
  ArrowUndoOutline,
  ArrowRedoOutline,
  GitNetworkOutline,
  ScanOutline,
  CloudUploadOutline,
  CheckmarkCircleOutline,
  HelpCircleOutline,
  TrashOutline,
  WarningOutline,
  SearchOutline,
  LocateOutline
} from '@vicons/ionicons5'
import { getTypeCompatibility, type BlockOutput, type BlockType } from '@/api/block'
import {
  validateWorkflow,
  type BlockInstance,
  type Wire,
  type WorkflowConfig,
  type WorkflowValidationIssue
} from '@/api/workflow'
import { workflowEditorModel } from '@/store/workflow-editor'
import { getTypeColor } from '@/utils/node-colors'
import { deepClone } from '@/utils/deep-clone'
import { useThemeStore } from '@/stores/theme'
// 导入 vue-flow 相关组件
import { VueFlow, useVueFlow, Panel, connectionExists } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls, ControlButton } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import CustomNode from './nodes/CustomNode.vue'
import CodeNode from './nodes/CodeNode.vue'
import NodeConfigPanel from './NodeConfigPanel.vue'
import NodeListPanel from './NodeListPanel.vue'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import type { Connection, Edge, EdgeChange, EdgeUpdateEvent, Node, NodeChange } from '@vue-flow/core'
import { MarkerType } from '@vue-flow/core'
import { useLayout, findFreeNodePosition, findOverlappingNodes, snapToGrid } from './useLayout'
import {
  filterWiresForBlocks,
  getUnknownBlockTypes,
  getRenderableNodePosition,
  mergeWorkflowConfig,
  parseWorkflowTransferPayload,
  WORKFLOW_TRANSFER_SCHEMA_VERSION
} from './workflow-data'
import { createUniqueNodeName } from './workflow-node-utils'
// ==================== 属性和事件定义 ====================
const props = defineProps<{
  blocks: BlockInstance[]
  wires: Wire[]
  blockTypes: BlockType[]
  initialName?: string
  initialDescription?: string
  initialWorkflowId?: string
  initialConfig?: WorkflowConfig
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:blocks': [blocks: BlockInstance[]]
  'update:wires': [wires: Wire[]]
  'update:config': [config: WorkflowConfig]
  save: [name: string, description: string, workflowId: string]
}>()

// ==================== 状态管理 ====================
const typeCompatibility = ref<Record<string, Record<string, boolean>>>({})
const typeCompatibilityReady = ref(false)
const typeCompatibilityUnavailable = ref(false)
const intent = workflowEditorModel.getIntent()
const viewState = workflowEditorModel.getViewState()

// 画布网格点颜色随主题变化，Background 组件只接受具体色值而非 CSS 变量
const themeStore = useThemeStore()
const canvasDotColor = computed(() => themeStore.seed.canvasDot)
// 缩略图同理：它把颜色直接写进 SVG 属性，无法解析 CSS 变量
const minimapNodeColor = computed(() => themeStore.seed.nodeHeader)
const minimapMaskColor = computed(() =>
  themeStore.isDark ? 'rgba(0, 0, 0, 0.55)' : 'rgba(240, 243, 248, 0.7)'
)

// 快捷键说明弹窗
const showShortcutsModal = ref(false)

/** 快捷键说明表，与下方 handleKeydown 的实现保持一致 */
const shortcutHints = [
  { keys: 'Ctrl / ⌘ + S', description: '保存工作流' },
  { keys: 'Ctrl / ⌘ + Z', description: '撤销上一步操作' },
  { keys: 'Ctrl / ⌘ + Shift + Z', description: '重做' },
  { keys: 'Ctrl / ⌘ + L', description: '整理布局，自动重排全部节点' },
  { keys: 'Ctrl / ⌘ + 0', description: '缩放至显示全部节点' },
  { keys: 'Ctrl / ⌘ + D', description: '复制当前选中的节点' },
  { keys: 'Ctrl / ⌘ + F', description: '查找节点并跳转' },
  { keys: 'Delete / Backspace', description: '删除选中的节点或连线' },
  { keys: '拖拽左侧节点', description: '从节点列表拖到画布即可添加' },
  { keys: '拖动连线端点', description: '可以把已有连线改接到其他端口' }
]

// 工具栏按钮状态
const saving = ref(false)
const importing = ref(false)
const resetting = ref(false)
const tidying = ref(false)
const validationChecking = ref(false)

type CanvasValidationIssue = {
  nodeId: string
  label: string
  message: string
  severity: 'error' | 'warning'
  code?: string
}
const serverValidationIssues = ref<CanvasValidationIssue[]>([])

// 设置对话框相关状态
const showSettingsModal = ref(false)
const message = useMessage()
const loadingBar = useLoadingBar()

const formRef = ref()
const formValue = ref({
  workflowId: '',
  name: '',
  description: '',
  config: {
    max_execution_time: 0
  }
})

// 初始化工作流ID
viewState.value.workflowId =
  'user:' + Array.from({ length: 5 }, () => Math.floor(Math.random() * 36).toString(36)).join('')
viewState.value.name = formValue.value.name
viewState.value.description = formValue.value.description

// 表单验证规则
const formRules = {
  workflowId: {
    required: true,
    trigger: ['blur', 'input'],
    validator: (rule: any, value: string) => {
      if (!value) {
        return new Error('工作流ID不能为空')
      }
      if (!/^[^:]+:[^:]+$/.test(value)) {
        return new Error('工作流ID必须是 group_id:workflow_id 的格式')
      }
      return true
    }
  },
  name: {
    required: true,
    trigger: ['blur', 'input'],
    message: '工作流名称不能为空'
  }
}

// ==================== Vue Flow 相关设置 ====================
const {
  nodes,
  edges,
  removeEdges,
  addEdges,
  setNodes,
  setEdges,
  fitView,
  addNodes,
  project,
  getSelectedNodes,
  removeSelectedNodes,
  addSelectedNodes,
  setCenter,
  getViewport
} = useVueFlow()

const { layout } = useLayout()
const selectedNode = computed(() =>
  getSelectedNodes.value.length > 0 ? getSelectedNodes.value[0] : null
)

let graphHistoryReady = false
let graphHistoryPending = false
let restoringGraph = false

/**
 * 将一次用户操作前的状态写入历史。图形数据会在 debounce 后同步到 store，
 * 所以一次连续拖拽、配置编辑或删改连线只会产生一个可撤销的检查点。
 */
const recordHistoryBeforeCanvasMutation = () => {
  if (!graphHistoryReady || restoringGraph || graphHistoryPending) return
  intent.saveToHistory()
  graphHistoryPending = true
}

let lastCompatibilityNoticeAt = 0
const notifyCompatibilityUnavailable = () => {
  const now = Date.now()
  if (now - lastCompatibilityNoticeAt < 3000) return
  lastCompatibilityNoticeAt = now
  message.warning(
    typeCompatibilityUnavailable.value
      ? '类型兼容性服务暂不可用，已放行连线但未做类型校验，请自行确认端口类型'
      : '类型兼容性正在加载，已放行连线但未做类型校验'
  )
}

/**
 * 连线校验索引。
 *
 * isValidConnection 会被 vue-flow 对每个端口各调用一次（拖动连线时更是持续调用），
 * 原实现每次都要在 nodes 与 blockTypes 上做 4 次线性扫描，节点一多就明显卡手。
 * 这里预先建好 id → 端口名 → 类型 的查表；只读取 id / blockType / 端口定义，
 * 所以单纯拖动节点（仅坐标变化）不会让索引失效。
 */
const connectionTypeIndex = computed(() => {
  const outputs = new Map<string, Map<string, string>>()
  const inputs = new Map<string, Map<string, string>>()
  const blockTypeByName = new Map(props.blockTypes.map((type) => [type.type_name, type]))

  for (const node of nodes.value) {
    const typeName = node.data?.blockType?.type_name
    const blockType = typeName ? blockTypeByName.get(typeName) : undefined
    if (!blockType) continue

    // 代码节点的端口是用户在配置里自定义的，类型只能从 config 里取
    const isCodeBlock = typeName === 'internal:code'
    const outputList: any[] = isCodeBlock
      ? node.data?.config?.outputs || []
      : blockType.outputs || []
    const inputList: any[] = isCodeBlock ? node.data?.config?.inputs || [] : blockType.inputs || []

    outputs.set(
      node.id,
      new Map(outputList.filter((port) => port?.name && port?.type).map((port) => [port.name, port.type]))
    )
    inputs.set(
      node.id,
      new Map(inputList.filter((port) => port?.name && port?.type).map((port) => [port.name, port.type]))
    )
  }

  return { outputs, inputs }
})

/** 校验结果缓存；索引或兼容性表变化时整体作废 */
let connectionCheckCache = new Map<string, boolean>()
watch([connectionTypeIndex, typeCompatibility, typeCompatibilityReady], () => {
  connectionCheckCache = new Map()
})

// 连接验证功能
const isValidConnection = (connection: Connection) => {
  const cacheKey = `${connection.source}::${connection.sourceHandle}::${connection.target}::${connection.targetHandle}`
  const cached = connectionCheckCache.get(cacheKey)
  if (cached !== undefined) return cached

  const check = () => {
    const index = connectionTypeIndex.value
    // 获取源输出和目标输入的类型
    const sourceType = index.outputs.get(connection.source || '')?.get(connection.sourceHandle || '')
    const targetType = index.inputs.get(connection.target || '')?.get(connection.targetHandle || '')
    if (!sourceType || !targetType) return false

    // 兼容性表不可用时降级放行：接口故障不应让整块画布失去连线能力。
    // 端口是否存在仍然照常校验，只跳过类型匹配这一步。
    if (!typeCompatibilityReady.value) return true

    // 使用类型兼容性映射检查
    return typeCompatibility.value[sourceType]?.[targetType] === true
  }

  const result = check()
  connectionCheckCache.set(cacheKey, result)
  return result
}

// 修改 onConnect 函数
const handleConnect = (params: Connection) => {
  // make sure the connection is not already exists
  if (connectionExists(params, edges.value)) {
    return
  }
  if (!typeCompatibilityReady.value) {
    // 降级模式：提示一次但不再阻断，否则 /block/types/compatibility 持续失败
    // 就会把画布永久锁死。
    notifyCompatibilityUnavailable()
  }
  if (isValidConnection(params)) {
    const edge = buildEdge(params)
    if (edge) {
      recordHistoryBeforeCanvasMutation()
      addEdges([edge])
      updateWires()
      updateBlocks()
    }
  } else {
    message.error('类型不兼容，无法连接')
  }
}

const handleEdgeUpdate = ({ edge, connection }: EdgeUpdateEvent) => {
  if (connectionExists(connection, edges.value)) {
    return
  }
  if (!typeCompatibilityReady.value) {
    notifyCompatibilityUnavailable()
  }
  if (isValidConnection(connection)) {
    // 删除旧的线，建立新的线
    const newEdge = buildEdge(connection)
    if (newEdge) {
      recordHistoryBeforeCanvasMutation()
      removeEdges([edge])
      if (edges.value.find((e) => e.id === newEdge.id) === undefined) {
        addEdges([newEdge])
      }
      updateWires()
    }
  } else {
    message.error('类型不兼容，无法连接')
  }
}

const buildEdge = (params: Connection, blocks = viewState.value.blocks): Edge | null => {
  // 获取源节点类型和输出类型
  const sourceBlock = blocks.find((block) => block.name === params.source)
  if (!sourceBlock) return null

  const blockType = props.blockTypes.find((type) => type.type_name === sourceBlock.type_name)
  if (!blockType) return null

  let type = null
  if (blockType.type_name == 'internal:code') {
    type = sourceBlock.config.outputs.find(
      (output: any) => output.name === params.sourceHandle
    )?.type
  } else {
    type = blockType.outputs.find(
      (output: BlockOutput) => output.name === params.sourceHandle
    )?.type
  }
  if (!type) return null

  return {
    ...params,
    id: `${params.source}-${params.sourceHandle}-${params.target}-${params.targetHandle}`,
    markerEnd: MarkerType.ArrowClosed,
    style: { stroke: getTypeColor(type).color_on, strokeWidth: 2 },
    class: 'workflow-edge',
    updatable: true
  }
}

// ==================== 数据转换函数 ====================
// 将 BlockInstance 转换为 vue-flow 节点
const convertCustomNodeToVueFlowNode = (block: BlockInstance, blockType: BlockType): Node => {
  return {
    id: block.name,
    type: 'custom', // 使用自定义节点类型
    position: getRenderableNodePosition(block.position),
    data: {
      label: blockType.label,
      blockType: blockType,
      config: block.config || {},
      inputs: blockType.inputs,
      outputs: blockType.outputs
    }
  }
}

const convertCodeNodeToVueFlowNode = (block: BlockInstance, blockType: BlockType): Node => {
  return {
    id: block.name,
    type: 'code', // 使用自定义节点类型
    position: getRenderableNodePosition(block.position),
    data: {
      label: blockType.label,
      blockType: blockType,
      config: block.config || {},
      inputs: block.config?.inputs || [],
      outputs: block.config?.outputs || []
    }
  }
}

const convertBlocksToNodes = (blocks: BlockInstance[]): Node[] => {
  return blocks
    .map((block) => {
      const blockType = props.blockTypes.find((type) => type.type_name === block.type_name)
      if (!blockType) return null
      if (blockType.type_name == 'internal:code') {
        return convertCodeNodeToVueFlowNode(block, blockType)
      }
      return convertCustomNodeToVueFlowNode(block, blockType)
    })
    .filter((it) => it !== null)
}

// 将 Wire 转换为 vue-flow Edge
const convertWiresToEdges = (wires: Wire[], blocks = viewState.value.blocks): Edge[] => {
  return filterWiresForBlocks(wires, blocks)
    .map((wire) => {
      // 构造一个Connection对象，然后使用buildEdge函数
      const connection: Connection = {
        source: wire.source_block,
        sourceHandle: wire.source_output,
        target: wire.target_block,
        targetHandle: wire.target_input
      }

      return buildEdge(connection, blocks) as Edge
    })
    .filter((it) => it !== null)
}

// 将 vue-flow 节点转换回 BlockInstance
const convertNodesToBlocks = (sourceNodes = nodes.value): BlockInstance[] => {
  return sourceNodes.map((node) => {
    return {
      type_name: node.data?.blockType?.type_name,
      name: node.id,
      config: node.data?.config || {},
      position: {
        x: Math.round(node.position.x),
        y: Math.round(node.position.y)
      }
    }
  })
}

// 将 vue-flow 边转换回 Wire
const convertEdgesToWires = (): Wire[] => {
  return edges.value.map((edge) => ({
    source_block: edge.source,
    source_output: edge.sourceHandle || '',
    target_block: edge.target,
    target_input: edge.targetHandle || ''
  }))
}

// ==================== 数据更新函数 ====================

type DebouncedFunction = (() => Promise<void>) & { cancel: () => void }

const debounce = (func: () => void, delay: number): DebouncedFunction => {
  let timer: number | null = null
  let resolvePending: (() => void) | null = null
  const debounced = function (this: any, ...args: any[]) {
    return new Promise<void>((resolve) => {
      if (timer === null) {
        resolvePending = resolve
        timer = window.setTimeout(() => {
          try {
            func.apply(this, args)
          } finally {
            timer = null
            resolvePending?.()
            resolvePending = null
          }
        }, delay)
      } else {
        resolve()
      }
    })
  } as DebouncedFunction

  debounced.cancel = () => {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
      resolvePending?.()
      resolvePending = null
    }
  }

  return debounced
}
// 更新区块数据
const updateBlocks = debounce(() => {
  const blocks = convertNodesToBlocks()
  intent.updateBlocks(blocks)
  lastEmittedBlocks = blocks
  emit('update:blocks', blocks)
  graphHistoryPending = false
}, 500)

// 更新连线数据
const updateWires = debounce(() => {
  const wires = convertEdgesToWires()
  intent.updateWires(wires)
  lastEmittedWires = wires
  emit('update:wires', wires)
  graphHistoryPending = false
}, 500)

/**
 * 记录最近一次向父组件发出的数组引用。
 *
 * 父组件收到 update:blocks / update:wires 后会把同一个数组原样赋回 props，
 * 于是 props 的监听器又触发一轮 initGraphData——这是一个自激回路。
 * 用引用比较把「自己刚发出去的那一份」识别出来并跳过，即可断开回路。
 */
let lastEmittedBlocks: BlockInstance[] | null = null
let lastEmittedWires: Wire[] | null = null

// 直接消费 Vue Flow 的变更事件：选择和尺寸变化只影响界面，不必写回工作流。
// 这样打开节点配置、拖拽或编辑代码时都不会再序列化整张图作变更比对。
const hasPersistentGraphChange = (changes: Array<NodeChange | EdgeChange>) =>
  changes.some((change) => change.type !== 'select' && change.type !== 'dimensions')

const handleNodesChange = (changes: NodeChange[]) => {
  if (restoringGraph || !hasPersistentGraphChange(changes)) return
  recordHistoryBeforeCanvasMutation()
  updateBlocks()
}

const handleEdgesChange = (changes: EdgeChange[]) => {
  if (restoringGraph || !hasPersistentGraphChange(changes)) return
  recordHistoryBeforeCanvasMutation()
  updateWires()
}

const handleNodeConfigMutation = () => {
  recordHistoryBeforeCanvasMutation()
  updateBlocks()
}

/**
 * 立即同步一次图形数据，不经过 debounce。
 *
 * debounce 的实现是「首次调用后 delay 毫秒内的后续调用直接 resolve」，
 * 所以在最后一次编辑后立刻点保存，那次编辑可能还没写回 viewState。
 * 保存前调用本函数可确保提交的是画布上的最新状态。
 */
const flushGraphData = () => {
  updateBlocks.cancel()
  updateWires.cancel()

  const blocks = convertNodesToBlocks()
  const wires = convertEdgesToWires()
  intent.updateBlocks(blocks)
  intent.updateWires(wires)
  lastEmittedBlocks = blocks
  lastEmittedWires = wires
  emit('update:blocks', blocks)
  emit('update:wires', wires)
  graphHistoryPending = false
}

// 恢复图形
const restoreGraph = () => {
  restoringGraph = true
  try {
    const vueFlowNodes = convertBlocksToNodes(viewState.value.blocks)
    const vueFlowEdges = convertWiresToEdges(viewState.value.wires)
    const nodesWithoutPosition = new Set(
      viewState.value.blocks.filter((block) => !block.position).map((block) => block.name)
    )

    setNodes(vueFlowNodes)
    setEdges(vueFlowEdges)

    // null / undefined 表示服务端没有保存过布局；{ x: 0, y: 0 } 是用户明确的
    // 合法坐标，不能再被当成“未布局”而覆盖。只为缺失坐标的节点补上 dagre 结果。
    if (vueFlowNodes.length > 0 && nodesWithoutPosition.size > 0) {
      const laidOutNodes = layout(vueFlowNodes, vueFlowEdges, 'LR')
      const laidOutById = new Map(laidOutNodes.map((node) => [node.id, node]))
      const positionedNodes = vueFlowNodes.map((node) =>
        nodesWithoutPosition.has(node.id) ? laidOutById.get(node.id) || node : node
      )
      setNodes(positionedNodes)

      const positionedBlocks = convertNodesToBlocks(positionedNodes)
      intent.updateBlocks(positionedBlocks)
      lastEmittedBlocks = positionedBlocks
      emit('update:blocks', positionedBlocks)
    }
  } finally {
    restoringGraph = false
  }
  nextTick(() => {
    fitView()
  })
}

/**
 * 一键整理布局。
 *
 * 与首次加载时的自动布局共用同一套 dagre 逻辑，但由用户主动触发，
 * 用于修正手工拖拽后互相压叠的节点。会写入历史，可以撤销。
 */
const handleTidyLayout = () => {
  if (nodes.value.length === 0) {
    message.info('画布上还没有节点')
    return
  }
  tidying.value = true
  try {
    recordHistoryBeforeCanvasMutation()
    // 这里的节点全部已经渲染过，DOM 实测尺寸永远比估算值准；
    // measured 模式强制优先采用实测值，避免按估算留错空隙。
    setNodes(layout(nodes.value, edges.value, 'LR', { measured: true }))
    updateBlocks()
    nextTick(() => {
      fitView()
      message.success('已重新排布节点')
    })
  } finally {
    tidying.value = false
  }
}

/** 缩放至适应全部节点 */
const handleFitView = () => {
  fitView({ padding: 0.2 })
}

/** 尺寸尚未测量时的兜底值，取常见节点的偏大一侧 */
const FALLBACK_NODE_WIDTH = 240
const FALLBACK_NODE_HEIGHT = 140

/**
 * 收集画布上所有节点的包围盒，供落点计算判断重叠。
 *
 * 已渲染节点用 DOM 实测尺寸；刚插入还没测量的用一个保守的兜底值，
 * 宁可稍大也不能偏小，否则新节点会压在旧节点上。
 */
const getOccupiedBoxes = (excludeIds: Set<string> = new Set()) =>
  nodes.value
    .filter((node) => !excludeIds.has(node.id))
    .map((node) => ({
      id: node.id,
      x: node.position.x,
      y: node.position.y,
      width: node.dimensions?.width || FALLBACK_NODE_WIDTH,
      height: node.dimensions?.height || FALLBACK_NODE_HEIGHT
    }))

/**
 * 复制当前选中的节点。
 *
 * 复用 onDrop 里的唯一名生成规则，保证副本名不与既有节点冲突；
 * 副本带上偏移量，不会与原节点完全重叠。
 */
const handleDuplicateSelection = () => {
  const selected = getSelectedNodes.value
  if (selected.length === 0) {
    message.info('请先选中要复制的节点')
    return
  }

  recordHistoryBeforeCanvasMutation()
  const created: Node[] = []
  const existingIds = new Set(nodes.value.map((node) => node.id))
  // 原先固定 +40/+40，副本必然压在原节点上（节点至少 220px 宽）。
  // 改为从「原节点右下角外侧」起算，再交给 findFreeNodePosition 找空位。
  const occupied = getOccupiedBoxes()

  for (const node of selected) {
    const newId = createUniqueNodeName(node.data?.blockType?.type_name || node.id, existingIds)
    existingIds.add(newId)

    const size = {
      width: node.dimensions?.width || FALLBACK_NODE_WIDTH,
      height: node.dimensions?.height || FALLBACK_NODE_HEIGHT
    }
    const position = findFreeNodePosition(
      { x: node.position.x + size.width + 40, y: node.position.y },
      size,
      occupied
    )
    occupied.push({ id: newId, ...position, ...size })

    created.push({
      ...node,
      id: newId,
      selected: false,
      position,
      // 深拷贝配置，避免副本与原节点共享同一个 config 对象
      data: {
        ...node.data,
        config: deepClone(node.data?.config || {})
      }
    })
  }

  addNodes(created)
  updateBlocks()
  message.success(`已复制 ${created.length} 个节点`)
}

// ==================== 事件处理函数 ====================
// 保存处理函数
const handleSave = async () => {
  try {
    // 先把画布最新状态同步进 viewState，避免 debounce 丢掉最后一次编辑
    flushGraphData()
    // A prop update can arrive while validation awaits; retain this save's form values.
    const workflowName = formValue.value.name
    const workflowDescription = formValue.value.description
    const workflowId = formValue.value.workflowId
    const workflowConfig = mergeWorkflowConfig(formValue.value.config, viewState.value.config)
    const errors = await formRef.value?.validate()
    if (errors?.length > 0 || !workflowName || !workflowId) {
      message.error('工作流信息需要修改')
      showSettingsModal.value = true
      return
    }
    // 存在必需输入未连接等问题时给出提示，但不阻断保存——
    // 用户可能在分多次搭建工作流，中途保存是正常需求
    if (validationIssues.value.length > 0) {
      message.warning(`已保存，但仍有 ${validationIssues.value.length} 处问题待处理`)
    }
    saving.value = true
    loadingBar.start()
    showSettingsModal.value = false
    viewState.value.config = workflowConfig
    emit('update:config', workflowConfig)
    emit('save', workflowName, workflowDescription, workflowId)
  } catch (error: any) {
    message.error(error?.message || '保存失败')
  } finally {
    saving.value = false
    loadingBar.finish()
  }
}

// 重置处理函数
const handleReset = () => {
  resetting.value = true
  loadingBar.start()
  // 刷新页面
  window.location.reload()
}

// 撤销处理函数
const handleUndo = () => {
  if (!viewState.value.canUndo) return
  // 拖拽或配置编辑可能仍在 debounce 窗口内，先把“当前态”写回 store，
  // 这样 undo 同时生成的 redo 快照才会包含最后一次用户编辑。
  flushGraphData()
  intent.undo()
  workflowEditorModel.performActionWithoutHistory(() => {
    restoreGraph()
  })
}

// 重做处理函数
const handleRedo = () => {
  if (!viewState.value.canRedo) return
  // 与 handleUndo 保持对称：debounce 窗口内的最后一次编辑也要先写回 store，
  // 否则重做时生成的 undo 快照会丢掉它。
  flushGraphData()
  intent.redo()
  workflowEditorModel.performActionWithoutHistory(() => {
    restoreGraph()
  })
}

// 导入处理函数
const handleImport = async () => {
  importing.value = true
  loadingBar.start()
  let importFinished = false
  const finishImport = () => {
    if (importFinished) return
    importFinished = true
    if (importWatchdogTimer !== null) {
      clearTimeout(importWatchdogTimer)
      importWatchdogTimer = null
    }
    window.removeEventListener('blur', handleWindowBlurForPicker)
    window.removeEventListener('focus', handleWindowFocusAfterPicker)
    importing.value = false
    loadingBar.finish()
  }

  /**
   * 兜底解锁。
   *
   * 部分浏览器（含较旧的 Safari / Firefox ESR）不派发 input 的 cancel 事件，
   * 用户在文件选择框里按取消后 importing 与 loadingBar 会永久卡住。
   * 这里按「选择框弹出使窗口失焦 → 关闭后重新获得焦点」的顺序判断：
   * 重新获得焦点且迟迟没有 change 事件，就认为用户取消了。
   */
  let importWatchdogTimer: number | null = null
  let pickerBlurred = false
  const handleWindowBlurForPicker = () => {
    pickerBlurred = true
  }
  const handleWindowFocusAfterPicker = () => {
    if (!pickerBlurred || importWatchdogTimer !== null) return
    importWatchdogTimer = window.setTimeout(() => {
      importWatchdogTimer = null
      finishImport()
    }, 1500)
  }

  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.json'
  input.addEventListener('cancel', finishImport, { once: true })
  input.onchange = (e: Event) => {
    // 已经选中文件，取消兜底不再需要，否则会在读取途中把状态提前解锁
    if (importWatchdogTimer !== null) {
      clearTimeout(importWatchdogTimer)
      importWatchdogTimer = null
    }
    window.removeEventListener('blur', handleWindowBlurForPicker)
    window.removeEventListener('focus', handleWindowFocusAfterPicker)
    const file = (e.target as HTMLInputElement).files?.[0]
    if (!file) {
      finishImport()
      return
    }

    const reader = new FileReader()
    reader.onload = (event) => {
      try {
        const data = parseWorkflowTransferPayload(JSON.parse(event.target?.result as string))
        const importedBlocks = data.blocks as unknown as BlockInstance[]
        const importedWires = data.wires as unknown as Wire[]
        const unknownBlockTypes = getUnknownBlockTypes(importedBlocks, props.blockTypes)
        if (unknownBlockTypes.length > 0) {
          throw new Error(`缺少区块类型 ${unknownBlockTypes.join('、')}`)
        }
        // 端口对不上的连线不再让整份导入失败：节点定义随版本演进是常态，
        // 全有或全无会让用户拿不回任何内容。这里丢弃无法识别的连线，
        // 并把逐条明细报给用户，行为回到早期的宽容策略。
        const vueFlowEdges = convertWiresToEdges(importedWires, importedBlocks)
        const acceptedWireKeys = new Set(
          vueFlowEdges.map(
            (edge) => `${edge.source}::${edge.sourceHandle}::${edge.target}::${edge.targetHandle}`
          )
        )
        const droppedWires = importedWires.filter(
          (wire) =>
            !acceptedWireKeys.has(
              `${wire.source_block}::${wire.source_output}::${wire.target_block}::${wire.target_input}`
            )
        )
        const acceptedWires = importedWires.filter(
          (wire) =>
            acceptedWireKeys.has(
              `${wire.source_block}::${wire.source_output}::${wire.target_block}::${wire.target_input}`
            )
        )

        recordHistoryBeforeCanvasMutation()
        workflowEditorModel.performActionWithoutHistory(() => {
          viewState.value.blocks = importedBlocks
          viewState.value.wires = acceptedWires
          viewState.value.name = data.name || viewState.value.name
          viewState.value.description = data.description || viewState.value.description
          viewState.value.workflowId = data.workflow_id || viewState.value.workflowId
          viewState.value.config = mergeWorkflowConfig(
            data.config as WorkflowConfig | undefined,
            viewState.value.config
          )
          formValue.value = {
            workflowId: viewState.value.workflowId,
            name: viewState.value.name,
            description: viewState.value.description,
            config: mergeWorkflowConfig(
              data.config as WorkflowConfig | undefined,
              formValue.value.config
            )
          }
        })
        restoreGraph()
        intent.updateWires(acceptedWires)
        lastEmittedBlocks = viewState.value.blocks
        lastEmittedWires = acceptedWires
        emit('update:blocks', viewState.value.blocks)
        emit('update:wires', acceptedWires)
        emit('update:config', viewState.value.config)
        if (droppedWires.length > 0) {
          // 明细逐条列出，用户才知道该手工补哪几根线
          importDroppedWires.value = droppedWires.map(
            (wire) =>
              `${wire.source_block}.${wire.source_output} → ${wire.target_block}.${wire.target_input}`
          )
          showImportReportModal.value = true
          message.warning(`导入完成，但有 ${droppedWires.length} 根连线无法识别`)
        } else {
          message.success('导入成功')
        }
      } catch (error) {
        const detail = error instanceof Error ? error.message : '文件格式错误'
        message.error(`导入失败：${detail}`)
      } finally {
        finishImport()
      }
    }
    reader.onerror = () => {
      message.error('导入失败：无法读取文件')
      finishImport()
    }
    reader.readAsText(file)
  }
  window.addEventListener('blur', handleWindowBlurForPicker)
  window.addEventListener('focus', handleWindowFocusAfterPicker)
  input.click()
}

// 返回处理函数
const handleBack = () => {
  if (window.confirm('确定要离开此页面吗？您未保存的更改可能会丢失！')) {
    window.history.back()
  }
}

/**
 * 导出为 JSON 文件。
 *
 * 与既有的 handleImport 互为逆操作：导入读取 { blocks, wires }，
 * 这里就按同样结构导出，导出的文件可以直接再导入回来。
 */
const handleExport = () => {
  try {
    const payload = {
      schema_version: WORKFLOW_TRANSFER_SCHEMA_VERSION,
      name: viewState.value.name || formValue.value.name,
      description: viewState.value.description || formValue.value.description,
      workflow_id: viewState.value.workflowId || formValue.value.workflowId,
      blocks: convertNodesToBlocks(),
      wires: convertEdgesToWires(),
      config: viewState.value.config
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json;charset=utf-8'
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    const safeId = (payload.workflow_id || 'workflow').replace(/[^\w.-]/g, '_')
    link.download = `${safeId}.json`
    link.click()
    // 立刻 revoke 会在部分浏览器里把还没启动的下载掐掉，
    // 推到下一轮事件循环之后再释放。
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    message.success('已导出工作流')
  } catch (error: any) {
    message.error(error?.message || '导出失败')
  }
}

/**
 * 工作流静态检查。
 *
 * 执行器只在运行时才会因缺少必需输入而报错，用户在编辑器里看不出问题。
 * 这里在保存前把三类常见错误挑出来：必需输入未连接、节点完全孤立、
 * 以及整张图没有可作为入口的无输入节点（执行器以这类节点为起点）。
 */
const localValidationIssues = computed(() => {
  const issues: CanvasValidationIssue[] = []
  if (nodes.value.length === 0) return issues

  const connectedTargets = new Set(
    edges.value.map((edge) => `${edge.target}::${edge.targetHandle}`)
  )
  const touchedNodes = new Set<string>()
  edges.value.forEach((edge) => {
    touchedNodes.add(edge.source)
    touchedNodes.add(edge.target)
  })

  for (const node of nodes.value) {
    const label = node.data?.label || node.id
    const inputs = node.data?.inputs || []

    for (const input of inputs) {
      if (input.required && !connectedTargets.has(`${node.id}::${input.name}`)) {
        issues.push({
          nodeId: node.id,
          label,
          message: `必需输入「${input.label || input.name}」未连接`,
          severity: 'error',
          code: 'missing_required_input'
        })
      }
    }

    if (nodes.value.length > 1 && !touchedNodes.has(node.id)) {
      issues.push({
        nodeId: node.id,
        label,
        message: '节点没有任何连线，不会被执行',
        severity: 'warning',
        code: 'isolated_node'
      })
    }
  }

  const hasEntry = nodes.value.some((node) => (node.data?.inputs || []).length === 0)
  if (!hasEntry) {
    issues.push({
      nodeId: '',
      label: '工作流',
      message: '没有无输入的起始节点，执行器将找不到入口',
      severity: 'error',
      code: 'no_entry_node'
    })
  }

  return issues
})

/**
 * 节点框重叠检查。
 *
 * 「流程图框重叠」是用户最直观的抱怨，但它既不属于服务端校验，也不属于
 * 原有的本地连线检查。这里按真实渲染尺寸算一次两两相交，并当成 warning
 * 汇入统一的问题列表，让画布角标和「检查」按钮都能提示到它。
 */
const overlappingNodeIds = computed(() => findOverlappingNodes(getOccupiedBoxes()))

const overlapValidationIssues = computed<CanvasValidationIssue[]>(() =>
  nodes.value
    .filter((node) => overlappingNodeIds.value.has(node.id))
    .map((node) => ({
      nodeId: node.id,
      label: node.data?.label || node.id,
      message: '节点与其他节点重叠，建议点击「自动排布」',
      severity: 'warning' as const,
      code: 'node_overlap'
    }))
)

const validationIssues = computed(() => {
  const serverIssueKeys = new Set(
    serverValidationIssues.value.map((issue) => `${issue.nodeId}:${issue.code || issue.message}`)
  )
  return [
    ...serverValidationIssues.value,
    ...localValidationIssues.value.filter(
      (issue) => !serverIssueKeys.has(`${issue.nodeId}:${issue.code || issue.message}`)
    ),
    ...overlapValidationIssues.value
  ]
})

/**
 * 每个节点上的问题角标数据，通过 provide 下发给 CustomNode。
 *
 * 复用同一份 validationIssues，节点上的角标与工具栏的计数永远一致。
 */
const nodeIssueSummary = computed(() => {
  const summary = new Map<string, { count: number; severity: 'error' | 'warning'; text: string }>()
  for (const issue of validationIssues.value) {
    if (!issue.nodeId) continue
    const existing = summary.get(issue.nodeId)
    if (existing) {
      existing.count += 1
      existing.text += `\n${issue.message}`
      if (issue.severity === 'error') existing.severity = 'error'
    } else {
      summary.set(issue.nodeId, { count: 1, severity: issue.severity, text: issue.message })
    }
  }
  return summary
})

provide('workflowNodeIssues', nodeIssueSummary)

/** 把视图移到指定节点并选中它，供角标列表与节点搜索共用 */
const focusNode = (nodeId: string) => {
  const node = nodes.value.find((item) => item.id === nodeId)
  if (!node) return
  removeSelectedNodes(getSelectedNodes.value)
  addSelectedNodes([node])
  setCenter(
    node.position.x + (node.dimensions?.width || FALLBACK_NODE_WIDTH) / 2,
    node.position.y + (node.dimensions?.height || FALLBACK_NODE_HEIGHT) / 2,
    { zoom: getViewport().zoom, duration: 300 }
  )
}

/** 问题清单弹窗：列出全部问题，点任意一条即可跳到对应节点 */
const showIssueListModal = ref(false)

const handleIssueJump = (nodeId: string) => {
  if (!nodeId) {
    message.info('该问题不指向具体节点')
    return
  }
  focusNode(nodeId)
  showIssueListModal.value = false
}

/** 导入时被丢弃的连线明细 */
const importDroppedWires = ref<string[]>([])
const showImportReportModal = ref(false)

// ==================== 画布内节点搜索 ====================
const showNodeSearch = ref(false)
const nodeSearchValue = ref<string | null>(null)

/** 节点数量多时靠搜索定位比在缩略图里找更快 */
const nodeSearchOptions = computed(() =>
  nodes.value.map((node) => ({
    label: `${node.data?.label || node.id}（${node.id}）`,
    value: node.id
  }))
)

const handleNodeSearchSelect = (nodeId: string) => {
  nodeSearchValue.value = null
  showNodeSearch.value = false
  focusNode(nodeId)
}

const toggleNodeSearch = () => {
  showNodeSearch.value = !showNodeSearch.value
  if (!showNodeSearch.value) {
    nodeSearchValue.value = null
  }
}

const createValidationPayload = () => {
  const fullWorkflowId = formValue.value.workflowId || viewState.value.workflowId || 'user:draft'
  const separator = fullWorkflowId.indexOf(':')
  const groupId = separator >= 0 ? fullWorkflowId.slice(0, separator) : 'user'
  const workflowId = separator >= 0 ? fullWorkflowId.slice(separator + 1) : fullWorkflowId
  return {
    group_id: groupId || 'user',
    workflow_id: workflowId || 'draft',
    name: formValue.value.name || viewState.value.name || '未命名工作流',
    description: formValue.value.description || viewState.value.description || '',
    blocks: convertNodesToBlocks(),
    wires: convertEdgesToWires(),
    config: mergeWorkflowConfig(formValue.value.config, viewState.value.config)
  }
}

const toCanvasValidationIssue = (issue: WorkflowValidationIssue): CanvasValidationIssue => {
  const node = nodes.value.find((item) => item.id === issue.node_name)
  return {
    nodeId: issue.node_name || '',
    label: node?.data?.label || issue.node_name || '工作流',
    message: issue.message,
    severity: issue.severity,
    code: issue.code
  }
}

/** 点击检查按钮：先调用后端的无副作用预检，再定位第一个问题。 */
const handleValidate = async () => {
  validationChecking.value = true
  serverValidationIssues.value = []
  try {
    // 与保存前相同，先让画布的最后一次拖拽、连线或配置编辑进入草稿。
    flushGraphData()
    const result = await validateWorkflow(createValidationPayload())
    serverValidationIssues.value = [...result.errors, ...result.warnings].map(toCanvasValidationIssue)
  } catch (error) {
    // 网络短暂失败时仍保留原有的本地即时检查，不会阻断编辑或保存。
    message.warning('未能完成服务端预检，已显示本地检查结果')
  } finally {
    validationChecking.value = false
  }

  if (validationIssues.value.length === 0) {
    message.success('检查通过，未发现问题')
    return
  }
  const first = validationIssues.value[0]
  if (first.nodeId) {
    focusNode(first.nodeId)
  }
  message.warning(
    `发现 ${validationIssues.value.length} 处问题，首个：${first.label} — ${first.message}`
  )
}

// 编辑信息处理函数
const handleEditInfo = () => {
  formValue.value = {
    workflowId: viewState.value.workflowId || '',
    name: viewState.value.name || '',
    description: viewState.value.description || '',
    config: viewState.value.config || {}
  }
  showSettingsModal.value = true
}

// ==================== 初始化函数 ====================
// 初始化图形数据
let _graphDataInitialized = false
// 类型兼容性表在一次会话内不变，但 initGraphData 会被 props 的 deep watch
// 反复触发。缓存这个 Promise，避免每次画布数据变动都重新请求一遍。
let _compatibilityPromise: Promise<Record<string, Record<string, boolean>>> | null = null
let compatibilityRetryTimer: ReturnType<typeof setTimeout> | null = null
const loadTypeCompatibility = () => {
  if (!_compatibilityPromise) {
    _compatibilityPromise = getTypeCompatibility().catch((error) => {
      // 失败后允许下一次重试，否则画布将永久失去连线校验能力
      _compatibilityPromise = null
      throw error
    })
  }
  return _compatibilityPromise
}

const refreshTypeCompatibility = () => {
  void loadTypeCompatibility()
    .then((compatibility) => {
      typeCompatibility.value = compatibility
      typeCompatibilityReady.value = true
      typeCompatibilityUnavailable.value = false
      if (compatibilityRetryTimer) {
        clearTimeout(compatibilityRetryTimer)
        compatibilityRetryTimer = null
      }
    })
    .catch(() => {
      typeCompatibilityReady.value = false
      typeCompatibilityUnavailable.value = true
      // 自动重试不重复弹窗；用户实际尝试连线时才给出精确且节流的提示。
      if (!compatibilityRetryTimer) {
        compatibilityRetryTimer = setTimeout(() => {
          compatibilityRetryTimer = null
          refreshTypeCompatibility()
        }, 5000)
      }
    })
}

const initGraphData = () => {
  // 恢复数据不能等待非关键的类型兼容性请求，否则接口短暂失败会让整个画布白屏。
  // 空白工作流同样必须初始化共享编辑器状态。此前只在存在节点/连线时
  // 初始化，导致从已有工作流切换到“新建工作流”后，单例仍保留上一张图。
  if (props.initialConfig) {
    if (_graphDataInitialized) return
    _graphDataInitialized = true
    // 更新 viewState
    intent.initialize({
      blocks: props.blocks,
      wires: props.wires,
      blockTypes: props.blockTypes,
      name: props.initialName,
      description: props.initialDescription,
      workflowId: props.initialWorkflowId,
      config: props.initialConfig
    })
    workflowEditorModel.performActionWithoutHistory(() => {
      restoreGraph()
    })
  }

  // 类型兼容性仅影响新连线的校验，后台加载失败时自动重试，且不因 props
  // 更新重复弹出警告。
  refreshTypeCompatibility()
}

// 初始化属性数据
const initPropertiesData = () => {
  viewState.value.name = props.initialName || ''
  viewState.value.description = props.initialDescription || ''
  viewState.value.workflowId = props.initialWorkflowId || ''
  viewState.value.config = mergeWorkflowConfig(props.initialConfig, viewState.value.config)

  formValue.value = {
    workflowId: props.initialWorkflowId || ':',
    name: props.initialName || '',
    description: props.initialDescription || '',
    config: mergeWorkflowConfig(props.initialConfig, formValue.value.config)
  }
  if (formValue.value.workflowId == ':') {
    formValue.value.workflowId =
      'user:' +
      Array.from({ length: 5 }, () => Math.floor(Math.random() * 36).toString(36)).join('')
  }
}

// ==================== 生命周期钩子和监听器 ====================

/**
 * props 回环判定。
 *
 * 画布 emit('update:blocks') → 父组件把同一个数组赋回 props → 深监听触发
 * initGraphData → 再次走一遍初始化，形成自激回路。每次编辑都要付一遍
 * 深比较 + refreshTypeCompatibility 的代价。
 * 这里按引用识别「刚才自己发出去的那一份」；只有真正来自外部的数据
 * （首次加载、路由切换、重新拉取）才继续走初始化。
 */
const isEchoOfOwnEmit = (
  blocksChanged: boolean,
  wiresChanged: boolean,
  blockTypesChanged: boolean
) =>
  !blockTypesChanged &&
  (!blocksChanged || (lastEmittedBlocks !== null && props.blocks === lastEmittedBlocks)) &&
  (!wiresChanged || (lastEmittedWires !== null && props.wires === lastEmittedWires))

// 监听 props 变化
// 只观察引用与长度，不做 deep 比较：blocks/wires 的内部编辑总是由画布自己
// 发起，父组件只会整体替换数组。
watch(
  [() => props.blocks, () => props.wires, () => props.blockTypes],
  ([blocks, wires, blockTypes], [previousBlocks, previousWires, previousBlockTypes]) => {
    if (
      isEchoOfOwnEmit(
        blocks !== previousBlocks,
        wires !== previousWires,
        blockTypes !== previousBlockTypes
      )
    )
      return
    initGraphData()
  }
)
watch([() => props.initialName, () => props.initialDescription, () => props.initialWorkflowId], initPropertiesData)
watch(
  () => props.initialConfig,
  (config) => {
    viewState.value.config = mergeWorkflowConfig(config, viewState.value.config)
    formValue.value.config = mergeWorkflowConfig(config, formValue.value.config)
  },
  { deep: true }
)

// 页面离开确认处理函数
const beforeunloadHandler = (event: BeforeUnloadEvent) => {
  event.preventDefault()
  event.returnValue = '您确定要离开此页面吗？未保存的更改可能会丢失。'
  return event.returnValue
}

/**
 * 键盘快捷键。
 *
 * 提取为具名函数是为了能在 onBeforeUnmount 里正确注销——原先用匿名函数
 * 注册在 document 上，组件卸载后监听器仍然存在，离开编辑器再回来会叠加，
 * 一次 Ctrl+Z 会触发多次撤销。
 * 另外在输入框/编辑器内按键时不拦截，避免影响正常文本编辑。
 */
const handleKeydown = (e: KeyboardEvent) => {
  const target = e.target as HTMLElement | null
  const isTextEntry =
    !!target &&
    (target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable ||
      target.closest('.monaco-editor') !== null)

  const ctrl = e.ctrlKey || e.metaKey

  if (ctrl && e.key.toLowerCase() === 'z') {
    if (isTextEntry) return
    e.preventDefault()
    if (e.shiftKey) {
      handleRedo()
    } else {
      handleUndo()
    }
  } else if (ctrl && e.key.toLowerCase() === 's') {
    // 保存要在任何位置都生效，包括正在编辑代码时
    e.preventDefault()
    handleSave()
  } else if (ctrl && e.key.toLowerCase() === 'l') {
    if (isTextEntry) return
    e.preventDefault()
    handleTidyLayout()
  } else if (ctrl && e.key === '0') {
    if (isTextEntry) return
    e.preventDefault()
    handleFitView()
  } else if (ctrl && e.key.toLowerCase() === 'd') {
    if (isTextEntry) return
    e.preventDefault()
    handleDuplicateSelection()
  } else if (ctrl && e.key.toLowerCase() === 'f') {
    // 大工作流里按名称找节点比在缩略图上找更快；沿用浏览器「查找」的肌肉记忆
    if (isTextEntry) return
    e.preventDefault()
    showNodeSearch.value = true
  }
}

// 组件挂载
onMounted(() => {
  // 注册自定义节点类型
  // const { addNodeTypes } = useVueFlow()
  // addNodeTypes({ custom: CustomNode })

  initGraphData()
  initPropertiesData()
  graphHistoryReady = true

  // 添加键盘快捷键
  document.addEventListener('keydown', handleKeydown)

  // 添加离开页面时的确认提示
  window.addEventListener('beforeunload', beforeunloadHandler)
})

// 组件卸载
onBeforeUnmount(() => {
  updateBlocks.cancel()
  updateWires.cancel()
  window.removeEventListener('beforeunload', beforeunloadHandler)
  document.removeEventListener('keydown', handleKeydown)
  if (compatibilityRetryTimer) {
    clearTimeout(compatibilityRetryTimer)
    compatibilityRetryTimer = null
  }
})

// 关闭节点配置面板
const closeNodeConfig = () => {
  removeSelectedNodes(getSelectedNodes.value)
}

// 添加拖放处理函数
const onDrop = (event: DragEvent) => {
  if (!event.dataTransfer) return

  const data = event.dataTransfer.getData('application/vueflow')
  if (!data) return

  try {
    const blockType = JSON.parse(data) as BlockType

    // 获取画布上的位置
    const { x, y } = project({ x: event.clientX, y: event.clientY })

    // 生成唯一 ID，和复制节点使用同一条命名规则。
    const newId = createUniqueNodeName(
      blockType.type_name,
      nodes.value.map((node) => node.id)
    )
    var type = 'custom'
    if (blockType.type_name == 'internal:code') {
      type = 'code'
    }

    // 原先直接落在光标处：既不对齐网格，也允许正好压在已有节点上。
    // 这里先对齐到与画布网格一致的 20px，再找一个不与既有节点重叠的空位。
    const droppedSize = { width: FALLBACK_NODE_WIDTH, height: FALLBACK_NODE_HEIGHT }
    const snapped = { x: snapToGrid(x), y: snapToGrid(y) }
    const position = findFreeNodePosition(snapped, droppedSize, getOccupiedBoxes())
    if (position.x !== snapped.x || position.y !== snapped.y) {
      message.info('落点已有节点，已自动挪到旁边的空位')
    }

    // 创建新节点
    const newNode = {
      id: newId,
      type: type,
      position,
      data: {
        label: blockType.label,
        blockType: blockType,
        config: {},
        inputs: blockType.inputs,
        outputs: blockType.outputs
      }
    }

    recordHistoryBeforeCanvasMutation()
    addNodes([newNode])
    updateBlocks()
  } catch (error) {
    console.error('添加节点失败:', error)
  }
}
</script>

<template>
  <div class="workflow-canvas">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      fit-view-on-init
      @nodes-change="handleNodesChange"
      @edges-change="handleEdgesChange"
      @edge-update="handleEdgeUpdate"
      @connect="handleConnect"
      :default-zoom="1"
      :min-zoom="0.2"
      :max-zoom="4"
      :snap-to-grid="true"
      class="vue-flow-canvas"
      @drop="onDrop"
      @dragover.prevent
    >
      <template #node-custom="customNodeProps">
        <CustomNode v-bind="customNodeProps" :isValidConnection="isValidConnection" />
      </template>
      <template #node-code="codeNdoeProps">
        <CodeNode v-bind="codeNdoeProps" :isValidConnection="isValidConnection" />
      </template>
      <Background :pattern-color="canvasDotColor" :gap="20" />
      <!--
        在 vue-flow 自带控件里再挂两个按钮：
        「自动排布」原先只有 Ctrl+L 和顶部工具栏图标，放在这里是因为缩放控件
        是用户调整视图时最先找的地方，重叠问题也正是在那时被发现的；
        「查找节点」则用于大工作流里快速定位。
      -->
      <Controls>
        <template #default>
          <ControlButton
            class="canvas-control-button"
            title="自动排布：按连线关系重排全部节点并消除重叠（Ctrl+L）"
            aria-label="自动排布"
            @click="handleTidyLayout"
          >
            <NIcon>
              <GitNetworkOutline />
            </NIcon>
          </ControlButton>
          <ControlButton
            class="canvas-control-button"
            title="查找节点：按名称快速跳转到画布上的节点"
            aria-label="查找节点"
            @click="toggleNodeSearch"
          >
            <NIcon>
              <SearchOutline />
            </NIcon>
          </ControlButton>
        </template>
      </Controls>
      <MiniMap
        pannable
        zoomable
        :node-color="minimapNodeColor"
        :mask-color="minimapMaskColor"
        class="workflow-minimap"
      />
      <Panel position="top-center" class="toolbar" :class="{ 'toolbar--shifted': !!selectedNode }">
        <NSpace :size="4" align="center">
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :loading="saving"
                @click="handleSave"
                class="toolbar-button"
                aria-label="保存工作流"
              >
                <template #icon>
                  <NIcon>
                    <SaveOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>保存工作流（Ctrl+S）</span>
          </NTooltip>

          <NDivider vertical class="toolbar-divider" />

          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :disabled="!viewState.canUndo"
                @click="handleUndo"
                class="toolbar-button"
                aria-label="撤销"
              >
                <template #icon>
                  <NIcon>
                    <ArrowUndoOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>撤销（Ctrl+Z）</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :disabled="!viewState.canRedo"
                @click="handleRedo"
                class="toolbar-button"
                aria-label="重做"
              >
                <template #icon>
                  <NIcon>
                    <ArrowRedoOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>重做（Ctrl+Shift+Z）</span>
          </NTooltip>

          <NDivider vertical class="toolbar-divider" />

          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :loading="tidying"
                @click="handleTidyLayout"
                class="toolbar-button"
                aria-label="整理布局"
              >
                <template #icon>
                  <NIcon>
                    <GitNetworkOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>整理布局：自动重排节点，消除重叠</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                @click="handleFitView"
                class="toolbar-button"
                aria-label="适应画布"
              >
                <template #icon>
                  <NIcon>
                    <ScanOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>缩放至显示全部节点</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :loading="validationChecking"
                :type="validationIssues.length > 0 ? 'warning' : 'default'"
                @click="handleValidate"
                class="toolbar-button"
                aria-label="检查工作流"
              >
                <template #icon>
                  <NIcon>
                    <CheckmarkCircleOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>
              检查工作流：{{
                validationIssues.length > 0 ? `发现 ${validationIssues.length} 处问题` : '当前无问题'
              }}
            </span>
          </NTooltip>

          <NDivider vertical class="toolbar-divider" />

          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :loading="importing"
                @click="handleImport"
                class="toolbar-button"
                aria-label="导入工作流"
              >
                <template #icon>
                  <NIcon>
                    <DownloadOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>从 JSON 文件导入</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                @click="handleExport"
                class="toolbar-button"
                aria-label="导出工作流"
              >
                <template #icon>
                  <NIcon>
                    <CloudUploadOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>导出为 JSON 文件</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                :loading="resetting"
                @click="handleReset"
                class="toolbar-button"
                aria-label="重置工作流"
              >
                <template #icon>
                  <NIcon>
                    <RefreshOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>重置：放弃未保存的修改并重新加载</span>
          </NTooltip>

          <NDivider vertical class="toolbar-divider" />

          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                @click="handleEditInfo"
                class="toolbar-button"
                aria-label="编辑工作流信息"
              >
                <template #icon>
                  <NIcon>
                    <SettingsOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>编辑工作流信息</span>
          </NTooltip>
          <NTooltip placement="bottom" trigger="hover">
            <template #trigger>
              <NButton
                quaternary
                circle
                @click="showShortcutsModal = true"
                class="toolbar-button"
                aria-label="快捷键说明"
              >
                <template #icon>
                  <NIcon>
                    <HelpCircleOutline />
                  </NIcon>
                </template>
              </NButton>
            </template>
            <span>快捷键说明</span>
          </NTooltip>
        </NSpace>
      </Panel>
      <Panel position="top-right" style="margin: 0; height: 100%">
        <NodeConfigPanel
          v-if="selectedNode"
          :selected-node="selectedNode"
          @close="closeNodeConfig"
          @before-node-mutation="handleNodeConfigMutation"
          :block-types="props.blockTypes"
          :type-compatibility="typeCompatibility"
        />
      </Panel>
      <Panel position="top-left" style="margin: 0; height: 100%">
        <NodeListPanel :block-types="props.blockTypes"></NodeListPanel>
      </Panel>
      <!-- 画布内节点搜索：由左下角控件里的放大镜切换显示 -->
      <Panel v-if="showNodeSearch" position="bottom-left" class="node-search-panel">
        <NSelect
          v-model:value="nodeSearchValue"
          filterable
          clearable
          size="small"
          placeholder="输入节点名称或 ID 后回车跳转"
          :options="nodeSearchOptions"
          :consistent-menu-width="false"
          @update:value="(value) => value && handleNodeSearchSelect(value)"
        />
      </Panel>
    </VueFlow>

    <!-- 设置对话框 -->
    <NModal
      v-model:show="showSettingsModal"
      preset="card"
      title="工作流设置"
      class="settings-modal"
      :style="{ width: 'min(600px, calc(100vw - 32px))' }"
    >
      <NForm
        ref="formRef"
        :model="formValue"
        :rules="formRules"
        label-placement="left"
        label-width="100"
        require-mark-placement="right-hanging"
        size="medium"
        class="settings-form"
      >
        <NFormItem label="工作流ID" path="workflowId">
          <NInput v-model:value="formValue.workflowId" placeholder="请输入 group_id:workflow_id" />
        </NFormItem>
        <NFormItem label="名称" path="name">
          <NInput v-model:value="formValue.name" placeholder="请输入工作流名称" />
        </NFormItem>
        <NFormItem label="描述" path="description">
          <NInput
            v-model:value="formValue.description"
            type="textarea"
            placeholder="请输入工作流描述"
          />
        </NFormItem>
        <NFormItem label="最大执行时间(秒)" path="config.max_execution_time">
          <NInputNumber
            v-model:value="formValue.config.max_execution_time"
            placeholder="执行超过此时间后将强制停止"
            :min="0"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showSettingsModal = false"> 取消 </NButton>
          <NButton type="primary" :loading="saving" @click="handleSave"> 保存 </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 快捷键说明对话框 -->
    <NModal
      v-model:show="showShortcutsModal"
      preset="card"
      title="快捷键与操作提示"
      class="settings-modal"
      :style="{ width: 'min(520px, calc(100vw - 32px))' }"
    >
      <NList hoverable>
        <NListItem v-for="item in shortcutHints" :key="item.keys">
          <div class="shortcut-row">
            <NTag size="small" :bordered="false" class="shortcut-key">{{ item.keys }}</NTag>
            <NText depth="2">{{ item.description }}</NText>
          </div>
        </NListItem>
      </NList>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showShortcutsModal = false">知道了</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 校验结果浮层：有问题时常驻左下角，点击可定位 -->
    <!-- 点击改为打开问题清单：原先只能跳到「第一个」问题，问题多时用户要反复
         点按钮才能逐个走过。清单里每条都能直接跳转，重新检查按钮仍在弹窗内。 -->
    <div
      v-if="validationIssues.length > 0"
      class="validation-badge"
      role="button"
      tabindex="0"
      @click="showIssueListModal = true"
      @keydown.enter="showIssueListModal = true"
      @keydown.space.prevent="showIssueListModal = true"
    >
      <NIcon size="16">
        <CheckmarkCircleOutline />
      </NIcon>
      <span>{{ validationIssues.length }} 处待处理问题</span>
    </div>

    <!-- 问题清单：逐条列出并支持跳转到对应节点 -->
    <NModal
      v-model:show="showIssueListModal"
      preset="card"
      title="待处理问题"
      class="settings-modal"
      :style="{ width: 'min(560px, calc(100vw - 32px))' }"
    >
      <NList hoverable clickable>
        <NListItem
          v-for="(issue, index) in validationIssues"
          :key="`${issue.nodeId}-${issue.code || index}`"
          @click="handleIssueJump(issue.nodeId)"
        >
          <div class="issue-row">
            <NIcon size="16" :class="issue.severity === 'error' ? 'issue-error' : 'issue-warning'">
              <WarningOutline />
            </NIcon>
            <div class="issue-text">
              <div class="issue-label">{{ issue.label }}</div>
              <NText depth="3">{{ issue.message }}</NText>
            </div>
            <NIcon v-if="issue.nodeId" size="16" class="issue-jump">
              <LocateOutline />
            </NIcon>
          </div>
        </NListItem>
      </NList>
      <template #footer>
        <NSpace justify="end">
          <NButton :loading="validationChecking" @click="handleValidate">重新检查</NButton>
          <NButton @click="showIssueListModal = false">关闭</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 导入结果：列出被丢弃的连线，便于手工补齐 -->
    <NModal
      v-model:show="showImportReportModal"
      preset="card"
      title="导入结果"
      class="settings-modal"
      :style="{ width: 'min(560px, calc(100vw - 32px))' }"
    >
      <NText depth="2">以下连线在当前版本中找不到对应端口，已跳过，其余内容导入成功：</NText>
      <NList>
        <NListItem v-for="item in importDroppedWires" :key="item">
          <NText code>{{ item }}</NText>
        </NListItem>
      </NList>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showImportReportModal = false">知道了</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 加载遮罩 -->
    <div v-if="props.loading" class="loading-overlay">
      <NSpin size="large" />
    </div>
  </div>
</template>

<style>
.workflow-canvas {
  width: 100vw;
  height: calc(100vh - 28px);
  position: fixed;
  top: 0;
  left: 0;
  background: var(--canvas-bg-color, var(--background-color));
  z-index: 2;
}

.vue-flow-canvas {
  width: 100%;
  height: 100%;
}

.toolbar {
  padding: 0.5rem;
  background: var(--panel-bg-color, rgba(255, 255, 255, 0.9));
  backdrop-filter: blur(10px);
  border-radius: var(--radius-md);
  box-shadow: var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.1));
  animation: slide-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex;
  justify-content: center;
  z-index: 100;
  /* 保留左右安全边距；窄屏通过横向滚动保持全部工具可达。 */
  max-width: calc(100vw - 48px);
  overflow-x: auto;
  overscroll-behavior-inline: contain;
  transition: margin-left 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

/*
 * 打开右侧节点配置面板（500px）时，居中的工具栏会压在面板标题上。
 * vue-flow 的 .vue-flow__panel.center 用 left:50% + translateX(-50%) 做居中，
 * 这里用 margin-left 叠加偏移（而不是覆盖 transform，那会破坏居中），
 * 左移半个面板宽度再多留 24px 间隙。
 */
.vue-flow__panel.toolbar--shifted {
  margin-left: -274px;
  max-width: calc(100vw - 600px);
}

.toolbar > * {
  margin: 0 0.5rem;
}

/* 工具栏分组竖线，让保存/历史/布局/文件/设置四组一眼可辨 */
/* naive-ui 用 .n-divider--vertical 设 margin: 0 8px，这里靠叠加类名把
   特异性提到它之上，不再需要 !important */
.toolbar .n-divider.toolbar-divider {
  height: 20px;
  margin: 0 2px;
}

/* 缩略图：定位在右下角，避开右侧配置面板 */
/* vue-flow 的 .vue-flow__minimap 自带白色底，叠加自身类名即可覆盖 */
.vue-flow__minimap.workflow-minimap {
  border-radius: var(--radius-sm);
  overflow: hidden;
  box-shadow: var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.1));
  background: var(--elevated-bg-color, #ffffff);
  border: 1px solid var(--border-color, #e5e7eb);
}

/* 快捷键说明的按键标签 */
.shortcut-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* naive-ui 的 .n-tag 用运行时注入的样式设底色，注入顺序不保证在本文件之后，
   所以叠加 .n-tag 把特异性提高一级，替代原来的 !important */
.n-tag.shortcut-key {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  background-color: var(--code-bg-color, #f3f4f6);
  color: var(--text-color, #333);
  min-width: 150px;
  justify-content: center;
}

/* 校验提示浮层 */
.validation-badge {
  position: absolute;
  left: 356px;
  bottom: 16px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  cursor: pointer;
  z-index: 110;
  color: var(--warning-color, #f0a020);
  background: var(--panel-bg-color, rgba(255, 255, 255, 0.9));
  border: 1px solid var(--warning-color, #f0a020);
  backdrop-filter: blur(10px);
  box-shadow: var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.1));
  transition: all 0.2s ease;
}

.validation-badge:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-hover, 0 6px 16px rgba(0, 0, 0, 0.16));
}

/* 问题清单的每一行 */
.issue-row {
  display: flex;
  align-items: center;
  gap: var(--space-2, 8px);
}

.issue-text {
  flex: 1 1 auto;
  min-width: 0;
}

.issue-label {
  font-size: var(--font-size-sm, 12px);
  font-weight: 500;
  color: var(--text-color, #333);
}

.issue-error {
  color: var(--error-color, #d03050);
}

.issue-warning {
  color: var(--warning-color, #f0a020);
}

.issue-jump {
  color: var(--text-color-tertiary, #999);
  flex-shrink: 0;
}

/* 画布内节点搜索：贴在左下角控件右侧，宽度足够显示中文节点名 */
.vue-flow__panel.node-search-panel {
  left: 48px;
  bottom: 16px;
  width: 260px;
  padding: var(--space-1, 4px);
  border-radius: var(--radius-md);
  background: var(--panel-bg-color, rgba(255, 255, 255, 0.9));
  box-shadow: var(--box-shadow-lg, var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.1)));
  backdrop-filter: blur(10px);
  z-index: 110;
}

/* 挂在 vue-flow 控件里的自定义按钮，图标尺寸与内建按钮对齐 */
.vue-flow__controls-button.canvas-control-button svg {
  width: 13px;
  height: 13px;
  max-width: 13px;
  max-height: 13px;
}

/* 右侧配置抽屉在中小屏会占据大半视口，固定偏移会把工具栏推到屏外。 */
@media (max-width: 1100px) {
  .toolbar,
  .vue-flow__panel.toolbar--shifted {
    width: calc(100vw - 24px);
    max-width: calc(100vw - 24px);
    margin-left: 0;
  }

  .toolbar > * {
    flex: 0 0 auto;
  }

  .validation-badge {
    left: 16px;
  }
}

@media (max-width: 640px) {
  .toolbar {
    padding: 0.375rem;
  }

  .toolbar > * {
    margin: 0 0.25rem;
  }

  .toolbar-divider {
    display: none;
  }

  .validation-badge {
    max-width: calc(100vw - 32px);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.toolbar-button {
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

.toolbar-button:hover {
  transform: translateY(-2px);
  background-color: rgba(var(--primary-color-rgb, 0, 122, 255), 0.1);
}

.toolbar-button:active {
  transform: translateY(0);
}

.settings-modal {
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.settings-form {
  margin: 1rem 0;
}

@keyframes slide-in {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }

  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: scale(0.95);
  }

  to {
    opacity: 1;
    transform: scale(1);
  }
}

.loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: var(--panel-bg-color, rgba(255, 255, 255, 0.8));
  z-index: 200;
  display: flex;
  justify-content: center;
  align-items: center;
}

.selected {
  box-shadow: 0 0 0 1px var(--primary-color, #007bff);
}

/* 添加边缘选中样式 */
.workflow-edge {
  transition: all 0.2s ease;
}

.workflow-edge.selected {
  filter: drop-shadow(0 0 3px rgba(var(--primary-color-rgb, 0, 123, 255), 0.5));
  animation: edge-blink 1.5s infinite;
}

@keyframes edge-blink {
  0% {
    filter: drop-shadow(0 0 3px rgba(var(--primary-color-rgb, 0, 123, 255), 0.2));
  }

  50% {
    filter: drop-shadow(0 0 5px rgba(var(--primary-color-rgb, 0, 123, 255), 1));
  }

  100% {
    filter: drop-shadow(0 0 3px rgba(var(--primary-color-rgb, 0, 123, 255), 0.2));
  }
}

/* vue-flow 内建控件跟随主题，避免深色下出现白色按钮块 */
.vue-flow__controls {
  box-shadow: var(--box-shadow, 0 4px 12px rgba(0, 0, 0, 0.1));
}

.vue-flow__controls-button {
  background: var(--elevated-bg-color, #ffffff);
  border-bottom: 1px solid var(--border-color, #eee);
  fill: var(--text-color-secondary, #666);
}

.vue-flow__controls-button:hover {
  background: var(--node-muted-bg, #f4f4f4);
  fill: var(--primary-color, #007bff);
}

.vue-flow__attribution {
  background: transparent;
  color: var(--text-color-tertiary, #999);
}
</style>
