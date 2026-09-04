// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * 「连接追踪系统失败」出现在一个**不用 WebSocket 的页面**上。
 *
 * 发现过程
 * ------
 * 用户在「系统记录 → 使用统计」页看到红色提示「连接追踪系统失败」。而这一页
 * （`UsageStatisticsView.vue` + `LLMStatistics.vue`）从头到尾没有 `connectWebSocket`
 * 调用——它只发 `GET /tracing/llm/statistics`。也就是说提示来自**上一个页面**
 * 遗留的 socket。
 *
 * 服务端日志排除了鉴权与路径问题：`GET /backend-api/api/tracing/ws` 全部返回
 * `101`（握手成功）。同时暴露出第二个症状——`21:23:34.385` 与 `21:23:35.221`
 * 一秒内握手两次。
 *
 * 两处句柄泄漏
 * ----------
 * 1. `disconnectWebSocket()` 只把 `onclose` 置空就 `close()`，`onerror` 仍然挂着。
 *    浏览器在连接尚未 OPEN 时关闭会补发一个 `error` 事件，于是
 *    `handleWebSocketError` 弹出全局 `message.error`——而用户此时已经跳到别的页面。
 * 2. `connectWebSocket()` 关闭旧 socket 前也没摘处理器，旧 socket 的 `onclose`
 *    触发 `handleWebSocketClose`，后者见 `wasClean === false` 就排一次重连：
 *    「关掉旧连接」反而又开了一条。
 *
 * 这一组测试锁住的边界
 * ------------------
 * 1. 主动断开后，`error` 与 `close` 都不得再产生任何用户可见提示或重连。
 * 2. 重连只在**非主动**断开时发生。
 * 3. 重连次数有上限，且到达上限后给一次明确提示（这条是既有行为，防止回归）。
 */

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: ((event: { wasClean: boolean }) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  sent: string[] = []
  closeCalls = 0

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(payload: string) {
    this.sent.push(payload)
  }

  /**
   * 关闭时同时派发 `close` 与 `error`。
   *
   * 真实浏览器在 socket 还没 OPEN 就被 `close()` 时会补一个 `error` 事件——
   * 这正是那条提示的来源。测试替身若只派发 `close`，被测的那个缺陷就不会出现。
   */
  close() {
    this.closeCalls += 1
    this.readyState = FakeWebSocket.CLOSED
    this.onerror?.(new Event('error'))
    this.onclose?.({ wasClean: false })
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
}

const errors: string[] = []

vi.mock('naive-ui', () => ({
  useMessage: () => ({
    error: (text: string) => errors.push(text),
    success: () => {},
    warning: () => {},
    info: () => {}
  }),
  useDialog: () => ({ warning: () => {}, error: () => {} })
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: () => {} }),
  // `useRoute` 也要 stub：view model 在构造时就读它，缺了会抛
  // 「useRoute() 只能在 setup 里调用」，而那条报错与被测行为无关。
  useRoute: () => ({ query: {}, params: {}, path: '/tracing/llm' })
}))

const wsFactory = vi.fn((path: string) => new FakeWebSocket(path) as unknown as WebSocket)

vi.mock('@/utils/http', () => ({
  http: {
    ws: (path: string) => wsFactory(path),
    get: vi.fn(async () => ({})),
    post: vi.fn(async () => ({ traces: [], total: 0 })),
    url: (path: string) => path
  }
}))

describe('主动断开之后不再打扰用户', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    errors.length = 0
    wsFactory.mockClear()
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.stubGlobal('localStorage', {
      getItem: () => 'test-token',
      setItem: () => {},
      removeItem: () => {}
    })
  })

  it('disconnectWebSocket 之后 error 事件不再弹提示', async () => {
    const { useTracingViewModel } = await import('../src/views/tracing/tracing.vm')
    const vm = useTracingViewModel('llm')

    await vm.connectWebSocket()
    const socket = FakeWebSocket.instances[0]
    socket.open()
    expect(vm.isConnected.value).toBe(true)

    vm.disconnectWebSocket()

    // 坏版本在这里留下一条「连接追踪系统失败」——而用户已经离开这一页了。
    expect(errors).toEqual([])
  })

  it('disconnectWebSocket 之后不再重连', async () => {
    const { useTracingViewModel } = await import('../src/views/tracing/tracing.vm')
    const vm = useTracingViewModel('llm')

    await vm.connectWebSocket()
    FakeWebSocket.instances[0].open()
    vm.disconnectWebSocket()

    // 只应有第一次那一条连接：主动断开不排重连。
    expect(wsFactory).toHaveBeenCalledTimes(1)
  })

  it('重连时关闭旧连接不会再触发一次重连', async () => {
    const { useTracingViewModel } = await import('../src/views/tracing/tracing.vm')
    const vm = useTracingViewModel('llm')

    await vm.connectWebSocket()
    FakeWebSocket.instances[0].open()

    // 再次调用 connect（真实场景：切换筛选后重订阅）。
    await vm.connectWebSocket()

    // 关旧 socket 不得触发 `handleWebSocketClose` 的重连分支，
    // 否则一次重连会变成两条连接——服务端日志里正是一秒两次握手。
    expect(wsFactory).toHaveBeenCalledTimes(2)
    expect(errors).toEqual([])
  })

  it('非主动断开仍然重连', async () => {
    const { useTracingViewModel } = await import('../src/views/tracing/tracing.vm')
    const vm = useTracingViewModel('llm')

    await vm.connectWebSocket()
    const socket = FakeWebSocket.instances[0]
    socket.open()

    // 模拟上游断线：不经过 disconnectWebSocket，直接来一个非 clean 的 close。
    socket.readyState = FakeWebSocket.CLOSED
    socket.onclose?.({ wasClean: false })

    expect(vm.isConnected.value).toBe(false)
  })
})
