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

/** 单个 Provider 的熔断与最近尝试快照，来自 `GET /llm/resilience/status`。 */
export interface ProviderResilienceStatus {
  provider: string
  priority: number
  enable: boolean
  participate_in_failover: boolean
  circuit: {
    state: 'closed' | 'open' | 'half-open'
    failure_count: number
    error_rate: number
    requests: number
    recovery_successes: number
    recovery_success_threshold: number
    next_recovery_time: number | null
  }
  recent_attempts: {
    model: string
    attempt: number
    retry_index: number
    success: boolean
    error_category: string | null
    error_summary: string | null
    ttft_seconds: number | null
    partial_output: boolean
  }[]
}

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

  listPricing() {
    return http.get<PricingCatalogResponse>('/llm/pricing')
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
  }
}
