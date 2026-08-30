/**
 * 使用统计的时间范围预设（需求 9）。
 *
 * 此前本页只有一个 `datetimerange` 选择器：想看「最近 7 天」必须自己算两个时刻
 * 再点两次日历。而按天回看是这个页面最常做的动作——账单异常、流量变化、
 * 某个模型上线，第一步都是「先看最近几天」。
 *
 * **边界必须按用户选择的时区算，不是浏览器时区。** 本页的时区可选（跨时区对账
 * 时要看到对方眼里的「今天」），如果预设按浏览器本地时间算日界，一个 UTC+8 的
 * 查看者选了 `UTC` 之后，「今天」会横跨上游眼里的两天——而这类错位不会报错，
 * 只会让两边对出来的数字差一截，且差多少取决于当前几点。
 */

/** 预设键。`custom` 表示用户自己选了区间，不由这里计算。 */
export type UsageRangePreset = 'today' | '24h' | '7d' | '14d' | '30d' | 'custom'

export interface UsageRangePresetOption {
  label: string
  value: UsageRangePreset
}

/**
 * 可选项。
 *
 * 「今天」与「近 24 小时」都保留，它们回答的不是同一个问题：上午九点时
 * 「今天」只覆盖 9 小时，「近 24 小时」会跨到昨天下午。把两者合并成一个，
 * 就得替用户决定他问的是哪一个。
 */
export const USAGE_RANGE_PRESETS: UsageRangePresetOption[] = [
  { label: '今天', value: 'today' },
  { label: '近 24 小时', value: '24h' },
  { label: '近 7 天', value: '7d' },
  { label: '近 14 天', value: '14d' },
  { label: '近 30 天', value: '30d' },
  { label: '自定义', value: 'custom' }
]

const MINUTE_MS = 60_000
const DAY_MS = 86_400_000

/**
 * 某个时刻在指定时区的 UTC 偏移（分钟，东为正）。
 *
 * 做法是把该时刻按目标时区格式化，再把读出来的墙上时间当作 UTC 反算，
 * 两者之差就是偏移。不用固定偏移表：夏令时地区的偏移随时刻变化。
 *
 * 时区名非法时返回 0（等价于按 UTC 处理）而不是抛异常——一个可自由输入的
 * 时区框拿到错字不该让整页崩掉，而 `Intl` 对未知时区名会抛。
 */
export function zoneOffsetMinutes(instantMs: number, timeZone: string): number {
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).formatToParts(new Date(instantMs))
  } catch {
    return 0
  }
  const read = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? '0')
  // `hour12: false` 在部分实现里把午夜给成 24，Date.UTC 会把它滚到次日。
  const hour = read('hour') % 24
  const asUtc = Date.UTC(
    read('year'),
    read('month') - 1,
    read('day'),
    hour,
    read('minute'),
    read('second')
  )
  // 取整到分钟。`asUtc` 只到秒精度，直接相除会在输入带毫秒时得到
  // 479.99998 这样的偏移；那个残差会渗进日界计算，让「逐日回退」原地打转
  // （表现为 7 天预设只回退一天），而现代 IANA 偏移都是整分钟。
  return Math.round((asUtc - instantMs) / MINUTE_MS)
}

/**
 * 指定时区里「那一天零点」对应的 UTC 时刻。
 *
 * 两次求偏移是必要的：先用当前时刻的偏移把墙上时间截到零点，再用**零点那一刻**
 * 的偏移换回 UTC。夏令时切换日两个偏移不同，只算一次会让日界偏移一小时——
 * 那种错误不会报错，只表现为当天的数字少了或多了一小时的量。
 */
export function startOfDayInZone(instantMs: number, timeZone: string): number {
  const offset = zoneOffsetMinutes(instantMs, timeZone)
  const wallDayStart = Math.floor((instantMs + offset * MINUTE_MS) / DAY_MS) * DAY_MS
  const firstGuess = wallDayStart - offset * MINUTE_MS
  const settledOffset = zoneOffsetMinutes(firstGuess, timeZone)
  if (settledOffset === offset) return firstGuess
  return wallDayStart - settledOffset * MINUTE_MS
}

/**
 * 把预设解析成 `[start, end]` 毫秒时间戳。
 *
 * `custom` 返回 `null`：那不是「没有范围」，而是「范围由用户自己给」，
 * 调用方不该拿它去覆盖用户已经选好的区间。
 *
 * 按天的预设包含今天，且从**当天零点**起算（`7d` = 今天在内的 7 个日历日），
 * 而不是「now 减 7×24 小时」：后者的首尾都是半天，日趋势图上第一根柱子
 * 永远偏低，会被读成「那天用量下降」。
 */
export function resolveUsageRange(
  preset: UsageRangePreset,
  timeZone: string,
  nowMs: number = Date.now()
): [number, number] | null {
  if (preset === 'custom') return null
  if (preset === '24h') return [nowMs - DAY_MS, nowMs]
  const days = preset === 'today' ? 1 : Number(preset.replace('d', ''))
  if (!Number.isFinite(days) || days < 1) return null
  const todayStart = startOfDayInZone(nowMs, timeZone)
  if (days === 1) return [todayStart, nowMs]
  // 逐日回退而不是减 (days-1)*DAY_MS：跨夏令时的那一天只有 23 或 25 小时，
  // 按固定 24 小时倒推会落到前一天的 23:00 或当天的 01:00。
  let start = todayStart
  for (let index = 1; index < days; index += 1) {
    start = startOfDayInZone(start - 1, timeZone)
  }
  return [start, nowMs]
}
