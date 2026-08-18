// @vitest-environment happy-dom

import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LLMView from '../src/views/llm/LLMView.vue'
import type { ConfigSchema, LLMBackend } from '../src/api/llm'

const deferred = <T>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const { messageError, llmApi } = vi.hoisted(() => ({
  messageError: vi.fn(),
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
  useMessage: () => ({ error: messageError, success: vi.fn() }),
  NModal: { template: '<div><slot /></div>' },
  NCard: { template: '<div><slot /></div>' }
}))

vi.mock('../src/api/llm', () => ({ llmApi }))

const backend = (name: string, adapter: string): LLMBackend => ({
  name,
  adapter,
  config: { endpoint: `${name}-endpoint` },
  enable: true,
  models: []
})

const schema = (title: string): ConfigSchema => ({ title, type: 'object', properties: {} })

const mountView = () =>
  mount(LLMView, {
    global: {
      stubs: {
        LLMAdapterList: {
          name: 'LLMAdapterList',
          props: ['adapters', 'selectedAdapter'],
          emits: ['select', 'create'],
          template: '<div data-test="adapter-list" />'
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
          template: '<div data-test="adapter-config" />'
        },
        LLMEmptyState: true,
        LLMModelForm: true,
        LLMConfirmContent: true
      }
    }
  })

describe('LLMView request ordering', () => {
  beforeEach(() => {
    messageError.mockReset()
    Object.values(llmApi).forEach((mock) => mock.mockReset())
    llmApi.getAdapterTypes.mockResolvedValue({ types: ['adapter-a', 'adapter-b'] })
    llmApi.getBackends.mockResolvedValue({ data: { backends: [] } })
    llmApi.getAdapterConfigSchema.mockResolvedValue({ configSchema: schema('default') })
    llmApi.getAdapterSupportsAutoDetectModels.mockResolvedValue({
      supportsAutoDetectModels: false
    })
  })

  it('ignores stale schema and support responses without clearing the current adapter config', async () => {
    const schemaA = deferred<{ configSchema: ConfigSchema }>()
    const schemaB = deferred<{ configSchema: ConfigSchema }>()
    const supportA = deferred<{ supportsAutoDetectModels: boolean }>()
    const supportB = deferred<{ supportsAutoDetectModels: boolean }>()
    llmApi.getAdapterConfigSchema.mockImplementation((adapterType: string) =>
      adapterType === 'adapter-a' ? schemaA.promise : schemaB.promise
    )
    llmApi.getAdapterSupportsAutoDetectModels.mockImplementation((adapterType: string) =>
      adapterType === 'adapter-a' ? supportA.promise : supportB.promise
    )

    const wrapper = mountView()
    await flushPromises()
    const list = wrapper.findComponent({ name: 'LLMAdapterList' })
    list.vm.$emit('select', backend('a', 'adapter-a'))
    await nextTick()
    list.vm.$emit('select', backend('b', 'adapter-b'))
    await nextTick()

    schemaB.resolve({ configSchema: schema('Schema B') })
    supportB.resolve({ supportsAutoDetectModels: true })
    await flushPromises()

    const config = wrapper.findComponent({ name: 'LLMAdapterConfig' })
    const edited = { ...backend('b', 'adapter-b'), config: { endpoint: 'manual-edit' } }
    config.vm.$emit('update:adapter', edited)
    await nextTick()

    schemaA.resolve({ configSchema: schema('Schema A') })
    supportA.resolve({ supportsAutoDetectModels: false })
    await flushPromises()

    const current = wrapper.findComponent({ name: 'LLMAdapterConfig' })
    expect((current.props('configSchema') as ConfigSchema).title).toBe('Schema B')
    expect((current.props('adapter') as LLMBackend).config.endpoint).toBe('manual-edit')
    expect(current.props('isAutoDetectModelsSupported')).toBe(true)
  })

  it('aborts schema and capability requests on unmount', async () => {
    let schemaSignal: AbortSignal | undefined
    let supportSignal: AbortSignal | undefined
    const pendingSchema = deferred<{ configSchema: ConfigSchema }>()
    const pendingSupport = deferred<{ supportsAutoDetectModels: boolean }>()
    llmApi.getAdapterConfigSchema.mockImplementation(
      (_adapterType: string, signal: AbortSignal) => {
        schemaSignal = signal
        return pendingSchema.promise
      }
    )
    llmApi.getAdapterSupportsAutoDetectModels.mockImplementation(
      (_adapterType: string, signal: AbortSignal) => {
        supportSignal = signal
        return pendingSupport.promise
      }
    )

    const wrapper = mountView()
    await flushPromises()
    wrapper.findComponent({ name: 'LLMAdapterList' }).vm.$emit(
      'select',
      backend('a', 'adapter-a')
    )
    await nextTick()

    expect(schemaSignal?.aborted).toBe(false)
    expect(supportSignal?.aborted).toBe(false)
    wrapper.unmount()
    expect(schemaSignal?.aborted).toBe(true)
    expect(supportSignal?.aborted).toBe(true)
  })
})
