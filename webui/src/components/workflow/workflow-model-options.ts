type SelectOption = {
  value: unknown
}

/**
 * n-select 的 `value` 能接受的取值。
 *
 * 与 naive-ui 的 `Value`（`es/select/src/interface`）一致，另外允许 `null`
 * 表示「槽位为空」——本模块正是靠返回 null 把一个已失效的模型显示成空槽。
 */
export type SelectSlotValue = string | number | Array<string | number> | null

const MODEL_SLOT_NAME = /^(model_name|fallback_model_[1-4])$/

/**
 * Keep an unavailable configured model out of the visual select value while
 * retaining the raw workflow draft. This makes an expired primary/fallback
 * appear as an empty slot and prevents unrelated edits from erasing it.
 */
export const getVisibleModelSlotValue = (
  configName: string,
  rawValue: unknown,
  options: SelectOption[]
): SelectSlotValue => {
  // 返回类型不是 unknown：这个值直接绑给 n-select 的 `value`，而它只接受
  // string / number / 二者的数组 / null。返回 unknown 会把类型检查推到模板层，
  // 那里只能靠断言解决，于是「真的返回了别的东西」这个问题也一起被吞掉。
  if (!MODEL_SLOT_NAME.test(configName) || rawValue === null || rawValue === undefined) {
    return toSelectSlotValue(rawValue)
  }

  return options.some((option) => option.value === rawValue) ? toSelectSlotValue(rawValue) : null
}

/**
 * 把任意草稿值收窄成 n-select 能接受的形状。
 *
 * 工作流草稿是自由 JSON，槽位里可能残留对象或布尔——那些在下拉里既选不中也
 * 显示不出来，落成 null（空槽）比原样传给组件更接近真实可用性。
 */
function toSelectSlotValue(value: unknown): SelectSlotValue {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' || typeof value === 'number') return value
  if (Array.isArray(value)) {
    const atoms = value.filter(
      (item): item is string | number => typeof item === 'string' || typeof item === 'number'
    )
    return atoms.length === value.length ? atoms : null
  }
  return null
}
