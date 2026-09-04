/**
 * 动态配置表单的字段呈现规则。
 *
 * 抽成独立模块是因为这里有一处**没有症状**的缺陷：占位符的表达式原来写成
 *
 *     property.examples?.[0] || (property.default !== undefined && property.default !== null)
 *       ? String(property.default)
 *       : ''
 *
 * `||` 的优先级高于 `?:`，所以它等价于
 * `(examples?.[0] || default存在) ? String(default) : ''`——条件为真时**一律**
 * 取 `default`，`examples` 永远不会被用到。凡是给了示例值的字段，
 * 界面显示的都是默认值，而示例值往往才是那个「照这样填」的提示
 * （例如 `owner/name` 之于一个仓库坐标字段）。
 *
 * 它不报错、不白屏，只是一直显示错的提示。放在 `.vue` 里只能靠 grep 源码
 * 「验证」，而那种断言看得见字符串、看不见运算优先级。
 */

/** JSON Schema 里与呈现有关的那几个键。 */
export interface PlaceholderSource {
  examples?: unknown[]
  default?: unknown
  readOnly?: boolean
  title?: string
}

/**
 * 一个字段该显示什么占位提示。
 *
 * 优先级：`examples[0]` > `default` > 空串。
 *
 * `examples` 优先于 `default` 是有理由的：`default` 是「不填就用这个值」，
 * 而 `examples` 是「照这个样子填」。当两者都有时，用户需要看到的是后者——
 * 默认值反正会生效，不需要提示。
 *
 * `false` 与 `0` 必须被当作有效值：用 `||` 判断会把它们跳过，
 * 而一个默认 `false` 的开关或默认 `0` 的超时都是完全正常的声明。
 */
export function fieldPlaceholder(property: PlaceholderSource | null | undefined): string {
  if (!property) return ''

  const example = Array.isArray(property.examples) ? property.examples[0] : undefined
  if (example !== undefined && example !== null && example !== '') {
    return String(example)
  }
  const fallback = property.default
  if (fallback !== undefined && fallback !== null && fallback !== '') {
    return String(fallback)
  }
  return ''
}

/**
 * 这个字段是否只读。
 *
 * 只认显式的 `true`：schema 里缺这个键、或者写成字符串 `"false"` 时，
 * 字段必须**可编辑**。把「没声明」当成只读会让一整页表单静默变成不可填，
 * 而用户看到的是一个有值、改不动的输入框——那正是本轮 Api Base 那个
 * 误读的形状（那一处其实是 placeholder，字段本身可编辑）。
 */
export function isReadOnlyField(property: PlaceholderSource | null | undefined): boolean {
  return property?.readOnly === true
}
