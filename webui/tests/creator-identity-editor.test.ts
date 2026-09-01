/**
 * 创建者渠道身份要能在界面上编辑（需求 10）。
 *
 * 后端已经能读能写（`GET /system/config` + `POST /system/config/agent-runtime`），
 * 但前端 `AgentRuntimeForm` 里没有这个字段，`AgentRuntimeCard.vue` 也没有编辑区。
 *
 * 这个字段的含义是"哪些 IM 渠道身份属于项目创建者"。默认空表意味着**聊天侧
 * 谁都拿不到创建者身份**——MCP 工具列表恒空、command Hook 恒被拒，包括创建者
 * 本人。用户在 QQ 里对自己的机器人说"帮我装个 skill"，得到一次正常回复、
 * 工具一个没生效，而界面上没有任何地方解释为什么。
 *
 * 界面上必须说清三件事，否则这个开关会被误用：
 *
 * 1. **它授予的是宿主操作权限**，不是普通配置项。
 * 2. **群聊默认不生效**。群里所有人都看得到创建者发的指令并照抄；照抄的人
 *    `sender_scope` 不同因而拿不到身份，但把宿主操作暴露在多人可见会话里
 *    是另一回事，要开必须显式勾选。
 * 3. **改完要重启**。这批参数在启动时被读进 executor（后端返回
 *    `restart_required: true`）。不说的话用户会以为下一条消息就生效。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const vm = readFileSync(
  resolve(root, 'src/views/settings/viewmodels/agent-runtime.vm.ts'),
  'utf-8'
)
const card = readFileSync(
  resolve(root, 'src/views/settings/components/AgentRuntimeCard.vue'),
  'utf-8'
)

describe('创建者渠道身份编辑', () => {
  it('自检：确实读到了 Agent 运行时配置的 vm 与面板', () => {
    expect(vm).toMatch(/AgentRuntimeForm/)
    expect(card).toMatch(/tool_search_threshold/)
  })

  it('表单类型带上 creator_channel_identities', () => {
    const at = vm.indexOf('export interface AgentRuntimeForm')
    expect(at, '找不到 AgentRuntimeForm').toBeGreaterThan(-1)
    const body = vm.slice(at, vm.indexOf('\n}', at))
    expect(body, '表单里没有创建者身份字段').toMatch(/creator_channel_identities/)
  })

  it('身份条目有独立类型，五个字段与后端一一对应', () => {
    const at = vm.indexOf('CreatorChannelIdentity')
    expect(at, '缺少 CreatorChannelIdentity 类型').toBeGreaterThan(-1)
    const declaration = vm.slice(at, vm.indexOf('\n}', at))
    for (const field of [
      'channel_type',
      'sender_scope',
      'account_scope',
      'adapter_instance',
      'allow_group_chat'
    ]) {
      expect(declaration, `身份类型缺字段 ${field}`).toContain(field)
    }
  })

  it('读配置时带回这个字段，而不是每次都重置为空', () => {
    // 只在提交时带上、读取时丢掉，会让用户每次打开设置页都看到空列表，
    // 保存一次就把已有声明清掉。
    const at = vm.indexOf('const fetchConfig')
    const body = vm.slice(at, vm.indexOf('const save', at))
    expect(body, 'fetchConfig 没有读回创建者身份').toMatch(
      /creator_channel_identities/
    )
  })

  it('提交时把这个字段发出去', () => {
    // 提交函数叫 handleSubmit，不叫 save。断言原先用 `indexOf('const save')`
    // 取到 -1，`slice(-1)` 得到最后一个字符——于是它检查的是一个换行符，
    // 恒为失败。改为定位真实的提交调用。
    const at = vm.indexOf("http.post('/system/config/agent-runtime'")
    expect(at, '找不到提交调用').toBeGreaterThan(-1)
    const body = vm.slice(at, vm.indexOf('})', at))
    expect(body, '保存时没有提交创建者身份').toMatch(/creator_channel_identities/)
  })

  it('面板上有增删条目的入口', () => {
    expect(card).toMatch(/data-test="add-creator-identity"/)
    expect(card).toMatch(/data-test="remove-creator-identity"/)
  })

  it('渠道类型是下拉而不是自由文本', () => {
    // 后端只接受六个渠道名，写错会静默匹配不上任何消息。
    expect(card).toMatch(/creatorChannelOptions|channelTypeOptions/)
  })

  it('界面说清这是宿主操作权限，不是普通配置', () => {
    const text = card.replace(/\s+/g, '')
    expect(
      /创建者|宿主|服务器/.test(text),
      '面板没有说明这个开关授予的是什么'
    ).toBe(true)
  })

  it('界面说清群聊默认不生效', () => {
    const text = card.replace(/\s+/g, '')
    expect(/群聊|群里|多人/.test(text), '没有解释 allow_group_chat 的风险').toBe(true)
  })

  it('界面说清改完要重启', () => {
    // 后端返回 restart_required: true；不说的话用户以为下一条消息就生效。
    const text = card.replace(/\s+/g, '')
    expect(/重启|重新启动/.test(text), '没有告知需要重启').toBe(true)
  })
})
