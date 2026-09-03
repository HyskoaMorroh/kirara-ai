// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  ONE_DAY_MS,
  checkInterval,
  isDirty,
  lastRunText,
  nextRunText,
  resultTag,
  runSummary,
  savedMessage
} from '../src/views/llm/autoDetectSchedule'

/**
 * 自动检测计划表按**行为**验证。
 *
 * 这份测试替换的是 `llm-auto-detect-schedule.test.ts` 里那 40 条源码 grep。
 * 那些断言长这样：
 *
 *     expect(viewSource).toContain("if (!row.last_run) return '—'")
 *     expect(viewSource).toContain('尚未成功检测过')
 *
 * 第一条把一整行代码当字符串钉住——重构成等价写法它就红，而算错时间它照绿。
 * 第二条只证明那句中文在文件里出现过，不证明它在正确的分支上出现。
 *
 * 这一页的输出是**时刻**与**是否已关闭**。算错的后果是用户按一个错的时间去等，
 * 或者以为关掉了其实还在每天访问上游。所以这些断言调用函数、检查返回值。
 */

const row = (over: Record<string, unknown> = {}) => ({
  name: 'openai',
  interval_days: 5,
  last_run: '2026-09-01T00:00:00Z',
  ...over
})

describe('下一轮时刻', () => {
  it('按 last_run 加间隔算出来', () => {
    const last = Date.parse('2026-09-01T00:00:00Z')
    const expected = new Date(last + 5 * ONE_DAY_MS).toLocaleString()

    expect(nextRunText(row())).toBe(expected)
  })

  it('一天的毫秒数没写错', () => {
    // `86_400` 少三个零、`864_000_00` 多打一位，都是合法数字。
    // 那种错在源码 grep 里看不出来，而算出的时刻会差几个数量级。
    expect(ONE_DAY_MS).toBe(24 * 60 * 60 * 1000)
  })

  it('间隔为 0 是「已关闭」', () => {
    expect(nextRunText(row({ interval_days: 0 }))).toBe('已关闭')
  })

  it('间隔缺失按关闭处理，不按 0 天算出「现在」', () => {
    expect(nextRunText(row({ interval_days: null }))).toBe('已关闭')
    expect(nextRunText(row({ interval_days: undefined }))).toBe('已关闭')
  })

  it('负间隔不会算出一个过去的时刻', () => {
    // 保存路径拦着负数，但读到的可能是手改过的 config.yaml。
    // 算出过去的时刻会让人以为「早该跑了却没跑」。
    expect(nextRunText(row({ interval_days: -3 }))).toBe('已关闭')
  })

  it('从没跑过时说清将在首轮触发，而不是编一个时间', () => {
    // 后台循环首轮延迟带 0–300 秒随机抖动，编「大约 X」会让人按那个时间等。
    const text = nextRunText(row({ last_run: null }))
    expect(text).toContain('尚未成功检测过')
    expect(text).not.toMatch(/\d{4}/)
  })

  it('时间戳解析不了时说明原因，而不是显示 Invalid Date', () => {
    expect(nextRunText(row({ last_run: 'not-a-date' }))).toBe('上次时间无法解析')
  })

  it('已关闭优先于「从没跑过」——关掉的行不该说「将在首轮触发」', () => {
    expect(nextRunText(row({ interval_days: 0, last_run: null }))).toBe('已关闭')
  })
})

describe('上次时刻', () => {
  it('没有时显示破折号', () => {
    expect(lastRunText(row({ last_run: null }))).toBe('—')
    expect(lastRunText(row({ last_run: '' }))).toBe('—')
  })

  it('解析不了时原样回显，而不是换成一句解释', () => {
    // 那串原文是唯一的线索。换成「无法解析」等于把它藏起来。
    expect(lastRunText(row({ last_run: '2026-13-45' }))).toBe('2026-13-45')
  })

  it('正常时间戳按本地格式显示', () => {
    const expected = new Date('2026-09-01T00:00:00Z').toLocaleString()
    expect(lastRunText(row())).toBe(expected)
  })
})

describe('本轮结果标签', () => {
  it('还没跑过时不显示任何标签', () => {
    expect(resultTag(null, 'openai')).toBeNull()
  })

  it('跑过但这一轮没有这个后端时也不显示', () => {
    // 显示成「失败」会指控一件没发生的事。
    expect(resultTag({ other: true }, 'openai')).toBeNull()
  })

  it('成功与失败是两个档位', () => {
    expect(resultTag({ openai: true }, 'openai')).toEqual({
      label: '本次成功',
      type: 'success'
    })
    expect(resultTag({ openai: false }, 'openai')).toEqual({
      label: '本次失败',
      type: 'error'
    })
  })
})

describe('未保存判定', () => {
  it('草稿与服务端值相同时不算脏', () => {
    expect(isDirty(row({ interval_days: 5 }), 5)).toBe(false)
  })

  it('服务端 null 与草稿 0 视为相同', () => {
    // 后端用 null 表示未设置，界面用 0 表示关闭，两者是同一种状态。
    // 不对齐的话，一个从未设置过的后端刚加载完就显示「有未保存修改」。
    expect(isDirty(row({ interval_days: null }), 0)).toBe(false)
  })

  it('改过就算脏', () => {
    expect(isDirty(row({ interval_days: 5 }), 7)).toBe(true)
    expect(isDirty(row({ interval_days: null }), 3)).toBe(true)
  })

  it('草稿还没建立时不算脏', () => {
    expect(isDirty(row(), undefined)).toBe(false)
  })
})

describe('间隔校验', () => {
  it('接受正整数与 0', () => {
    expect(checkInterval(5)).toEqual({ ok: true, value: 5 })
    expect(checkInterval(0)).toEqual({ ok: true, value: 0 })
  })

  it('小数向下截断', () => {
    // 向下取整让检测比要求的更频繁一点；向上会漏过一个周期。
    expect(checkInterval(1.7)).toEqual({ ok: true, value: 1 })
  })

  it('拒绝负数', () => {
    expect(checkInterval(-1)).toEqual({ ok: false, error: '间隔天数不能为负数' })
  })

  it('拒绝非数字与非有限值', () => {
    for (const value of [null, undefined, '5', NaN, Infinity, {}]) {
      expect(checkInterval(value).ok, `${String(value)} 不该被接受`).toBe(false)
    }
  })
})

describe('保存提示', () => {
  it('0 说的是「已关闭」而不是「保存为 0 天」', () => {
    // 共用一句会让人不确定到底关没关。
    expect(savedMessage('openai', 0)).toBe('已关闭 openai 的自动检测')
  })

  it('正数说明具体天数', () => {
    expect(savedMessage('openai', 7)).toBe('openai 的检测间隔已保存为 7 天')
  })
})

describe('立即检测汇总', () => {
  it('一个都没跑与「全成功」不是同一句话', () => {
    // 说成「0 个成功」会让人以为跑过了。
    expect(runSummary({})).toEqual({
      level: 'warning',
      text: '没有后端配置了检测间隔，这一轮什么都没做'
    })
  })

  it('全成功报总数', () => {
    expect(runSummary({ a: true, b: true })).toEqual({
      level: 'success',
      text: '2 个后端检测完成'
    })
  })

  it('有失败时点出失败数，并降为 warning', () => {
    expect(runSummary({ a: true, b: false, c: false })).toEqual({
      level: 'warning',
      text: '3 个后端里有 2 个检测失败'
    })
  })
})
