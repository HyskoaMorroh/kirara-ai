// 需求 22.2：定价的「生效时间」必须能填对，而不是填完才被后端拒绝。
//
// `effective_from` 在后端是 `datetime` 且**强制要求带时区**
// （`llm/pricing.py` 的 `require_timezone` 校验器直接 raise）。前端却是一个
// 裸 `<input v-model>`：没有 type、没有校验、没有格式提示。于是用户按最自然的
// 写法填 `2026-01-01 00:00`，点保存，拿到一个 4xx——而错误信息来自 pydantic，
// 说的是 `effective_from must include a timezone`。
//
// 这类缺陷的形态是「能填，但填对要靠猜」：界面既不阻止错的写法，也不说明对的
// 写法。而定价填错的后果不是报错了事——一个生效时间落在错误时刻的版本会让
// 之后所有请求按错误价格计费，且**没有任何症状**，直到有人去核对账单。
//
// 这些用例钉住三件事：接受什么、拒绝什么、以及拒绝时说不说得出下一步。

import { describe, expect, it } from 'vitest'

import {
  defaultEffectiveFrom,
  normalizeEffectiveFrom
} from '../src/views/llm/pricing-effective-from'

describe('normalizeEffectiveFrom', () => {
  it('accepts a full ISO-8601 timestamp with a UTC offset', () => {
    const result = normalizeEffectiveFrom('2026-01-01T00:00:00Z')

    expect(result.ok).toBe(true)
    if (result.ok) {
      // 归一化成后端一定能解析的形态，而不是把用户的原样字符串直接送出去。
      expect(result.value).toBe(new Date('2026-01-01T00:00:00Z').toISOString())
    }
  })

  it('accepts a numeric offset such as +08:00', () => {
    const result = normalizeEffectiveFrom('2026-01-01T08:00:00+08:00')

    expect(result.ok).toBe(true)
    if (result.ok) {
      // +08:00 的 08:00 就是 UTC 的 00:00：归一化必须保住这个事实。
      expect(result.value).toBe(new Date('2026-01-01T00:00:00Z').toISOString())
    }
  })

  it('rejects a local timestamp with no timezone at all', () => {
    // 这是用户最自然的写法，也是后端唯一会拒绝的那一类。
    const result = normalizeEffectiveFrom('2026-01-01 00:00')

    expect(result.ok).toBe(false)
    if (!result.ok) {
      // 必须说出「缺时区」这件事本身，而不是笼统的「格式错误」：
      // 后者会让人去改年月日的写法，而那一半本来是对的。
      expect(result.error).toContain('时区')
    }
  })

  it('rejects a date without a time of day', () => {
    // `2026-01-01` 会被 Date 解析成 UTC 午夜——看起来成功了，
    // 实际是我们替用户选了一个他没写的时刻，而定价的生效时刻不该被猜。
    const result = normalizeEffectiveFrom('2026-01-01')

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error.length).toBeGreaterThan(0)
    }
  })

  it('rejects text that is not a timestamp', () => {
    const result = normalizeEffectiveFrom('下周一')

    expect(result.ok).toBe(false)
  })

  it('rejects an empty value instead of silently using now', () => {
    // 留空时替用户填「现在」等于悄悄决定了计费起点。
    const result = normalizeEffectiveFrom('   ')

    expect(result.ok).toBe(false)
  })

  it('rejects a calendar-shaped string that is not a real date', () => {
    // `2026-02-30` 在 Date 里是 Invalid Date，但它长得完全像一个合法输入。
    const result = normalizeEffectiveFrom('2026-02-30T00:00:00Z')

    expect(result.ok).toBe(false)
  })
})

describe('defaultEffectiveFrom', () => {
  it('produces a value its own validator accepts', () => {
    // 默认值必须自己就能过校验，否则「什么都不改直接保存」会失败——
    // 那是新建定价时最常见的一次操作。
    const result = normalizeEffectiveFrom(defaultEffectiveFrom())

    expect(result.ok).toBe(true)
  })
})
