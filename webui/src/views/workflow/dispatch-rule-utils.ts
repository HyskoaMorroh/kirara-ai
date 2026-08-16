import { deepClone } from '@/utils/deep-clone'

/**
 * 规则编辑器必须使用独立草稿。规则包含嵌套的 rule_groups/config/metadata，
 * 浅拷贝会让“取消”仍然污染列表中的原规则。
 *
 * 深拷贝本身由 `@/utils/deep-clone` 唯一实现（原先这里的 JSON 克隆与
 * store/workflow-editor 的 structuredClone 是两份会漂移的实现）；这里保留
 * 该导出名，语义上说明“为什么规则需要克隆”。
 */
export const cloneDispatchRule = <TRule extends object>(rule: TRule): TRule => deepClone(rule)
