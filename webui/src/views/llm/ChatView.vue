<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NSelect,
  NTag
} from 'naive-ui'
import {
  CheckmarkCircleOutline,
  CopyOutline,
  GitNetworkOutline,
  PaperPlaneOutline,
  PersonCircleOutline,
  RefreshOutline,
  ShieldCheckmarkOutline
} from '@vicons/ionicons5'

import { llmApi } from '@/api/llm'
import type { WebUIChatRequest, WebUIChatResponse } from '@/api/llm'
import { listAgents } from '@/api/agent'
import type { AgentSummary } from '@/api/agent'
import { readChatStream } from './chat-stream'
import { copyTextToClipboard, splitMessageSegments } from './message-segments'

type ChatType = 'c2c' | 'group'
type MessageRole = 'user' | 'assistant'

interface ChatMessage {
  id: number
  role: MessageRole
  text: string
  agentId?: string | null
  status?: WebUIChatResponse['status']
  confirmationId?: string | null
  /** 这条气泡是否还在流式接收中。为真时显示光标动画。 */
  streaming?: boolean
}

const agents = ref<AgentSummary[]>([])
const agentsLoading = ref(true)
const agentsError = ref('')
const selectedAgentId = ref<string | null>(null)
const sessionId = ref('webui-session')
const username = ref('WebUI user')
const chatType = ref<ChatType>('c2c')
const groupId = ref('')
const messageInput = ref('')
const messages = ref<ChatMessage[]>([])
const sending = ref(false)
const errorMessage = ref('')
const pendingConfirmationId = ref<string | null>(null)
const transcript = ref<HTMLElement | null>(null)
/**
 * 是否走 SSE。默认开：需求 4 要的就是「看得见的流式」，而这条渠道天生支持。
 *
 * 仍然留一个开关而不是硬编码，因为两条路径都必须存在（需求 4 的原文是「流式
 * **和**非流式」），而且这是唯一能在同一界面上核对「两条路径给出同一段回复」
 * 的地方——反向代理关掉分块传输时，也需要能立刻切回去确认问题在代理而不在项目。
 */
const streamingEnabled = ref(true)
let streamAbort: AbortController | null = null
let messageSequence = 0

const selectedAgent = computed(() =>
  agents.value.find((agent) => agent.agent_id === selectedAgentId.value) || null
)

const agentOptions = computed(() =>
  agents.value.map((agent) => ({
    label: agent.display_name ? `${agent.display_name} (${agent.agent_id})` : agent.agent_id,
    value: agent.agent_id,
    disabled: !agent.enabled
  }))
)

const modelChain = computed(() => selectedAgent.value?.model_priority.join(' -> ') || '未配置')
const channelRelations = computed(() => selectedAgent.value?.relations.channels || [])
const canSend = computed(() => Boolean(messageInput.value.trim() && sessionId.value.trim() && !sending.value))

const chooseInitialAgent = (items: AgentSummary[]) => {
  const current = items.find((agent) => agent.agent_id === selectedAgentId.value && agent.enabled)
  if (current) return current.agent_id
  return (
    items.find((agent) => agent.enabled && agent.relations.is_default)?.agent_id ||
    items.find((agent) => agent.enabled && agent.relations.channels.includes('webui'))?.agent_id ||
    items.find((agent) => agent.enabled)?.agent_id ||
    null
  )
}

const loadAgentRelations = async () => {
  agentsLoading.value = true
  agentsError.value = ''
  try {
    const result = await listAgents()
    agents.value = result
    selectedAgentId.value = chooseInitialAgent(result)
  } catch (error) {
    agents.value = []
    selectedAgentId.value = null
    agentsError.value = error instanceof Error ? error.message : 'Agent 关系加载失败'
  } finally {
    agentsLoading.value = false
  }
}

const setChatType = (value: ChatType) => {
  chatType.value = value
  errorMessage.value = ''
  if (value === 'c2c') groupId.value = ''
}

const scrollToLatest = async () => {
  await nextTick()
  transcript.value?.lastElementChild?.scrollIntoView({ block: 'nearest' })
}

