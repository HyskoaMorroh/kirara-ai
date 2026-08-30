/**
 * 把一条追踪记录的 `attempts` 明细整理成可展示的行。
 *
 * `attempts` 一直在后端 `to_dict()` 里返回（每条带 provider、retry_index、
 * success、error_category、时间戳与 partial_output），但此前没有任何界面消费它。
 * 只给「重试 2 次、转移 1 次」两个数字回答不了运维真正要问的问题：
 * **哪一家在失败、失败类型是什么、换到哪一家之后成功了**——而这三件事各自
 * 对应完全不同的动作（调超时 / 查那家的配额 / 把它摘出故障转移池）。
 *
 * 明细来自一个 JSON 列，因此这里对缺字段一律保守处理：不知道就给 `null`，
 * 绝不用 0 或 `false` 冒充事实。
 */

/** 这次尝试相对于上一次的关系。 */
export type TraceAttemptKind =
  /** 第一次尝试。 */
  | 'initial'
  /** 与上一次同一个 provider——同一家再试。 */
  | 'retry'
  /** 与上一次不同 provider——换了一家。 */
  | 'failover'
  /** provider 缺失，无法判断。 */
  | 'unknown'

export interface TraceAttemptRow {
  /** 第几次尝试；缺失时为 `null`（不假设它等于数组下标）。 */
  attempt: number | null
  provider: string | null
  model: string | null
  kind: TraceAttemptKind
  succeeded: boolean | null
  errorCategory: string | null
  errorSummary: string | null
  /** 首字节耗时（秒）。非流式请求没有首字节，此时为 `null` 而不是 0。 */
  ttftSeconds: number | null
  /** 这次尝试总耗时（秒）；时间戳不全时为 `null`。 */
  durationSeconds: number | null
  /** 这次失败前是否已经向用户产出过内容——重发会造成重复。 */
  partialOutput: boolean
}

function optionalNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function optionalText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function gap(from: unknown, to: unknown): number | null {
  const start = optionalNumber(from)
  const end = optionalNumber(to)
  if (start === null || end === null) return null
  return Math.max(0, end - start)
}

export function summarizeTraceAttempts(
  attempts: readonly Record<string, unknown>[] | null | undefined
): TraceAttemptRow[] {
  if (!Array.isArray(attempts) || attempts.length === 0) return []

  let previousProvider: string | null = null
  return attempts.map((raw, index) => {
    const provider = optionalText(raw?.provider)
    let kind: TraceAttemptKind
    if (provider === null) {
      // provider 缺失时无法判断是否换了家。标成 failover 是一个论断，
      // 而这里我们不知道。
      kind = 'unknown'
    } else if (index === 0) {
      kind = 'initial'
    } else if (previousProvider === null) {
      kind = 'unknown'
    } else {
      kind = provider === previousProvider ? 'retry' : 'failover'
    }
    previousProvider = provider

    return {
      attempt: optionalNumber(raw?.attempt),
      provider,
      model: optionalText(raw?.model),
      kind,
      succeeded: typeof raw?.success === 'boolean' ? raw.success : null,
      errorCategory: optionalText(raw?.error_category),
      errorSummary: optionalText(raw?.error_summary),
      ttftSeconds: gap(raw?.started_at, raw?.first_byte_at),
      durationSeconds: gap(raw?.started_at, raw?.completed_at),
      partialOutput: raw?.partial_output === true
    }
  })
}

/** 明细表里 `kind` 列的中文标签。 */
export const TRACE_ATTEMPT_KIND_LABELS: Record<TraceAttemptKind, string> = {
  initial: '首次',
  retry: '重试',
  failover: '故障转移',
  unknown: '未知'
}
