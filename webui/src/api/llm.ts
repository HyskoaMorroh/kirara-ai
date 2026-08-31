import type { ModelInfo } from '@/components/form/types'
import { http } from '@/utils/http'

export interface LLMBackend {
  name: string
  adapter: string
  config: Record<string, any>
  enable: boolean
  models: ModelInfo[]
  /**
   * 容错与故障转移预算。
   *
   * 后端 `LLMBackendUpdateRequest` 直接继承 `LLMBackendConfig`，这些字段本来就会
   * 被接收；此前前端类型没有声明它们，表单提交的 payload 缺字段，pydantic 用默认值
   * 补齐，于是「改一个开关」会把整套调好的重试、超时和熔断参数重置回出厂值。
   * 声明为可选是为了兼容旧的本地缓存数据；实际保存时由 {@link resilienceDefaults}
   * 保证新建配置带齐全部字段。
   */
  auto_detect_interval_days?: number
  priority?: number
  participate_in_failover?: boolean
  max_retries?: number
  retry_backoff_seconds?: number
  retry_backoff_max_seconds?: number
  /** 兼容旧配置的总时间预算；新配置优先使用下面两个专用键。 */
  request_timeout_seconds?: number
  non_stream_timeout_seconds?: number
  stream_first_byte_timeout_seconds?: number
  stream_idle_timeout_seconds?: number
  stream_total_timeout_seconds?: number
  circuit_failure_threshold?: number
  circuit_error_rate_threshold?: number
  circuit_min_requests?: number
  circuit_recovery_timeout_seconds?: number
  circuit_recovery_success_threshold?: number
  /**
   * 推理强度档位；留空表示沿用上游默认。
   *
   * 与上面那批容错字段不同，这一项**不进** `resilienceDefaults()`：
   * 后端默认值是 `None`（不指定），如果前端替它填一个具体档位，
   * 就等于让「不指定」这个状态无法表达，而不支持思考的模型收到该字段会报错。
   */
  reasoning_effort?: 'low' | 'medium' | 'high' | 'max' | null
  /**
   * 移除该供应商回复里的 AI 自我署名。
   *
   * 与 `reasoning_effort` 同理不进 `resilienceDefaults()`：后端默认 `false`，
   * 而这是一个**会改写模型输出**的开关，默认值只能是「不改写」。
   * 声明在这里的作用是让编辑表单把它一起提交——类型里没有这个键时，
   * payload 里也不会有它。
   */
  hide_ai_attribution?: boolean
  /**
   * 请求整流开关（需求 8）。
   *
   * 与 `hide_ai_attribution` 同理不进 `resilienceDefaults()`：后端默认全开，
   * 前端补一份 `true` 会让用户在 config.yaml 里关掉的那次被一次无关的编辑
   * 重新打开。后端的 `exclude_unset=True` 只能救「没发这个键」，
   * 救不了「前端补发了一个值」。
   *
   * 语义见 `kirara_ai/llm/rectifier.py`：只有上游**真的拒绝**且错误命中
   * 白名单时才改一处并重试一次。
   */
  rectifier_enabled?: boolean
  rectify_thinking_signature?: boolean
  rectify_thinking_budget?: boolean
  /** 会改变模型看到的内容（图片被换成占位文本），因此单列一项可关。 */
  rectify_media_fallback?: boolean
  /**
   * 上游不认识 `reasoning_effort` 时删掉该字段再重试一次。
   *
   * 与其他三项同理不进 `resilienceDefaults()`：后端默认开启，
   * 前端补一份 `true` 会让用户在 config.yaml 里关掉的那次被一次无关的编辑
   * 重新打开。
   */
  rectify_reasoning_effort_unsupported?: boolean
}

/**
 * 与 `kirara_ai/config/global_config.py` 的 `LLMBackendConfig` 字段默认值逐一对应。
 *
 * 新建后端时必须带齐这些字段，否则「保存后再打开」会看到一批空输入框，
 * 用户改动其中任意一项都会把其余项提交成 `undefined`。
 */
