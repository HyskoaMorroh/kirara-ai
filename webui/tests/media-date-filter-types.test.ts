/**
 * 媒体列表的日期筛选必须真的能筛。
 *
 * `n-date-picker` 的 `value` 类型是 `number | [number, number]`（毫秒时间戳），
 * 而 `dateRange` 声明成 `[string, string] | null`。类型层报 TS2322，运行时后果更实在：
 *
 * 组件把选中的区间以**毫秒数**写回 `v-model`，于是 `dateRange.value[0]` 拿到的是
 * `1767225600000` 这样的数字，被直接塞进声明为 `string` 的 `searchParams.start_date`。
 * 后端 `MediaSearchParams.start_date` 是 `Optional[datetime]`，pydantic 能把毫秒
 * 时间戳解析成正确的 datetime（已实测），所以筛选**碰巧能工作** —— 但这是
 * 两处类型都写错、错误互相抵消的结果，不是设计。
 *
 * 一旦有人按声明把它当字符串处理（比如加个 `.slice(0, 10)` 或拼进 URL），
 * 就会拿到 `"1767225600000".slice(0,10)` 这种静默错值。
 *
 * 同理 `configAutoRemoveUnreferenced` 声明成 `boolean | null`，而 `n-switch` 的
 * `value` 只接受 `string | number | boolean`。`null` 表示"配置还没加载回来"，
 * 这个语义是对的，但不能直接绑给 switch —— 需要在模板层落到 `false`。
 *
 * 判据：**声明的类型必须是真实流动的类型。** 两处都错、恰好抵消，比一处错更难查。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const vm = readFileSync(resolve(root, 'src/views/media/media.vm.ts'), 'utf-8')
const view = readFileSync(resolve(root, 'src/views/media/MediaList.vue'), 'utf-8')

describe('媒体日期筛选的类型一致性', () => {
  it('自检：确实用了 n-date-picker 的区间模式', () => {
    expect(view).toMatch(/<n-date-picker[\s\S]{0,200}type="daterange"/)
  })

  it('dateRange 按 n-date-picker 的真实类型声明为毫秒时间戳', () => {
    const at = vm.indexOf('const dateRange')
    expect(at, '找不到 dateRange 声明').toBeGreaterThan(-1)
    const line = vm.slice(at, vm.indexOf('\n', at))
    expect(
      line,
      'dateRange 声明成字符串区间，但 n-date-picker 写回的是毫秒数字区间'
    ).not.toMatch(/\[string,\s*string\]/)
    expect(line).toMatch(/\[number,\s*number\]/)
  })

  it('毫秒时间戳转成后端期望的形式后才进查询参数', () => {
    // 后端 start_date 是 Optional[datetime]；毫秒数字能被 pydantic 解析，
    // 但前端声明的是 string，直接赋值会让类型与实际值长期不符。
    const at = vm.indexOf('searchParams.start_date = ')
    expect(at, '找不到 start_date 赋值').toBeGreaterThan(-1)
    const region = vm.slice(Math.max(0, at - 400), at + 400)
    expect(
      region,
      '毫秒时间戳直接赋给声明为 string 的字段；需要显式转换（toISOString 或 String）'
    ).toMatch(/toISOString|new Date|String\(/)
  })

  it('未加载的配置不直接绑给 n-switch', () => {
    // `boolean | null` 里的 null 表示「还没加载回来」，语义要保留；
    // 但 n-switch 只接受 string | number | boolean，模板层必须落到 false。
    const at = view.indexOf('configAutoRemoveUnreferenced')
    const region = view.slice(Math.max(0, at - 200), at + 300)
    expect(
      region,
      'n-switch 直接绑了可能为 null 的值；应在模板或 computed 里落到 false'
    ).toMatch(/\?\?|=== true|Boolean\(|!!/)
  })
})
