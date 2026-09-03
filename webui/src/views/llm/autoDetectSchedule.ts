/**
 * 自动检测计划表里的纯逻辑。
 *
 * 抽成独立模块而不是留在 `AutoDetectScheduleView.vue` 里，是因为这几条规则**是**
 * 这一页的行为，而它们此前只被源码 grep「测过」——
 * `llm-auto-detect-schedule.test.ts` 的 40 条断言全是
 * `expect(viewSource).toContain(...)`，包括把整行代码当字符串钉住的
 * `toContain("if (!row.last_run) return '—'")`。
 *
 * 那种断言在行为坏掉时照样绿：改一个比较运算符、把 `86_400_000` 写成
 * `86_400`、把 `<= 0` 写成 `< 0`，字符串全都还在。而这一页的输出是**时刻**与
 * **是否已关闭**——算错的后果是用户按一个错的时间去等，或以为关掉了其实没关。
 *
 * 与 `documentAuthoring.ts` 同一个理由（那次是正则丢了反斜杠，
 * 源码 grep 测试全绿而整条路不可用）。
 */

/** 一天的毫秒数。写成常量是为了让下面的乘法在测试里可被独立核对。 */
export const ONE_DAY_MS = 86_400_000

export interface ScheduleRow {
  name: string
  interval_days?: number | null
  last_run?: string | null
}

/**
 * 下一轮预计时刻。
 *
 * `last_run` 缺失时**不猜**：后台循环的首轮延迟带 0–300 秒随机抖动，
 * 编一个「大约 X」会让人按那个时间去等。
 *
 * `interval_days <= 0` 是「已关闭」，其中 `0` 是用户显式关闭的取值。
 * 负数不该出现（保存路径拦着），出现时按关闭处理而不是算出一个过去的时刻。
 */
export function nextRunText(row: ScheduleRow): string {
  const interval = row.interval_days ?? 0
  if (interval <= 0) return '已关闭'
  if (!row.last_run) return '尚未成功检测过，将在启动后首轮触发'
  const last = new Date(row.last_run)
  if (Number.isNaN(last.getTime())) return '上次时间无法解析'
  return new Date(last.getTime() + interval * ONE_DAY_MS).toLocaleString()
}

/**
 * 上次检测时刻。
 *
 * 解析不了时**原样回显**而不是显示「无法解析」：那串原文是唯一的线索，
 * 换成一句解释等于把它藏起来。
 */
export function lastRunText(row: ScheduleRow): string {
  if (!row.last_run) return '—'
  const last = new Date(row.last_run)
  return Number.isNaN(last.getTime()) ? row.last_run : last.toLocaleString()
}

export interface ResultTag {
  label: string
  type: 'success' | 'error'
}

/**
 * 「立即检测」这一轮里某个后端的结果。
 *
 * 没跑过（`null`）与跑过但没这个后端（键不存在）都返回 `null`——
 * 都是「这一轮没有它的结论」，而显示成「失败」会指控一件没发生的事。
 */
export function resultTag(
  results: Record<string, boolean> | null,
  name: string
): ResultTag | null {
  if (!results || !(name in results)) return null
  return results[name]
    ? { label: '本次成功', type: 'success' }
    : { label: '本次失败', type: 'error' }
}

/**
 * 草稿值与服务端值是否不同（决定「保存」按钮是否可点）。
 *
 * 服务端的 `null` 与草稿的 `0` 视为相同：后端用 `null` 表示未设置，
 * 界面用 `0` 表示关闭，两者是同一种状态。不这样对齐的话，
 * 一个从未设置过的后端刚加载完就显示「有未保存修改」。
 */
export function isDirty(row: ScheduleRow, draft: number | undefined): boolean {
  return typeof draft === 'number' && draft !== (row.interval_days ?? 0)
}

export type IntervalCheck =
  | { ok: true; value: number }
  | { ok: false; error: string }

/**
 * 校验并规范化要保存的间隔天数。
 *
 * 截断而不是四舍五入：`n-input-number` 允许输入 `1.7`，而「1.7 天」没有意义。
 * 向下取整让用户得到的检测比他要的更频繁一点，反过来会漏过一个周期。
 */
export function checkInterval(draft: unknown): IntervalCheck {
  if (typeof draft !== 'number' || !Number.isFinite(draft)) {
    return { ok: false, error: '间隔天数必须是数字' }
  }
  if (draft < 0) return { ok: false, error: '间隔天数不能为负数' }
  return { ok: true, value: Math.trunc(draft) }
}

/** 保存成功后的提示语。`0` 与正数是两件事，共用一句会让人不确定到底关没关。 */
export function savedMessage(name: string, value: number): string {
  return value === 0
    ? `已关闭 ${name} 的自动检测`
    : `${name} 的检测间隔已保存为 ${value} 天`
}

/**
 * 「立即检测」结束后的汇总语与档位。
 *
 * 三种处境分开说：一个都没跑（没有后端配了间隔）、全成功、有失败。
 * 把「什么都没做」说成「0 个成功」会让人以为跑过了。
 */
export function runSummary(results: Record<string, boolean>): {
  level: 'success' | 'warning'
  text: string
} {
  const total = Object.keys(results).length
  const failed = Object.values(results).filter((ok) => !ok).length
  if (total === 0) {
    return { level: 'warning', text: '没有后端配置了检测间隔，这一轮什么都没做' }
  }
  if (failed === 0) return { level: 'success', text: `${total} 个后端检测完成` }
  return { level: 'warning', text: `${total} 个后端里有 ${failed} 个检测失败` }
}
