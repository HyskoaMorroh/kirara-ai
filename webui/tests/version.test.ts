import { describe, expect, it } from 'vitest'
import { normalizeAppVersion, version } from '@/utils/version'

describe('normalizeAppVersion', () => {
  it.each([
    ['v3.3.0b7', '3.3.0-b7'],
    ['3.3.0a7', '3.3.0-a7'],
    ['3.3.0rc1', '3.3.0-rc1'],
    ['v3.3.0', '3.3.0'],
    ['3.3.0-beta.2', '3.3.0-beta.2']
  ])('normalizes %s as %s', (rawVersion, expected) => {
    expect(normalizeAppVersion(rawVersion)).toBe(expected)
  })

  it.each(['dev-abc1234', 'unknown'])('keeps an explicit non-release identity: %s', (rawVersion) => {
    expect(normalizeAppVersion(rawVersion)).toBe(rawVersion)
  })

  it('does not disguise a missing identity as version 0.0.0', () => {
    expect(normalizeAppVersion('')).toBe('unknown')
  })
})

describe('version.compare', () => {
  it('compares PEP 440 prerelease strings after normalization', () => {
    expect(version.compare('3.3.0b8', '3.3.0b7')).toBe(1)
  })

  it('does not claim an update relationship for development identities', () => {
    expect(version.compare('3.3.0-b8', 'dev-abc1234')).toBe(0)
  })
})

