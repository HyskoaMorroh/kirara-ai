// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import { fieldPlaceholder, isReadOnlyField } from '../src/components/form/fieldPresentation'

/**
 * 占位符的优先级必须是 `examples` > `default`。
 *
 * 发现过程：用户指着模型配置页的 `Api Base` 问「这个网址为何不能修改」。
 * 那一格显示 `https://api.openai.com/v1` 是**占位符**（字段本身可编辑，
 * 后端 `OpenAIConfig.api_base` 声明了这个默认值），不是已填的值。
 *
 * 顺着那行代码读下去发现一个真缺陷：
 *
 *     property.examples?.[0] || (property.default !== undefined && ...)
 *       ? String(property.default) : ''
 *
 * `||` 的优先级高于 `?:`，整个条件被求成 `(examples?.[0] || default存在)`，
 * 为真时**一律**取 `String(property.default)`——`examples` 永远不会被用到。
 *
 * 它不报错、不白屏，只是所有给了示例值的字段都在显示默认值。而 `examples`
 * 恰恰是「照这样填」的提示（`owner/name` 之于一个仓库坐标字段），
 * `default` 是「不填就用这个」——后者不需要提示，因为它反正会生效。
 */

describe('占位符优先级', () => {
  it('两者都有时用 examples——这是修复的核心', () => {
    // 坏版本在这一条上返回 'DEFAULT'。
    expect(fieldPlaceholder({ examples: ['EXAMPLE'], default: 'DEFAULT' })).toBe('EXAMPLE')
  })

  it('只有 default 时用 default', () => {
    expect(fieldPlaceholder({ default: 'https://api.openai.com/v1' })).toBe(
      'https://api.openai.com/v1'
    )
  })

  it('只有 examples 时用 examples', () => {
    expect(fieldPlaceholder({ examples: ['owner/name'] })).toBe('owner/name')
  })

  it('都没有时返回空串，而不是 "undefined"', () => {
    // `String(undefined)` 是 'undefined'，直接显示给用户是一个假的提示值。
    expect(fieldPlaceholder({})).toBe('')
    expect(fieldPlaceholder(null)).toBe('')
    expect(fieldPlaceholder(undefined)).toBe('')
  })
})

describe('假值必须被当作有效值', () => {
  it('default 为 false 时显示 "false"', () => {
    // 用 `||` 判断会跳过它，而一个默认关闭的开关是完全正常的声明。
    expect(fieldPlaceholder({ default: false })).toBe('false')
  })

  it('default 为 0 时显示 "0"', () => {
    expect(fieldPlaceholder({ default: 0 })).toBe('0')
  })

  it('examples 里的 0 同样有效', () => {
    expect(fieldPlaceholder({ examples: [0], default: 999 })).toBe('0')
  })

  it('空串不算有值，回落到下一级', () => {
    // 空占位符与「没有占位符」在界面上是同一回事，
    // 但它不该挡住 default 那一级。
    expect(fieldPlaceholder({ examples: [''], default: 'DEFAULT' })).toBe('DEFAULT')
  })

  it('examples 为空数组时回落到 default', () => {
    expect(fieldPlaceholder({ examples: [], default: 'DEFAULT' })).toBe('DEFAULT')
  })

  it('examples 不是数组时不抛错', () => {
    // schema 由后端生成，但这个组件也渲染插件声明的 schema。
    expect(fieldPlaceholder({ examples: 'not-an-array' as never, default: 'D' })).toBe('D')
  })

  it('null 元素跳过', () => {
    expect(fieldPlaceholder({ examples: [null], default: 'D' })).toBe('D')
  })
})

describe('只读判定', () => {
  it('只认显式 true', () => {
    expect(isReadOnlyField({ readOnly: true })).toBe(true)
  })

  it('没声明时字段可编辑', () => {
    // 把「没声明」当成只读，会让一整页表单静默变成不可填，
    // 而用户看到的是一个有值、改不动的输入框。
    expect(isReadOnlyField({})).toBe(false)
    expect(isReadOnlyField(null)).toBe(false)
  })

  it('字符串 "false" 不算只读', () => {
    // JSON Schema 里这个键该是布尔，但手写的 schema 会出现字符串。
    // 按真值判断会把 "false" 当成只读——那是反的。
    expect(isReadOnlyField({ readOnly: 'false' as never })).toBe(false)
  })

  it('字符串 "true" 也不算只读', () => {
    // 严格只认布尔 true：接受字符串会让上一条的反例重新变得可能。
    expect(isReadOnlyField({ readOnly: 'true' as never })).toBe(false)
  })
})

describe('组件真的接上了这两个函数', () => {
  it('DynamicConfigForm 用的是共享实现', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, resolve } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(
      resolve(here, '../src/components/form/DynamicConfigForm.vue'),
      'utf-8'
    )

    expect(source).toMatch(/from '\.\/fieldPresentation'/)
    expect(source).toMatch(/placeholder: fieldPlaceholder\(property\)/)
    expect(source).toMatch(/disabled: isReadOnlyField\(property\)/)
    // 坏表达式不能再留在 placeholder 那一处。
    // 只查 `placeholder:` 那一行：文件里另外两处 `value || examples?.[0] || default`
    // 是纯 `||` 回落链（没有 `?:`），不受优先级影响，是正确的写法。
    expect(source).not.toMatch(/placeholder:[\s\S]{0,120}examples\?\.\[0\] \|\|/)
  })
})
