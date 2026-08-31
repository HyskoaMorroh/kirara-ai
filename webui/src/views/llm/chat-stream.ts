/**
 * 读一条 Server-Sent Events 流。
 *
 * 需求 4 的浏览器端一半。后端 `/llm/chat/stream` 把一轮回复送成
 * `start` / `delta` / `reset` / `done` / `error` 五种事件；这里把字节流解析成
 * 事件回调。
 *
 * 用 `fetch` + `ReadableStream` 而不是 `EventSource`，原因是硬性的：
 * `EventSource` 只能发 GET，而且不能带自定义请求头——这个后端要 POST 一个 JSON
 * 请求体，还要 `Authorization: Bearer`。把 token 放进查询串是另一条路，但 URL 会
 * 进日志和 referrer，项目的鉴权中间件也明确拒绝查询串 token。
 *
 * 解析要点（都对应一种真实的坏法）：
 *
 * - **按空行切事件，而不是按 chunk。** 网络分片与事件边界无关：一个 `delta` 可能
 *   跨两个 chunk 到达，两个 `delta` 也可能挤在同一个 chunk 里。按 chunk 解析在
 *   本地开发（几乎总是整块到达）看起来完全正常，上线后随机丢字。
 * - **UTF-8 要用流式解码。** 一个中文字符占三字节，可能被切在两个 chunk 之间。
 *   逐 chunk 独立 `decode()` 会在断点处产出替换字符 `�`。`TextDecoder` 的
 *   `{ stream: true }` 会把不完整的尾字节留到下一次。
 * - **`data:` 可以有多行。** SSE 规定多个 `data:` 行按 `\n` 拼接。后端目前一行
 *   写完，但按单行解析会让某天的一次换行变成静默的解析失败。
 */

/** 后端约定的事件名。`reset` 表示整段替换而不是追加。 */
export type ChatStreamEventName = 'start' | 'delta' | 'reset' | 'done' | 'error'

export interface ChatStreamEvent {
  event: ChatStreamEventName | string
  data: Record<string, unknown>
}

export interface ChatStreamHandlers {
  /** 每个解析出的事件。抛出的异常会中止读取并向上传播。 */
  onEvent: (event: ChatStreamEvent) => void
  /** 取消信号；中止时 `readChatStream` 以 `AbortError` 结束。 */
  signal?: AbortSignal
}

const EVENT_FIELD = 'event:'
const DATA_FIELD = 'data:'

/** 把一段 SSE 文本块解析成事件。返回未消费完的尾巴。 */
export function parseSseChunk(
  buffer: string,
  emit: (event: ChatStreamEvent) => void
): string {
  let rest = buffer
  for (;;) {
    // SSE 允许 \n\n 与 \r\n\r\n 两种分隔；两者都要认。
    const boundary = rest.search(/\r?\n\r?\n/)
    if (boundary < 0) return rest
    const raw = rest.slice(0, boundary)
    const matched = /\r?\n\r?\n/.exec(rest.slice(boundary))
    rest = rest.slice(boundary + (matched ? matched[0].length : 2))

    let name = ''
    const payload: string[] = []
    for (const line of raw.split(/\r?\n/)) {
      if (line.startsWith(EVENT_FIELD)) {
        name = line.slice(EVENT_FIELD.length).trim()
        continue
      }
      if (line.startsWith(DATA_FIELD)) {
        payload.push(line.slice(DATA_FIELD.length).trim())
      }
    }
    if (!name) continue
    const body = payload.join('\n')
    let data: Record<string, unknown> = {}
    if (body) {
      try {
        const parsed = JSON.parse(body)
        if (parsed && typeof parsed === 'object') data = parsed as Record<string, unknown>
      } catch {
        // 负载坏了不该让整条流断掉：其余事件仍然有用，而这一条按空负载交出去，
        // 调用方会把它当成一次没有文本的事件而不是一次崩溃。
        data = {}
      }
    }
    emit({ event: name, data })
  }
}

/**
 * 读取一条聊天 SSE 流直到结束。
 *
 * `body` 是要 POST 的请求体。返回时流已经正常结束（后端放出 `done` 或 `error`
 * 之后关闭）。HTTP 层面的失败（401、400、5xx）在这里抛出——那些发生在流开始
 * 之前，仍然有可用的状态码。
 */
export async function readChatStream(
  path: string,
  body: unknown,
  handlers: ChatStreamHandlers,
  fetchImpl: typeof fetch = fetch
): Promise<void> {
  const token = localStorage.getItem('token')
  const response = await fetchImpl(`/backend-api/api${path}`, {
    method: 'POST',
    credentials: 'include',
    signal: handlers.signal,
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(body)
  })

  if (!response.ok) {
    // 流还没开始，状态码仍然可用。读一次正文把后端的说明带出去。
    let detail = ''
    try {
      const text = await response.text()
      const parsed = text.trim() ? JSON.parse(text) : null
      if (parsed && typeof parsed === 'object' && typeof parsed.error === 'string') {
        detail = parsed.error
      }
    } catch {
      // 正文不是 JSON：状态码本身已经足够说明问题。
    }
    throw new Error(detail || `请求失败 (HTTP ${response.status})`)
  }

  const stream = response.body
  if (!stream) {
    throw new Error('浏览器未提供可读流，无法接收流式回复')
  }

  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = parseSseChunk(buffer, handlers.onEvent)
    }
    // 收尾：把解码器里可能残留的字节交出来，并解析最后一个没有尾随空行的事件。
    buffer += decoder.decode()
    if (buffer.trim()) parseSseChunk(`${buffer}\n\n`, handlers.onEvent)
  } finally {
    // 取消读取器：不做的话，提前 return / 抛出时底层连接会挂在半开状态。
    reader.releaseLock?.()
  }
}