const appendMessage = (message: Omit<ChatMessage, 'id'>) => {
  messages.value.push({ id: ++messageSequence, ...message })
  void scrollToLatest()
}

const buildRequest = (text: string): WebUIChatRequest => {
  const request: WebUIChatRequest = {
    message: text,
    session_id: sessionId.value.trim(),
    username: username.value.trim() || 'WebUI user',
    chat_type: chatType.value
  }
  if (chatType.value === 'group') request.group_id = groupId.value.trim()
  if (selectedAgentId.value) request.agent_id = selectedAgentId.value
  return request
}

const validate = (text: string) => {
  if (!sessionId.value.trim()) return '会话 ID 不能为空'
  if (chatType.value === 'group' && !groupId.value.trim()) return '群聊必须填写群组 ID'
  if (!text.trim()) return '请输入消息内容'
  return ''
}

const submit = async (overrideText?: string) => {
  const text = (overrideText ?? messageInput.value).trim()
  const validationError = validate(text)
  if (validationError) {
    errorMessage.value = validationError
    return
  }

  errorMessage.value = ''
  sending.value = true
  appendMessage({ role: 'user', text })
  if (overrideText === undefined) messageInput.value = ''

  if (streamingEnabled.value) {
    await submitStreaming(text)
    return
  }

  try {
    const response = await llmApi.chat(buildRequest(text))
    // Keep the canonical session identity returned by the dispatcher so a
    // confirmation resumes the exact channel session that produced it.
    sessionId.value = response.session_id
    pendingConfirmationId.value = response.confirmation_id
    appendMessage({
      role: 'assistant',
      text: response.text || (response.status === 'awaiting_confirmation' ? '需要确认后才能继续' : '已完成'),
      agentId: response.agent_id,
      status: response.status,
      confirmationId: response.confirmation_id
    })
  } catch (error) {
    errorMessage.value = `消息发送失败：${error instanceof Error ? error.message : '未知错误'}`
  } finally {
    sending.value = false
  }
}

/**
 * 走 SSE 的那一条。
 *
 * 先插入一条空的 assistant 气泡，随 `delta` 就地追加——这条气泡就是需求 4 要的
 * 「用户看得见的流式」。它必须**先**出现：等第一个 delta 到达才插入的话，
 * 首字节之前界面上什么都没有，与非流式毫无区别。
 *
 * 三件必须成对的事：
 *
 * - `done` 之后用它的 `text` 覆盖气泡（而不是保留累积值）。两者正常情况下逐字
 *   相同，但供应商的回复策略（例如隐去 AI 署名）是在聚合之后执行的，只有 `done`
 *   带的是最终那一版。
 * - `error` 之后气泡不能停在半句话上而没有任何说明：把已收到的部分留下，
 *   另外给出错误——删掉已生成内容会让用户以为什么都没发生。
 * - 无论哪条路径，`sending` 都要落回 false，否则输入框永久锁死。
 */
