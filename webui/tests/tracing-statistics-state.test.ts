// @vitest-environment happy-dom

import { describe, expect, it, vi } from 'vitest'

import { useTracingViewModel } from '../src/views/tracing/tracing.vm'
import type { TraceBase, TracerDelegate } from '../src/views/tracing/tracing.vm'

const { httpGet, httpPost, messageError } = vi.hoisted(() => ({
  httpGet: vi.fn(),
  httpPost: vi.fn(),
  messageError: vi.fn()
}))

vi.mock('../src/utils/http', () => ({
  http: {
    get: httpGet,
    post: httpPost,
    ws: vi.fn()
  }
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} })
}))

vi.mock('naive-ui', () => ({
  useDialog: () => ({}),
  useMessage: () => ({ error: messageError })
}))

type Statistics = { total: number }

const delegate: TracerDelegate<TraceBase, Statistics> = {
  getFilterOptions: () => ({}),
  getTableColumns: () => [],
  formatStatistics: (stats) => stats.total === 0 ? [] : [{ label: '请求', value: stats.total }],
  getDetailFields: () => []
}

describe('trace statistics request state', () => {
  it('distinguishes loading, failed, empty, and populated statistics', async () => {
    const vm = useTracingViewModel('llm', delegate)

    expect(vm.statisticsStatus.value).toBe('idle')
    expect(vm.formattedStatistics.value).toBeNull()

    let rejectRequest: ((error: Error) => void) | undefined
    httpGet.mockImplementationOnce(() => new Promise((_resolve, reject) => {
      rejectRequest = reject
    }))

    const failedRequest = vm.fetchStatistics()
    expect(vm.statisticsStatus.value).toBe('loading')
    expect(vm.statisticsError.value).toBeNull()

    rejectRequest?.(new Error('upstream credentials and internals must stay private'))
    await failedRequest

    expect(vm.statisticsStatus.value).toBe('error')
    expect(vm.statisticsError.value).toBe('统计信息加载失败，请稍后重试。')
    expect(vm.formattedStatistics.value).toBeNull()
    expect(messageError).toHaveBeenCalledWith('获取统计信息失败')

    httpGet.mockResolvedValueOnce({ total: 0 })
    await vm.fetchStatistics()
    expect(vm.statisticsStatus.value).toBe('ready')
    expect(vm.statisticsError.value).toBeNull()
    expect(vm.formattedStatistics.value).toEqual([])

    httpGet.mockResolvedValueOnce({ total: 7 })
    await vm.fetchStatistics()
    expect(vm.formattedStatistics.value).toEqual([{ label: '请求', value: 7 }])
  })

  it('keeps trace list failures recoverable without masking the original error', async () => {
    const vm = useTracingViewModel('llm', delegate)
    const requestError = new Error('trace backend unavailable')
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    httpPost.mockRejectedValueOnce(requestError)

    await expect(vm.fetchTraces()).resolves.toBeUndefined()

    expect(vm.isLoading.value).toBe(false)
    expect(messageError).toHaveBeenCalledWith('获取追踪记录失败')
    expect(consoleError).toHaveBeenCalledWith('获取追踪记录失败:', requestError)

    consoleError.mockRestore()
  })
})
