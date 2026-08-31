import { ref } from 'vue'
import { http } from '@/utils/http'
import { useMessage } from 'naive-ui'

/**
 * 与 `kirara_ai/config/global_config.py` 的 `AgentRuntimeConfig` 一一对应。
 *
 * 只声明这一路由允许写入的四项。后端用的是键白名单：多提交一个键会得到
 * 400 而不是被静默丢掉——静默丢掉时界面会显示「保存成功」，而那个值
 * 从未生效。
 */
export type ProcessReplyStreamMode = 'off' | 'aggregate' | 'incremental'

export interface AgentRuntimeForm {
  turn_deadline_seconds: number
  reply_stream_mode: ProcessReplyStreamMode
  /** 按渠道覆盖进程默认；界面上以「渠道 + 档位」的成对列表编辑。 */
  channel_reply_stream_modes: Record<string, ProcessReplyStreamMode>
  tool_search_threshold: number
}

export interface AgentRuntimeConfigResponse {
  agent_runtime: AgentRuntimeForm
}

/** 界面上的一行渠道覆盖。用数组而不是直接绑对象：编辑中的渠道名可以为空。 */
export interface ChannelModeRow {
  channel: string
  mode: ProcessReplyStreamMode
}

const DEFAULTS = (): AgentRuntimeForm => ({
  turn_deadline_seconds: 0,
  reply_stream_mode: 'off',
  channel_reply_stream_modes: {},
  tool_search_threshold: 12
})

export function useAgentRuntimeViewModel() {
  const loading = ref(false)
  const message = useMessage()

  const formData = ref<AgentRuntimeForm>(DEFAULTS())
  const channelRows = ref<ChannelModeRow[]>([])

  const fetchConfig = async () => {
    loading.value = true
    try {
      const response = await http.get<AgentRuntimeConfigResponse>('/system/config')
      const runtime = response.agent_runtime ?? DEFAULTS()
      formData.value = {
        turn_deadline_seconds: runtime.turn_deadline_seconds ?? 0,
        reply_stream_mode: runtime.reply_stream_mode ?? 'off',
        channel_reply_stream_modes: { ...(runtime.channel_reply_stream_modes ?? {}) },
        tool_search_threshold: runtime.tool_search_threshold ?? 12
      }
      channelRows.value = Object.entries(formData.value.channel_reply_stream_modes).map(
        ([channel, mode]) => ({ channel, mode })
      )
    } catch (error: any) {
      message.error(error.response?.data?.message ?? '获取 Agent 运行时配置失败')
    } finally {
      loading.value = false
    }
  }

  const addChannelRow = () => {
    channelRows.value.push({ channel: '', mode: 'off' })
  }

  const removeChannelRow = (index: number) => {
    channelRows.value.splice(index, 1)
  }

  /**
   * 把编辑中的行折叠成后端要的对象。
   *
   * 空渠道名整行丢掉而不是提交一个空键：后端会拒绝空键并返回 400，
   * 而用户此刻只是还没填完那一行，报错在这里没有任何帮助。
   */
  const collectChannelModes = (): Record<string, ProcessReplyStreamMode> => {
    const collected: Record<string, ProcessReplyStreamMode> = {}
    for (const row of channelRows.value) {
      const channel = row.channel.trim()
      if (!channel) continue
      collected[channel] = row.mode
    }
    return collected
  }

  const handleSubmit = async () => {
    loading.value = true
    try {
      await http.post('/system/config/agent-runtime', {
        turn_deadline_seconds: formData.value.turn_deadline_seconds,
        reply_stream_mode: formData.value.reply_stream_mode,
        channel_reply_stream_modes: collectChannelModes(),
        tool_search_threshold: formData.value.tool_search_threshold
      })
      // 这批参数在启动时被读进 executor。不说这句，用户会以为下一条消息
      // 就按新档位取回，然后去排查一个并不存在的问题。
      message.success('已保存，重启服务后生效')
    } catch (error: any) {
      message.error(error.response?.data?.error ?? '保存 Agent 运行时配置失败')
    } finally {
      loading.value = false
    }
  }

  return {
    loading,
    formData,
    channelRows,
    fetchConfig,
    addChannelRow,
    removeChannelRow,
    handleSubmit
  }
}
