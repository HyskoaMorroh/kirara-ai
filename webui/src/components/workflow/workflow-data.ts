type NamedBlock = {
  name: string
}

type WorkflowWire = {
  source_block: string
  target_block: string
}

type TypedBlock = {
  type_name: string
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
