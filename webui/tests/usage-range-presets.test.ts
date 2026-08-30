// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import {
  USAGE_RANGE_PRESETS,
  resolveUsageRange,
  startOfDayInZone,
  zoneOffsetMinutes
} from '../src/views/tracing/usage-range-presets'

/**
 * 需求 9：使用统计要有时间范围预设。
 *
 * 此前本页只有一个 `datetimerange`：想看「最近 7 天」得自己算两个时刻再点两次
 * 日历。而按天回看是这个页面最常做的第一步动作。
 *
 * 真正容易错的是**日界按哪个时区算**。本页时区可选（跨时区对账要看到对方眼里的
 * 「今天」），如果预设按浏览器本地时间算日界，选了 `UTC` 的 UTC+8 查看者拿到的
 * 「今天」会横跨上游眼里的两天——这类错位不会报错，只让两边数字差一截，
 * 且差多少取决于当前几点。
 */

const here = dirname(fileURLToPath(import.meta.url))
const viewSource = readFileSync(
  resolve(here, '../src/views/tracing/UsageStatisticsView.vue'),
  'utf-8'
)

const HOUR = 3_600_000

describe('zone offset', () => {
  it('reports a positive offset east of UTC', () => {
    expect(zoneOffsetMinutes(Date.UTC(2026, 5, 15, 12), 'Asia/Shanghai')).toBe(480)
  })

  it('tracks daylight saving instead of using a fixed table', () => {
    // 纽约 1 月是 -5，7 月是 -4。固定偏移表会在半年里全错一小时。
    expect(zoneOffsetMinutes(Date.UTC(2026, 0, 15, 12), 'America/New_York')).toBe(-300)
    expect(zoneOffsetMinutes(Date.UTC(2026, 6, 15, 12), 'America/New_York')).toBe(-240)
  })

  it('falls back to UTC for an unknown zone name instead of throwing', () => {
    // 时区框可自由输入；一个错字不该让整页崩掉。
    expect(zoneOffsetMinutes(Date.UTC(2026, 5, 15, 12), 'Not/AZone')).toBe(0)
  })
})

describe('start of day', () => {
  it('uses the requested zone, not the runner local time', () => {
    // 2026-06-15T02:00Z 在上海已是 10:00，当天零点是 2026-06-14T16:00Z。
    const start = startOfDayInZone(Date.UTC(2026, 5, 15, 2), 'Asia/Shanghai')
    expect(new Date(start).toISOString()).toBe('2026-06-14T16:00:00.000Z')
  })

  it('is idempotent', () => {
    const first = startOfDayInZone(Date.UTC(2026, 5, 15, 2), 'Asia/Shanghai')
    expect(startOfDayInZone(first, 'Asia/Shanghai')).toBe(first)
  })

  it('lands on midnight on a spring-forward day', () => {
    // 2026-03-08 纽约凌晨 2 点跳到 3 点，那天只有 23 小时。
    const start = startOfDayInZone(Date.UTC(2026, 2, 8, 18), 'America/New_York')
    expect(new Date(start).toISOString()).toBe('2026-03-08T05:00:00.000Z')
  })
})

describe('range presets', () => {
  const now = Date.UTC(2026, 5, 15, 2)

  it('offers today, 24h and multi-day options', () => {
    const values = USAGE_RANGE_PRESETS.map((option) => option.value)
    expect(values).toContain('today')
    expect(values).toContain('24h')
    expect(values).toContain('7d')
    expect(values).toContain('30d')
    // 「自定义」必须在列：预设不能取消用户自己选区间的能力。
    expect(values).toContain('custom')
  })

  it('keeps today and last-24-hours as separate answers', () => {
    // 上海时间上午 10 点：「今天」只有 10 小时，「近 24 小时」跨到昨天。
    const today = resolveUsageRange('today', 'Asia/Shanghai', now)!
    const last24 = resolveUsageRange('24h', 'Asia/Shanghai', now)!
    expect(today[1] - today[0]).toBe(10 * HOUR)
    expect(last24[1] - last24[0]).toBe(24 * HOUR)
  })

  it('starts multi-day presets at a calendar day boundary', () => {
    // 不是「now 减 7×24 小时」：那样首尾各半天，趋势图第一根柱子永远偏低，
    // 会被读成「那天用量下降」。
    const [start] = resolveUsageRange('7d', 'Asia/Shanghai', now)!
    expect(new Date(start).toISOString()).toBe('2026-06-08T16:00:00.000Z')
  })

  it('counts the current day as one of the days', () => {
    const [start] = resolveUsageRange('today', 'Asia/Shanghai', now)!
    const [weekStart] = resolveUsageRange('7d', 'Asia/Shanghai', now)!
    expect(start - weekStart).toBe(6 * 24 * HOUR)
  })

  it('follows the selected zone rather than the browser', () => {
    // 同一时刻、两个时区，日界不同——这正是「时区可选」必须贯穿到预设的理由。
    const shanghai = resolveUsageRange('today', 'Asia/Shanghai', now)![0]
    const utc = resolveUsageRange('today', 'UTC', now)![0]
    expect(new Date(shanghai).toISOString()).toBe('2026-06-14T16:00:00.000Z')
    expect(new Date(utc).toISOString()).toBe('2026-06-15T00:00:00.000Z')
  })

  it('spans 30 calendar days across a DST change', () => {
    // 3 月 8 日跳表；固定 24 小时倒推会落在 04:00 或 06:00 而不是午夜。
    const [start] = resolveUsageRange('30d', 'America/New_York', Date.UTC(2026, 2, 20, 15))!
    expect(new Date(start).toISOString()).toBe('2026-02-19T05:00:00.000Z')
  })

  it('returns null for custom so it never overwrites a user range', () => {
    expect(resolveUsageRange('custom', 'UTC', now)).toBeNull()
  })
})

describe('statistics view wiring', () => {
  it('renders the preset control', () => {
    expect(viewSource).toContain('data-test="range-preset"')
  })

  it('keeps the explicit range picker', () => {
    // 预设是快捷方式，不是替代品：对一次具体账单还是要能填精确区间。
    expect(viewSource).toContain('data-test="range-filter"')
  })

  it('recomputes the preset range when the timezone changes', () => {
    // 改时区后不重算，界面上的「今天」就还是上一个时区的今天。
    expect(viewSource).toMatch(/watch\(\s*timezone/)
  })
})