export const resilienceDefaults = (): Required<
  Pick<
    LLMBackend,
    | 'auto_detect_interval_days'
    | 'priority'
    | 'participate_in_failover'
    | 'max_retries'
    | 'retry_backoff_seconds'
    | 'retry_backoff_max_seconds'
    | 'request_timeout_seconds'
    | 'non_stream_timeout_seconds'
    | 'stream_first_byte_timeout_seconds'
    | 'stream_idle_timeout_seconds'
    | 'stream_total_timeout_seconds'
    | 'circuit_failure_threshold'
    | 'circuit_error_rate_threshold'
    | 'circuit_min_requests'
    | 'circuit_recovery_timeout_seconds'
    | 'circuit_recovery_success_threshold'
  >
> => ({
  auto_detect_interval_days: 5,
  priority: 100,
  participate_in_failover: true,
  max_retries: 0,
  retry_backoff_seconds: 0.5,
  retry_backoff_max_seconds: 5,
  request_timeout_seconds: 60,
  non_stream_timeout_seconds: 60,
  stream_first_byte_timeout_seconds: 15,
  stream_idle_timeout_seconds: 30,
  stream_total_timeout_seconds: 60,
  circuit_failure_threshold: 3,
  circuit_error_rate_threshold: 0.5,
  circuit_min_requests: 10,
  circuit_recovery_timeout_seconds: 30,
  circuit_recovery_success_threshold: 2
})

/**
 * 单个 Provider 的熔断与最近尝试快照，来自 `GET /llm/resilience/status`。
 *
 * **字段形状以后端 `LLMManager.get_resilience_status()` 为准**：熔断快照是
 * `**breaker.snapshot()` 平铺进来的，不是嵌在 `circuit` 对象里。此前这里声明成
 * 嵌套形状且带了后端并不返回的 `error_summary` / `ttft_seconds`——因为这个类型
 * 从来没有调用点，类型与实际响应不符的问题一直没被发现。有了调用点之后，
 * 这类偏差会在 `type-check` 阶段暴露，而不是等到运行时读到 undefined。
 */
export interface ProviderResilienceAttempt {
  trace_id: string
  provider: string
  model: string
  attempt: number
  retry_index: number
  success: boolean
  error_category: string | null
  started_at: number
  completed_at: number | null
  first_byte_at: number | null
  /** 已向用户产出过内容：此时不得静默切换供应商并拼接重复回复。 */
  partial_output: boolean
}

/**
 * 一次熔断状态迁移。
 *
 * 与 `ProviderResilienceRow` 的当前快照分属两件事：快照回答「现在是什么状态」，
 * 这里回答「什么时候、因为什么变成这个状态」。只有后者能复盘一次隔离——
 * 轮询间隔内发生的 open → half-open → closed 在快照里完全不可见。
 */
export interface ProviderCircuitTransition {
  from_state: 'closed' | 'open' | 'half-open'
  to_state: 'closed' | 'open' | 'half-open'
  /**
   * `failure_threshold` 连续失败达标；`error_rate` 错误率达标（处置不同：
   * 前者调阈值，后者查上游稳定性）；`recovery_timeout` 等待期走完转半开；
   * `recovery_success` 探测成功攒够阈值后闭合；`half_open_probe_failed`
   * 半开探测又失败、重新打开。
   */
  reason:
    | 'failure_threshold'
    | 'error_rate'
    | 'recovery_timeout'
    | 'recovery_success'
    | 'half_open_probe_failed'
  /** 单调时钟秒，与 `next_recovery_time` 同一时基。 */
  at: number
  failure_count: number
  error_rate: number
}

