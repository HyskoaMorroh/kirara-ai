import { describe, expect, it } from 'vitest'
import { useLatestRequest } from '../src/composables/useLatestRequest'

describe('useLatestRequest', () => {
  it('aborts the previous request and accepts only the latest generation', () => {
    const requests = useLatestRequest()
    const first = requests.begin()
    const second = requests.begin()

    expect(first.signal.aborted).toBe(true)
    expect(second.signal.aborted).toBe(false)
    expect(requests.isCurrent(first.generation)).toBe(false)
    expect(requests.isCurrent(second.generation)).toBe(true)
  })

  it('invalidates non-abortable promises when cancelled', () => {
    const requests = useLatestRequest()
    const pending = requests.begin()

    requests.cancel()

    expect(pending.signal.aborted).toBe(true)
    expect(requests.isCurrent(pending.generation)).toBe(false)
  })

  it('keeps generations monotonic across cancellation', () => {
    const requests = useLatestRequest()
    const first = requests.begin()
    requests.cancel()
    const second = requests.begin()

    expect(second.generation).toBeGreaterThan(first.generation)
    expect(requests.isCurrent(second.generation)).toBe(true)
  })
})
