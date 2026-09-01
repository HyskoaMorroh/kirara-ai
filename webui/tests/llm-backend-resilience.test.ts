// @vitest-environment happy-dom

/**
 * The backend accepts a full resilience budget on every backend write
 * (`LLMBackendUpdateRequest` extends `LLMBackendConfig`), but the WebUI's
 * `LLMBackend` type only declared name/adapter/config/enable/models. Any save
 * from the UI therefore posted a payload without `priority`,
 * `participate_in_failover`, the retry/timeout keys or the circuit keys — and
 * pydantic filled every missing field with its default, silently resetting a
 * tuned provider back to stock on an unrelated edit.
 *
 * These tests pin the round trip: what the list endpoint returns must survive
 * an edit that only touches one unrelated field.
 */

import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LLMView from '../src/views/llm/LLMView.vue'
import { resilienceDefaults } from '../src/api/llm'
import type { ConfigSchema, LLMBackend } from '../src/api/llm'

const { messageError, messageSuccess, llmApi } = vi.hoisted(() => ({
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  llmApi: {
    getAdapterTypes: vi.fn(),
    getBackends: vi.fn(),
    getAdapterConfigSchema: vi.fn(),
    getAdapterSupportsAutoDetectModels: vi.fn(),
    createBackend: vi.fn(),
    updateBackend: vi.fn(),
    deleteBackend: vi.fn(),
    getBackendModels: vi.fn()
  }
}))

vi.mock('naive-ui', () => ({
  useMessage: () => ({ error: messageError, success: messageSuccess, warning: vi.fn() }),
  NModal: { template: '<div><slot /></div>' },
  NCard: { template: '<div><slot /></div>' },
  // 供应商配置导入 / 导出的工具条与冲突提示也用到这两个组件。
  NAlert: { template: '<div><slot /><slot name="action" /></div>' },
  NButton: { template: '<button><slot /></button>' }
}))

// The view depends on the real defaults table, so only the HTTP surface is mocked.
vi.mock('../src/api/llm', async () => {
  const actual = await vi.importActual<typeof import('../src/api/llm')>('../src/api/llm')
  return { resilienceDefaults: actual.resilienceDefaults, llmApi }
})

const schema = (title: string): ConfigSchema => ({ title, type: 'object', properties: {} })

/** A provider whose resilience budget has been tuned away from every default. */
const tunedBackend = (): LLMBackend => ({
  name: 'tuned',
  adapter: 'adapter-a',
  config: { endpoint: 'https://example.invalid' },
  enable: true,
  models: [],
  auto_detect_interval_days: 9,
  priority: 1,
  participate_in_failover: true,
  max_retries: 6,
  retry_backoff_seconds: 0.75,
  retry_backoff_max_seconds: 12,
  request_timeout_seconds: 600,
  non_stream_timeout_seconds: 600,
  stream_first_byte_timeout_seconds: 90,
  stream_idle_timeout_seconds: 180,
  stream_total_timeout_seconds: 900,
  circuit_failure_threshold: 8,
  circuit_error_rate_threshold: 0.7,
  circuit_min_requests: 15,
  circuit_recovery_timeout_seconds: 90,
  circuit_recovery_success_threshold: 3
})

const mountView = () =>
  mount(LLMView, {
    global: {
      stubs: {
        LLMAdapterList: {
          name: 'LLMAdapterList',
          props: ['adapters', 'selectedAdapter'],
          emits: ['select', 'create'],
          template: '<div />'
        },
        LLMAdapterConfig: {
          name: 'LLMAdapterConfig',
          props: [
            'adapter',
            'adapterTypes',
            'configSchema',
            'loading',
            'isCreating',
            'isAutoDetectModelsSupported',
            'modelAbilities'
          ],
          emits: [
            'update:adapter',
            'save',
            'delete',
            'add-model',
            'edit-model',
            'auto-detect-models'
          ],
          template: '<div />'
        },
        LLMEmptyState: true,
        LLMModelForm: true,
        LLMConfirmContent: true
      }
    }
  })

