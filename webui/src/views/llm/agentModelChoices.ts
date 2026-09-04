/**
 * Agent 编辑器里「模型优先链」与「Provider 白名单」的候选项。
 *
 * 为什么需要这一层
 * --------------
 * 那两格原来是纯文本框：用户得手打模型 ID（`gpt-5.6`）与后端名。而这些名字
 * **就在另一个页面上**——`GET /llm/backends` 返回每个后端及其 `models`，
 * 也就是「模型配置」页里看到的那些。
 *
 * 手打的后果不是「多打几个字」：模型 ID 拼错不会当场报错，Agent 保存成功，
 * 直到某次真实对话解析不到那个模型才失败——而那时看到的是运行时错误，
 * 与「我三天前拼错了一个字母」看不出关系。
 *
 * 为什么仍然允许自由输入
 * --------------------
 * 做成纯下拉会让两种正当用法变得不可能：模型来自一个**尚未登记**的后端；
 * 或者用户想先把 Agent 配好、再去配供应商。因此界面用可筛选可创建的选择器，
 * 这个模块只负责「给出候选」，不负责限制取值。
 */

import type { SelectMixedOption } from 'naive-ui/es/select/src/interface'

import type { LLMBackend } from '@/api/llm'

/**
 * 一个可选模型。
 *
 * 只含 `value` 与 `label` 两个键：naive-ui 的 `SelectMixedOption` 不接受额外
 * 字段，带上 `backends` / `enabled` 会让 `:options` 处的类型检查失败。
 * 那两项信息已经编进 `label`（`模型ID · 后端名`、未启用时加后缀），
 * 因此不需要再单独传给组件。
 */
export interface ModelChoice {
  /** 模型 ID，也就是要写进 `model_priority` 的值。 */
  value: string
  /** 下拉里显示的文字：`模型ID · 后端名`，未启用时带「（未启用）」。 */
  label: string
}

/** 汇总过程中的中间结构，不直接交给组件。 */
interface ModelAccumulator {
  value: string
  backends: string[]
  enabled: boolean
}

/** 判断一个模型能否用于对话。与后端 `LLMAbility.Chat`（1 << 1）一致。 */
const CHAT_ABILITY_BIT = 1 << 1

/**
 * 从后端列表汇总出可用于对话的模型候选。
 *
 * 三条规则，每条都有具体理由：
 *
 * 1. **只列能对话的模型。** `image_generation` 与 `embedding` 放进模型优先链，
 *    第一次对话必然失败，而报错指向「模型不支持对话」——与用户做过的事无关。
 *    判据同时看 `type === 'llm'` 与 Chat 能力位：`type` 是声明的类别，
 *    `ability` 是适配器真正宣称能做的事，只看前者会放进一个不能聊天的 llm。
 *
 * 2. **停用后端的模型仍然列出，但标注出来。** 直接过滤掉会让「我明明配过这个
 *    模型」变成一个找不到的东西；而不标注则会让用户选一个当前拿不到的模型。
 *
 * 3. **同一个模型 ID 只出现一次**，并记下所有提供它的后端。多个后端提供同一个
 *    模型是故障转移的正常形态，列成多条会让下拉里出现一串看起来重复的选项。
 */
export function collectModelChoices(backends: readonly LLMBackend[]): SelectMixedOption[] {
  const byId = new Map<string, ModelAccumulator>()

  for (const backend of backends) {
    for (const model of backend.models || []) {
      const id = String(model?.id ?? '').trim()
      if (!id) continue
      if (String(model?.type ?? '') !== 'llm') continue
      const ability = typeof model?.ability === 'number' ? model.ability : 0
      if (!(ability & CHAT_ABILITY_BIT)) continue

      const existing = byId.get(id)
      if (existing) {
        if (!existing.backends.includes(backend.name)) existing.backends.push(backend.name)
        existing.enabled = existing.enabled || Boolean(backend.enable)
      } else {
        byId.set(id, {
          value: id,
          backends: [backend.name],
          enabled: Boolean(backend.enable)
        })
      }
    }
  }

  // 排序：可用的排在前面，其余按模型 ID。可用与不可用混排会让用户在一长串
  // 里挑不出「现在就能跑」的那些。
  return [...byId.values()]
    .sort((left, right) => {
      if (left.enabled !== right.enabled) return left.enabled ? -1 : 1
      return left.value.localeCompare(right.value)
    })
    .map((choice) => ({
      value: choice.value,
      label: choice.enabled
        ? `${choice.value} · ${choice.backends.join(' / ')}`
        : `${choice.value} · ${choice.backends.join(' / ')}（未启用）`
    }))
}

/** Provider 白名单的候选项：所有后端名。 */
export interface ProviderChoice {
  value: string
  label: string
}

/**
 * 后端名候选。
 *
 * 与模型不同，这里**不过滤停用的后端**也不排序到后面：白名单是「允许用哪几家」
 * 这个策略声明，而一家后端今天停用、明天启用是常事——把它从白名单候选里拿掉，
 * 会让用户重新启用后忘记它还没进白名单。
 */
export function collectProviderChoices(backends: readonly LLMBackend[]): SelectMixedOption[] {
  const names = new Set<string>()
  for (const backend of backends) {
    const name = String(backend?.name ?? '').trim()
    if (name) names.add(name)
  }
  return [...names]
    .sort((left, right) => left.localeCompare(right))
    .map((name) => ({ value: name, label: name }))
}

/**
 * 把用户填的模型链与候选对比，给出「这些名字在当前配置里找不到」的清单。
 *
 * 用于在表单上提示而**不是阻止保存**：一个尚未登记的后端上的模型是合法取值，
 * 拒绝保存会让「先配 Agent 再配供应商」这个顺序不可能。但不提示也不行——
 * 拼错一个字母的后果是运行时失败，而那时的报错与拼写无关。
 */
export function unknownModels(
  models: readonly string[],
  choices: readonly SelectMixedOption[]
): string[] {
  const known = new Set(choices.map((choice) => String(choice.value)))
  const unknown: string[] = []
  for (const model of models) {
    const trimmed = String(model ?? '').trim()
    if (!trimmed || known.has(trimmed)) continue
    if (!unknown.includes(trimmed)) unknown.push(trimmed)
  }
  return unknown
}
