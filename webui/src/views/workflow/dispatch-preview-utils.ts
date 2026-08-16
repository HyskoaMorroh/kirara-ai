/**
 * 兼容层：判定标签的唯一实现已移到 `@/api/dispatch`，与 `getRuleTypeLabel`
 * 这类同源的「后端枚举 → 中文标签」映射放在一起。此处仅保留原导入路径。
 */
export {
  DISPATCH_PREVIEW_DECISIONS,
  getDispatchPreviewDecisionLabel,
  getDispatchPreviewDecisionType
} from '@/api/dispatch'
