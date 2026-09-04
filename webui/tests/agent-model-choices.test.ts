// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  collectModelChoices,
  collectProviderChoices,
  unknownModels
} from '../src/views/llm/agentModelChoices'

/**
 * Agent 的模型优先链必须能从已配供应商里选，而不是手打模型 ID。
 *
 * 发现过程：用户指着「新建 Agent」页的「主模型 ID」问「这个需要自己手动输入？
 * 不是从模型配置中自动拉取？」——那两格确实是纯文本框，而模型名就在
 * `GET /llm/backends` 的返回里（也就是「模型配置」页显示的那些）。
 *
 * 手打的后果不是「多打几个字」：模型 ID 拼错不会当场报错，Agent 保存成功，
 * 直到某次真实对话解析不到那个模型才失败——而那时看到的是运行时错误，
 * 与「我三天前拼错了一个字母」看不出关系。
 */

const backend = (over: Record<string, unknown> = {}) =>
  ({
    name: 'openai-main',
    adapter: 'openai',
    config: {},
    enable: true,
    models: [{ id: 'gpt-5.6', type: 'llm', ability: 14 }],
    ...over
  }) as never

/** `LLMAbility.TextChat` = Chat|TextInput|TextOutput = 2|4|8 = 14。 */
const CHAT = 14
/** `LLMAbility.Unknown` = 0，`ImageGeneration` = 16|32 = 48。 */
const NOT_CHAT = 48

describe('只列能对话的模型', () => {
  it('取出 llm 且带 Chat 能力位的模型', () => {
    const options = collectModelChoices([backend()])

    expect(options.map((item) => item.value)).toEqual(['gpt-5.6'])
  })

  it('跳过 image_generation 与 embedding', () => {
    // 把它们放进模型优先链，第一次对话必然失败，而报错指向
    // 「模型不支持对话」——与用户做过的事无关。
    const options = collectModelChoices([
      backend({
        models: [
          { id: 'gpt-image-1', type: 'image_generation', ability: NOT_CHAT },
          { id: 'text-embed', type: 'embedding', ability: 4 },
          { id: 'gpt-5.6', type: 'llm', ability: CHAT }
        ]
      })
    ])

    expect(options.map((item) => item.value)).toEqual(['gpt-5.6'])
  })

  it('type 是 llm 但没有 Chat 能力位的也跳过', () => {
    // `type` 是声明的类别，`ability` 是适配器真正宣称能做的事。
    // 只看前者会放进一个不能聊天的 llm——那与后端 `_chat_capable_models`
    // 的判据必须一致，否则界面列得出、启动兜底选不中。
    const options = collectModelChoices([
      backend({ models: [{ id: 'weird', type: 'llm', ability: 0 }] })
    ])

    expect(options).toEqual([])
  })

  it('模型列表缺失或为空时不抛错', () => {
    expect(collectModelChoices([backend({ models: undefined })])).toEqual([])
    expect(collectModelChoices([backend({ models: [] })])).toEqual([])
    expect(collectModelChoices([])).toEqual([])
  })

  it('空 ID 与空白 ID 跳过', () => {
    const options = collectModelChoices([
      backend({
        models: [
          { id: '', type: 'llm', ability: CHAT },
          { id: '   ', type: 'llm', ability: CHAT },
          { id: ' gpt-5.6 ', type: 'llm', ability: CHAT }
        ]
      })
    ])

    // 首尾空白要去掉：`' gpt-5.6 '` 与 `'gpt-5.6'` 是同一个模型，
    // 不 trim 会让下拉里出现两条看起来一样的选项。
    expect(options.map((item) => item.value)).toEqual(['gpt-5.6'])
  })
})

describe('停用的后端仍然列出但标注', () => {
  it('未启用后端的模型带「（未启用）」后缀', () => {
    // 直接过滤掉会让「我明明配过这个模型」变成找不到的东西；
    // 不标注则会让用户选一个当前拿不到的模型。
    const options = collectModelChoices([backend({ enable: false })])

    expect(options).toHaveLength(1)
    expect(String(options[0].label)).toContain('未启用')
  })

  it('可用的排在未启用的前面', () => {
    const options = collectModelChoices([
      backend({ name: 'off', enable: false, models: [{ id: 'aaa', type: 'llm', ability: CHAT }] }),
      backend({ name: 'on', enable: true, models: [{ id: 'zzz', type: 'llm', ability: CHAT }] })
    ])

    // 按字母序 aaa 在前，但它来自停用后端——用户要先看到「现在就能跑」的。
    expect(options.map((item) => item.value)).toEqual(['zzz', 'aaa'])
  })

  it('同一模型来自一启用一停用的后端时算可用', () => {
    const options = collectModelChoices([
      backend({ name: 'off', enable: false }),
      backend({ name: 'on', enable: true })
    ])

    expect(options).toHaveLength(1)
    expect(String(options[0].label)).not.toContain('未启用')
  })
})

