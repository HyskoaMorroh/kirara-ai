// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  RESOURCE_ID_PATTERN,
  VERSION_PREFIX_PATTERN,
  authoringFormError,
  suggestNextVersion
} from '../src/views/resources/documentAuthoring'

/**
 * 「从纯文本创建提示词」的表单校验必须按**行为**验证，不是 grep 源码。
 *
 * 这组用例存在的直接原因：两处版本号正则曾写成 `/^d+.d+.d+/`（丢了反斜杠）。
 * 它是合法正则，不报任何错，只是永远匹配不上——
 * `/^d+.d+.d+/.test('1.0.0')` 是 `false`。
 *
 * 后果不是「校验松了」而是**整条路不可用**：`authoringError` 对任何输入都返回
 * 「版本号需形如 1.0.0」，保存按钮永远拦下；`suggestNextVersion('1.0.0')`
 * 永远回落到 `'1.0.1'`，从 `2.3.4` 编辑时建议出一个比当前更小的版本号，
 * 而后端要求严格递增——用户会看到「版本必须递增」这个与他没做错任何事无关的错误。
 *
 * 当时这一页的测试全绿：它们 `expect(viewSource).toMatch(...)` 检查源码里
 * 有没有那一行字符串。字符串在，行为不在。所以这份测试**调用函数**。
 */

describe('版本号建议', () => {
  it('patch 位 +1', () => {
    expect(suggestNextVersion('1.0.0')).toBe('1.0.1')
    expect(suggestNextVersion('2.3.4')).toBe('2.3.5')
  })

  it('两位数的 patch 不被截断', () => {
    // 字符串拼接容易写成 `+ '1'`，那会把 9 变成 91。
    expect(suggestNextVersion('1.2.9')).toBe('1.2.10')
    expect(suggestNextVersion('1.2.19')).toBe('1.2.20')
  })

  it('带预发布后缀时只看前三段', () => {
    expect(suggestNextVersion('3.3.0-b14')).toBe('3.3.1')
  })

  it('解析不出来时退回 1.0.1，而不是抛错', () => {
    for (const value of ['', '   ', 'main', 'v1', '1.0']) {
      expect(suggestNextVersion(value), `${value} 应退回 1.0.1`).toBe('1.0.1')
    }
  })

  it('建议值必然大于当前版本——后端要求严格递增', () => {
    // 这一条是上面那个缺陷的直接判据：丢了反斜杠时，从 2.3.4 编辑
    // 会建议 1.0.1，比当前更小，保存必然被后端拒绝。
    for (const current of ['1.0.0', '2.3.4', '10.0.1', '3.3.0']) {
      const next = suggestNextVersion(current)
      const parse = (value: string) => value.split('.').map(Number)
      const [aMajor, aMinor, aPatch] = parse(current)
      const [bMajor, bMinor, bPatch] = parse(next)
      const bigger =
        bMajor > aMajor ||
        (bMajor === aMajor && bMinor > aMinor) ||
        (bMajor === aMajor && bMinor === aMinor && bPatch > aPatch)
      expect(bigger, `${current} -> ${next} 不是递增`).toBe(true)
    }
  })
})

describe('版本号正则真的匹配版本号', () => {
  it('接受三段数字', () => {
    for (const value of ['1.0.0', '10.20.30', '3.3.0-b14', '1.0.0+build']) {
      expect(VERSION_PREFIX_PATTERN.test(value), `${value} 应被接受`).toBe(true)
    }
  })

  it('拒绝不是版本号的输入', () => {
    for (const value of ['', 'd.d.d', '1.0', 'v1.0.0', 'abc']) {
      expect(VERSION_PREFIX_PATTERN.test(value), `${value} 不该被接受`).toBe(false)
    }
  })
})

describe('资源 ID 正则与后端同一套', () => {
  it('接受常见形态', () => {
    for (const value of ['prompt.mine', 'a', 'a-b_c.d', 'A1']) {
      expect(RESOURCE_ID_PATTERN.test(value), `${value} 应被接受`).toBe(true)
    }
  })

  it('拒绝会成为危险路径段的形态', () => {
    // ID 会成为磁盘路径的一段（`resources/installed/<id>/<version>`）。
    for (const value of ['', '.hidden', 'a/b', 'a\\b', '..', 'a b', 'a.', '-a', 'a-']) {
      expect(RESOURCE_ID_PATTERN.test(value), `${value} 不该被接受`).toBe(false)
    }
  })

  it('长度上限与后端一致（128）', () => {
    expect(RESOURCE_ID_PATTERN.test('a'.repeat(128))).toBe(true)
    expect(RESOURCE_ID_PATTERN.test('a'.repeat(129))).toBe(false)
  })
})

describe('表单校验', () => {
  const form = (over: Partial<Parameters<typeof authoringFormError>[0]> = {}) => ({
    resource_id: 'prompt.mine',
    content: '先给结论。\n',
    version: '1.0.0',
    ...over
  })

  it('填齐了就放行——这是这组测试的核心断言', () => {
    // 丢反斜杠的那个版本在这一条上会红：它对任何输入都返回错误。
    expect(authoringFormError(form(), { editing: false })).toBe('')
    expect(authoringFormError(form(), { editing: true })).toBe('')
  })

  it('空正文拦下', () => {
    expect(authoringFormError(form({ content: '' }), { editing: false })).toBe('正文不能为空')
    expect(authoringFormError(form({ content: '   \n' }), { editing: false })).toBe('正文不能为空')
  })

  it('版本号不合法时拦下', () => {
    expect(authoringFormError(form({ version: '1.0' }), { editing: false })).toBe(
      '版本号需形如 1.0.0'
    )
  })

  it('版本号首尾空白不算错', () => {
    expect(authoringFormError(form({ version: '  1.0.0  ' }), { editing: false })).toBe('')
  })

  it('新建时校验 ID', () => {
    expect(authoringFormError(form({ resource_id: '' }), { editing: false })).toBe(
      '资源 ID 不能为空'
    )
    expect(authoringFormError(form({ resource_id: 'a/b' }), { editing: false })).toBe(
      '资源 ID 只能含字母、数字、点、下划线与连字符'
    )
  })

  it('编辑时不校验 ID——那时它不可改', () => {
    // 报一个用户改不了的字段的错，等于给出一个无法照做的指示。
    expect(authoringFormError(form({ resource_id: '' }), { editing: true })).toBe('')
  })

  it('正文优先于版本号报错——先说最要紧的那个', () => {
    expect(authoringFormError(form({ content: '', version: 'x' }), { editing: false })).toBe(
      '正文不能为空'
    )
  })
})
