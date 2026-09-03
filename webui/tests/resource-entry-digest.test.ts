// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import { entryDigestMatches } from '../src/views/resources/entryDigest'

/**
 * 「你看到的正文与运行时载入的是同一份」这个论断，按**行为**验证。
 *
 * 替换的是 `resource-entry-content.test.ts` 里的
 * `expect(viewSource).toContain('entryDigestMatches')`——那只证明这个名字存在。
 *
 * 判断错的两个方向都很糟：
 *
 * - 该不匹配时说匹配 → 用户以为看到的就是运行时那份，而磁盘文件已被篡改；
 * - 该匹配时说不匹配 → 一个完好的资源被显示成可疑，用户去排查一个不存在的问题。
 *
 * 因此这里的默认答案是「不成立」：缺任何输入都返回 false，
 * 而不是「没发现问题所以算通过」。
 */

const DIGEST = 'a'.repeat(64)
const OTHER = 'b'.repeat(64)

describe('摘要一致', () => {
  it('版本与摘要都对得上时成立', () => {
    expect(
      entryDigestMatches(
        { version: '1.0.0', content_sha256: DIGEST },
        [{ version: '1.0.0', content_sha256: DIGEST }]
      )
    ).toBe(true)
  })

  it('多版本时按版本号取那一条再比', () => {
    expect(
      entryDigestMatches({ version: '2.0.0', content_sha256: OTHER }, [
        { version: '1.0.0', content_sha256: DIGEST },
        { version: '2.0.0', content_sha256: OTHER }
      ])
    ).toBe(true)
  })

  it('摘要不同时不成立', () => {
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: OTHER }, [
        { version: '1.0.0', content_sha256: DIGEST }
      ])
    ).toBe(false)
  })

  it('摘要碰巧等于**别的版本**时也不成立', () => {
    // 只看「有没有哪一版的摘要等于它」会把「读到的是旧版正文」误判成一致。
    // 必须先按版本号取记录。
    expect(
      entryDigestMatches({ version: '2.0.0', content_sha256: DIGEST }, [
        { version: '1.0.0', content_sha256: DIGEST },
        { version: '2.0.0', content_sha256: OTHER }
      ])
    ).toBe(false)
  })

  it('注册表里没有这个版本时不成立', () => {
    expect(
      entryDigestMatches({ version: '9.9.9', content_sha256: DIGEST }, [
        { version: '1.0.0', content_sha256: DIGEST }
      ])
    ).toBe(false)
  })
})

describe('缺输入时默认不成立', () => {
  it('没读到正文时不成立', () => {
    // 「还没读到」不是「一致」。
    expect(entryDigestMatches(null, [{ version: '1.0.0', content_sha256: DIGEST }])).toBe(false)
    expect(entryDigestMatches(undefined, [{ version: '1.0.0', content_sha256: DIGEST }])).toBe(
      false
    )
  })

  it('没有版本列表时不成立', () => {
    expect(entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST }, null)).toBe(false)
    expect(entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST }, undefined)).toBe(
      false
    )
  })

  it('空版本列表不成立', () => {
    expect(entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST }, [])).toBe(false)
  })

  it('两边摘要都为空时不成立', () => {
    // `'' === ''` 是真。按真处理等于把「两边都不知道摘要」说成「校验通过」——
    // 这是这个函数里最容易写错的一处。
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: '' }, [
        { version: '1.0.0', content_sha256: '' }
      ])
    ).toBe(false)
  })

  it('任一边摘要为空都不成立', () => {
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: '' }, [
        { version: '1.0.0', content_sha256: DIGEST }
      ])
    ).toBe(false)
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST }, [
        { version: '1.0.0', content_sha256: '' }
      ])
    ).toBe(false)
  })
})

describe('比较是精确的', () => {
  it('大小写不同不算一致', () => {
    // SHA-256 十六进制串在这个项目里一律小写（后端 `_SHA256_PATTERN` 要求）。
    // 放宽成不区分大小写会掩盖一个真实的不一致来源。
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST.toUpperCase() }, [
        { version: '1.0.0', content_sha256: DIGEST }
      ])
    ).toBe(false)
  })

  it('前缀相同但长度不同不算一致', () => {
    expect(
      entryDigestMatches({ version: '1.0.0', content_sha256: DIGEST.slice(0, 32) }, [
        { version: '1.0.0', content_sha256: DIGEST }
      ])
    ).toBe(false)
  })
})
