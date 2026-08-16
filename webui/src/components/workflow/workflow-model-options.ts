type SelectOption = {
  value: unknown
}

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
): unknown => {
  if (!MODEL_SLOT_NAME.test(configName) || rawValue === null || rawValue === undefined) {
    return rawValue
  }

  return options.some((option) => option.value === rawValue) ? rawValue : null
}
