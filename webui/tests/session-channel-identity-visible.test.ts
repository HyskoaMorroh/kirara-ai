// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 会话列表要显示渠道身份，因为「哪个会话」这个问题只有它能回答。
 *
 * 需求 10 要求把每个入口统一映射到「渠道身份 → Agent → 上游模型/备用链 →
 * Prompt/Skill/Memory/MCP」这条关系链。会话是这条链上唯一带**具体人**的一环，
 * 而列表里的 `session_id` 是一个 64 位 SHA-256 摘要——它对人没有任何含义。
 *
 * 后端从本版起把 `ChannelContext` 随历史一并落盘，`GET /agents/sessions`
 * 每一行都带 `channel_identity`（五个字段：channel_type、adapter_instance、
 * account_scope、conversation_scope、sender_scope）。前端类型里没有这一项，
 * 界面上也没有——于是运维看到一屏摘要，无法回答任何一个真实问题：
 * 「张三在 QQ 上那个卡住的会话是哪一行」「这批会话是 Telegram 还是企业微信来的」。
 *
 * 更要紧的是**删除**：清空历史与删除会话都以那串摘要为唯一标识。
 * 分不清哪一行属于谁的时候，这两个动作只能靠猜。
 *
 * 这组用例钉住行为：类型里有这一项、界面上渲染它、且 `null`（升级前写入的
 * 老会话）显示成一个明确的「未记录」而不是空白或 "null" 字样。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/agent.ts')
const viewSource = read('../src/views/llm/AgentView.vue')

/** 后端 `SessionStore._CHANNEL_IDENTITY_FIELDS` 的五个字段。 */
const IDENTITY_FIELDS = [
  'channel_type',
  'adapter_instance',
  'account_scope',
  'conversation_scope',
  'sender_scope'
] as const

describe('类型声明', () => {
  it('`SessionSummary` 带 channel_identity', () => {
    expect(apiSource).toMatch(/channel_identity/)
  })

  it('它可以为 null——升级前写入的会话没有这一项', () => {
    // 做成必填会让老会话在类型层就对不上，而后端明确返回 `null`。
    expect(apiSource).toMatch(/channel_identity[^\n]*\|\s*null|channel_identity\?:/)
  })

  it.each(IDENTITY_FIELDS)('声明了 %s', (field) => {
    // 五个字段是一组：缺一个就无法回答「同一个人在同一个群里的两个会话」
    // 该显示成什么。
    //
    // 断言落在**身份类型本身**上，不在 `channel_identity` 那一行附近取一段
    // 字符：字段声明在一个独立 interface 还是内联在 `SessionSummary` 里是写法
    // 选择，钉住其中一种会让一次正当的重构变成红灯。
    const start = apiSource.indexOf('interface SessionChannelIdentity')
    expect(start, '没有找到渠道身份类型').toBeGreaterThan(-1)
    const block = apiSource.slice(start, apiSource.indexOf('}', start))
    expect(block, `渠道身份类型里缺 ${field}`).toContain(field)
  })

  it('`channel_identity` 用的正是那个身份类型', () => {
    // 两处各写一份字段列表时，改一处漏一处不会报错——而漏的那处会在运行时
    // 读到 undefined，显示成空白。
    expect(apiSource).toMatch(/channel_identity:\s*SessionChannelIdentity\s*\|\s*null/)
  })
})

describe('会话表里的渠道身份', () => {
  it('表头有这一列', () => {
    expect(viewSource).toMatch(/columnheader[^>]*>\s*渠道|>渠道/)
  })

  it('渲染发送者标识，而不只是渠道类型', () => {
    // 只显示 `telegram` 回答不了「是谁」——同一个渠道上有几十个会话。
    expect(viewSource).toMatch(/sender_scope/)
  })

  it('渲染渠道类型', () => {
    expect(viewSource).toMatch(/channel_type/)
  })

  it('老会话显示明确的「未记录」，不是空白也不是 null 字样', () => {
    // 空白会被读成「渠道身份丢了」；`null` 字样是把内部表示漏给了用户。
    // 这两者都比一句「未记录」糟：后者说明的是「这个会话建于升级之前」。
    expect(viewSource).toMatch(/未记录/)
  })

  it('身份文本由一个函数产出，不在模板里拼五个字段', () => {
    // 模板里拼接会让「`null` 怎么显示」这个判断散落在多处，
    // 而那正是最容易漏的一处。
    expect(viewSource).toMatch(/function channelIdentityText|const channelIdentityText/)
  })
})