describe('同一个模型 ID 只出现一次', () => {
  it('合并多个后端提供的同名模型，并列出全部来源', () => {
    // 多个后端提供同一个模型是故障转移的正常形态，
    // 列成多条会让下拉里出现一串看起来重复的选项。
    const options = collectModelChoices([
      backend({ name: 'primary' }),
      backend({ name: 'fallback' })
    ])

    expect(options).toHaveLength(1)
    expect(String(options[0].label)).toContain('primary')
    expect(String(options[0].label)).toContain('fallback')
  })

  it('同一后端内重复声明也只出现一次', () => {
    const options = collectModelChoices([
      backend({
        models: [
          { id: 'gpt-5.6', type: 'llm', ability: CHAT },
          { id: 'gpt-5.6', type: 'llm', ability: CHAT }
        ]
      })
    ])

    expect(options).toHaveLength(1)
    expect(String(options[0].label)).toBe('gpt-5.6 · openai-main')
  })
})

describe('Provider 白名单候选', () => {
  it('列出所有后端名', () => {
    const options = collectProviderChoices([
      backend({ name: 'b-second' }),
      backend({ name: 'a-first' })
    ])

    expect(options.map((item) => item.value)).toEqual(['a-first', 'b-second'])
  })

  it('停用的后端也列出，且不排到后面', () => {
    // 白名单是「允许用哪几家」这个策略声明，而一家后端今天停用明天启用是常事。
    // 把它从候选里拿掉，会让用户重新启用后忘记它还没进白名单。
    const options = collectProviderChoices([backend({ name: 'off', enable: false })])

    expect(options.map((item) => item.value)).toEqual(['off'])
    expect(String(options[0].label)).toBe('off')
  })

  it('重名只出现一次，空名跳过', () => {
    const options = collectProviderChoices([
      backend({ name: 'same' }),
      backend({ name: 'same' }),
      backend({ name: '   ' })
    ])

    expect(options.map((item) => item.value)).toEqual(['same'])
  })
})

describe('未知模型提示', () => {
  const options = collectModelChoices([backend()])

  it('填了候选里没有的名字时报出来', () => {
    expect(unknownModels(['gpt-5.6', 'typo-model'], options)).toEqual(['typo-model'])
  })

  it('全部命中时为空', () => {
    expect(unknownModels(['gpt-5.6'], options)).toEqual([])
  })

  it('空串与空白不算未知', () => {
    // 新增一行还没填时是空串，那不是「拼错了」。
    expect(unknownModels(['', '   '], options)).toEqual([])
  })

  it('同一个错名只报一次', () => {
    expect(unknownModels(['typo', 'typo'], options)).toEqual(['typo'])
  })

  it('候选为空时全部算未知——但那只是提示', () => {
    // 拿不到后端列表（接口失败）时不该把用户已填的模型说成错的，
    // 因此界面上这条提示与 `backendLoadError` 分开显示。
    expect(unknownModels(['gpt-5.6'], [])).toEqual(['gpt-5.6'])
  })
})

describe('组件接线', () => {
  const readView = async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, resolve } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))
    return readFileSync(resolve(here, '../src/views/llm/AgentView.vue'), 'utf-8')
  }

  it('模型链用可筛选可创建的选择器', async () => {
    const source = await readView()

    expect(source).toMatch(/data-test="model-priority-select"/)
    // `tag` 不能去掉：模型可能来自尚未登记的后端，
    // 或者用户想先配 Agent 再配供应商。
    expect(source).toMatch(/model-priority-select[\s\S]{0,400}/)
    expect(source).toMatch(/<n-select[\s\S]{0,300}tag[\s\S]{0,300}model-priority-select/)
  })

  it('Provider 白名单是多选，不再经过逗号分隔的文本', async () => {
    const source = await readView()

    expect(source).toMatch(/data-test="provider-allowlist-select"/)
    expect(source).not.toMatch(/providerText/)
  })

  it('候选与 Agent 列表并行加载', async () => {
    const source = await readView()

    expect(source).toMatch(/Promise\.all\(\[loadAgents\(\), loadResourceCatalog\(\), loadBackends\(\)\]\)/)
  })

  it('拉取失败只提示，不阻断编辑', async () => {
    const source = await readView()

    expect(source).toMatch(/backendLoadError/)
    expect(source).toMatch(/仍可手动输入模型 ID/)
  })
})
