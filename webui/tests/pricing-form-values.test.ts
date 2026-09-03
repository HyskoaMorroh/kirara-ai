// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import { copyVersion, pricingLabel } from '../src/views/llm/pricingForm'

/**
 * 定价表单的提交前处理，按**行为**验证。
 *
 * 替换的是 `pricing-display-name.test.ts` 里那条把写法钉住的断言：
 *
 *     toMatch(/copyVersion[\s\S]{0,400}display_name\s*=\s*label\s*\?\s*label\s*:\s*null/)
 *
 * 它无法区分「改好了」与「改坏了」：重构成 `label || null` 它红，
 * 把条件写反成 `label ? null : label` 它也红——两种情况同一个信号。
 *
 * 这一步错了的后果很具体：后端 `reject_blank_display_name` 对空白标签直接拒绝，
 * 不转换就是 400，而用户看到的是一条与他所做无关的校验错误。
 */

const version = (over: Record<string, unknown> = {}) => ({
  version_id: 'v1',
  provider: 'openai',
  model: 'gpt-5.6',
  display_name: '',
  effective_from: '2026-09-01',
  currency: 'USD',
  input_per_million: '1',
  output_per_million: '2',
  cache_read_per_million: '0',
  cache_write_per_million: '0',
  ...over
})

describe('提交前整理', () => {
  it('没填时转成 null，而不是空串', () => {
    // 这是这个函数存在的全部理由。空串会被后端拒绝。
    expect(copyVersion(version({ display_name: '' })).display_name).toBeNull()
  })

  it('纯空白也算没填', () => {
    for (const value of ['   ', '\t', '\n', ' \n ']) {
      expect(copyVersion(version({ display_name: value })).display_name).toBeNull()
    }
  })

  it('null 与 undefined 保持为 null', () => {
    expect(copyVersion(version({ display_name: null })).display_name).toBeNull()
    expect(copyVersion(version({ display_name: undefined })).display_name).toBeNull()
  })

  it('填了就保留，并去掉首尾空白', () => {
    // 条件写反（`label ? null : label`）时这一条红——而那正是上面那条
    // 源码 grep 断言区分不出来的情形。
    expect(copyVersion(version({ display_name: '  正式版  ' })).display_name).toBe('正式版')
  })

  it('中间的空格不动', () => {
    expect(copyVersion(version({ display_name: 'GPT 5.6 正式版' })).display_name).toBe(
      'GPT 5.6 正式版'
    )
  })

  it('只有 0 这种「假值字符串」也算填了', () => {
    // `label || null` 对 `'0'` 是安全的（非空字符串为真），
    // 但把判断写成 `Number(label) ? ...` 就会把它吞掉。
    expect(copyVersion(version({ display_name: '0' })).display_name).toBe('0')
  })

  it('其余字段逐字不动', () => {
    // 只该动一个键。多动一个就是一次用户没做过的改动被写进定价目录。
    const input = version({ display_name: 'x' })
    const output = copyVersion(input)
    for (const key of Object.keys(input)) {
      if (key === 'display_name') continue
      expect(output[key as keyof typeof output], `${key} 被改动了`).toBe(
        input[key as keyof typeof input]
      )
    }
  })

  it('返回新对象，不原地改', () => {
    // 原地改会让表单里那个输入框在提交瞬间变成 null，
    // 用户看到自己刚填的字消失了。
    const input = version({ display_name: '   ' })
    const output = copyVersion(input)
    expect(output).not.toBe(input)
    expect(input.display_name).toBe('   ')
  })
})

describe('表格里显示什么名字', () => {
  it('有显示名用显示名', () => {
    expect(pricingLabel({ display_name: '正式版', model: 'gpt-5.6' })).toBe('正式版')
  })

  it('没有显示名回落到模型标识，而不是空白', () => {
    // 这正是 pricing display_name 那个缺陷的形态：后端一直返回它、
    // 前端类型没声明，标签永远回落到 id。回落本身是对的，
    // 空白才是不可接受的——它读起来像「这一行没有数据」。
    expect(pricingLabel({ display_name: null, model: 'gpt-5.6' })).toBe('gpt-5.6')
    expect(pricingLabel({ display_name: '', model: 'gpt-5.6' })).toBe('gpt-5.6')
    expect(pricingLabel({ display_name: '   ', model: 'gpt-5.6' })).toBe('gpt-5.6')
  })

  it('两者都没有时返回空串而不抛错', () => {
    expect(pricingLabel({})).toBe('')
  })
})
