// @vitest-environment happy-dom

// 需求 14「首次上手」：新部署第一次打开必须看得见「你还缺什么、下一步做什么」。
//
// 后端 `GET /system/readiness` 一直提供 7 项检查，每项都带 summary 与
// **可执行的 remediation**。但 `webui/src/api/system.ts` 是个 0 字节空文件，
// 全仓库 `grep readiness` 在 `webui/src/` 零命中——那份诊断只能靠 curl 看到，
// 文档里也确实是教用户手敲 curl。最需要它的恰恰是刚部署完、还没配好任何东西
// 的人，而那种人不会去读 curl 示例。
//
// 另一半问题：引导步骤的完成状态是**纯前端点击痕迹**（点一下就写 localStorage），
// 不校验是否真配了 IM/LLM。于是换个浏览器全部归零、配好了也不打勾——
// 一个勾选状态与事实无关的清单，比没有清单更糟：它会让人以为自己配完了。

import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import GuideView from '../src/views/guide/GuideView.vue'

const { messageSuccess, routerPush, getReadiness } = vi.hoisted(() => ({
  messageSuccess: vi.fn(),
  routerPush: vi.fn(),
  getReadiness: vi.fn()
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: routerPush }) }))

vi.mock('../src/api/system', () => ({
  systemApi: { getReadiness },
  READINESS_CHECK_LABELS: {
    data_directories_writable: '数据目录可写',
    im_available: '聊天平台已连接',
    llm_available: '模型后端可用'
  }
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
    template: '<button type="button" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'
  },
  NCard: {
    props: ['title'],
    template: '<section><header>{{ title }}<slot name="header-extra" /></header><slot /></section>'
  },
  NAlert: { props: ['type'], template: '<div><slot /></div>' },
  NDivider: { template: '<hr />' },
  NGi: { template: '<div><slot /></div>' },
  NGrid: { template: '<div><slot /></div>' },
  NIcon: { template: '<i><slot /></i>' },
  NPopconfirm: { emits: ['positive-click'], template: '<div><slot name="trigger" /><slot /></div>' },
  NProgress: { template: '<div />' },
  NSpace: { template: '<div><slot /></div>' },
  NStep: { template: '<div><slot /></div>' },
  NSteps: { template: '<div><slot /></div>' },
  NStatistic: { props: ['label'], template: '<div>{{ label }}<slot /></div>' },
  NRow: { template: '<div><slot /></div>' },
  NCol: { template: '<div><slot /></div>' },
  NTag: { props: ['type'], template: '<span><slot /></span>' },
  NTooltip: { template: '<div><slot name="trigger" /><slot /></div>' }
}))

const check = (
  id: string,
  status: string,
  summary: string,
  remediation: string
) => ({ id, status, summary, remediation, evidence: {} })

describe('GuideView readiness panel', () => {
  beforeEach(() => {
    localStorage.clear()
    getReadiness.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows what is still missing and what to do about it', async () => {
    getReadiness.mockResolvedValue({
      ready: false,
      timestamp: '2026-08-29T10:00:00Z',
      checks: [
        check('data_directories_writable', 'pass', '数据目录可写', '无需处理'),
        check('im_available', 'warn', '部分 IM 适配器尚未建立连接', '检查 IM 适配器运行状态'),
        check('llm_available', 'fail', '没有可用的模型后端', '添加并启用至少一个 LLM 后端')
      ]
    })

    const wrapper = mount(GuideView)
    await flushPromises()

    const text = wrapper.text()
    // remediation 是这块面板存在的全部理由：它是「下一步做什么」。
    expect(text).toContain('添加并启用至少一个 LLM 后端')
    expect(text).toContain('检查 IM 适配器运行状态')
    // 通过的检查不该占据视觉重量，但也不能凭空消失——需要能确认它检查过。
    expect(wrapper.find('[data-test="readiness-panel"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('does not show remediation for checks that passed', async () => {
    getReadiness.mockResolvedValue({
      ready: true,
      timestamp: '2026-08-29T10:00:00Z',
      checks: [check('data_directories_writable', 'pass', '数据目录可写', '无需处理')]
    })

    const wrapper = mount(GuideView)
    await flushPromises()

    // 「无需处理」出现在界面上等于噪声：全绿时应该说一句「都就绪了」。
    expect(wrapper.text()).not.toContain('无需处理')

    wrapper.unmount()
  })

  it('treats an unreachable readiness endpoint as unknown rather than broken', async () => {
    // 读不到就绪状态不等于「没就绪」。把前者显示成后者，会让人去修一个
    // 不存在的问题——而真正的问题只是这一个诊断接口没响应。
    getReadiness.mockRejectedValue(new Error('network down'))

    const wrapper = mount(GuideView)
    await flushPromises()

    const text = wrapper.text()
    expect(text).not.toContain('未就绪')
    expect(wrapper.find('[data-test="readiness-unknown"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('marks a step complete from real readiness, not from having clicked it', async () => {
    // 这是「勾选状态与事实无关」那一半。llm_available 为 pass 时，
    // 即使用户从未点过那一步，它也应该显示为已完成。
    getReadiness.mockResolvedValue({
      ready: false,
      timestamp: '2026-08-29T10:00:00Z',
      checks: [
        check('im_available', 'fail', '没有启用任何聊天平台', '启用至少一个 IM 适配器'),
        check('llm_available', 'pass', '模型后端可用', '无需处理')
      ]
    })

    const wrapper = mount(GuideView)
    await flushPromises()

    expect(wrapper.find('[data-test="step-llm-verified"]').exists()).toBe(true)
    // 反过来同样重要：点过但其实没配好，不能显示成已完成。
    expect(wrapper.find('[data-test="step-im-verified"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('never reports a step as verified when readiness is unknown', async () => {
    // 拿不到就绪状态时不能断言任何一步「已完成」：那是一个我们没有依据的论断。
    getReadiness.mockRejectedValue(new Error('network down'))
    localStorage.setItem('completedSteps', JSON.stringify({ llm: true }))

    const wrapper = mount(GuideView)
    await flushPromises()

    expect(wrapper.find('[data-test="step-llm-verified"]').exists()).toBe(false)

    wrapper.unmount()
  })
})
