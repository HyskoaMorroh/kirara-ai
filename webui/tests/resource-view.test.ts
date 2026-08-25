// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResourceView from '../src/views/resources/ResourceView.vue'
import type { ManagedResource, ResourceType } from '../src/api/resource'

const { api, dialogWarning, message } = vi.hoisted(() => ({
  api: {
    listResources: vi.fn(),
    listResourceAudit: vi.fn(),
    listRepositories: vi.fn(),
    installResource: vi.fn(),
    updateResource: vi.fn(),
    enableResource: vi.fn(),
    disableResource: vi.fn(),
    bindResourceWorkflow: vi.fn(),
    restoreResource: vi.fn(),
    checkResourceUpdates: vi.fn(),
    updateRemoteResource: vi.fn(),
    searchResourceCatalog: vi.fn(),
    getCatalogItem: vi.fn(),
    installCatalogItem: vi.fn()
  },
  dialogWarning: vi.fn(),
  message: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn()
  }
}))

vi.mock('../src/api/resource', () => ({
  ...api,
  type: undefined
}))

vi.mock('../src/api/agent', () => ({
  listAgents: vi.fn().mockResolvedValue([])
}))

vi.mock('naive-ui', () => {
  const passthrough = (tag: string, props: Record<string, unknown> = {}) =>
    defineComponent({
      name: tag,
      inheritAttrs: false,
      props,
      setup(_props, { slots, attrs }) {
        return () => h('div', attrs, slots.default?.())
      }
    })

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

  const NSelect = defineComponent({
    name: 'NSelect',
    props: {
      value: { type: String, required: true },
      options: { type: Array, required: true }
    },
    emits: ['update:value'],
    setup(props, { emit, attrs }) {
      return () =>
        h(
          'select',
          {
            ...attrs,
            value: props.value,
            onChange: (event: Event) =>
              emit('update:value', (event.target as HTMLSelectElement).value)
          },
          props.options.map((option) =>
            h('option', { value: (option as { value: string }).value }, (option as { label: string }).label)
          )
        )
    }
  })

  const NTag = defineComponent({
    name: 'NTag',
    setup(_props, { slots }) {
      return () => h('span', { class: 'tag' }, slots.default?.())
    }
  })

  const NTooltip = defineComponent({
    name: 'NTooltip',
    setup(_props, { slots }) {
      return () => h('span', { class: 'tooltip' }, slots.trigger?.())
    }
  })

  const NDataTable = defineComponent({
    name: 'NDataTable',
    props: {
      columns: { type: Array, required: true },
      data: { type: Array, required: true }
    },
    setup(props) {
      return () =>
        h(
          'div',
          { 'data-test': 'resource-table' },
          props.data.map((row) =>
            h(
              'div',
              { class: 'resource-row', 'data-resource-id': (row as ManagedResource).resource_id },
              props.columns.map((column) => {
                const typedColumn = column as {
                  key?: string
                  render?: (item: ManagedResource) => unknown
                }
                return h(
                  'div',
                  { class: 'resource-cell' },
                  typedColumn.render
                    ? [typedColumn.render(row as ManagedResource)]
                    : String((row as Record<string, unknown>)[typedColumn.key || ''] ?? '')
                )
              })
            )
          )
        )
    }
  })

  return {
    useDialog: () => ({ warning: dialogWarning }),
    useMessage: () => message,
    NAlert: passthrough('NAlert'),
    NButton,
    NCard: passthrough('NCard'),
    NDataTable,
    NDescriptions: passthrough('NDescriptions'),
    NDescriptionsItem: passthrough('NDescriptionsItem'),
    NDivider: passthrough('NDivider'),
    NEmpty: defineComponent({
      name: 'NEmpty',
      setup(_props, { slots }) {
        return () => h('div', { 'data-test': 'empty-state' }, [h('span', '暂无已安装资源'), slots.extra?.()])
      }
    }),
    NForm: passthrough('NForm'),
    NFormItem: passthrough('NFormItem'),
    NIcon: passthrough('NIcon'),
    NInput: defineComponent({
      name: 'NInput',
      props: { value: { type: String, default: '' } },
      emits: ['update:value'],
      setup(props, { emit, attrs }) {
        return () =>
          h('input', {
            ...attrs,
            value: props.value,
            onInput: (event: Event) => emit('update:value', (event.target as HTMLInputElement).value)
          })
      }
    }),
    NModal: defineComponent({
      name: 'NModal',
      props: { show: Boolean, title: String },
      setup(props, { slots }) {
        return () => (props.show ? h('section', { 'data-test': 'modal' }, [h('h2', props.title), slots.default?.(), slots.footer?.()]) : null)
      }
    }),
    NPagination: passthrough('NPagination'),
    NSelect,
    NSkeleton: passthrough('NSkeleton'),
    NSpace: passthrough('NSpace'),
    NTag,
    NTooltip,
    NUpload: passthrough('NUpload'),
    NUploadDragger: passthrough('NUploadDragger'),
    type: undefined
  }
})