/**
 * 上游在响应头里报告的限额余量。
 *
 * 每一项都可以是 `null`——那表示**上游没报这一项**，与「这一项是 0」是两件不同
 * 的事，且只有后者要报警。很多兼容端点根本不返回限额头，把「没上报」显示成 0
 * 会造出一个不存在的紧急情况。
 *
 * 请求数与 Token 两组余量分开：它们会分别见底，且处置相反——请求数见底要降频，
 * Token 见底要缩短上下文。
 */
export interface RateLimitHeadroom {
  limit_requests: number | null
  remaining_requests: number | null
  limit_tokens: number | null
  remaining_tokens: number | null
  reset_requests_seconds: number | null
  reset_tokens_seconds: number | null
  /** 上游明确要求的等待秒数（`retry-after`），通常只在 429 时出现。 */
  retry_after_seconds: number | null
  /** 余量比例 0–1。缺 limit 或 remaining 任一半时为 `null`，不反推。 */
  request_headroom: number | null
  token_headroom: number | null
}

/**
 * 一个后端的自动检测计划状态。字段与 `TaskScheduler.get_status()` 平铺一致。
 */
export interface AutoDetectScheduleRow {
  name: string
  /** 天。`0` 表示这个后端关闭了自动检测。 */
  interval_days: number
  /**
   * 上一次**成功**检测的 ISO 时间；`null` 表示从未成功跑过。
   *
   * `null` 与「很久以前」是两件事：前者可能是从没到期、也可能是每次都失败，
   * 界面必须让它区别于一个真实的旧时间戳，否则运维会以为它跑过。
   */
  last_run: string | null
  /** 当前这个后端配置里有多少个模型。 */
  model_count: number
}

export interface AutoDetectScheduleResponse {
  /** 后台调度循环是否在跑。为 false 时所有间隔配置都不会生效。 */
  running: boolean
  backends: AutoDetectScheduleRow[]
}

export interface ProviderResilienceRow {
  model: string
  provider: string
  /** 数字越小越优先；队列即按它排序。 */
  priority: number
  state: 'closed' | 'open' | 'half-open'
  failure_count: number
  error_rate: number
  requests: number
  recovery_successes: number
  recovery_success_threshold: number
  /** 熔断打开时的预计恢复时刻（单调时钟秒），closed/half-open 为 null。 */
  next_recovery_time: number | null
  recent_error_category: string | null
  recent_attempts: ProviderResilienceAttempt[]
  /** 最近 10 次状态迁移，最早在前。 */
  recent_transitions: ProviderCircuitTransition[]
  /**
   * 上游报告的限额余量；上游从不报这些头时为 `null`。
   *
   * 与熔断状态同行，因为两者回答同一个问题的两面：「这家现在能不能用」。
   * 熔断说的是「它已经坏了」，余量说的是「它还剩多少、多久后会坏」——
   * 后者是唯一能在撞上限之前给出信号的东西。
   */
  rate_limit: RateLimitHeadroom | null
}

/**
 * 兼容别名：早期声明使用了这个名字。保留导出以免外部引用断裂，
 * 但字段已按后端真实响应更正。
 */
export type ProviderResilienceStatus = ProviderResilienceRow

export interface ConfigSchema {
  title: string
  type: string
  properties: Record<
    string,
    {
      title: string
      type: string
      description?: string
      default?: any
      minimum?: number
      maximum?: number
      enum?: any[]
      enumNames?: string[]
    }
  >
  required?: string[]
}

export interface WebUIChatRequest {
  message: string
  session_id: string
  username: string
  chat_type: 'c2c' | 'group'
  group_id?: string
  agent_id?: string
}

export interface WebUIChatResponse {
  status: 'completed' | 'awaiting_confirmation'
  text: string
  agent_id: string | null
  session_id: string
  session_key: string
  confirmation_id: string | null
}

export interface PricingVersion {
  version_id: string
  provider: string
  model: string
  effective_from: string
  currency: string
  input_per_million: string
  output_per_million: string
  cache_read_per_million: string
  cache_write_per_million: string
}

