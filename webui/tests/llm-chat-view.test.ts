// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ChatView from '../src/views/llm/ChatView.vue'

const { chat, listAgents, readChatStream } = vi.hoisted(() => ({
  chat: vi.fn(),
  listAgents: vi.fn(),
  readChatStream: vi.fn()
}))

vi.mock('../src/api/llm', () => ({
  llmApi: { chat }
}))

vi.mock('../src/api/agent', () => ({
  listAgents
}))

vi.mock('../src/views/llm/chat-stream', () => ({
  readChatStream
}))

vi.mock('naive-ui', () => {
  const passthrough = (tag: string) => ({
    name: tag,
    template: `<section><slot name="header" /><slot /></section>`
  })

  return {
    NAlert: passthrough('NAlert'),
    NButton: {
      name: 'NButton',
      emits: ['click'],
      template: '<button type="button" v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>'
    },
    NCard: passthrough('NCard'),
    NIcon: { template: '<i><slot /></i>' },
    NInput: {
      name: 'NInput',
      inheritAttrs: false,
      props: ['value'],
      emits: ['update:value'],
      template: '<textarea v-bind="$attrs" :value="value" @input="$emit(\'update:value\', $event.target.value)" />'
    },
    NSelect: {
      name: 'NSelect',
      inheritAttrs: false,
      props: ['value', 'options'],
      emits: ['update:value'],
      template: '<select v-bind="$attrs" :value="value" @change="$emit(\'update:value\', $event.target.value)"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option></select>'
    },
    NTag: { template: '<span><slot /></span>' }
  }
})

const agent = {
  agent_id: 'research-agent',
  display_name: 'Research Agent',
  enabled: true,
  workflow_id: null,
  model_priority: ['primary-model', 'backup-model'],
  provider_allowlist: ['test-provider'],
  capabilities: ['research'],
  prompt_bindings: [],
  skill_bindings: [],
  memory_bindings: [],
  mcp_bindings: [],
  hook_bindings: [],
  mcp_allowlist: ['context7'],
  allow_tools: true,
  max_tool_iterations: 3,
  relations: {
    channels: ['webui', 'telegram'],
    accounts: [],
    sessions: [],
    is_default: true
  }
}

describe('ChatView', () => {
  beforeEach(() => {
    chat.mockReset()
    listAgents.mockReset()
    readChatStream.mockReset()
    listAgents.mockResolvedValue([agent])
    chat.mockResolvedValue({
      status: 'completed',
      text: 'The paper is about reproducible workflows.',
      agent_id: 'research-agent',
      session_id: 'research-1',
      session_key: 'webui/webui/webui/c2c:research-1/research-1',
      confirmation_id: null
    })
  })

  /**
   * 切到非流式。
   *
   * 界面默认走 SSE（需求 4：这条渠道天生支持逐段显示，默认关掉等于功能不存在）。
   * 下面几条用例验的是**非流式**那一半契约——需求 4 的原文是「流式**和**非流式」,
   * 两条路径都必须存在且都必须对。
   */
  const useNonStreaming = async (wrapper: ReturnType<typeof mount>) => {
    await wrapper.get('[data-test="stream-mode-off"]').trigger('click')
  }

  it('loads the unified Agent relation and sends a private chat through the API', async () => {
    const wrapper = mount(ChatView)
    await flushPromises()
    await useNonStreaming(wrapper)

    expect(wrapper.text()).toContain('Research Agent')
    expect(wrapper.text()).toContain('primary-model -> backup-model')
    expect(wrapper.text()).toContain('webui')

    await wrapper.get('[data-test="session-id"]').setValue('research-1')
    await wrapper.get('[data-test="message-input"]').setValue('Summarize this paper')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(chat).toHaveBeenCalledWith({
      message: 'Summarize this paper',
      session_id: 'research-1',
      username: 'WebUI user',
      chat_type: 'c2c',
      agent_id: 'research-agent'
    })
    expect(wrapper.text()).toContain('Summarize this paper')
    expect(wrapper.text()).toContain('The paper is about reproducible workflows.')
    expect(wrapper.text()).toContain('主模型链已返回')
  })

  it('requires a group id for group chat and resumes a pending confirmation explicitly', async () => {
    const wrapper = mount(ChatView)
    await flushPromises()
    await useNonStreaming(wrapper)

    await wrapper.get('[data-test="chat-type-group"]').trigger('click')
    await wrapper.get('[data-test="message-input"]').setValue('Please publish the notes')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    expect(chat).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('群聊必须填写群组 ID')

    await wrapper.get('[data-test="group-id"]').setValue('study-room')
    chat.mockResolvedValueOnce({
      status: 'awaiting_confirmation',
      text: '需要确认后才能继续',
      agent_id: 'research-agent',
      session_id: 'member-7',
      session_key: 'webui/webui/webui/group:study-room/member-7',
      confirmation_id: 'confirm-123'
    })
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('需要确认后才能继续')
    expect(wrapper.text()).toContain('confirm-123')
    await wrapper.get('[aria-label="确认待处理操作"]').trigger('click')
    await flushPromises()

    expect(chat).toHaveBeenLastCalledWith({
      message: '确认 confirm-123',
      session_id: 'member-7',
      username: 'WebUI user',
      chat_type: 'group',
      group_id: 'study-room',
      agent_id: 'research-agent'
    })
  })

  it('keeps a failed request visible without leaking provider details', async () => {
    chat.mockRejectedValueOnce(new Error('Agent runtime failed'))
    const wrapper = mount(ChatView)
    await flushPromises()
    await useNonStreaming(wrapper)

    await wrapper.get('[data-test="message-input"]').setValue('Run diagnostics')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('消息发送失败')
    expect(wrapper.text()).toContain('Agent runtime failed')
  })
})

