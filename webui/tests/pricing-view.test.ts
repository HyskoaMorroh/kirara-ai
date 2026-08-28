// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PricingView from '../src/views/llm/PricingView.vue'
import type { PricingVersion } from '../src/api/llm'
import { HttpRequestError } from '../src/utils/http'

const { api, dialogWarning, message } = vi.hoisted(() => ({
  api: {
    listPricing: vi.fn(),
    createPricing: vi.fn(),
    updatePricing: vi.fn(),
    deletePricing: vi.fn(),
    importPricing: vi.fn(),
    restorePricing: vi.fn(),
    exportPricing: vi.fn()
  },
  dialogWarning: vi.fn(),
  message: {
    success: vi.fn(),
    error: vi.fn()
  }
}))

vi.mock('../src/api/llm', async () => {
  const actual = await vi.importActual<typeof import('../src/api/llm')>('../src/api/llm')
  return { ...actual, llmApi: api }
})

vi.mock('naive-ui', () => {
  const NButton = defineComponent({
    name: 'NButton',
    inheritAttrs: false,
    emits: ['click'],
    setup(_props, { attrs, emit, slots }) {
      return () =>
        h(
          'button',
          { ...attrs, type: 'button', onClick: () => emit('click') },
          [slots.icon?.(), slots.default?.()]
        )
    }
  })
  const passthrough = (name: string) =>
    defineComponent({
      name,
      inheritAttrs: false,
      setup(_props, { attrs, slots }) {
        return () => h('div', attrs, slots.default?.())
      }
    })

  return {
    NAlert: passthrough('NAlert'),
    NButton,
    NCard: passthrough('NCard'),
    NTag: passthrough('NTag'),
    useDialog: () => ({ warning: dialogWarning }),
    useMessage: () => message
  }
})

const version = (overrides: Partial<PricingVersion> = {}): PricingVersion => ({
  version_id: 'openai:gpt-4o:2026-08-27',
  provider: 'openai',
  model: 'gpt-4o',
  effective_from: '2026-08-27T00:00:00Z',
  currency: 'USD',
  input_per_million: '2.5',
  output_per_million: '10',
  cache_read_per_million: '1.25',
  cache_write_per_million: '2.5',
  ...overrides
})

const listResponse = (versions: PricingVersion[] = [version()], revision = 3) => ({
  data: { revision, versions, backup_generations: [1, 2] }
})

const mountView = () => mount(PricingView)

describe('PricingView', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
    Object.values(message).forEach((mock) => mock.mockReset())
    dialogWarning.mockReset()
    api.listPricing.mockResolvedValue(listResponse())
    api.createPricing.mockResolvedValue({ data: { revision: 4, version: version() } })
    api.updatePricing.mockResolvedValue({ data: { revision: 4, version: version() } })
    api.deletePricing.mockResolvedValue({ data: { revision: 4, version_id: version().version_id } })
    api.importPricing.mockResolvedValue({ data: { revision: 4, imported_count: 1 } })
    api.restorePricing.mockResolvedValue({
      data: { revision: 4, restored_generation: 1, version_count: 1 }
    })
  })

  it('loads the catalog and exposes a compact empty state when no versions exist', async () => {
    api.listPricing.mockResolvedValue(listResponse([], 0))

    const wrapper = mountView()
    await flushPromises()

    expect(api.listPricing).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('还没有成本定价版本')
    expect(wrapper.find('[data-test="create-pricing"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('修订 0')
  })

  it('creates a pricing version with the current revision and refreshes the list', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-test="create-pricing"]').trigger('click')
    await wrapper.get('input[name="provider"]').setValue('anthropic')
    await wrapper.get('input[name="model"]').setValue('claude-sonnet')
    await wrapper.get('input[name="version_id"]').setValue('anthropic:claude-sonnet:2026-08-27')
    await wrapper.get('[data-test="save-pricing"]').trigger('click')
    await flushPromises()

    expect(api.createPricing).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 3,
        version: expect.objectContaining({
          provider: 'anthropic',
          model: 'claude-sonnet',
          version_id: 'anthropic:claude-sonnet:2026-08-27'
        })
      })
    )
    expect(api.listPricing).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('定价版本已保存')
  })

  it('requires confirmation before delete and restore operations', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get(`[aria-label="删除定价 ${version().version_id}"]`).trigger('click')
    expect(dialogWarning).toHaveBeenCalledOnce()
    expect(api.deletePricing).not.toHaveBeenCalled()
    await dialogWarning.mock.calls[0][0].onPositiveClick()
    expect(api.deletePricing).toHaveBeenCalledWith(version().version_id, {
      expected_revision: 3,
      confirmed: true
    })

    await wrapper.get('[aria-label="恢复第 1 代备份"]').trigger('click')
    expect(dialogWarning).toHaveBeenCalledTimes(2)
    expect(api.restorePricing).not.toHaveBeenCalled()
    await dialogWarning.mock.calls[1][0].onPositiveClick()
    expect(api.restorePricing).toHaveBeenCalledWith({
      expected_revision: 3,
      generation: 1,
      confirmed: true
    })
  })

  it('imports structured JSON, downloads exports, and never displays a server path', async () => {
    const wrapper = mountView()
    await flushPromises()
    const file = new File([JSON.stringify({ schema: 'kirara-ai.price-catalog' })], 'prices.json', {
      type: 'application/json'
    })
    const fileInput = wrapper.get('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    await flushPromises()

    expect(api.importPricing).toHaveBeenCalledWith({
      expected_revision: 3,
      catalog: { schema: 'kirara-ai.price-catalog' }
    })

    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    api.exportPricing.mockResolvedValue(
      new Response('{"schema":"kirara-ai.price-catalog"}', {
        headers: { 'Content-Disposition': 'attachment; filename="price-catalog.json"' }
      })
    )
    await wrapper.get('[data-test="export-pricing"]').trigger('click')
    await flushPromises()

    expect(api.exportPricing).toHaveBeenCalledOnce()
    expect(click).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('E:\\')
    click.mockRestore()
  })

  it('refreshes after a revision conflict and tells the operator to retry', async () => {
    api.updatePricing.mockRejectedValueOnce(
      new HttpRequestError('Price catalog revision conflict', 409, {
        code: 'revision_conflict',
        expected_revision: 3,
        current_revision: 4
      })
    )
    api.listPricing.mockResolvedValueOnce(listResponse()).mockResolvedValueOnce(
      listResponse([version({ input_per_million: '3.5' })], 4)
    )

    const wrapper = mountView()
    await flushPromises()
    await wrapper.get(`[aria-label="编辑定价 ${version().version_id}"]`).trigger('click')
    await wrapper.get('[data-test="save-pricing"]').trigger('click')
    await flushPromises()

    expect(api.listPricing).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('定价目录已被其他操作更新')
    expect(wrapper.get('input[name="input_per_million"]').element.value).toBe('3.5')
  })
})
