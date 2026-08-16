/**
 * 生成工作流节点的稳定唯一名称。
 *
 * 区块类型使用 `group:name` 形式；画布拖入、键盘添加和复制节点都应采用同一
 * 命名规则，避免把完整 type_name 误写进节点名称或产生重复名称。
 */
export const createUniqueNodeName = (
  typeName: string,
  existingNames: Iterable<string>
): string => {
  const suffix = typeName.split(':').filter(Boolean).pop() || 'node'
  const names = new Set(existingNames)

  if (!names.has(suffix)) return suffix

  let index = 1
  let candidate = `${suffix}_${index}`
  while (names.has(candidate)) {
    index += 1
    candidate = `${suffix}_${index}`
  }
  return candidate
}