const submitStreaming = async (text: string) => {
  const placeholderId = ++messageSequence
  messages.value.push({ id: placeholderId, role: 'assistant', text: '', streaming: true })
  void scrollToLatest()

  const patch = (mutate: (message: ChatMessage) => void) => {
    const target = messages.value.find((item) => item.id === placeholderId)
    if (target) mutate(target)
  }

  streamAbort = new AbortController()
  try {
    await readChatStream(
      '/llm/chat/stream',
      buildRequest(text),
      {
        signal: streamAbort.signal,
        onEvent: (event) => {
          if (event.event === 'delta' && typeof event.data.text === 'string') {
            patch((message) => {
              message.text += event.data.text as string
            })
            void scrollToLatest()
            return
          }
          if (event.event === 'reset' && typeof event.data.text === 'string') {
            patch((message) => {
              message.text = event.data.text as string
            })
            return
          }
          if (event.event === 'done') {
            const finalText = typeof event.data.text === 'string' ? event.data.text : ''
            if (typeof event.data.session_id === 'string') sessionId.value = event.data.session_id
            const confirmationId =
              typeof event.data.confirmation_id === 'string' ? event.data.confirmation_id : null
            pendingConfirmationId.value = confirmationId
            patch((message) => {
              message.streaming = false
              message.text =
                finalText ||
                message.text ||
                (event.data.status === 'awaiting_confirmation' ? '需要确认后才能继续' : '已完成')
              message.agentId =
                typeof event.data.agent_id === 'string' ? event.data.agent_id : null
              message.status = event.data.status as WebUIChatResponse['status']
              message.confirmationId = confirmationId
            })
            return
          }
          if (event.event === 'error') {
            const detail = typeof event.data.error === 'string' ? event.data.error : '未知错误'
            errorMessage.value = `生成失败：${detail}`
            patch((message) => {
              message.streaming = false
              // 已收到的部分留着：删掉会让用户以为什么都没发生过。
              if (!message.text) message.text = '（生成失败，未收到内容）'
            })
          }
        }
      }
    )
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) {
      errorMessage.value = `消息发送失败：${error instanceof Error ? error.message : '未知错误'}`
    }
    patch((message) => {
      message.streaming = false
      if (!message.text) message.text = '（生成中断，未收到内容）'
    })
  } finally {
    streamAbort = null
    sending.value = false
    // 无论走哪条路径都要清掉进行标记。只在 done / error 分支里清是不够的：
    // 一条被上游或代理提前关掉的流不会送来任何终局事件，那时光标会永远闪，
    // 而实际上已经没有人在生成了。
    patch((message) => {
      message.streaming = false
      if (!message.text) message.text = '（生成结束，未收到内容）'
    })
  }
}

const confirmPending = async () => {
  const confirmationId = pendingConfirmationId.value
  if (!confirmationId || sending.value) return
  await submit(`确认 ${confirmationId}`)
}

const handleComposerKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    if (canSend.value) void submit()
  }
}

/**
 * 每条消息的片段。按代码围栏切开，代码走代码框 + 复制按钮那条渲染路径。
 *
 * 用 computed 而不是在模板里逐条调用：切分是纯函数，但模板里的调用会在
 * 每次任意响应式变化时对**所有**消息重跑一遍（发送状态一变就是一次全量），
 * 而一条长回复的切分不该被输入框的每次按键带着算。
 */
const messageSegments = computed(() =>
  new Map(messages.value.map((item) => [item.id, splitMessageSegments(item.text)]))
)

/** 刚复制成功的代码块，用于给按钮一个短暂的「已复制」反馈。 */
const copiedCodeKey = ref<string | null>(null)
let copiedResetTimer: ReturnType<typeof setTimeout> | null = null

const copyCode = async (key: string, code: string) => {
  const copied = await copyTextToClipboard(code)
  if (!copied) {
    // 剪贴板不可用（非 HTTPS、权限被关、旧浏览器）时给一句可执行的话，
    // 而不是一个用户无法处置的失败提示。代码本身仍然在框里可以手动选中。
    errorMessage.value = '浏览器未授权剪贴板，请手动选中代码复制'
    return
  }
  errorMessage.value = ''
  copiedCodeKey.value = key
  if (copiedResetTimer) clearTimeout(copiedResetTimer)
  copiedResetTimer = setTimeout(() => {
    copiedCodeKey.value = null
  }, 1600)
}

onBeforeUnmount(() => {
  if (copiedResetTimer) clearTimeout(copiedResetTimer)
  // 离开页面时中止仍在读的流：不中止的话那次上游请求会跑到底，费用照付而没有人
  // 会收到结果，而读循环还持有一个 reader 不放。
  streamAbort?.abort()
})

onMounted(() => {
  void loadAgentRelations()
})
</script>

