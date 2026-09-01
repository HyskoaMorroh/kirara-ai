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

/**
 * 一条被声明为「项目创建者本人」的 IM 渠道身份。
 *
 * 字段与后端 `CreatorChannelIdentity` 一一对应。它授予的是**宿主操作权限**：
 * 声明之后，来自这个身份的消息能在 IM 渠道上用 MCP 工具与 command Hook。
 * 不声明时聊天侧谁都拿不到创建者身份——包括创建者本人。
 */
export interface CreatorChannelIdentity {
  channel_type: string
  sender_scope: string
  /** 留空表示该渠道下任意机器人账号都算。 */
  account_scope: string | null
  /** 留空表示任意适配器实例。 */
  adapter_instance: string | null
  /**
   * 群聊里是否也认这个身份。
   *
   * 默认 false 是刻意的安全默认：群里所有人都看得到创建者发的指令并照抄，
   * 照抄的人 `sender_scope` 不同因而拿不到身份，但把宿主操作暴露在多人可见的
   * 会话里是另一回事。
   */
  allow_group_chat: boolean
}

/** 后端 `SUPPORTED_CHANNEL_TYPES` 的六个取值。写错会静默匹配不上任何消息。 */
export const CREATOR_CHANNEL_TYPES = [
  'webui',
  'http',
  'onebot',
  'qqbot',
  'telegram',
  'wecom'
] as const

export interface AgentRuntimeForm {
  turn_deadline_seconds: number
  reply_stream_mode: ProcessReplyStreamMode
  /** 按渠道覆盖进程默认；界面上以「渠道 + 档位」的成对列表编辑。 */
  channel_reply_stream_modes: Record<string, ProcessReplyStreamMode>
  tool_search_threshold: number
  /** 哪些 IM 渠道身份属于创建者。空数组表示聊天侧不授予任何人。 */
  creator_channel_identities: CreatorChannelIdentity[]
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
  tool_search_threshold: 12,
  creator_channel_identities: []
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
        tool_search_threshold: runtime.tool_search_threshold ?? 12,
        // 必须读回而不是每次重置为空：只在提交时带上会让用户每次打开设置页
        // 都看到空列表，保存一次就把已有声明清掉。
        creator_channel_identities: (runtime.creator_channel_identities ?? []).map(
          (identity) => ({
            channel_type: identity.channel_type,
            sender_scope: identity.sender_scope,
            account_scope: identity.account_scope ?? null,
            adapter_instance: identity.adapter_instance ?? null,
            allow_group_chat: identity.allow_group_chat === true
          })
        )
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

  const addCreatorIdentity = () => {
    formData.value.creator_channel_identities.push({
      // 默认落在 onebot：它是本项目最常用的 IM 入口。
      channel_type: 'onebot',
      sender_scope: '',
      account_scope: null,
      adapter_instance: null,
      // 群聊默认关，与后端默认一致。
      allow_group_chat: false
    })
  }

  const removeCreatorIdentity = (index: number) => {
    formData.value.creator_channel_identities.splice(index, 1)
  }

  /**
   * 把编辑中的身份折叠成后端要的形状。
   *
   * 与 `collectChannelModes` 同一条纪律：没填发送者标识的整条丢掉，
   * 而不是提交一个空串让后端返回 400——用户此刻只是还没填完那一行。
   * 可选字段的空串归一成 null：后端把空串视为无效（要么省略要么非空）。
   */
  const collectCreatorIdentities = (): CreatorChannelIdentity[] =>
    formData.value.creator_channel_identities
      .filter((identity) => identity.sender_scope.trim())
      .map((identity) => ({
        channel_type: identity.channel_type,
        sender_scope: identity.sender_scope.trim(),
        account_scope: identity.account_scope?.trim() || null,
        adapter_instance: identity.adapter_instance?.trim() || null,
        allow_group_chat: identity.allow_group_chat === true
      }))

  const handleSubmit = async () => {
    loading.value = true
    try {
      await http.post('/system/config/agent-runtime', {
        turn_deadline_seconds: formData.value.turn_deadline_seconds,
        reply_stream_mode: formData.value.reply_stream_mode,
        channel_reply_stream_modes: collectChannelModes(),
        tool_search_threshold: formData.value.tool_search_threshold,
        creator_channel_identities: collectCreatorIdentities()
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
    addCreatorIdentity,
    removeCreatorIdentity,
    handleSubmit
  }
}
