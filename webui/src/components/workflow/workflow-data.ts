import type { BlockInput, BlockOutput, BlockType } from '@/api/block'

type NamedBlock = {
  name: string
}

type WorkflowWire = {
  source_block: string
  target_block: string
}

type PortWire = WorkflowWire & {
  source_output: string
  target_input: string
}

export type WorkflowConnection = {
  source: string | null
  sourceHandle?: string | null
  target: string | null
  targetHandle?: string | null
}

export type WorkflowConnectionValidation =
  | { valid: true }
  | {
      valid: false
      reason:
        | 'missing_endpoint'
        | 'missing_source_block'
        | 'missing_target_block'
        | 'unknown_source_port'
        | 'unknown_target_port'
        | 'incompatible_types'
    }

/**
 * 一次连线被拒的原因，含节点侧独有的「输入端口已被占用」。
 *
 * `validateWorkflowConnection` 的六种 reason 之外还有一条规则只存在于节点组件里：
 * 一个输入端口只允许一条边。它此前 `return false` 了事，与类型不兼容的现象
 * 完全一样（线弹回来），用户无从判断该删掉已有的线还是该换端口。
 */
export type WorkflowConnectionRejectionReason =
  | Exclude<WorkflowConnectionValidation, { valid: true }>['reason']
  | 'input_already_connected'

const CONNECTION_REJECTION_MESSAGES: Record<WorkflowConnectionRejectionReason, string> = {
  // 每种拒绝都指出「哪里不对」和「下一步动哪里」。共用一句「类型不兼容」时，
  // 端口不存在与真正的类型冲突被混为一谈，而两者的处置完全不同。
  input_already_connected: '该输入端口已有连线，请先删除原有连线再连接',
  incompatible_types: '两端类型不兼容，请改用类型匹配的端口',
  unknown_source_port: '源节点上找不到这个输出端口，可能配置已变更，请刷新后重试',
  unknown_target_port: '目标节点上找不到这个输入端口，可能配置已变更，请刷新后重试',
  missing_source_block: '源节点不在当前画布中，请重新加载工作流',
  missing_target_block: '目标节点不在当前画布中，请重新加载工作流',
  missing_endpoint: '连线缺少端点信息，请重新拖动连接'
}

/** 把拒绝原因翻译成可操作的中文提示；未知原因也必须有话可说。 */
export const connectionRejectionMessage = (
  reason: WorkflowConnectionRejectionReason
): string =>
  CONNECTION_REJECTION_MESSAGES[reason] || '无法建立这条连线，请检查两端端口'

/**
 * 归并两条判定路径，给出唯一的拒绝原因；没有拒绝时返回 ``null``。
 *
 * 「输入已被占用」优先于类型判定：那条已有的连线是用户能直接看到并处理的东西，
 * 先告诉他类型问题只会让他去改一个本来没错的端口。
 */
export const findWorkflowConnectionRejection = (
  _connection: WorkflowConnection,
  state: {
    inputAlreadyConnected: boolean
    validation?: WorkflowConnectionValidation
  }
): WorkflowConnectionRejectionReason | null => {
  if (state.inputAlreadyConnected) return 'input_already_connected'
  if (state.validation && !state.validation.valid) return state.validation.reason
  return null
}

export type WorkflowConnectionPortIndex = {
  outputs: Map<string, Map<string, string>>
  inputs: Map<string, Map<string, string>>
}

type TypedBlock = {
  type_name: string
}

type ConfiguredBlock = NamedBlock & TypedBlock & {
  config?: Record<string, any>
}

export type WorkflowGraphIssue = {
  nodeId: string
  portName?: string
  code:
    | 'unknown_block_type'
    | 'unknown_source_port'
    | 'unknown_target_port'
    | 'missing_source_block'
    | 'missing_target_block'
    | 'code_node_without_ports'
  message: string
  severity: 'error' | 'warning'
}

export const WORKFLOW_TRANSFER_SCHEMA_VERSION = 1

export type WorkflowNodePosition = { x: number; y: number } | null | undefined

/**
 * 渲染层需要一个坐标，但原点本身是合法的持久化位置。调用方应通过 null /
 * undefined 判断是否要自动布局，不能依据这个临时回退坐标作判断。
 */
export const getRenderableNodePosition = (position: WorkflowNodePosition) =>
  position ? { x: position.x, y: position.y } : { x: 0, y: 0 }

type WorkflowTransferBlock = NamedBlock & TypedBlock & {
  config: Record<string, unknown>
}

type WorkflowTransferWire = WorkflowWire & {
  source_output: string
  target_input: string
}