<template>
  <main class="chat-page" aria-labelledby="chat-page-title">
    <aside class="relation-panel" aria-labelledby="relation-title">
      <header class="relation-header">
        <div>
          <span class="eyebrow"><n-icon aria-hidden="true"><git-network-outline /></n-icon>统一路由</span>
          <h1 id="chat-page-title">Agent 对话</h1>
        </div>
        <n-button quaternary circle :loading="agentsLoading" aria-label="刷新 Agent 关系" @click="loadAgentRelations">
          <template #icon><n-icon aria-hidden="true"><refresh-outline /></n-icon></template>
        </n-button>
      </header>

      <n-alert v-if="agentsError" type="error" :show-icon="true">{{ agentsError }}</n-alert>
      <section class="identity-settings" aria-label="渠道身份">
        <h2 id="relation-title">渠道身份</h2>
        <label>
          <span>会话 ID</span>
          <n-input v-model:value="sessionId" data-test="session-id" placeholder="webui-session" />
        </label>
        <label>
          <span>显示名称</span>
          <n-input v-model:value="username" data-test="username" placeholder="WebUI user" />
        </label>
        <div class="field-group">
          <span>会话类型</span>
          <div class="segmented" role="group" aria-label="会话类型">
            <button type="button" :class="{ active: chatType === 'c2c' }" data-test="chat-type-c2c" :aria-pressed="chatType === 'c2c'" @click="setChatType('c2c')">私聊</button>
            <button type="button" :class="{ active: chatType === 'group' }" data-test="chat-type-group" :aria-pressed="chatType === 'group'" @click="setChatType('group')">群聊</button>
          </div>
        </div>
        <label v-if="chatType === 'group'">
          <span>群组 ID</span>
          <n-input v-model:value="groupId" data-test="group-id" placeholder="例如 study-room" />
        </label>
      </section>

      <section class="agent-settings" aria-labelledby="agent-title">
        <h2 id="agent-title">Agent</h2>
        <n-select
          v-model:value="selectedAgentId"
          :options="agentOptions"
          :loading="agentsLoading"
          placeholder="选择 Agent"
          aria-label="选择 Agent"
        />
        <div v-if="selectedAgent" class="agent-detail">
          <div class="agent-name">
            <n-icon aria-hidden="true"><person-circle-outline /></n-icon>
            <div><strong>{{ selectedAgent.display_name || selectedAgent.agent_id }}</strong><small>{{ selectedAgent.agent_id }}</small></div>
          </div>
          <dl>
            <div><dt>模型链</dt><dd class="mono">{{ modelChain }}</dd></div>
            <div><dt>工具</dt><dd>{{ selectedAgent.allow_tools ? `${selectedAgent.max_tool_iterations} 轮` : '已禁用' }}</dd></div>
            <div><dt>MCP</dt><dd>{{ selectedAgent.mcp_allowlist.join(', ') || '未绑定' }}</dd></div>
          </dl>
          <div class="relation-tags" aria-label="已绑定渠道">
            <n-tag v-for="channel in channelRelations" :key="channel" size="small">{{ channel }}</n-tag>
          </div>
        </div>
        <p v-else-if="!agentsLoading" class="muted">没有可用 Agent，消息将无法显式指定 Agent。</p>
      </section>
    </aside>

    <section class="conversation" aria-label="对话工作区">
      <header class="conversation-header">
        <div>
          <strong>{{ selectedAgent?.display_name || selectedAgent?.agent_id || '自动路由' }}</strong>
          <span>{{ chatType === 'group' ? `群聊 · ${groupId || '未设置群组'}` : 'WebUI 私聊' }}</span>
        </div>
        <n-tag :type="selectedAgent?.enabled ? 'success' : 'default'">
          {{ selectedAgent?.enabled ? '运行中' : '待配置' }}
        </n-tag>
        <!--
          流式开关。需求 4 要求两条路径都存在，因此这里是一个可切换项而不是硬编码：
          它也是唯一能在同一界面上核对「两条路径给出同一段回复」的地方，
          并且在反向代理关掉分块传输时可以立刻切回去，确认问题在代理而不在项目。
        -->
        <div class="segmented" role="group" aria-label="回复取回方式">
          <button
            type="button"
            :class="{ active: streamingEnabled }"
            data-test="stream-mode-on"
            :aria-pressed="streamingEnabled"
            :disabled="sending"
            @click="streamingEnabled = true"
          >流式</button>
          <button
            type="button"
            :class="{ active: !streamingEnabled }"
            data-test="stream-mode-off"
            :aria-pressed="!streamingEnabled"
            :disabled="sending"
            @click="streamingEnabled = false"
          >非流式</button>
        </div>
      </header>

      <div ref="transcript" class="transcript" role="log" aria-live="polite" aria-relevant="additions">
        <div v-if="!messages.length" class="empty-conversation">
          <n-icon aria-hidden="true"><shield-checkmark-outline /></n-icon>
          <strong>开始一个受统一策略管理的会话</strong>
          <span>当前请求将按渠道身份和 Agent 关系解析模型、资源与工具策略。</span>
        </div>
        <article v-for="item in messages" :key="item.id" class="message-row" :class="item.role">
          <div class="message-meta">
            <span>{{ item.role === 'user' ? username : (item.agentId || 'Agent') }}</span>
            <n-tag v-if="item.status === 'awaiting_confirmation'" size="small" type="warning">等待确认</n-tag>
          </div>
          <div class="message-body">
            <template v-for="(segment, index) in messageSegments.get(item.id) || []" :key="`${item.id}-${index}`">
              <p v-if="segment.kind === 'text'">{{ segment.text }}</p>
              <figure v-else class="code-block">
                <figcaption>
                  <span class="code-language">{{ segment.language || '代码' }}</span>
                  <n-button
                    size="tiny"
                    quaternary
                    :data-test="`copy-code-${item.id}-${index}`"
                    :aria-label="`复制${segment.language || ''}代码`"
                    @click="copyCode(`${item.id}-${index}`, segment.code)"
                  >
                    <template #icon><n-icon aria-hidden="true"><copy-outline /></n-icon></template>
                    {{ copiedCodeKey === `${item.id}-${index}` ? '已复制' : '复制' }}
                  </n-button>
                </figcaption>
                <pre><code>{{ segment.code }}</code></pre>
              </figure>
            </template>
            <!--
              流式气泡的进行提示。首字节之前没有任何片段，此时必须有东西说明
              「在生成」——否则这条空气泡看起来像一次失败的发送。
              aria-live 在外层 .transcript 上，这里只需要一句可读的文本。
            -->
            <p v-if="item.streaming" class="streaming-indicator" data-test="streaming-indicator">
              <span class="streaming-cursor" aria-hidden="true"></span>
              <span class="visually-hidden">正在生成回复</span>
            </p>
          </div>
          <div v-if="item.role === 'assistant' && item.status === 'completed'" class="response-status">
            <n-icon aria-hidden="true"><checkmark-circle-outline /></n-icon>主模型链已返回
          </div>
          <div v-if="item.confirmationId" class="confirmation-box">
            <span>确认编号 <b class="mono">{{ item.confirmationId }}</b></span>
            <n-button
              v-if="pendingConfirmationId === item.confirmationId"
              type="warning"
              size="small"
              :loading="sending"
              aria-label="确认待处理操作"
              @click="confirmPending"
            >确认并继续</n-button>
          </div>
        </article>
        <article v-if="sending" class="message-row assistant pending" aria-label="Agent 正在处理">
          <div class="typing"><span></span><span></span><span></span></div>
        </article>
      </div>

      <div class="composer">
        <n-alert v-if="errorMessage" type="error" :show-icon="true" role="alert">{{ errorMessage }}</n-alert>
        <div class="composer-row">
          <n-input
            v-model:value="messageInput"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            placeholder="输入消息"
            data-test="message-input"
            aria-label="消息内容"
            @keydown="handleComposerKeydown"
          />
          <n-button
            type="primary"
            circle
            :disabled="!canSend"
            :loading="sending"
            data-test="send-message"
            aria-label="发送消息"
            @click="submit()"
          >
            <template #icon><n-icon aria-hidden="true"><paper-plane-outline /></n-icon></template>
          </n-button>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.chat-page { display: grid; grid-template-columns: minmax(260px, 320px) minmax(0, 1fr); height: calc(100vh - 28px); color: var(--text-color); background: var(--bg-color); }
