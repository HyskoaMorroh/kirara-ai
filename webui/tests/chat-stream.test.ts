import { beforeEach, describe, expect, it, vi } from 'vitest'
import { parseSseChunk, readChatStream } from '../src/views/llm/chat-stream'

/**
 * 需求 4：「本项目必须要实现流式和非流式输出」。
 *
 * 这一组钉住浏览器端的解析。三条断言对应三种真实的坏法，它们在本地开发时都
 * 「看起来正常」——本地几乎总是整块到达：
 *
 * - 事件边界与网络分片无关：一个 delta 可能跨两个 chunk；
 * - 一个中文字符三字节，可能被切在 chunk 之间，逐块独立解码会产出 `�`；
 * - `data:` 允许多行。
 */
describe('parseSseChunk', () => {
  it('解析一个完整事件并返回空尾巴', () => {
    const events: any[] = []
    const rest = parseSseChunk('event: delta\ndata: {"text":"甲"}\n\n', (e) => events.push(e))
    expect(rest).toBe('')
    expect(events).toEqual([{ event: 'delta', data: { text: '甲' } }])
  })

  it('一个 chunk 里的多个事件全部解析', () => {
    const events: any[] = []
    parseSseChunk(
      'event: delta\ndata: {"text":"甲"}\n\nevent: delta\ndata: {"text":"乙"}\n\n',
      (e) => events.push(e)
    )
    expect(events.map((e) => e.data.text)).toEqual(['甲', '乙'])
  })

  it('不完整的事件留在尾巴里，不被当成空事件交出', () => {
    const events: any[] = []
    const rest = parseSseChunk('event: delta\ndata: {"text":"甲"}', (e) => events.push(e))
    expect(events).toEqual([])
    expect(rest).toBe('event: delta\ndata: {"text":"甲"}')
  })

  it('认 CRLF 分隔', () => {
    const events: any[] = []
    parseSseChunk('event: done\r\ndata: {"text":"甲乙"}\r\n\r\n', (e) => events.push(e))
    expect(events).toEqual([{ event: 'done', data: { text: '甲乙' } }])
  })

  it('多行 data 按换行拼接后再解析', () => {
    const events: any[] = []
    parseSseChunk('event: done\ndata: {"text":\ndata: "甲"}\n\n', (e) => events.push(e))
    expect(events[0].data.text).toBe('甲')
  })

  it('负载坏掉时交出空负载而不是让整条流崩掉', () => {
    const events: any[] = []
    parseSseChunk('event: delta\ndata: not-json\n\n', (e) => events.push(e))
    expect(events).toEqual([{ event: 'delta', data: {} }])
  })

  it('没有 event 字段的块被跳过', () => {
    const events: any[] = []
    parseSseChunk('data: {"text":"甲"}\n\n', (e) => events.push(e))
    expect(events).toEqual([])
  })
})

/** 造一个把给定字节块依次交出的 Response。 */
const streamingResponse = (chunks: Uint8Array[]): Response => {
  let index = 0
  const body = {
    getReader: () => ({
      read: async () =>
        index < chunks.length
          ? { done: false, value: chunks[index++] }
          : { done: true, value: undefined },
      releaseLock: () => {}
    })
  }
  return {
    ok: true,
    status: 200,
    body,
    text: async () => ''
  } as unknown as Response
}

const encode = (text: string) => new TextEncoder().encode(text)

describe('readChatStream', () => {
  //: vitest 的 environment 是 node，没有 localStorage。桩里保存一个真实的值，
  //: 才能断言「token 确实被读出来放进请求头」——返回固定 null 的桩证明不了这件事。
  let stored: Record<string, string> = {}

  beforeEach(() => {
    stored = {}
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored[key] ?? null,
      setItem: (key: string, value: string) => {
        stored[key] = value
      },
      removeItem: (key: string) => {
        delete stored[key]
      },
      clear: () => {
        stored = {}
      }
    })
  })

  it('把事件按顺序交给回调', async () => {
    const events: any[] = []
    const fetchImpl = vi.fn(async () =>
      streamingResponse([
        encode('event: start\ndata: {"session_id":"s1"}\n\n'),
        encode('event: delta\ndata: {"text":"甲"}\n\n'),
        encode('event: done\ndata: {"text":"甲","status":"completed"}\n\n')
      ])
    )
    await readChatStream('/llm/chat/stream', { message: 'hi' }, { onEvent: (e) => events.push(e) }, fetchImpl as any)
    expect(events.map((e) => e.event)).toEqual(['start', 'delta', 'done'])
  })

  it('事件被网络分片切开时仍然完整解析', async () => {
    const events: any[] = []
    const fetchImpl = vi.fn(async () =>
      streamingResponse([
        encode('event: del'),
        encode('ta\ndata: {"text"'),
        encode(':"甲"}\n\n')
      ])
    )
    await readChatStream('/llm/chat/stream', {}, { onEvent: (e) => events.push(e) }, fetchImpl as any)
    expect(events).toEqual([{ event: 'delta', data: { text: '甲' } }])
  })

  it('一个中文字符被切在两个 chunk 之间时不产生替换字符', async () => {
    const events: any[] = []
    const full = encode('event: delta\ndata: {"text":"甲"}\n\n')
    // 「甲」是 E7 94 B2 三字节；在它中间切开。
    const cut = full.indexOf(0xe7) + 1
    const fetchImpl = vi.fn(async () =>
      streamingResponse([full.slice(0, cut), full.slice(cut)])
    )
    await readChatStream('/llm/chat/stream', {}, { onEvent: (e) => events.push(e) }, fetchImpl as any)
    expect(events[0].data.text).toBe('甲')
    expect(JSON.stringify(events)).not.toContain('�')
  })

  it('末尾没有空行的事件也被交出', async () => {
    const events: any[] = []
    const fetchImpl = vi.fn(async () =>
      streamingResponse([encode('event: done\ndata: {"text":"甲"}')])
    )
    await readChatStream('/llm/chat/stream', {}, { onEvent: (e) => events.push(e) }, fetchImpl as any)
    expect(events).toEqual([{ event: 'done', data: { text: '甲' } }])
  })

  it('流开始之前的 HTTP 失败抛出后端说明', async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: false,
      status: 409,
      text: async () => '{"error":"No Agent is configured for this channel identity"}'
    })) as any
    await expect(
      readChatStream('/llm/chat/stream', {}, { onEvent: () => {} }, fetchImpl)
    ).rejects.toThrow('No Agent is configured for this channel identity')
  })

  it('后端正文不是 JSON 时退回状态码说明', async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: false,
      status: 502,
      text: async () => '<html>bad gateway</html>'
    })) as any
    await expect(
      readChatStream('/llm/chat/stream', {}, { onEvent: () => {} }, fetchImpl)
    ).rejects.toThrow('HTTP 502')
  })

  it('带上 Authorization 与 event-stream 的 Accept', async () => {
    localStorage.setItem('token', 'tok')
    const fetchImpl = vi.fn(async () => streamingResponse([]))
    await readChatStream('/llm/chat/stream', { message: 'hi' }, { onEvent: () => {} }, fetchImpl as any)
    const [, init] = fetchImpl.mock.calls[0] as any
    expect(init.headers.Authorization).toBe('Bearer tok')
    expect(init.headers.Accept).toBe('text/event-stream')
    expect(init.method).toBe('POST')
    localStorage.removeItem('token')
  })
})
