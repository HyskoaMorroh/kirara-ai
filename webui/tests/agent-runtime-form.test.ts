// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  collectChannelModes,
  collectCreatorIdentities
} from '../src/views/settings/viewmodels/agentRuntimeForm'

/**
 * Agent 运行时表单的两条折叠规则，按**行为**验证。
 *
 * 替换的是 `agent-runtime-settings.test.ts` 里的
 * `expect(viewModelSource).toContain('collectChannelModes')`——
 * 那只证明这个名字在文件里出现过。
 *
 * 两条规则错了都是静默的：
 *
 * - 空渠道名没丢掉 → 提交空键 → 后端 400，而用户只是还没填完那一行；
 * - 可选字段空串没归一成 null → 后端把空串当无效 → 那条创建者身份匹配不上
 *   任何消息，而界面显示「保存成功」。后果是 MCP 工具与 command Hook
 *   在 IM 渠道上静默不可用，且没有任何提示指向真实原因。
 */

describe('渠道档位折叠', () => {
  it('折叠成 {渠道: 档位}', () => {
    expect(
      collectChannelModes([
        { channel: 'onebot', mode: 'aggregate' },
        { channel: 'telegram', mode: 'incremental' }
      ])
    ).toEqual({ onebot: 'aggregate', telegram: 'incremental' })
  })

  it('空渠道名整行丢掉，而不是提交一个空键', () => {
    // 提交空键会让后端返回 400，而用户此刻只是还没填完那一行。
    expect(
      collectChannelModes([
        { channel: '', mode: 'off' },
        { channel: 'onebot', mode: 'off' }
      ])
    ).toEqual({ onebot: 'off' })
  })

  it('纯空白的渠道名也算没填', () => {
    expect(collectChannelModes([{ channel: '   ', mode: 'off' }])).toEqual({})
  })

  it('渠道名首尾空白被去掉', () => {
    // 不去掉的话 `onebot ` 与 `onebot` 在后端是两个不同的键，
    // 而用户看不出自己多打了一个空格。
    expect(collectChannelModes([{ channel: '  onebot  ', mode: 'off' }])).toEqual({
      onebot: 'off'
    })
  })

  it('同名行后写覆盖先写', () => {
    // 一个渠道只能有一个档位，而两行同名时用户看到的是最后编辑的那个。
    expect(
      collectChannelModes([
        { channel: 'onebot', mode: 'off' },
        { channel: 'onebot', mode: 'incremental' }
      ])
    ).toEqual({ onebot: 'incremental' })
  })

  it('空列表折叠成空对象，而不是 undefined', () => {
    // 后端要的是一个对象；给 undefined 会让那个键从请求体里消失，
    // 于是「删掉所有渠道覆盖」这个操作静默无效。
    expect(collectChannelModes([])).toEqual({})
  })
})

describe('创建者身份折叠', () => {
  const draft = (over: Record<string, unknown> = {}) => ({
    channel_type: 'onebot',
    sender_scope: '12345',
    account_scope: null,
    adapter_instance: null,
    allow_group_chat: false,
    ...over
  })

  it('保留填齐的身份', () => {
    expect(collectCreatorIdentities([draft()])).toEqual([
      {
        channel_type: 'onebot',
        sender_scope: '12345',
        account_scope: null,
        adapter_instance: null,
        allow_group_chat: false
      }
    ])
  })

  it('没填发送者标识的整条丢掉', () => {
    expect(collectCreatorIdentities([draft({ sender_scope: '' })])).toEqual([])
    expect(collectCreatorIdentities([draft({ sender_scope: '   ' })])).toEqual([])
  })

  it('可选字段的空串归一成 null', () => {
    // 这一条是最要紧的：后端把空串视为无效（要么省略要么非空）。
    // 不归一时那条身份匹配不上任何消息，而界面显示保存成功——
    // MCP 工具与 command Hook 于是在 IM 渠道上静默不可用。
    const [identity] = collectCreatorIdentities([
      draft({ account_scope: '', adapter_instance: '   ' })
    ])
    expect(identity.account_scope).toBeNull()
    expect(identity.adapter_instance).toBeNull()
  })

  it('可选字段有值时保留并去掉首尾空白', () => {
    const [identity] = collectCreatorIdentities([
      draft({ account_scope: '  bot-1  ', adapter_instance: ' inst ' })
    ])
    expect(identity.account_scope).toBe('bot-1')
    expect(identity.adapter_instance).toBe('inst')
  })

  it('发送者标识去掉首尾空白', () => {
    const [identity] = collectCreatorIdentities([draft({ sender_scope: ' 12345 ' })])
    expect(identity.sender_scope).toBe('12345')
  })

  it('allow_group_chat 缺失时是 false，不是 undefined', () => {
    // 群里所有人都看得到创建者发的指令。默认必须是关，
    // 而 undefined 在后端可能被当作「未提供」从而落到别的默认值上。
    const [identity] = collectCreatorIdentities([draft({ allow_group_chat: undefined })])
    expect(identity.allow_group_chat).toBe(false)
  })

  it('只有布尔真才算打开', () => {
    // 表单控件在某些路径上可能给出字符串。`'false'` 是真值，
    // 按真处理会把群聊授权静默打开。
    for (const value of ['false', 'true', 1, 0, null, '']) {
      const [identity] = collectCreatorIdentities([draft({ allow_group_chat: value })])
      expect(identity.allow_group_chat, `${String(value)} 不该被当作真`).toBe(false)
    }
    const [enabled] = collectCreatorIdentities([draft({ allow_group_chat: true })])
    expect(enabled.allow_group_chat).toBe(true)
  })

  it('多条身份各自独立处理', () => {
    const result = collectCreatorIdentities([
      draft({ sender_scope: 'a' }),
      draft({ sender_scope: '' }),
      draft({ sender_scope: 'b', channel_type: 'telegram' })
    ])
    expect(result.map((item) => item.sender_scope)).toEqual(['a', 'b'])
    expect(result[1].channel_type).toBe('telegram')
  })

  it('空列表折叠成空数组', () => {
    // 空数组的含义是「聊天侧谁都拿不到创建者身份」，是一个有效状态。
    expect(collectCreatorIdentities([])).toEqual([])
  })
})