.relation-panel { box-sizing: border-box; overflow-y: auto; padding: var(--space-6); border-right: 1px solid var(--border-color); background: var(--card-bg-color); }
.relation-header, .conversation-header, .agent-name, .message-meta, .confirmation-box, .composer-row { display: flex; align-items: center; }
.relation-header, .conversation-header, .confirmation-box { justify-content: space-between; }
.eyebrow { display: flex; align-items: center; gap: var(--space-2); color: var(--primary-color-text); font-size: var(--font-size-sm); font-weight: 600; }
h1 { margin: var(--space-2) 0 0; font-size: var(--font-size-2xl); line-height: var(--line-height-tight); letter-spacing: 0; }
h2 { margin: 0 0 var(--space-3); font-size: var(--font-size-base); letter-spacing: 0; }
.identity-settings, .agent-settings { display: grid; gap: var(--space-3); padding: var(--space-5) 0; border-top: 1px solid var(--border-color); }
.identity-settings { margin-top: var(--space-5); }
label, .field-group { display: grid; gap: var(--space-2); color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.segmented { display: grid; grid-template-columns: repeat(2, 1fr); min-height: 34px; padding: 2px; border: 1px solid var(--border-color); border-radius: var(--radius-sm); background: var(--bg-color); }
.segmented button { border: 0; border-radius: calc(var(--radius-sm) - 2px); color: var(--text-color-secondary); background: transparent; cursor: pointer; }
.segmented button.active { color: var(--primary-color-text); background: var(--card-bg-color); box-shadow: var(--box-shadow-sm); font-weight: 600; }
.agent-detail { display: grid; gap: var(--space-4); padding-top: var(--space-2); }
.agent-name { gap: var(--space-3); }
.agent-name > .n-icon { flex: 0 0 auto; color: var(--primary-color); font-size: 28px; }
.agent-name div { display: grid; min-width: 0; }
.agent-name small, .conversation-header span, .muted { color: var(--text-color-secondary); font-size: var(--font-size-sm); }
dl { display: grid; gap: var(--space-2); margin: 0; }
dl div { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: var(--space-2); font-size: var(--font-size-sm); }
dt { color: var(--text-color-secondary); }
dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.relation-tags { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.conversation { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-width: 0; min-height: 0; }
.conversation-header { min-height: 64px; padding: 0 var(--space-6); border-bottom: 1px solid var(--border-color); background: var(--card-bg-color); }
.conversation-header > div { display: grid; gap: 2px; min-width: 0; }
.transcript { overflow-y: auto; padding: var(--space-6) max(var(--space-6), calc((100% - 820px) / 2)); }
.empty-conversation { display: grid; place-items: center; align-content: center; min-height: 100%; color: var(--text-color-secondary); text-align: center; }
.empty-conversation > .n-icon { margin-bottom: var(--space-4); color: var(--primary-color); font-size: 40px; }
.empty-conversation strong { margin-bottom: var(--space-2); color: var(--text-color); font-size: var(--font-size-lg); }
.empty-conversation span { max-width: 440px; line-height: var(--line-height-relaxed); }
.message-row { width: min(78%, 680px); margin-bottom: var(--space-5); }
.message-row.user { margin-left: auto; }
.message-meta { gap: var(--space-2); margin-bottom: var(--space-2); color: var(--text-color-secondary); font-size: var(--font-size-sm); }
.user .message-meta { justify-content: flex-end; }
.message-row p { box-sizing: border-box; margin: 0; padding: var(--space-3) var(--space-4); border: 1px solid var(--border-color); border-radius: var(--radius-sm); white-space: pre-wrap; overflow-wrap: anywhere; line-height: var(--line-height-relaxed); background: var(--card-bg-color); }
.message-row.user p { border-color: var(--primary-color); color: white; background: var(--primary-color); }
/* 一条回复里正文与代码块交替出现时用同一间距，否则代码框会贴在段落上，
   看起来像正文的一部分——而那正是修这一处之前的样子。 */
.message-body { display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; }
.code-block { margin: 0; border: 1px solid var(--border-color); border-radius: var(--radius-sm); overflow: hidden; background: var(--code-bg-color, color-mix(in srgb, var(--text-color) 4%, var(--card-bg-color))); }
.code-block figcaption { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); padding: var(--space-1) var(--space-2) var(--space-1) var(--space-3); border-bottom: 1px solid var(--border-color); background: color-mix(in srgb, var(--text-color) 6%, transparent); }
.code-language { color: var(--text-color-secondary); font-size: var(--font-size-sm); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
/* 代码横向自己滚，不让整页出现横向滚动条：一行很长的命令是常态，
   而页面级横向滚动会把左侧关系面板也推出视口。 */
.code-block pre { margin: 0; padding: var(--space-3); overflow-x: auto; }
.code-block code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: var(--font-size-sm); line-height: var(--line-height-relaxed); white-space: pre; }
/* 用户气泡是实心主色底，代码框在其中要恢复成可读的浅底，否则深底叠深底
   会让代码几乎看不见。 */
.message-row.user .code-block { border-color: color-mix(in srgb, white 40%, var(--primary-color)); color: var(--text-color); background: var(--card-bg-color); }
.message-row.user .code-block figcaption { background: color-mix(in srgb, var(--text-color) 6%, transparent); }
.response-status { display: flex; align-items: center; gap: var(--space-1); margin-top: var(--space-2); color: var(--success-color-text); font-size: var(--font-size-sm); }
/* 流式进行提示。首字节之前气泡里没有任何片段，此时这个光标是唯一说明
   「在生成」的东西——没有它，一条空气泡看起来像一次失败的发送。 */
.streaming-indicator { display: flex; align-items: center; margin: 0; min-height: 1.2em; }
.streaming-cursor { display: inline-block; width: 2px; height: 1.1em; background: var(--primary-color); animation: streaming-blink 1s steps(2, start) infinite; }
/* 屏幕阅读器专用文本：光标是纯装饰，读屏用户需要一句真正的话。
   不用 display:none —— 那会让它同时对辅助技术消失。 */
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@keyframes streaming-blink { to { opacity: 0; } }
/* 尊重系统的减少动效偏好：闪烁光标是前庭/注意力敏感人群最常点名的一类动效。 */
@media (prefers-reduced-motion: reduce) {
  .streaming-cursor { animation: none; opacity: .6; }
}
.confirmation-box { gap: var(--space-3); margin-top: var(--space-2); padding: var(--space-3); border-left: 3px solid var(--warning-color); color: var(--warning-color-text); background: color-mix(in srgb, var(--warning-color) 9%, var(--card-bg-color)); }
.confirmation-box span { min-width: 0; overflow-wrap: anywhere; }
.pending { width: 72px; }
.typing { display: flex; gap: var(--space-1); padding: var(--space-3) var(--space-4); }
.typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--text-color-secondary); animation: typing 1s infinite alternate; }
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }
.composer { display: grid; gap: var(--space-2); padding: var(--space-4) max(var(--space-6), calc((100% - 820px) / 2)); border-top: 1px solid var(--border-color); background: var(--card-bg-color); }
.composer-row { gap: var(--space-3); }
.composer-row > .n-button { flex: 0 0 auto; width: 42px; height: 42px; }
@keyframes typing { to { opacity: .28; transform: translateY(-2px); } }

@media (max-width: 900px) {
  .chat-page { grid-template-columns: 250px minmax(0, 1fr); }
  .relation-panel { padding: var(--space-4); }
  .transcript, .composer { padding-right: var(--space-4); padding-left: var(--space-4); }
  .message-row { width: 88%; }
}

@media (max-width: 768px) {
  .chat-page { grid-template-columns: 1fr; grid-template-rows: auto minmax(520px, 1fr); height: auto; min-height: calc(100vh - 84px); }
  .relation-panel { overflow: visible; border-right: 0; border-bottom: 1px solid var(--border-color); }
  .identity-settings, .agent-settings { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .identity-settings h2, .agent-settings h2, .agent-detail, .muted { grid-column: 1 / -1; }
  .conversation { min-height: 620px; }
}

@media (max-width: 480px) {
  .identity-settings, .agent-settings { grid-template-columns: 1fr; }
  .conversation-header { padding: 0 var(--space-4); }
  .message-row { width: 94%; }
  .confirmation-box { align-items: flex-start; flex-direction: column; }
}
</style>