export interface PricingCatalogResponse {
  data: {
    revision: number
    versions: PricingVersion[]
    backup_generations: number[]
  }
}

export const llmApi = {
  /**
   * 通过统一渠道调度链发送 WebUI 消息
   */
  chat(payload: WebUIChatRequest) {
    return http.post<WebUIChatResponse>('/llm/chat', payload)
  },

  /**
   * 获取适配器类型列表
   */
  getAdapterTypes(signal?: AbortSignal) {
    return http.get<{ types: string[] }>('/llm/types', { signal })
  },

  /**
   * 获取后端列表
   */
  getBackends(signal?: AbortSignal) {
    return http.get<{ data: { backends: LLMBackend[] } }>('/llm/backends', { signal })
  },

  /**
   * 创建后端
   */
  createBackend(backend: LLMBackend) {
    return http.post<{ data: LLMBackend }>('/llm/backends', backend)
  },

  /**
   * 更新后端
   */
  updateBackend(name: string, backend: LLMBackend) {
    return http.put<{ data: LLMBackend }>(`/llm/backends/${name}`, backend)
  },

  /**
   * 删除后端
   */
  deleteBackend(name: string) {
    return http.delete<void>(`/llm/backends/${name}`)
  },

  /**
   * 启用/禁用后端
   */
  toggleBackend(name: string, enable: boolean) {
    return http.post<void>(`/llm/backends/${name}/${enable ? 'enable' : 'disable'}`)
  },

  /**
   * 获取适配器配置模式
   */
  getAdapterConfigSchema(adapterType: string, signal?: AbortSignal) {
    return http.get<{ configSchema: ConfigSchema }>(`/llm/types/${adapterType}/config-schema`, {
      signal
    })
  },

  /**
   * 获取适配器支持自动检测模型
   */
  getAdapterSupportsAutoDetectModels(adapterType: string, signal?: AbortSignal) {
    return http.get<{ supportsAutoDetectModels: boolean }>(
      `/llm/types/${adapterType}/supports-auto-detect-models`,
      { signal }
    )
  },

  /**
   * 获取后端支持的模型列表
   */
  getBackendModels(backendName: string, signal?: AbortSignal) {
    return http.get<{ models: ModelInfo[] }>(`/llm/backends/${backendName}/auto-detect-models`, {
      signal
    })
  },

  /**
   * 模型目录的定期自动刷新计划。
   *
   * 后端一直提供这三个接口，但前端**零调用点**——运维要改一个后端的检测间隔
   * 只能 curl 或者手改 `data/config.yaml` 再重启。「定期自动刷新」这件事在产品上
   * 因此不可见：没有页面说明下一轮什么时候跑、上一轮跑没跑成、这个后端到底有没有
   * 开启。这里补上调用点，由「模型 → 自动检测计划」页消费。
   */
  getAutoDetectSchedule(signal?: AbortSignal) {
    return http.get<AutoDetectScheduleResponse>('/llm/auto-detect-schedule', { signal })
  },

  /**
   * 改一个后端的检测间隔天数。`0` 表示关闭。
   *
   * 这个动作会**写入 `data/config.yaml`**（后端带备份保存），因此界面上必须说明
   * 它不是一次临时查询。后端拒绝负数（400）。
   */
  updateAutoDetectSchedule(backendName: string, intervalDays: number) {
    return http.put<{ name: string; interval_days: number }>(
      `/llm/backends/${encodeURIComponent(backendName)}/auto-detect-schedule`,
      { interval_days: intervalDays }
    )
  },

  /**
   * 立刻对所有配置了间隔的后端跑一轮检测。
   *
   * 这会**访问每一个上游**并可能改写配置里的模型目录，不是只读操作。
   * 界面上要有确认，且要说清影响范围——它不是「刷新一下页面」。
   */
  runAutoDetectNow() {
    return http.post<{ results: Record<string, boolean> }>(
      '/llm/auto-detect-schedule/run'
    )
  },

  listPricing() {
    return http.get<PricingCatalogResponse>('/llm/pricing')
  },

  /**
   * 供应商容错状态：优先级队列、熔断三态与最近尝试记录。
   *
   * 后端一直提供这个接口（`GET /llm/resilience/status`），但前端从未调用——
   * 「故障转移队列可查」在产品上等于只能 curl。这里补上调用点，
   * 由模型管理页的容错状态面板消费。
   */
  getResilienceStatus(signal?: AbortSignal) {
    return http.get<{ data: ProviderResilienceRow[] }>('/llm/resilience/status', { signal })
  },

  /**
   * 把一个 Provider 的熔断器清回 `closed`，并同时撤销持久化的隔离。
   *
   * 没有这个动作时，一次上游抖动打开的熔断只能等满恢复窗口，或者重启整个
   * 进程——而重启会一并中断所有正在进行的对话。面板显示 `已熔断` 却没有任何
   * 动作能改变它，是这条接口存在的理由。
   *
   * `confirmed: true` 是后端的硬要求（缺它返回 400）：这个动作把一个刚被判定
   * 不健康的上游放回真实流量，因此不接受「顺手点一下」。
   */
  resetProviderCircuit(name: string) {
    return http.post<{ data: ProviderResilienceRow[] }>(
      `/llm/backends/${encodeURIComponent(name)}/circuit/reset`,
      { confirmed: true }
    )
  },

  createPricing(payload: { expected_revision: number; version: PricingVersion }) {
    return http.post<{ data: { revision: number; version: PricingVersion } }>('/llm/pricing', payload)
  },

  updatePricing(versionId: string, payload: { expected_revision: number; version: PricingVersion }) {
    return http.put<{ data: { revision: number; version: PricingVersion } }>(
      `/llm/pricing/${encodeURIComponent(versionId)}`,
      payload
    )
  },

  deletePricing(versionId: string, payload: { expected_revision: number; confirmed: true }) {
    return http.delete<{ data: { revision: number; version_id: string } }>(
      `/llm/pricing/${encodeURIComponent(versionId)}`,
      { body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' } }
    )
  },

  importPricing(payload: { expected_revision: number; catalog: unknown }) {
    return http.post<{ data: { revision: number; imported_count: number } }>(
      '/llm/pricing/import',
      payload
    )
  },

  restorePricing(payload: { expected_revision: number; generation: number; confirmed: true }) {
    return http.post<{ data: { revision: number; restored_generation: number; version_count: number } }>(
      '/llm/pricing/restore',
      payload
    )
  },

  exportPricing() {
    return http.fetch('/llm/pricing/export', { method: 'GET' })
  },

  /**
   * 导出供应商配置文档（**不含凭据**）。
   *
   * 走 `http.fetch` 而不是 `http.get`：后端带 `Content-Disposition`，
   * 需要拿到原始 `Response` 才能按它命名下载文件；`http.get` 会把响应体
   * 当 JSON 解析，头信息就丢了。
   */
  exportBackends() {
    return http.fetch('/llm/backends/export', { method: 'GET' })
  },

  /**
   * 导入供应商配置文档。
   *
   * `overwrite` 默认 `false`：同名后端由后端返回 409 并列出冲突名单，
   * 由用户确认后再重发 `overwrite: true`。静默覆盖会让目标机器上已填好的
   * 凭据与容错参数被一份导出文件冲掉。
   *
   * body 只允许 `document` 与 `overwrite`——后端对未知字段直接 400。
   */
  importBackends(payload: { document: unknown; overwrite?: boolean }) {
    return http.post<{ data: { imported_count: number; overwritten: string[] } }>(
      '/llm/backends/import',
      { document: payload.document, overwrite: payload.overwrite ?? false }
    )
  }
}
