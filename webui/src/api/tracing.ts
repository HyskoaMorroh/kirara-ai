/**
 * 投递时间线接口。
 *
 * 后端一直提供 `/tracing/delivery/summary` 与 `/tracing/delivery/recent`
 * （表 `im_delivery_timings`），但前端从未调用——需求 19.5 要求「给出 Telegram、
 * WeCom 与 QQ 的可比链路耗时」，而在产品上这等于只能 curl。
 *
 * 字段形状以 `kirara_ai/im/delivery_timing_store.py` 为准：
 * 阶段耗时可以为 `null`，那表示**没测到**，不是耗时为零。前端必须把这两种情况
 * 区分开显示，否则「非流式请求没有首字节」会被读成「首字节极快」。
 */
import { http } from '@/utils/http'

/** 六个阶段的键名，与后端 `iter_phase_names()` 一一对应。 */
export const DELIVERY_PHASES = [
  'queue_seconds',
  'llm_first_byte_seconds',
  'llm_generation_seconds',
  'formatting_seconds',
  'send_seconds',
  'total_seconds'
] as const

export type DeliveryPhase = (typeof DELIVERY_PHASES)[number]

/** 阶段中文名。「模型首字节」只在流式模式下测得到，文案里点明这一点。 */
export const DELIVERY_PHASE_LABELS: Record<DeliveryPhase, string> = {
  queue_seconds: '排队',
  llm_first_byte_seconds: '模型首字节',
  llm_generation_seconds: '模型生成',
  formatting_seconds: '排版分页',
  send_seconds: '平台发送',
  total_seconds: '端到端总计'
}

export interface DeliveryPhaseSummary {
  /** 只对**测到该阶段**的记录求平均；没有样本时为 null。 */
  avg_seconds: number | null
  max_seconds: number | null
  /** 样本数：平均值代表多少次请求。缺这个数字，平均值无法判读。 */
  samples: number
}

/**
 * 分段数量与重试次数的聚合。
 *
 * 需求 19.5 九项里的后两项——它们不是时间戳，因此不在 `phases` 里。
 * 口径与阶段耗时一致：只对**测到该值**的行求平均。
 * 一个都没测到时 `avg` / `max` 为 `null` 而不是 `0`：`retry_count: 0`
 * 是一个论断（「都没重试过」），会让人以为链路一切正常，而实际只是没有数据。
 */
export interface DeliveryCountSummary {
  avg: number | null
  max: number | null
  samples: number
}

export type DeliveryCountKey = 'segment_count' | 'retry_count'

export const DELIVERY_COUNT_LABELS: Record<DeliveryCountKey, string> = {
  segment_count: '分段数量',
  retry_count: '重试次数'
}

export interface DeliverySummary {
  deliveries: number
  failed_deliveries: number
  phases: Record<DeliveryPhase, DeliveryPhaseSummary>
  /** 分段数量与重试次数；回答「慢是因为分了很多页还是重试过」。 */
  counts: Record<DeliveryCountKey, DeliveryCountSummary>
  /** 有记录的渠道列表，供筛选使用。 */
  channels: string[]
}

export interface DeliveryRecord {
  id: number
  channel: string
  adapter_instance: string
  recorded_at: string | null
  status: string
  queue_seconds: number | null
  llm_first_byte_seconds: number | null
  llm_generation_seconds: number | null
  formatting_seconds: number | null
  send_seconds: number | null
  total_seconds: number | null
  segment_count: number | null
  retry_count: number | null
  correlation_id: string | null
}

export const tracingApi = {
  /**
   * 按渠道与时间范围聚合各阶段耗时。
   *
   * `start_time` / `end_time` 必须是带时区的 ISO-8601；后端会按系统时区解析，
   * 不带时区的字符串会被拒绝——这是为了避免「本地时间」在服务器上被解释成 UTC。
   */
  getDeliverySummary(
    params: { channel?: string; start_time?: string; end_time?: string } = {},
    signal?: AbortSignal
  ) {
    const query = new URLSearchParams()
    if (params.channel) query.set('channel', params.channel)
    if (params.start_time) query.set('start_time', params.start_time)
    if (params.end_time) query.set('end_time', params.end_time)
    const suffix = query.toString() ? `?${query}` : ''
    return http.get<DeliverySummary>(`/tracing/delivery/summary${suffix}`, { signal })
  },

  getRecentDeliveries(
    params: { channel?: string; limit?: number } = {},
    signal?: AbortSignal
  ) {
    const query = new URLSearchParams()
    if (params.channel) query.set('channel', params.channel)
    if (params.limit) query.set('limit', String(params.limit))
    const suffix = query.toString() ? `?${query}` : ''
    // 后端把列表放在 `items` 而不是 `data`：与 `/delivery/summary` 不同，
    // 这里照抄后端形状，不做「统一封装」——猜错封装名的结果是读到 undefined。
    return http.get<{ items: DeliveryRecord[] }>(
      `/tracing/delivery/recent${suffix}`,
      { signal }
    )
  }
}
