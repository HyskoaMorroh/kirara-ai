/**
 * 把一条回复正文切成「普通文本」与「代码块」两类片段。
 *
 * 需求 6 要求「代码要统一放到代码框里旁边有直接复制键」。WebUI 此前把整条回复
 * 塞进一个 `<p>`：代码与正文同一种字体、没有边框、也没有任何复制入口。
 * 浏览器里 `navigator.clipboard` 本来就可用，这个渠道没有平台限制可讲。
 *
 * 围栏识别口径与后端 `kirara_ai/im/text_render.py` 保持一致，否则同一段回复在
 * QQ 上被认成代码、在 WebUI 上不是，两边的排版会各说一套：
 *
 * - 反引号与波浪号都算围栏，至少三个；
 * - 结束围栏必须是同一个字符，且不短于开始围栏；
 * - 结束围栏后面不能跟语言标识（那是另一个代码块的开始）。
 *
 * 未闭合的围栏按「到正文结束」处理而不是退回成普通文本：流式或被截断的回复里
 * 这是常态，把它显示成普通段落会让缩进和换行全部塌掉——而那恰恰是用户要复制的东西。
 */

/** 一段普通文本。`text` 不含首尾多余空行。 */
export interface TextSegment {
  kind: 'text'
  text: string
}

/** 一个代码块。`code` 保留原始缩进与换行，`language` 缺省为空串。 */
export interface CodeSegment {
  kind: 'code'
  language: string
  code: string
}

export type MessageSegment = TextSegment | CodeSegment

/** 与后端 `_FENCE_START_PATTERN` 同义：至少三个反引号或波浪号，其后是可选语言。 */
const FENCE_START = /^(`{3,}|~{3,})[ \t]*([^`\n]*?)[ \t]*$/

interface FenceMatch {
  fence: string
  language: string
}

const matchFence = (line: string): FenceMatch | null => {
  const matched = FENCE_START.exec(line.trimStart())
  if (!matched) return null
  return { fence: matched[1], language: matched[2] || '' }
}

const closesFence = (line: string, opening: string): boolean => {
  const matched = matchFence(line)
  if (!matched) return false
  return (
    matched.fence[0] === opening[0] &&
    matched.fence.length >= opening.length &&
    matched.language === ''
  )
}

/**
 * 切分一条回复。返回的片段按原始顺序排列，拼回去等于原文的可见内容。
 *
 * 空正文返回空数组：调用方据此决定是否渲染气泡，而不是渲染一个空代码框。
 */
export function splitMessageSegments(text: string): MessageSegment[] {
  if (!text) return []

  const lines = text.split('\n')
  const segments: MessageSegment[] = []
  let buffer: string[] = []
  let openFence: string | null = null
  let language = ''

  const flushText = () => {
    // 只在片段内部保留空行；片段之间的空行属于分隔，不属于内容。
    const joined = buffer.join('\n').replace(/^\n+|\n+$/g, '')
    if (joined) segments.push({ kind: 'text', text: joined })
    buffer = []
  }

  const flushCode = () => {
    // 代码块内部的空行与缩进一律保留：那是用户要复制走的东西。
    // 只去掉围栏行本身留下的首尾换行。
    const code = buffer.join('\n').replace(/^\n+|\n+$/g, '')
    segments.push({ kind: 'code', language, code })
    buffer = []
    language = ''
  }

  for (const line of lines) {
    if (openFence === null) {
      const fence = matchFence(line)
      if (fence) {
        flushText()
        openFence = fence.fence
        language = fence.language
        continue
      }
      buffer.push(line)
      continue
    }
    if (closesFence(line, openFence)) {
      flushCode()
      openFence = null
      continue
    }
    buffer.push(line)
  }

  if (openFence === null) {
    flushText()
  } else {
    // 未闭合：仍然当代码块交出去。见文件头注释。
    flushCode()
  }

  return segments
}

/**
 * 把一段代码放进剪贴板。返回是否成功。
 *
 * 失败原因通常不是代码问题：非 HTTPS 的页面、被策略关掉的剪贴板权限、
 * 或者浏览器根本没有这个 API。因此不抛异常，让调用方给出一句可执行的提示
 * （「请手动选中复制」），而不是弹一个用户无法处置的错误。
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
  if (!clipboard || typeof clipboard.writeText !== 'function') return false
  try {
    await clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
