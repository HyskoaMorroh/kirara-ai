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
    blockType.type_name === 'internal:code'
      ? getConfiguredPorts(block, 'input')
      : blockType.inputs
  ).slice() as BlockInput[]
  const outputs = (
    blockType.type_name === 'internal:code'
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