/**
 * 流式那一半（需求 4）。
 *
 * 这些用例钉住「用户真的看得见文字逐段出现」：默认就走 SSE，气泡在首字节之前就
 * 出现（否则与非流式毫无区别），delta 就地追加，done 用最终文本覆盖，
 * error 不把已收到的内容抹掉。
 */
describe('ChatView streaming', () => {
  beforeEach(() => {
    chat.mockReset()
    listAgents.mockReset()
    readChatStream.mockReset()
    listAgents.mockResolvedValue([agent])
  })

  /** 造一个按给定事件序列回放的 readChatStream。 */
  const replay = (events: Array<{ event: string; data: Record<string, unknown> }>) =>
    readChatStream.mockImplementation(async (_path, _body, handlers) => {
      for (const item of events) handlers.onEvent(item)
    })

  it('streams by default and never touches the non-streaming endpoint', async () => {
    replay([
      { event: 'start', data: { session_id: 'research-1' } },
      { event: 'delta', data: { text: '模拟' } },
      { event: 'delta', data: { text: '回火' } },
      {
        event: 'done',
        data: {
          status: 'completed',
          text: '模拟回火',
          agent_id: 'research-agent',
          session_id: 'research-1',
          confirmation_id: null
        }
      }
    ])
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('讲讲模拟回火')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(readChatStream).toHaveBeenCalledTimes(1)
    expect(chat).not.toHaveBeenCalled()
    const [path, body] = readChatStream.mock.calls[0]
    expect(path).toBe('/llm/chat/stream')
    expect(body).toMatchObject({ message: '讲讲模拟回火', agent_id: 'research-agent' })
    expect(wrapper.text()).toContain('模拟回火')
    expect(wrapper.text()).toContain('主模型链已返回')
  })

  it('shows a progress indicator before the first byte arrives', async () => {
    // 气泡必须**先**出现。等第一个 delta 才插入的话，首字节之前界面上什么都没有，
    // 与非流式毫无区别——而首字节正是最慢的那一段。
    let release: (() => void) | null = null
    readChatStream.mockImplementation(
      () => new Promise<void>((resolve) => { release = resolve })
    )
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('hi')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="streaming-indicator"]').exists()).toBe(true)
    release?.()
    await flushPromises()
    expect(wrapper.find('[data-test="streaming-indicator"]').exists()).toBe(false)
  })

  it('keeps partial text when the stream reports an error', async () => {
    // 已收到的部分不能删：删掉会让用户以为什么都没发生过，
    // 而事实是模型已经产出了一段内容然后失败。
    replay([
      { event: 'delta', data: { text: '已经生成的一段' } },
      { event: 'error', data: { error: 'Agent runtime failed' } }
    ])
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('hi')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('已经生成的一段')
    expect(wrapper.text()).toContain('生成失败')
    expect(wrapper.find('[data-test="streaming-indicator"]').exists()).toBe(false)
  })

  it('replaces the bubble on a reset event instead of appending', async () => {
    replay([
      { event: 'delta', data: { text: '模拟回火' } },
      { event: 'reset', data: { text: '模拟退火算法' } },
      { event: 'done', data: { status: 'completed', text: '模拟退火算法' } }
    ])
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('hi')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('模拟退火算法')
    expect(wrapper.text()).not.toContain('模拟回火模拟退火算法')
  })

  it('uses the final text from done rather than the accumulated deltas', async () => {
    // 供应商级的回复策略（例如隐去 AI 署名）在聚合之后执行，只有 done 带的是
    // 最终那一版。保留累积值会让界面显示一段本该被处理掉的文本。
    replay([
      { event: 'delta', data: { text: '草稿' } },
      { event: 'done', data: { status: 'completed', text: '最终版本' } }
    ])
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('hi')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('最终版本')
    expect(wrapper.text()).not.toContain('草稿')
  })

  it('surfaces a pending confirmation from the done event', async () => {
    replay([
      {
        event: 'done',
        data: {
          status: 'awaiting_confirmation',
          text: '需要确认后才能继续',
          agent_id: 'research-agent',
          session_id: 'member-7',
          confirmation_id: 'confirm-123'
        }
      }
    ])
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('publish the notes')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('需要确认后才能继续')
    expect(wrapper.text()).toContain('confirm-123')
  })

  it('unlocks the composer after a transport failure', async () => {
    // sending 卡在 true 上会让输入框永久锁死——用户唯一的出路是刷新页面。
    readChatStream.mockRejectedValueOnce(new Error('网络中断'))
    const wrapper = mount(ChatView)
    await flushPromises()

    await wrapper.get('[data-test="message-input"]').setValue('hi')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('消息发送失败')
    await wrapper.get('[data-test="message-input"]').setValue('again')
    await wrapper.get('[data-test="send-message"]').trigger('click')
    await flushPromises()
    expect(readChatStream).toHaveBeenCalledTimes(2)
  })
})
