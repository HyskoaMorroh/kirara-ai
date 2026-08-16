import { describe, expect, it } from 'vitest'

import { getVisibleModelSlotValue } from '../src/components/workflow/workflow-model-options'

describe('getVisibleModelSlotValue', () => {
  const options = [
    { label: 'Current model', value: 'current-model' },
    { label: 'Backup model', value: 'backup-model' }
  ]

  it('keeps a configured model visible when it is still detected', () => {
    expect(getVisibleModelSlotValue('model_name', 'current-model', options)).toBe('current-model')
  })

  it('renders an unavailable configured model slot as empty without changing its raw value', () => {
    expect(getVisibleModelSlotValue('fallback_model_3', 'retired-model', options)).toBeNull()
  })

  it('does not alter non-model select values', () => {
    expect(getVisibleModelSlotValue('response_format', 'legacy-format', options)).toBe('legacy-format')
  })
})
