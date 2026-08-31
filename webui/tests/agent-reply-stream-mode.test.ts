import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * Agent 级 `reply_stream_mode` 必须能在界面上改（需求 4）。
 *
 * 三层优先级是 **Agent 声明 > 渠道默认 > 进程默认**，而后两层已经有
 * 「系统设置 → Agent 运行时」卡片可以配。最上面这一层此前完全没有入口：
 * 界面上没有、REST 里没有、落盘也丢——只能靠在进程内手工构造 `AgentDefinition`。
 *
 * 后端那一半已经补齐（见 `tests/agent_runtime/test_reply_stream_mode_persistence.py`
 * 与 `tests/web/api/agent/test_reply_stream_mode_api.py`）。这里钉前端：
 * 类型里要有这个字段，Agent 编辑表单里要有可选的档位，且必须包含 `inherit`——
 * 少了它，一个设过 `off` 的 Agent 就再也改不回「跟随上层」。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/agent.ts')
const viewSource = read('../src/views/llm/AgentView.vue')

describe('API 类型', () => {
  it('AgentSummary 带 reply_stream_mode', () => {
    expect(apiSource).toMatch(/AgentSummary[\s\S]{0,1200}reply_stream_mode/)
  })

  it('AgentConfigurationRequest 也带它', () => {
    // 只读不写等于「界面能看见但改不了」。
    expect(apiSource).toMatch(/AgentConfigurationRequest[\s\S]{0,1200}reply_stream_mode/)
  })

  it('四个档位都在类型里', () => {
    for (const mode of ['inherit', 'off', 'aggregate', 'incremental']) {
      expect(apiSource).toContain(`'${mode}'`)
    }
  })
})

describe('Agent 编辑表单', () => {
  it('有这一项的输入控件', () => {
    expect(viewSource).toContain('reply_stream_mode')
  })

  it('可以选回 inherit', () => {
    // 少了它，一个设过 off 的 Agent 再也改不回「跟随上层」。
    expect(viewSource).toMatch(/inherit/)
  })

  it('四个档位都能选', () => {
    for (const mode of ['inherit', 'off', 'aggregate', 'incremental']) {
      expect(viewSource).toContain(mode)
    }
  })

  it('说明 incremental 只在支持编辑消息的渠道上生效', () => {
    // 不说这一句，运维会给 QQ 配 incremental 然后以为坏了——那里它静默退化。
    expect(viewSource).toMatch(/Telegram|退化|退回/)
  })

  it('提交时带上这个字段', () => {
    expect(viewSource).toMatch(/reply_stream_mode:\s*\w/)
  })
})
