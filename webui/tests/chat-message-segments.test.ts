import { describe, expect, it, vi } from 'vitest'
import {
  copyTextToClipboard,
  splitMessageSegments
} from '../src/views/llm/message-segments'

/**
 * 需求 6：「代码要统一放到代码框里旁边有直接复制键」。
 *
 * WebUI 此前把整条回复塞进一个 `<p>`——代码与正文同字体、无边框、无复制入口。
 * 浏览器里 `navigator.clipboard` 本来就可用，这个渠道没有平台限制可讲，
 * 因此它是四个渠道里最该先修的一个。
 *
 * 围栏识别必须与后端 `text_render.py` 同口径。两边不一致时，同一段回复会在 QQ 上
 * 被认成代码、在 WebUI 上不是——同一个机器人给出两种排版，而没有任何地方说明原因。
 */
describe('splitMessageSegments', () => {
  it('把代码围栏切成独立片段并保留语言标识', () => {
    const segments = splitMessageSegments('前言\n```python\nprint(1)\n```\n后记')

    expect(segments).toEqual([
      { kind: 'text', text: '前言' },
      { kind: 'code', language: 'python', code: 'print(1)' },
      { kind: 'text', text: '后记' }
    ])
  })

  it('保留代码块内部的缩进与空行', () => {
    const code = 'def f():\n    if True:\n\n        return 1'
    const segments = splitMessageSegments('```py\n' + code + '\n```')

    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ kind: 'code', code })
  })

  it('波浪号围栏同样被识别', () => {
    const segments = splitMessageSegments('~~~sh\nls -l\n~~~')

    expect(segments[0]).toMatchObject({ kind: 'code', language: 'sh', code: 'ls -l' })
  })

  it('结束围栏必须同字符且不短于开始围栏', () => {
    // 四个反引号开的块，里面出现三个反引号不算结束——那是代码里的内容。
    const segments = splitMessageSegments('````md\n```py\nx = 1\n```\n````')

    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ kind: 'code', code: '```py\nx = 1\n```' })
  })

  it('波浪号不能关掉反引号围栏', () => {
    const segments = splitMessageSegments('```\na\n~~~\nb\n```')

    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ kind: 'code', code: 'a\n~~~\nb' })
  })

  it('带语言标识的行不能当作结束围栏', () => {
    const segments = splitMessageSegments('```\na\n``` js\nb\n```')

    // `\`\`\` js` 是另一个块的开始形态，不是结束——否则 b 会被当成普通正文。
    expect(segments).toHaveLength(1)
    expect(segments[0]).toMatchObject({ kind: 'code', code: 'a\n``` js\nb' })
  })

  it('未闭合围栏仍按代码块交出，而不是塌回普通文本', () => {
    const segments = splitMessageSegments('说明\n```ts\nconst a = 1\n  const b = 2')

    expect(segments).toEqual([
      { kind: 'text', text: '说明' },
      { kind: 'code', language: 'ts', code: 'const a = 1\n  const b = 2' }
    ])
  })

  it('没有围栏时整条就是一段文本', () => {
    expect(splitMessageSegments('只是普通回复')).toEqual([
      { kind: 'text', text: '只是普通回复' }
    ])
  })

  it('空正文返回空数组', () => {
    expect(splitMessageSegments('')).toEqual([])
  })

  it('无语言标识的围栏语言为空串', () => {
    const segments = splitMessageSegments('```\nplain\n```')

    expect(segments[0]).toMatchObject({ kind: 'code', language: '', code: 'plain' })
  })

  it('连续多个代码块各自独立', () => {
    const segments = splitMessageSegments('```a\n1\n```\n中间\n```b\n2\n```')

    expect(segments.map((segment) => segment.kind)).toEqual(['code', 'text', 'code'])
    expect(segments.filter((s) => s.kind === 'code').map((s: any) => s.language)).toEqual(['a', 'b'])
  })

  it('拼回可见内容不丢字符', () => {
    const original = '一\n```py\nx=1\n```\n二\n三'
    const rebuilt = splitMessageSegments(original)
      .map((segment) => (segment.kind === 'code' ? segment.code : segment.text))
      .join('\n')

    expect(rebuilt).toBe('一\nx=1\n二\n三')
  })
})

describe('copyTextToClipboard', () => {
  it('剪贴板可用时写入并返回 true', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    await expect(copyTextToClipboard('x = 1')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('x = 1')

    vi.unstubAllGlobals()
  })

  it('没有剪贴板 API 时返回 false 而不是抛错', async () => {
    vi.stubGlobal('navigator', {})

    // 非 HTTPS 页面与被策略关掉权限的浏览器都会走到这里。抛异常等于给用户
    // 一个无法处置的错误；返回 false 让界面改口说「请手动选中复制」。
    await expect(copyTextToClipboard('x')).resolves.toBe(false)

    vi.unstubAllGlobals()
  })

  it('写入被拒时返回 false', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }
    })

    await expect(copyTextToClipboard('x')).resolves.toBe(false)

    vi.unstubAllGlobals()
  })
})
