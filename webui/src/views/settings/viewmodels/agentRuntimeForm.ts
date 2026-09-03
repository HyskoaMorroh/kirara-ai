/**
 * Agent 运行时设置表单里的两条折叠规则。
 *
 * 抽成纯函数，是因为它们**是**这个表单的行为，而此前只被源码 grep「测过」：
 * `agent-runtime-settings.test.ts` 的断言是
 * `expect(viewModelSource).toContain('collectChannelModes')`——
 * 只证明这个名字在文件里出现过，不证明它丢掉了空行、不证明空串归一成了 null。
 *
 * 这两件事错了都是静默的：
 *
 * - 空渠道名没丢掉 → 提交一个空键 → 后端 400，而用户只是还没填完那一行；
 * - 可选字段的空串没归一成 null → 后端把空串当无效值 → 那条创建者身份
 *   在运行时匹配不上任何消息，而界面显示「保存成功」。
 *   后者的后果是 MCP 工具与 command Hook 在 IM 渠道上静默不可用。
 */

import type { CreatorChannelIdentity, ProcessReplyStreamMode } from './agent-runtime.vm'

export interface ChannelRow {
  channel: string
  mode: ProcessReplyStreamMode
}

/**
 * 把编辑中的渠道行折叠成后端要的对象。
 *
 * 空渠道名**整行丢掉**而不是提交一个空键：后端会拒绝空键并返回 400，
 * 而用户此刻只是还没填完那一行，报错在这里没有任何帮助。
 *
 * 同名行后写覆盖先写：一个渠道只能有一个档位，而两行同名时用户看到的是
 * 最后编辑的那个。
 */
export function collectChannelModes(
  rows: readonly ChannelRow[]
): Record<string, ProcessReplyStreamMode> {
  const collected: Record<string, ProcessReplyStreamMode> = {}
  for (const row of rows) {
    const channel = String(row.channel ?? '').trim()
    if (!channel) continue
    collected[channel] = row.mode
  }
  return collected
}

/** 编辑中的创建者身份行——可选字段允许是空串（用户清空了输入框）。 */
export interface DraftCreatorIdentity {
  channel_type: string
  sender_scope: string
  account_scope?: string | null
  adapter_instance?: string | null
  allow_group_chat?: boolean
}

/**
 * 把编辑中的创建者身份折叠成后端要的形状。
 *
 * 与 `collectChannelModes` 同一条纪律：没填发送者标识的整条丢掉，
 * 而不是提交一个空串让后端返回 400。
 *
 * 可选字段的空串归一成 `null`：后端把空串视为无效（要么省略要么非空），
 * 而 `null` 的语义是「任意」。不归一的话那条身份匹配不上任何消息，
 * 且界面显示保存成功——MCP 工具与 command Hook 于是在 IM 渠道上静默不可用。
 *
 * `allow_group_chat` 显式转成布尔并默认 `false`：群里所有人都看得到创建者
 * 发的指令，把宿主操作暴露在多人可见的会话里需要用户主动打开。
 */
export function collectCreatorIdentities(
  identities: readonly DraftCreatorIdentity[]
): CreatorChannelIdentity[] {
  return identities
    .filter((identity) => String(identity.sender_scope ?? '').trim())
    .map((identity) => ({
      channel_type: identity.channel_type,
      sender_scope: String(identity.sender_scope).trim(),
      account_scope: identity.account_scope?.trim() || null,
      adapter_instance: identity.adapter_instance?.trim() || null,
      allow_group_chat: identity.allow_group_chat === true
    }))
}
