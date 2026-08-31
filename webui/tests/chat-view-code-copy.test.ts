// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatView from '../src/views/llm/ChatView.vue'

/**
 * 需求 6：代码要放进代码框，旁边有直接复制键。
 *
 * 这些用例走的是组件层而不是纯函数层：切分逻辑已由
 * `chat-message-segments.test.ts` 钉住，这里要回答的是另一个问题——
 * 那个按钮**真的画出来了、真的写进剪贴板了**吗。此前 `ChatView.vue` 把整条回复
 * 塞进一个 `<p>`，切分函数写得再对也不会有任何按钮出现。
 */

const { listAgents, chat, readChatStream } = vi.hoisted(() => ({
  listAgents: vi.fn(),
  chat: vi.fn(),
  readChatStream: vi.fn()
}))

vi.mock('../src/api/agent', () => ({ listAgents }))
vi.mock('../src/api/llm', () => ({ llmApi: { chat } }))
vi.mock('../src/views/llm/chat-stream', () => ({ readChatStream }))

const AGENT = {
  agent_id: 'assistant',
  display_name: '助手',
  enabled: true,
  allow_tools: true,
  max_tool_iterations: 4,
  mcp_allowlist: [],
  model_chain: ['gpt'],
  channels: [],
  accounts: [],
  sessions: []
}

const mountChat = () => mount(ChatView, { global: { stubs: { 'n-icon': true } } })

/**
 * 走流式路径拿到一条带代码的回复。
 *
 * 默认路径就是 SSE（需求 4），因此这里直接回放事件而不是打非流式接口——
 * 代码框与复制按钮必须在**用户实际走的那条路**上出现。
 */
const sendAndGetReply = async (replyText: string) => {
  readChatStream.mockImplementation(async (_path, _body, handlers) => {
    handlers.onEvent({ event: 'start', data: { session_id: 'webui-session' } })
    handlers.onEvent({ event: 'delta', data: { text: replyText } })
    handlers.onEvent({
      event: 'done',
      data: {
        status: 'completed',
        text: replyText,
        agent_id: 'assistant',
        session_id: 'webui-session',
        confirmation_id: null
      }
    })
  })
  const wrapper = mountChat()
  await flushPromises()
  await wrapper.find('[data-test="session-id"] input').setValue('webui-session')
  await wrapper.find('textarea').setValue('给我一段代码')
  await wrapper.find('[data-test="send-message"]').trigger('click')
  await flushPromises()
  return wrapper
}

describe('ChatView 代码框与复制键', () => {
  beforeEach(() => {
    listAgents.mockReset()
    chat.mockReset()
    readChatStream.mockReset()
    listAgents.mockResolvedValue([AGENT])
  })

  it('回复里的代码围栏渲染成代码框，并带一个复制按钮', async () => {
    const wrapper = await sendAndGetReply('这样写：\n```python\nprint(1)\n```\n就好了。')

    const codeBlocks = wrapper.findAll('.code-block')
    // 回归点：修之前整条回复只有一个 `<p>`，这里是 0。
    expect(codeBlocks).toHaveLength(1)
    expect(codeBlocks[0].find('pre code').text()).toBe('print(1)')
    expect(codeBlocks[0].find('.code-language').text()).toBe('python')
    expect(codeBlocks[0].find('button').exists()).toBe(true)
  })

  it('点复制把代码原文写进剪贴板，不带围栏也不带语言标识', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })

    const wrapper = await sendAndGetReply('```sh\nls -l\n  nested\n```')
    await wrapper.find('.code-block button').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('ls -l\n  nested')
    vi.unstubAllGlobals()
  })

  it('复制成功后按钮短暂显示「已复制」', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } })

    const wrapper = await sendAndGetReply('```\nx\n```')
    expect(wrapper.find('.code-block button').text()).toContain('复制')

    await wrapper.find('.code-block button').trigger('click')
    await flushPromises()

    // 没有反馈时用户无法判断这一下点没点上，只能再点一次。
    expect(wrapper.find('.code-block button').text()).toContain('已复制')
    vi.unstubAllGlobals()
  })

  it('剪贴板不可用时给出可执行提示，而不是静默失败', async () => {
    vi.stubGlobal('navigator', {})

    const wrapper = await sendAndGetReply('```\nx\n```')
    await wrapper.find('.code-block button').trigger('click')
    await flushPromises()

    // 非 HTTPS 页面与被策略关掉权限的浏览器都会走到这里。代码仍在框里可手动选中，
    // 所以提示要说「手动选中复制」，而不是一句用户无法处置的失败。
    expect(wrapper.text()).toContain('手动选中代码复制')
    vi.unstubAllGlobals()
  })

  it('没有代码的普通回复不产生空代码框', async () => {
    const wrapper = await sendAndGetReply('好的，已经处理完了。')

    expect(wrapper.findAll('.code-block')).toHaveLength(0)
    expect(wrapper.text()).toContain('好的，已经处理完了。')
  })

  it('一条回复里的多个代码块各有独立的复制按钮', async () => {
    const wrapper = await sendAndGetReply('先\n```a\n1\n```\n再\n```b\n2\n```')

    const buttons = wrapper.findAll('.code-block button')
    // 共用一个按钮会让「复制第二段」变成「复制第一段」，而两次点击看起来一样。
    expect(buttons).toHaveLength(2)
    expect(wrapper.findAll('.code-block pre code').map((node) => node.text())).toEqual(['1', '2'])
  })
})