const resource = (resourceId: string, type: ResourceType, enabled: boolean, confirmationRequired = false): ManagedResource => ({
  resource_id: resourceId,
  type,
  current_version: '1.0.0',
  source: 'local package',
  entry: 'main.md',
  permissions: ['prompt.read'],
  content_sha256: 'a'.repeat(64),
  enabled,
  confirmation_required: confirmationRequired,
  workflow_id: null,
  installed_at: '2026-08-23T00:00:00Z',
  updated_at: '2026-08-23T00:00:00Z',
  versions: [
    {
      version: '1.0.0',
      source: 'local package',
      entry: 'main.md',
      permissions: ['prompt.read'],
      content_sha256: 'a'.repeat(64),
      installed_at: '2026-08-23T00:00:00Z'
    }
  ]
})

const skill = resource('demo.skill', 'skill', true)
const prompt = resource('demo.prompt', 'prompt', false, true)

const mountView = () => mount(ResourceView)

describe('ResourceView lifecycle controls', () => {
  beforeEach(() => {
    Object.values(api).forEach((mock) => mock.mockReset())
    dialogWarning.mockReset()
    Object.values(message).forEach((mock) => mock.mockReset())
    api.listResources.mockImplementation((type?: ResourceType) =>
      Promise.resolve(type === 'prompt' ? [prompt] : [skill, prompt])
    )
    api.listResourceAudit.mockResolvedValue({ items: [], total: 0, offset: 0, limit: 10 })
    api.listRepositories.mockResolvedValue([])
    api.enableResource.mockResolvedValue(skill)
    api.disableResource.mockResolvedValue({ ...skill, enabled: false })
    api.checkResourceUpdates.mockResolvedValue([])
    api.updateRemoteResource.mockResolvedValue(skill)
    api.searchResourceCatalog.mockResolvedValue({
      query: '',
      type: null,
      items: [{
        catalog_id: 'prompt:office-research',
        type: 'prompt',
        name: 'Office and Research Assistant',
        description: '办公和研究提示词',
        version: '1.0.0',
        source: 'catalog://kirara/prompt/office-research',
        branch: 'main',
        installed: false,
        enabled: false,
        installs: 12
      }],
      total_count: 1,
      limit: 20,
      offset: 0,
      remote: {
        provider: 'skills.sh',
        status: 'not_requested',
        error: null,
        total_count: null
      }
    })
    api.getCatalogItem.mockResolvedValue({
      catalog_id: 'prompt:office-research',
      type: 'prompt',
      name: 'Office and Research Assistant',
      description: '办公和研究提示词',
      version: '1.0.0',
      source: 'catalog://kirara/prompt/office-research',
      installed: false,
      enabled: false
    })
    api.installCatalogItem.mockResolvedValue(prompt)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('filters by resource type and reloads the selected type', async () => {
    const wrapper = mountView()
    await flushPromises()

    const selector = wrapper.get('select')
    await selector.setValue('prompt')
    await flushPromises()

    expect(api.listResources).toHaveBeenLastCalledWith('prompt')
    expect(wrapper.findAll('[data-resource-id="demo.prompt"]')).toHaveLength(1)
    expect(wrapper.find('[data-resource-id="demo.skill"]').exists()).toBe(false)
  })

  it('renders enabled and confirmation-required resources with distinct text states', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('已启用')
    expect(wrapper.text()).toContain('未启用')
  })

  it('asks for confirmation before enabling and does not call the API before approval', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[aria-label="启用资源"]').trigger('click')

    expect(dialogWarning).toHaveBeenCalledOnce()
    expect(api.enableResource).not.toHaveBeenCalled()

    const dialog = dialogWarning.mock.calls[0][0] as { onPositiveClick: () => Promise<void> }
    await dialog.onPositiveClick()
    expect(api.enableResource).toHaveBeenCalledWith('demo.prompt', true)
  })

  it('exposes install and update entry points and keeps an empty state actionable', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('button[aria-label="更新资源"]').trigger('click')
    expect(wrapper.text()).toContain('更新 demo.skill')

    wrapper.unmount()
    api.listResources.mockResolvedValue([])
    const emptyWrapper = mountView()
    await flushPromises()
    expect(emptyWrapper.text()).toContain('暂无已安装资源')
    expect(emptyWrapper.text()).toContain('安装第一个资源')
    await emptyWrapper.get('[aria-label="安装第一个资源"]').trigger('click')
    expect(emptyWrapper.text()).toContain('安装资源')
  })

  it('checks a remote resource before offering the write update action', async () => {
    api.checkResourceUpdates.mockResolvedValue([{
      resource_id: 'demo.skill',
      current_version: '1.0.0',
      update_available: true,
      next_version: '1.1.0'
    }])

    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('button[aria-label="更新资源"]').trigger('click')
    expect(wrapper.text()).toContain('检查更新')
    expect(wrapper.find('button[aria-label="执行资源更新"]').exists()).toBe(false)

    await wrapper.get('button[aria-label="检查资源更新"]').trigger('click')
    await flushPromises()

    expect(api.checkResourceUpdates).toHaveBeenCalledWith('demo.skill')
    expect(wrapper.text()).toContain('发现新版本 1.1.0')
    expect(wrapper.get('button[aria-label="执行资源更新"]').exists()).toBe(true)
    await wrapper.get('button[aria-label="执行资源更新"]').trigger('click')
    expect(dialogWarning).toHaveBeenCalledOnce()
    expect(api.updateRemoteResource).not.toHaveBeenCalled()

    const dialog = dialogWarning.mock.calls[0][0] as { onPositiveClick: () => Promise<void> }
    await dialog.onPositiveClick()
    expect(api.updateRemoteResource).toHaveBeenCalledWith('demo.skill')
  })

  it('shows the latest state and never offers a write action when no update exists', async () => {
    api.checkResourceUpdates.mockResolvedValue([{
      resource_id: 'demo.skill',
      current_version: '1.0.0',
      update_available: false
    }])

    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('button[aria-label="更新资源"]').trigger('click')
    await wrapper.get('button[aria-label="检查资源更新"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已是最新')
    expect(wrapper.find('button[aria-label="执行资源更新"]').exists()).toBe(false)
    expect(dialogWarning).not.toHaveBeenCalled()
    expect(api.updateRemoteResource).not.toHaveBeenCalled()
  })

  it('keeps update writes unavailable when the remote check fails', async () => {
    api.checkResourceUpdates.mockRejectedValue(new Error('upstream failure'))

    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('button[aria-label="更新资源"]').trigger('click')
    await wrapper.get('button[aria-label="检查资源更新"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('检查更新失败，请重试')
    expect(wrapper.find('button[aria-label="执行资源更新"]').exists()).toBe(false)
    expect(dialogWarning).not.toHaveBeenCalled()
    expect(api.updateRemoteResource).not.toHaveBeenCalled()
  })

  it('uses the unified catalog for empty-query discovery and catalog installation', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('button[aria-label="发现并安装资源"]').trigger('click')
    await flushPromises()

    expect(api.searchResourceCatalog).toHaveBeenCalledWith(undefined, '', 20, 0)
    expect(wrapper.text()).toContain('Office and Research Assistant')
    expect(wrapper.text()).not.toContain('skills.sh 搜索')

    await wrapper.get('button[aria-label="查看目录资源"]').trigger('click')
    await flushPromises()
    expect(api.getCatalogItem).toHaveBeenCalledWith('prompt:office-research')

    await wrapper.get('button[aria-label="安装目录资源"]').trigger('click')
    expect(dialogWarning).toHaveBeenCalledOnce()
    const dialog = dialogWarning.mock.calls[0][0] as { onPositiveClick: () => Promise<void> }
    await dialog.onPositiveClick()
    expect(api.installCatalogItem).toHaveBeenCalledWith('prompt:office-research', 'main')
  })

  it('shows remote source URLs and refreshes catalog detail after installation', async () => {
    const remoteSkill = {
      catalog_id: 'skill:owner/repository:skills/agent-browser',
      type: 'skill' as const,
      name: 'agent-browser',
      description: 'Browser skill',
      source_key: 'owner/repository:skills/agent-browser',
      source_url: 'https://github.com/owner/repository/tree/main/skills/agent-browser',
      branch: 'main',
      installed: false,
      enabled: false
    }
    api.searchResourceCatalog.mockResolvedValue({
      query: 'agent-browser',
      type: 'skill',
      items: [remoteSkill],
      total_count: 1,
      limit: 20,
      offset: 0,
      remote: {
        provider: 'skills.sh',
        status: 'ok',
        error: null,
        total_count: 1
      }
    })
    api.getCatalogItem
      .mockResolvedValueOnce(remoteSkill)
      .mockResolvedValueOnce({
        ...remoteSkill,
        installed: true,
        installed_resource_id: 'skill.agent-browser'
      })
    api.installCatalogItem.mockResolvedValue(skill)

    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('button[aria-label="发现并安装资源"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain(remoteSkill.source_url)
    await wrapper.get('button[aria-label="查看目录资源"]').trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="安装详情目录资源"]').trigger('click')
    const dialog = dialogWarning.mock.calls[0][0] as { onPositiveClick: () => Promise<void> }
    await dialog.onPositiveClick()
    await flushPromises()

    expect(api.getCatalogItem).toHaveBeenLastCalledWith(remoteSkill.catalog_id)
    expect(api.getCatalogItem).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('已安装，未启用')
    expect(wrapper.find('button[aria-label="安装详情目录资源"]').exists()).toBe(false)
  })
})
