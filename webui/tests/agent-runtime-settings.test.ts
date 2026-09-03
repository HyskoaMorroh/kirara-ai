import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * Agent 运行时的四个参数必须能从界面配，而不只能改 config.yaml。
 *
 * 需求 21.3 点名「请求总截止时间」必须集中配置并校验边界。
 * `agent_runtime.turn_deadline_seconds` 早就有真实消费点（下传 deadline
 * 与取消信号），但通往它的路只有一条：登服务器改 YAML。
 * 同一段配置里另外三项处境相同：`reply_stream_mode`、
 * `channel_reply_stream_modes`、`tool_search_threshold`。
 *
 * 这里以源码为断言对象（组件依赖 naive-ui，单测里挂载成本远高于收益），
 * 覆盖的是「契约是否成立」：字段是否读回、是否提交、边界是否在控件上表达、
 * 以及重启提示是否给出。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const viewModelSource = read('../src/views/settings/viewmodels/agent-runtime.vm.ts')
const cardSource = read('../src/views/settings/components/AgentRuntimeCard.vue')
const settingsSource = read('../src/views/settings/BasicSettings.vue')

/** 后端 `_AGENT_RUNTIME_WRITABLE_KEYS` 的全部取值。 */
const WRITABLE_KEYS = [
  'turn_deadline_seconds',
  'reply_stream_mode',
  'channel_reply_stream_modes',
  'tool_search_threshold'
]

/** 后端 `_PROCESS_REPLY_STREAM_MODES`；`inherit` 刻意不在其中。 */
const PROCESS_MODES = ['off', 'aggregate', 'incremental']

describe('Agent 运行时配置的读写契约', () => {
  it('读取走 GET /system/config 的 agent_runtime 段', () => {
    expect(viewModelSource).toContain("http.get")
    expect(viewModelSource).toContain('/system/config')
    expect(viewModelSource).toContain('agent_runtime')
  })

  it('保存打到专用路由，而不是复用其他配置段', () => {
    expect(viewModelSource).toContain('/system/config/agent-runtime')
  })

  it.each(WRITABLE_KEYS)('提交 payload 里带上 %s', (key) => {
    // 类型里没有这个键时 payload 里也不会有它——那会让界面显示「保存成功」
    // 而那一项从未被写入。
    expect(viewModelSource).toContain(key)
  })

  it.each(['turn_deadline_seconds', 'reply_stream_mode', 'tool_search_threshold'])(
    '表单能回显 %s 的当前值',
    (key) => {
      expect(cardSource).toContain(key)
    }
  )

  it('渠道覆盖以可增删的成对列表编辑', () => {
    // `channel_reply_stream_modes` 在卡片里不是一个绑定字段而是一组行：
    // 一个 Record 直接绑在表单上无法表达「正在输入、渠道名还没填完」这个中间态。
    expect(cardSource).toContain('channelRows')
    expect(cardSource).toContain('addChannelRow')
    expect(cardSource).toContain('removeChannelRow')
    // 折叠回 Record 的责任落在 viewmodel 上。
    // 折叠规则本身（丢空行、去空白、空串归一成 null）由
    // `agent-runtime-form.test.ts` 调用函数验证。这里只确认接线——
    // 只测规则不测接线，等于验证了一个没人调用的函数。
    expect(viewModelSource).toMatch(/collectChannelModes\(channelRows\.value\)/)
    expect(viewModelSource).toMatch(/collectCreatorIdentities\(/)
    expect(viewModelSource).toMatch(/from '\.\/agentRuntimeForm'/)
  })
})

describe('取回档位的取值范围', () => {
  it.each(PROCESS_MODES)('界面提供 %s 档', (mode) => {
    expect(cardSource).toContain(`'${mode}'`)
  })

  it('进程默认不提供 inherit', () => {
    // `inherit` 只在 Agent 层有意义（跟随上层）；进程默认没有上层可继承，
    // 在这里给出它会让整条解析链没有终点。
    expect(cardSource).not.toContain("value: 'inherit'")
  })
})

describe('边界在控件上表达，而不是只等后端报错', () => {
  it('总截止时间的输入框限定 0 到 3600', () => {
    expect(cardSource).toMatch(/:min="0"/)
    expect(cardSource).toContain('3600')
  })

  it('工具搜索阈值的输入框限定 0 到 500', () => {
    expect(cardSource).toContain('500')
  })

  it('说明文字讲清 0 的含义，两处都讲', () => {
    // `0` 在这两项上都是有意义的值而不是「没填」：一个是「不设总预算」，
    // 一个是「关闭渐进披露」。不写出来，用户会以为 0 等于未配置。
    expect(cardSource).toContain('不设总预算')
    expect(cardSource).toContain('关闭')
  })
})

describe('改动生效条件如实告知', () => {
  it('保存后提示需要重启', () => {
    // 这批参数在启动时被读进 executor。不说这句，用户会以为下一条消息
    // 就按新档位取回，然后去排查一个并不存在的问题。
    expect(viewModelSource).toContain('重启')
  })
})

describe('入口可达', () => {
  it('卡片挂进系统设置页', () => {
    expect(settingsSource).toContain('AgentRuntimeCard')
  })
})
