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

  it.each([
    ['3.3.0b8', '3.3.0b11'],
    ['3.3.0b9', '3.3.0b10'],
    ['3.3.0a9', '3.3.0a12'],
    ['3.3.0rc2', '3.3.0rc10']
  ])(
    // semver 把 `b11` 当成一个字母数字标识符按字典序比较，于是 `b11 < b8`。
    // PEP 440 的序号是数字，`b11` 必须大于 `b8`。两位数序号一出现，
    // 「有新版本」的判断就会反向：用户装着 b11，界面提示他升级到 b8。
    'orders a two-digit prerelease above a one-digit one: %s < %s',
    (older, newer) => {
      expect(version.compare(older, newer)).toBe(-1)
      expect(version.compare(newer, older)).toBe(1)
    }
  )

  it.each([
    ['3.3.0a5', '3.3.0b1'],
    ['3.3.0b11', '3.3.0rc1'],
    ['3.3.0rc1', '3.3.0']
  ])('keeps the release-stage order: %s < %s', (older, newer) => {
    expect(version.compare(older, newer)).toBe(-1)
    expect(version.compare(newer, older)).toBe(1)
  })

  it('treats an identical version as equal', () => {
    expect(version.compare('3.3.0b11', '3.3.0b11')).toBe(0)
  })

  it('still compares npm-style spellings of the same release', () => {
    expect(version.compare('3.3.0-b11', '3.3.0b11')).toBe(0)
    expect(version.compare('3.3.0-b8', '3.3.0b11')).toBe(-1)
  })

  it('does not claim an update relationship for development identities', () => {
    expect(version.compare('3.3.0-b8', 'dev-abc1234')).toBe(0)
  })
})
