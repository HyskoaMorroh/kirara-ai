export interface LatestRequest {
  generation: number
  signal: AbortSignal
}

/**
 * 为只允许最新一次结果写回状态的异步操作提供统一代次和取消信号。
 *
 * 即使底层 Promise 不支持 AbortSignal，递增的 generation 仍能阻止旧结果写回。
 */
export function useLatestRequest() {
  let generation = 0
  let controller: AbortController | null = null

  const begin = (): LatestRequest => {
    controller?.abort()
    controller = new AbortController()
    generation += 1
    return { generation, signal: controller.signal }
  }

  const isCurrent = (candidate: number) => candidate === generation && !controller?.signal.aborted

  const cancel = () => {
    controller?.abort()
    controller = null
    generation += 1
  }

  return { begin, isCurrent, cancel }
}