describe('LLM backend resilience round trip', () => {
  beforeEach(() => {
    messageError.mockReset()
    messageSuccess.mockReset()
    Object.values(llmApi).forEach((mock) => mock.mockReset())
    llmApi.getAdapterTypes.mockResolvedValue({ types: ['adapter-a'] })
    llmApi.getBackends.mockResolvedValue({ data: { backends: [tunedBackend()] } })
    llmApi.getAdapterConfigSchema.mockResolvedValue({ configSchema: schema('default') })
    llmApi.getAdapterSupportsAutoDetectModels.mockResolvedValue({
      supportsAutoDetectModels: false
    })
    llmApi.updateBackend.mockResolvedValue({ data: tunedBackend() })
    llmApi.createBackend.mockResolvedValue({ data: tunedBackend() })
  })

  it('keeps every tuned resilience field when saving an unrelated edit', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent({ name: 'LLMAdapterList' }).vm.$emit('select', tunedBackend())
    await nextTick()

    const config = wrapper.findComponent({ name: 'LLMAdapterConfig' })
    const edited = { ...(config.props('adapter') as LLMBackend), enable: false }
    config.vm.$emit('update:adapter', edited)
    await nextTick()
    wrapper.findComponent({ name: 'LLMAdapterConfig' }).vm.$emit('save')
    await flushPromises()

    expect(llmApi.updateBackend).toHaveBeenCalledTimes(1)
    const [, payload] = llmApi.updateBackend.mock.calls[0] as [string, LLMBackend]
    const expected = tunedBackend()
    expect(payload.enable).toBe(false)
    expect(payload.priority).toBe(expected.priority)
    expect(payload.participate_in_failover).toBe(expected.participate_in_failover)
    expect(payload.max_retries).toBe(expected.max_retries)
    expect(payload.retry_backoff_seconds).toBe(expected.retry_backoff_seconds)
    expect(payload.retry_backoff_max_seconds).toBe(expected.retry_backoff_max_seconds)
    expect(payload.non_stream_timeout_seconds).toBe(expected.non_stream_timeout_seconds)
    expect(payload.stream_first_byte_timeout_seconds).toBe(
      expected.stream_first_byte_timeout_seconds
    )
    expect(payload.stream_idle_timeout_seconds).toBe(expected.stream_idle_timeout_seconds)
    expect(payload.stream_total_timeout_seconds).toBe(expected.stream_total_timeout_seconds)
    expect(payload.circuit_failure_threshold).toBe(expected.circuit_failure_threshold)
    expect(payload.circuit_error_rate_threshold).toBe(expected.circuit_error_rate_threshold)
    expect(payload.circuit_min_requests).toBe(expected.circuit_min_requests)
    expect(payload.circuit_recovery_timeout_seconds).toBe(
      expected.circuit_recovery_timeout_seconds
    )
    expect(payload.circuit_recovery_success_threshold).toBe(
      expected.circuit_recovery_success_threshold
    )
    expect(payload.auto_detect_interval_days).toBe(expected.auto_detect_interval_days)
  })

  it('gives a newly created backend the documented resilience defaults', async () => {
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent({ name: 'LLMAdapterList' }).vm.$emit('create', 'adapter-a')
    await nextTick()

    const created = wrapper.findComponent({ name: 'LLMAdapterConfig' }).props(
      'adapter'
    ) as LLMBackend
    // 断言「带齐了默认值」，而不是复制一份具体数字。
    // 之前这里写死 15/30，后端把超时默认放宽后这条测试会失败，而失败原因
    // 与它想守的行为（新建时字段不为 undefined）毫无关系。两端默认值是否
    // 一致由 resilience-defaults-parity.test.ts 单独钉住。
    const defaults = resilienceDefaults()
    expect(created.priority).toBe(defaults.priority)
    expect(created.participate_in_failover).toBe(defaults.participate_in_failover)
    expect(created.max_retries).toBe(defaults.max_retries)
    expect(created.stream_first_byte_timeout_seconds).toBe(
      defaults.stream_first_byte_timeout_seconds
    )
    expect(created.stream_idle_timeout_seconds).toBe(defaults.stream_idle_timeout_seconds)
    expect(created.circuit_failure_threshold).toBe(defaults.circuit_failure_threshold)
    expect(created.circuit_min_requests).toBe(defaults.circuit_min_requests)
  })
})
