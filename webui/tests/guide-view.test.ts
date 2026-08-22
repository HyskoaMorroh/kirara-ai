// @vitest-environment happy-dom

import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import GuideView from '../src/views/guide/GuideView.vue'

const { messageSuccess, routerPush } = vi.hoisted(() => ({
  messageSuccess: vi.fn(),
  routerPush: vi.fn()
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush })
}))

vi.mock('../src/stores/app', () => ({
  useAppStore: () => ({
    systemStatus: {
      uptime: 0,
      activeAdapters: 0,
      activeBackends: 0,
      loadedPlugins: 0,
      cpuUsage: 0,
      memoryUsage: { percent: 0, used: 0, free: 0 },
      version: 'test',
      platform: 'test',
      cpuInfo: 'test',
      pythonVersion: 'test'
    }
  })
}))

vi.mock('../src/components/LLMStatistics.vue', () => ({
  default: { template: '<div data-test="llm-statistics" />' }
}))

vi.mock('naive-ui', () => ({
  useMessage: () => ({ success: messageSuccess }),
  NButton: {
    emits: ['click'],
    template:
      '<button type="button" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'
  },
  NCard: {
    props: ['title'],
    template: '<section><header>{{ title }}<slot name="header-extra" /></header><slot /></section>'
  },
  NDivider: { template: '<hr />' },
  NGi: { template: '<div><slot /></div>' },
  NGrid: { template: '<div><slot /></div>' },
  NIcon: { template: '<i><slot /></i>' },
  NPopconfirm: {
    emits: ['positive-click'],
    template: '<div><slot name="trigger" /><slot /></div>'
  },
  NProgress: { template: '<div />' },
  NSpace: { template: '<div><slot /></div>' },
  NStep: { template: '<div><slot /></div>' },
  NSteps: { template: '<div><slot /></div>' },
  NTooltip: { template: '<div><slot name="trigger" /><slot /></div>' }
}))

const allStepsComplete = {
  plugins: true,
  im: true,
  llm: true,
  dispatch: true,
  workflow: true
}

describe('GuideView quick start recovery', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('hideGuide', 'true')
    localStorage.setItem('completedSteps', JSON.stringify(allStepsComplete))
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('restores a hidden completed guide from the quick start page', async () => {
    const wrapper = mount(GuideView)

    expect(wrapper.text()).toContain('快速开始引导已隐藏')
    expect(wrapper.text()).toContain('重新显示引导')

    const restoreButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('重新显示引导'))
    expect(restoreButton).toBeDefined()

    await restoreButton?.trigger('click')
    await nextTick()

    expect(wrapper.text()).toContain('快速开始')
    expect(wrapper.text()).toContain('重置进度')
    expect(localStorage.getItem('hideGuide')).toBe('false')

    wrapper.unmount()
  })
})
