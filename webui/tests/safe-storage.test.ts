import { describe, expect, it } from 'vitest'
import { readJsonRecord, readStorageItem, writeStorageItem } from '../src/utils/safe-storage'

describe('safe storage helpers', () => {
  it('falls back safely when browser storage is unavailable or malformed', () => {
    const unavailable = {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      }
    }

    expect(readStorageItem(unavailable, 'hideGuide')).toBeNull()
    expect(readJsonRecord(unavailable, 'completedSteps')).toEqual({})
    expect(() => writeStorageItem(unavailable, 'hideGuide', 'true')).not.toThrow()
  })

  it('returns an empty record for invalid saved JSON', () => {
    expect(readJsonRecord({ getItem: () => '{invalid' }, 'completedSteps')).toEqual({})
  })
})