export type WorkflowTransferPayload = {
  schema_version?: number
  name?: string
  description?: string
  workflow_id?: string
  config?: Record<string, unknown>
  blocks: WorkflowTransferBlock[]
  wires: WorkflowTransferWire[]
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/**
 * 校验导入文件的最小契约。
 *
 * 未携带 schema_version 的历史导出文件仍被视为兼容；新版导出必须声明当前
 * 版本，避免将未来格式在当前版本中静默错误地解释。
 */
export const parseWorkflowTransferPayload = (value: unknown): WorkflowTransferPayload => {
  if (!isRecord(value) || !Array.isArray(value.blocks) || !Array.isArray(value.wires)) {
    throw new Error('导入文件必须包含 blocks 和 wires 数组')
  }

  if (
    value.schema_version !== undefined &&
    value.schema_version !== WORKFLOW_TRANSFER_SCHEMA_VERSION
  ) {
    throw new Error(`不支持的工作流导入版本：${String(value.schema_version)}`)
  }

  const blocks = value.blocks as WorkflowTransferBlock[]
  if (
    blocks.some(
      (block) =>
        !isRecord(block) ||
        typeof block.name !== 'string' ||
        !block.name ||
        typeof block.type_name !== 'string' ||
        !block.type_name ||
        !isRecord(block.config)
    )
  ) {
    throw new Error('导入文件包含无效的节点定义')
  }

  const blockNames = new Set(blocks.map((block) => block.name))
  if (blockNames.size !== blocks.length) {
    throw new Error('导入文件中的节点名称重复')
  }

  const wires = value.wires as WorkflowTransferWire[]
  if (
    wires.some(
      (wire) =>
        !isRecord(wire) ||
        typeof wire.source_block !== 'string' ||
        typeof wire.source_output !== 'string' ||
        typeof wire.target_block !== 'string' ||
        typeof wire.target_input !== 'string'
    )
  ) {
    throw new Error('导入文件包含无效的连线定义')
  }

  if (value.config !== undefined && !isRecord(value.config)) {
    throw new Error('导入文件中的工作流配置无效')
  }

  return value as WorkflowTransferPayload
}

export const filterWiresForBlocks = <TWire extends WorkflowWire, TBlock extends NamedBlock>(
  wires: TWire[],
  blocks: TBlock[]
): TWire[] => {
  const blockNames = new Set(blocks.map((block) => block.name))
  return wires.filter(
    (wire) => blockNames.has(wire.source_block) && blockNames.has(wire.target_block)
  )
}

/** Convert the persistent wire model without losing dynamic handle identity. */
export const workflowWireToConnection = (wire: PortWire): WorkflowConnection => ({
  source: wire.source_block,
  sourceHandle: wire.source_output,
  target: wire.target_block,
  targetHandle: wire.target_input
})

/** Reject incomplete new connections, while allowing explicit preservation of legacy handles. */
export const connectionToWorkflowWire = (
  connection: WorkflowConnection,
  options: { preserveIncompleteHandles?: boolean } = {}
): PortWire => {
  if (!connection.source || !connection.target) {
    throw new Error('连线必须包含源节点、源端口、目标节点和目标端口')
  }
  if (
    !options.preserveIncompleteHandles &&
    (!connection.sourceHandle || !connection.targetHandle)
  ) {
    throw new Error('连线必须包含源节点、源端口、目标节点和目标端口')
  }
  return {
    source_block: connection.source,
    source_output: connection.sourceHandle || '',
    target_block: connection.target,
    target_input: connection.targetHandle || ''
  }
}

/** Keep the existing edge identity stable for Vue Flow selection and update behavior. */
export const createWorkflowEdgeId = (connection: WorkflowConnection) =>
  `${connection.source}-${connection.sourceHandle}-${connection.target}-${connection.targetHandle}`

export const mergeWorkflowConfig = <TConfig extends object>(
  config: TConfig | undefined,
  fallback: TConfig
): TConfig => ({ ...fallback, ...config })

export const getUnknownBlockTypes = <TBlock extends TypedBlock, TBlockType extends TypedBlock>(
  blocks: TBlock[],
  blockTypes: TBlockType[]
): string[] => {
  const knownTypes = new Set(blockTypes.map((blockType) => blockType.type_name))
  return [...new Set(blocks.map((block) => block.type_name).filter((typeName) => !knownTypes.has(typeName)))]
}

const asPort = (port: any, direction: 'input' | 'output'): BlockInput | BlockOutput | null => {
  if (!port || typeof port.name !== 'string' || !port.name) return null
  const common = {
    name: port.name,
    label: typeof port.label === 'string' && port.label ? port.label : port.name,
    description: typeof port.description === 'string' ? port.description : '',
    type: typeof port.type === 'string' && port.type ? port.type : 'unknown'
  }
  return direction === 'input'
    ? { ...common, required: port.required === true }
    : common
}

/**
 * The block type whose ports are declared per instance rather than on the class.
 *
 * `CodeBlock` builds its ports in `__init__` from the `inputs`/`outputs` config
 * lists, so `/block/types` reports zero ports for it and the canvas has to read
 * the instance config instead.
 */
export const CODE_BLOCK_TYPE_NAME = 'internal:code'

/**
 * The runtime data type of every custom-script port.
 *
 * `CodeBlock.__init__` types each port `Any`, and the backend type system treats
 * `Any` as compatible with everything. The config panel still lets the user pick
 * a label-level type (default `str`) for readability and handle color, but that
 * choice must never be used to refuse a connection the runtime would accept.
 */
export const CODE_BLOCK_PORT_RUNTIME_TYPE = 'Any'

const getConfiguredPorts = (block: ConfiguredBlock, direction: 'input' | 'output') => {
  const key = direction === 'input' ? 'inputs' : 'outputs'
  return Array.isArray(block.config?.[key])
    ? block.config[key].map((port) => asPort(port, direction)).filter(Boolean)
    : []
}

/**
 * Return metadata safe for rendering even when the server no longer exposes a
 * saved block type. The original type name is deliberately kept in the
 * placeholder so a save/import round trip cannot turn it into undefined.
 */
export const getCanvasBlockType = (
  block: ConfiguredBlock,
  blockTypes: BlockType[]
): BlockType => {
  const knownType = blockTypes.find((blockType) => blockType.type_name === block.type_name)
  if (knownType) return knownType

  return {
    type_name: block.type_name,
    name: block.type_name,
    label: `节点类型不可用：${block.type_name}`,
    description: '服务端未提供此节点类型的元数据；原节点与连线已保留，修复类型后即可继续编辑。',
    color: '#64748b',
    inputs: getConfiguredPorts(block, 'input') as BlockInput[],
    outputs: getConfiguredPorts(block, 'output') as BlockOutput[],
    configs: []
  }
}

/**
 * Get the ports that must exist in the canvas. Historical wires may reference
 * a port removed from current metadata, so add a visible diagnostic port for
 * those handles instead of rendering a line that ends in empty space.
 */
export const getCanvasBlockPorts = (
  block: ConfiguredBlock,
  blockType: BlockType,
  wires: PortWire[] = []
) => {
  const inputs = (
    blockType.type_name === CODE_BLOCK_TYPE_NAME
      ? getConfiguredPorts(block, 'input')
      : blockType.inputs
  ).slice() as BlockInput[]
  const outputs = (
    blockType.type_name === CODE_BLOCK_TYPE_NAME
      ? getConfiguredPorts(block, 'output')
      : blockType.outputs
  ).slice() as BlockOutput[]

  const inputNames = new Set(inputs.map((port) => port.name))
  const outputNames = new Set(outputs.map((port) => port.name))
  for (const wire of wires) {
    if (wire.target_block === block.name && !inputNames.has(wire.target_input)) {
      inputs.push({
        name: wire.target_input,
        label: `未知输入：${wire.target_input}`,
        description: '当前节点元数据中不存在此端口；保留用于诊断历史连线。',
        type: 'unknown',
        required: false
      })
      inputNames.add(wire.target_input)
    }
    if (wire.source_block === block.name && !outputNames.has(wire.source_output)) {
      outputs.push({
        name: wire.source_output,
        label: `未知输出：${wire.source_output}`,
        description: '当前节点元数据中不存在此端口；保留用于诊断历史连线。',
        type: 'unknown'
      })
      outputNames.add(wire.source_output)
    }
  }
  return { inputs, outputs }
}

/** Build the reusable lookup used by both canvas drag checks and pure validation. */
export const createWorkflowConnectionPortIndex = (
  blocks: ConfiguredBlock[],
  blockTypes: BlockType[]
): WorkflowConnectionPortIndex => {
  const outputs = new Map<string, Map<string, string>>()
  const inputs = new Map<string, Map<string, string>>()

  for (const block of blocks) {
    const ports = getCanvasBlockPorts(block, getCanvasBlockType(block, blockTypes))
    // A custom-script port is `Any` at runtime; the type stored on the port is a
    // display hint, so validating against it would reject valid connections.
    const portType = (declared: string) =>
      block.type_name === CODE_BLOCK_TYPE_NAME ? CODE_BLOCK_PORT_RUNTIME_TYPE : declared
    outputs.set(
      block.name,
      new Map(ports.outputs.map((port) => [port.name, portType(port.type)]))
    )
    inputs.set(
      block.name,
      new Map(ports.inputs.map((port) => [port.name, portType(port.type)]))
    )
  }

  return { outputs, inputs }
}

/**
 * Validate direction and type against the same dynamic port model used for rendering.
 * A missing compatibility table means the caller deliberately chose existence-only
 * validation; an available table must explicitly allow the source/target pair.
 */
export const validateWorkflowConnection = (
  connection: WorkflowConnection,
  blocks: ConfiguredBlock[],
  blockTypes: BlockType[],
  compatibility?: Record<string, Record<string, boolean>>,
  portIndex?: WorkflowConnectionPortIndex
): WorkflowConnectionValidation => {
  if (
    !connection.source ||
    !connection.sourceHandle ||
    !connection.target ||
    !connection.targetHandle
  ) {
    return { valid: false, reason: 'missing_endpoint' }
  }

  const index = portIndex || createWorkflowConnectionPortIndex(blocks, blockTypes)
  if (!index.outputs.has(connection.source)) {
    return { valid: false, reason: 'missing_source_block' }
  }
  if (!index.inputs.has(connection.target)) {
    return { valid: false, reason: 'missing_target_block' }
  }

  const sourceType = index.outputs.get(connection.source)?.get(connection.sourceHandle)
  if (!sourceType) return { valid: false, reason: 'unknown_source_port' }

  const targetType = index.inputs.get(connection.target)?.get(connection.targetHandle)
  if (!targetType) return { valid: false, reason: 'unknown_target_port' }

  if (compatibility && compatibility[sourceType]?.[targetType] !== true) {
    // `Any` is universally compatible in the backend type system
    // (`TypeSystem.is_compatible` returns True as soon as either side is `Any`).
    // Honor that here so a table that predates a dynamically typed port — a
    // custom-script port is always `Any` — cannot refuse a valid connection.
    if (
      sourceType !== CODE_BLOCK_PORT_RUNTIME_TYPE &&
      targetType !== CODE_BLOCK_PORT_RUNTIME_TYPE
    ) {
      return { valid: false, reason: 'incompatible_types' }
    }
  }
  return { valid: true }
}

/** Diagnose graph data that cannot be represented by current block metadata. */
export const getWorkflowGraphIssues = (
  blocks: ConfiguredBlock[],
  wires: PortWire[],
  blockTypes: BlockType[]
): WorkflowGraphIssue[] => {
  const blockTypeByName = new Map(blockTypes.map((blockType) => [blockType.type_name, blockType]))
  const issues: WorkflowGraphIssue[] = []

  for (const block of blocks) {
    if (!blockTypeByName.has(block.type_name)) {
      issues.push({
        nodeId: block.name,
        code: 'unknown_block_type',
        message: `节点类型「${block.type_name}」当前不可用，已显示为占位节点`,
        severity: 'error'
      })
    }

    // A freshly added custom-script node inherits the empty class-level port
    // list, so it renders without a single handle and cannot be wired. That is
    // a deliberate dynamic-port boundary on the backend, but leaving it silent
    // makes it look like the node is broken or the wires were dropped.
    if (block.type_name === CODE_BLOCK_TYPE_NAME) {
      const ports = getCanvasBlockPorts(block, getCanvasBlockType(block, blockTypes))
      if (ports.inputs.length === 0 && ports.outputs.length === 0) {
        issues.push({
          nodeId: block.name,
          code: 'code_node_without_ports',
          message: '自定义脚本节点尚未定义端口，因此无法连线；请在节点配置面板添加输入或输出端口',
          severity: 'warning'
        })
      }
    }
  }

  for (const wire of wires) {
    const source = blocks.find((block) => block.name === wire.source_block)
    const target = blocks.find((block) => block.name === wire.target_block)
    if (!source) {
      issues.push({
        nodeId: '',
        code: 'missing_source_block',
        message: `连线引用了不存在的源节点「${wire.source_block}」`,
        severity: 'error'
      })
    }
    if (!target) {
      issues.push({
        nodeId: '',
        code: 'missing_target_block',
        message: `连线引用了不存在的目标节点「${wire.target_block}」`,
        severity: 'error'
      })
    }
    if (!source || !target) continue

    const sourceType = getCanvasBlockType(source, blockTypes)
    const targetType = getCanvasBlockType(target, blockTypes)
    const sourcePorts = getCanvasBlockPorts(source, sourceType).outputs
    const targetPorts = getCanvasBlockPorts(target, targetType).inputs

    if (!sourcePorts.some((port) => port.name === wire.source_output)) {
      issues.push({
        nodeId: source.name,
        portName: wire.source_output,
        code: 'unknown_source_port',
        message: `连线引用了不存在的输出端口「${wire.source_output}」`,
        severity: 'warning'
      })
    }
    if (!targetPorts.some((port) => port.name === wire.target_input)) {
      issues.push({
        nodeId: target.name,
        portName: wire.target_input,
        code: 'unknown_target_port',
        message: `连线引用了不存在的输入端口「${wire.target_input}」`,
        severity: 'warning'
      })
    }
  }

  return issues
}
