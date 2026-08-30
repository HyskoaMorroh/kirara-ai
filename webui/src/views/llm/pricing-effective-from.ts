/**
 * 定价「生效时间」的输入校验与归一化。
 *
 * 后端 `PriceVersion.effective_from` 是 `datetime`，并且有一个校验器**强制要求
 * 带时区**（`llm/pricing.py` 的 `require_timezone`）。前端此前是一个裸
 * `<input v-model>`：没有 type、没有校验、没有格式提示，于是用户按最自然的写法
 * 填 `2026-01-01 00:00`，点保存，拿到一个来自 pydantic 的 4xx。
 *
 * 抽成独立模块的理由：定价填错的后果不是「报错了事」——生效时刻落在错误位置的
 * 版本会让之后所有请求按错误价格计费，而且**没有任何症状**，直到有人去核对账单。
 * 这类判定值得单独测，而不是埋在组件里靠界面测试间接覆盖。
 */

export type EffectiveFromResult =
  | { ok: true; value: string }
  | { ok: false; error: string }

/**
 * 必须同时具备三段：日期、时刻、时区。
 *
 * 时区之外还要求时刻，理由与「不猜」一致：`2026-01-01` 会被 `Date` 解析成 UTC
 * 午夜，看起来成功了，实际是我们替用户选了一个他没有写出来的时刻。
 */
const ISO_WITH_TIMEZONE =
  /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})$/

/** 有日期有时刻但没有时区——最常见的那一种错误，单独识别以便给出针对性说明。 */
const ISO_WITHOUT_TIMEZONE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/

export const EFFECTIVE_FROM_HINT =
  '需带时区，例如 2026-01-01T00:00:00Z 或 2026-01-01T08:00:00+08:00'

/**
 * 校验并归一化用户输入的生效时间。
 *
 * 成功时返回 UTC ISO 字符串，而不是原样回传：后端按 UTC 存储与比较，
 * 在这里归一化能让「界面上显示的时刻」与「用于计费判定的时刻」是同一个值。
 */
export function normalizeEffectiveFrom(input: string): EffectiveFromResult {
  const text = (input || '').trim()
  if (!text) {
    // 留空时替用户填「现在」等于悄悄决定了计费起点。
    return { ok: false, error: `请填写生效时间：${EFFECTIVE_FROM_HINT}` }
  }
  if (ISO_WITHOUT_TIMEZONE.test(text)) {
    return {
      ok: false,
      error: `生效时间缺少时区。${EFFECTIVE_FROM_HINT}`
    }
  }
  if (!ISO_WITH_TIMEZONE.test(text)) {
    return {
      ok: false,
      error: `生效时间格式无法识别（需含日期、时刻与时区）。${EFFECTIVE_FROM_HINT}`
    }
  }
  // 形状对了不等于日期存在：`2026-02-30T00:00:00Z` 完全符合上面的正则。
  const normalized = text.replace(' ', 'T')
  const parsed = new Date(normalized)
  if (Number.isNaN(parsed.getTime())) {
    return { ok: false, error: `生效时间不是一个真实存在的时刻。${EFFECTIVE_FROM_HINT}` }
  }
  // `Date` 对不存在的日期不报错，而是**静默滚到下一个月**：
  // `2026-02-30` 变成 `2026-03-02`。对一个决定「从哪一刻开始按新价计费」的字段，
  // 静默改掉月份是最坏的失败形态——它会成功保存，然后整整两天按错误的版本计费，
  // 而界面上显示的是一个用户从未填过的日期。
  // 因此把年月日读回来与输入比对：滚过月的日期在这里必然不相等。
  const [datePart] = normalized.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  // 带偏移量的输入（`+08:00`）在 UTC 下的日期可能合法地差一天，
  // 所以按输入自身的偏移量还原回「输入时区里的那一天」再比。
  const offsetMinutes = parsedOffsetMinutes(normalized)
  const localized = new Date(parsed.getTime() + offsetMinutes * 60_000)
  if (
    localized.getUTCFullYear() !== year ||
    localized.getUTCMonth() + 1 !== month ||
    localized.getUTCDate() !== day
  ) {
    return {
      ok: false,
      error: `生效时间里的日期不存在（如 2 月 30 日）。${EFFECTIVE_FROM_HINT}`
    }
  }
  return { ok: true, value: parsed.toISOString() }
}

/**
 * 从输入串里读出时区偏移量（分钟）。`Z` 为 0。
 *
 * 只用于把 UTC 时刻还原回「用户写的那个时区里的日历日」，
 * 以便判断日期有没有被 `Date` 滚过月。
 */
function parsedOffsetMinutes(text: string): number {
  if (text.endsWith('Z')) return 0
  const match = /([+-])(\d{2}):?(\d{2})$/.exec(text)
  if (!match) return 0
  const sign = match[1] === '-' ? -1 : 1
  return sign * (Number(match[2]) * 60 + Number(match[3]))
}

/** 新建定价时的默认值。必须自己就能过上面的校验。 */
export function defaultEffectiveFrom(): string {
  return new Date().toISOString()
}
