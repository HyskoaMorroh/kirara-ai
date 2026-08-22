import { describe, expect, it } from 'vitest'
import {
  hideQuickStartGuide,
  isQuickStartGuideVisible,
  resetQuickStartGuideProgress,
  showQuickStartGuide,
  shouldShowQuickStartRestore
} from '../src/views/guide/guide-visibility'

const createStorage = () => {
  const values = new Map<string, string>()

  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value)
  }
}

describe('quick start guide visibility', () => {
  it('is visible by default and can be restored after being hidden', () => {
    const storage = createStorage()

    expect(isQuickStartGuideVisible(storage)).toBe(true)

    hideQuickStartGuide(storage)
    expect(isQuickStartGuideVisible(storage)).toBe(false)

    showQuickStartGuide(storage)
    expect(isQuickStartGuideVisible(storage)).toBe(true)
  })

  it('keeps the guide usable when browser storage is unavailable', () => {
    const unavailable = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      }
    }

    expect(isQuickStartGuideVisible(unavailable)).toBe(true)
    expect(() => hideQuickStartGuide(unavailable)).not.toThrow()
    expect(() => showQuickStartGuide(unavailable)).not.toThrow()
  })

  it('keeps the restore entry visible after every guide step is complete', () => {
    expect(shouldShowQuickStartRestore(false)).toBe(true)
    expect(shouldShowQuickStartRestore(true)).toBe(false)
  })

  it('resets only the guide progress and keeps the guide visible', () => {
    const storage = createStorage()
    storage.setItem('completedSteps', JSON.stringify({ plugins: true, im: true }))
    storage.setItem('hideGuide', 'false')

    resetQuickStartGuideProgress(storage)

    expect(storage.getItem('completedSteps')).toBe('{}')
    expect(isQuickStartGuideVisible(storage)).toBe(true)
  })
})
