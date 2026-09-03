/**
 * 定价表单的提交前处理。
 *
 * 抽成纯函数，是因为「没填」到 `null` 的这一步**是**这个表单能不能保存：
 * 后端对空白 `display_name` 直接拒绝（`reject_blank_display_name`），
 * 而输入框里的「没填」天然是空串。不转换就是 400，而用户看到的是一条
 * 与他做的事无关的校验错误。
 *
 * 此前这一步只被源码 grep 覆盖：
 * `toMatch(/copyVersion[\s\S]{0,400}display_name\s*=\s*label\s*\?\s*label\s*:\s*null/)`
 * ——把一行代码的写法钉住，重构成 `label || null` 就红，
 * 而把条件写反（`label ? null : label`）时那条正则也匹配不上，
 * 但它匹配不上的原因是写法变了，不是行为错了。两种情况给出同一个红，
 * 于是这条断言无法区分「改好了」与「改坏了」。
 */

/**
 * 只约束这个函数真正读写的那一个键。
 *
 * 刻意**不加** `[key: string]: unknown` 索引签名：加了之后
 * `interface PricingVersion` 那类没有索引签名的具体类型就不再可赋值给它，
 * `tsc` 报 TS2345，而这个函数需要接受的正是那种类型。
 */
export interface PricingFormValues {
  display_name?: string | null
}

/**
 * 把表单值整理成可提交的形状。
 *
 * 只动 `display_name` 这一个键：其余字段的校验在后端（Pydantic），
 * 在这里再抄一份数值边界会与后端漂移，而漂移的方向是「前端放行、后端拒绝」
 * 或者反过来，两者都让用户看到一条对不上的错误。
 *
 * 返回**新对象**：原地改会让表单里那个输入框在提交瞬间变成 `null`，
 * 用户看到自己刚填的字消失了。
 */
export function copyVersion<T extends PricingFormValues>(version: T): T {
  const copy = { ...version }
  const label = String(copy.display_name ?? '').trim()
  // 「没填」必须是 `null` 而不是空串：后端对空白标签直接拒绝（那会在表格里
  // 留下一行没有身份的价格）。
  copy.display_name = label || null
  return copy
}

/**
 * 表格里这一行该显示什么名字。
 *
 * 有显示名用显示名，没有回落到模型标识——**不显示空白**。
 * 这正是 pricing display_name 那个缺陷的形态：后端一直返回它，
 * 前端类型没声明，于是标签永远回落到 id，用户填的名字看不见。
 */
export function pricingLabel(version: {
  display_name?: string | null
  model?: string | null
}): string {
  const label = String(version.display_name ?? '').trim()
  return label || String(version.model ?? '')
}
