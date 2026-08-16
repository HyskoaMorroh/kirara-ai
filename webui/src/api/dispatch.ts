import { http } from '@/utils/http'

export interface SimpleRule {
  type: string
  config: Record<string, any> // 规则类型特定的配置
}

export interface RuleGroup {
  operator: 'and' | 'or'
  rules: SimpleRule[]
}

export interface DispatchRule {
  rule_id: string
  name: string
  description: string
  workflow_id: string
  priority: number
  enabled: boolean
  rule_groups: RuleGroup[] // 规则组列表，组之间是 AND 关系
  metadata: Record<string, any> // 其他元数据
}

export interface DispatchRuleConfigSchema {
  configSchema: Record<string, any>
  error?: string
}

export type DispatchPreviewDecision =
  | 'selected'
  | 'shadowed'
  | 'not_matched'
  | 'indeterminate'
  | 'disabled'

/**
 * 一条规则在真实调度顺序中的位置与遮蔽状态，由后端
 * `workflow.core.dispatch.reachability` 唯一计算。
 *
 * 前端不再自行推导「哪条规则永远不会被触发」：这套语义只要有两份实现就必然
 * 漂移，而调度器以后端为准。
 */
export interface DispatchRuleReachability {
  rule_id: string
  name: string
  workflow_id: string
  priority: number
  enabled: boolean
  /** 从 1 开始的匹配次序；只统计已启用的规则，已禁用规则为 null */
  order: number | null
  /** 该规则本身是否为无条件规则（会拦下所有消息） */
  catch_all: boolean
  /** 排在某条已启用的无条件规则之后，对任何消息都不会被判断到 */
  unreachable: boolean
  /** 造成不可达的那条无条件规则 ID */
  shadowed_by_rule_id: string | null
}

export interface DispatchReachabilityResponse {
  reachability: DispatchRuleReachability[]
}

export interface DispatchPreviewInput {
  content: string
  chat_type: '私聊' | '群聊'
  sender_id: string
  group_id?: string | null
  mentioned: boolean
  draft_rule?: DispatchRule
}

export interface DispatchPreviewRuleResult {
  rule_id: string
  name: string
  workflow_id: string
  priority: number
  enabled: boolean
  matched: boolean | null
  decision: DispatchPreviewDecision
  explanation: Record<string, any>
  /** 从 1 开始的匹配次序，与 /dispatch/reachability 一致；已禁用规则为 null */
  order: number | null
  catch_all: boolean
  unreachable: boolean
  shadowed_by_rule_id: string | null
}

export interface DispatchPreviewResponse {
  selected_rule_id: string | null
  selected_workflow_id: string | null
  rules: DispatchPreviewRuleResult[]
}

export const dispatchApi = {
  // 获取所有可用的规则类型
  getRuleTypes: () => {
    return http.get<{ types: string[] }>('/dispatch/types')
  },

  // 获取所有规则
  getRules: () => {
    return http.get<{ rules: DispatchRule[]; reachability: DispatchRuleReachability[] }>(
      '/dispatch/rules'
    )
  },

  // 获取规则配置模式
  getRuleConfigSchema: (type: string) => {
    return http.get<DispatchRuleConfigSchema>(`/dispatch/types/${type}/config-schema`)
  },

  // 静态可达性分析：不需要示例消息，用于编辑草稿时的即时遮蔽反馈。
  analyzeReachability: (draftRule?: DispatchRule) => {
    return http.post<DispatchReachabilityResponse>('/dispatch/reachability', {
      draft_rule: draftRule ?? null
    })
  },

  // 试运行只解释规则顺序与条件结果，不会执行工作流或保存规则。
  previewRules: (input: DispatchPreviewInput) => {
    return http.post<DispatchPreviewResponse>('/dispatch/preview', input)
  },

  // 创建规则
  createRule: (rule: Partial<DispatchRule>) => {
    return http.post<{ rule: DispatchRule }>('/dispatch/rules', rule)
  },

  // 更新规则
  updateRule: (ruleId: string, rule: Partial<DispatchRule>) => {
    return http.put<{ rule: DispatchRule }>(`/dispatch/rules/${ruleId}`, rule)
  },

  // 删除规则
  deleteRule: (ruleId: string) => {
    return http.delete(`/dispatch/rules/${ruleId}`)
  },

  // 启用规则
  enableRule: (ruleId: string) => {
    return http.post(`/dispatch/rules/${ruleId}/enable`)
  },

  // 禁用规则
  disableRule: (ruleId: string) => {
    return http.post(`/dispatch/rules/${ruleId}/disable`)
  }
}

const _ruleTypeLabels = {
  prefix: '以……开头',
  regex: '正则表达式',
  keyword: '包含……词',
  random: '以……概率',
  sender: '发送者为……',
  sender_mismatch: '发送者不为……',
  bot_mention: '被@',
  chat_type: '聊天类型',
  im_instance: 'IM实例',
  fallback: '任意输入'
}
export const getRuleTypeLabel = (type: string) => {
  return _ruleTypeLabels[type as keyof typeof _ruleTypeLabels] || type
}

/**
 * 后端 `DispatchPreviewRuleResult.decision` 的全部取值。
 *
 * 与 `DispatchPreviewDecision` 联合类型同源：类型在编译期擦除，运行时的测试
 * 需要一个可枚举的集合来校验标签映射的完整性（后端新增判定类型而这里没加标签，
 * 界面会渲染出 undefined）。
 */
export const DISPATCH_PREVIEW_DECISIONS = [
  'selected',
  'shadowed',
  'not_matched',
  'indeterminate',
  'disabled'
] as const satisfies readonly DispatchPreviewDecision[]

/**
 * 试运行判定的展示文案与色彩。
 *
 * 与 `getRuleTypeLabel` 放在一起：两者都是「后端枚举 → 中文标签」的同一类映射，
 * 原先拆到 views 下的独立微模块反而让人以为它们是两套无关的东西。
 */
const previewDecisionLabels: Record<DispatchPreviewDecision, string> = {
  selected: '将执行',
  shadowed: '匹配但被前序规则截断',
  not_matched: '未命中',
  indeterminate: '无法确定',
  disabled: '已禁用'
}

export const getDispatchPreviewDecisionLabel = (decision: DispatchPreviewDecision) =>
  previewDecisionLabels[decision]

export const getDispatchPreviewDecisionType = (decision: DispatchPreviewDecision) => {
  switch (decision) {
    case 'selected':
      return 'success'
    case 'shadowed':
      return 'warning'
    case 'indeterminate':
      return 'info'
    case 'not_matched':
    case 'disabled':
      return 'default'
  }
}
