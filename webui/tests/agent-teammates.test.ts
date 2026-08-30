// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * Teammates 的界面契约（需求 8）。
 *
 * 后端有字段、有执行链路，但配不出来就等于没有。这里钉住三条容易做错的地方：
 *
 * 1. **只能选已启用且不是自己的 Agent。** 选中停用的 Agent 会得到一个调用时
 *    必定失败的工具；选中自己是最短的无限委派。
 * 2. **前端先拦自委派。** 后端也会拒，但错误信息到界面上会变成一句泛泛的
 *    校验失败，用户不知道为什么。
 * 3. **文案要说清「队友看不到本次对话历史」。** 这是它与 MCP 工具最大的差别，
 *    不写清楚会让人以为队友能接着上文说。
 */

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(resolve(here, '../src/views/llm/AgentView.vue'), 'utf-8')

describe('teammates configuration UI', () => {
  it('renders a multi-select bound to teammate_agent_ids', () => {
    expect(source).toContain('data-test="teammate-agents"')
    const block = source.slice(source.indexOf('data-test="teammate-agents"') - 500)
    expect(block.slice(0, 900)).toContain('form.teammate_agent_ids')
    expect(block.slice(0, 900)).toContain('multiple')
  })

  it('offers only enabled agents other than the one being edited', () => {
    const options = source.match(/const teammateOptions[\s\S]*?\n\)/)
    expect(options, 'teammateOptions 未定义').not.toBeNull()
    const body = options![0]
    expect(body).toContain('agent.enabled')
    expect(body).toContain('agent.agent_id !== form.value.agent_id')
  })

  it('rejects self-delegation before sending the request', () => {
    const validate = source.match(/function validate\(\)[\s\S]*?\n}/)
    expect(validate, 'validate 未找到').not.toBeNull()
    expect(validate![0]).toContain('teammate_agent_ids')
    expect(validate![0]).toMatch(/自己|self/)
  })

  it('tolerates a backend response without the field', () => {
    // 旧后端不返回该键时表单不能变成 undefined，否则 multiple select 会报错。
    expect(source).toContain('agent.teammate_agent_ids ?? []')
  })

  it('states that a teammate cannot see the current conversation', () => {
    const section = source.slice(source.indexOf('teammates-heading'))
    expect(section.slice(0, 1200)).toMatch(/看不到本次对话历史/)
  })

  it('includes teammates in the empty form so creation posts a defined value', () => {
    const empty = source.match(/function emptyForm\(\)[\s\S]*?\n}/)
    expect(empty, 'emptyForm 未找到').not.toBeNull()
    expect(empty![0]).toContain('teammate_agent_ids: []')
  })
})
