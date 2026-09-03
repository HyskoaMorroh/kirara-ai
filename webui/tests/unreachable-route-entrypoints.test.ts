// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * Agent 必须能删掉，供应商配置必须能回滚。
 *
 * 两条都是「后端建好了、界面到不了」——`scripts/audit_unreachable_routes.py`
 * 普查 180 条路由后剩下的两处真实缺口（其余是被批量接口取代的细粒度路由、
 * 机器面向的端点、当链接打开而非 fetch 的下载地址）。
 *
 * **`DELETE /agents/<id>`**：`AgentRegistry.remove()` 写得很完整——默认 Agent
 * 不能删、还有渠道/账号/会话绑定的不能删，各自抛带原因的 `ValueError`。
 * 而界面上一个 Agent 只能新建和编辑：建错一个名字就永久留在列表里，
 * 而它仍然参与「渠道身份 → Agent」的解析。
 *
 * **`POST /llm/backends/restore`**：`save_config_with_backup` 每次写入前留一份
 * `config.yaml.bak`，这条接口把 `llms.api_backends` 单独取回来。
 * 定价目录早就有「恢复」按钮（`PricingView.vue`），供应商配置这条更敏感的路径
 * 反而只能登服务器手工编辑 YAML。
 *
 * 这份用例只锁**入口存在且带确认**：删除与回滚都是不可逆的，
 * 而它们的具体语义（谁不能删、回滚只动哪一段）由后端测试覆盖。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const agentApi = read('../src/api/agent.ts')
const agentView = read('../src/views/llm/AgentView.vue')
const llmApi = read('../src/api/llm.ts')
const llmView = read('../src/views/llm/LLMView.vue')

describe('删除 Agent', () => {
  it('API 客户端有这个函数，且走 DELETE', () => {
    expect(agentApi).toContain('deleteAgent')
    expect(agentApi).toMatch(/http\.delete[^\n]*\/agents\/\$\{encodeURIComponent\(agentId\)\}/)
  })

  it('界面有入口', () => {
    expect(agentView).toMatch(/data-test="delete-agent"/)
  })

  it('删除前要确认，且确认框里写出 Agent 的名字', () => {
    // 「确定删除吗」问不出用户到底选中了哪一个——Agent 列表里的行看起来很像。
    // 名字取自表单（显示名优先、回落到 ID），并插进标题与正文。
    expect(agentView).toMatch(/dialog\.warning\([\s\S]{0,600}deleteAgent/)
    expect(agentView).toMatch(/const label = form\.value\.display_name \|\| form\.value\.agent_id/)
    expect(agentView).toMatch(/title: `确认删除 Agent \$\{label\}`/)
  })

  it('新建中的 Agent 不显示删除按钮', () => {
    // 它还不存在，删除对它没有意义，而按钮会让人以为「取消新建」。
    expect(agentView).toMatch(/v-if="selectedAgent && !isCreating"[\s\S]{0,400}delete-agent/)
  })

  it('删除后刷新列表并清空编辑器', () => {
    // 留着一个已经不存在的 Agent 在编辑器里，下一次「保存」会把它重新建出来。
    expect(agentView).toMatch(/deleteAgent\([\s\S]{0,400}loadAgents\(\)/)
  })

  it('后端拒绝时把原因显示出来，而不是一句「删除失败」', () => {
    // 后端给的是「默认 Agent 不能删」「还有渠道绑定」这类可照做的原因，
    // 换成通用文案等于把用户能解决的问题变成一个死胡同。
    expect(agentView).toMatch(/deleteAgent[\s\S]{0,700}errorMessage\.value = /)
  })
})

describe('回滚供应商配置', () => {
  it('API 客户端有这个函数，且必须带 confirmed', () => {
    expect(llmApi).toContain('restoreBackends')
    // 后端要求 `confirmed: true`，写成可选会让第一次调用必然 400。
    expect(llmApi).toMatch(/restoreBackends\([^)]*confirmed: true/)
  })

  it('界面有入口', () => {
    expect(llmView).toMatch(/data-test="restore-backends"/)
  })

  it('确认框说清会丢掉什么', () => {
    // 「恢复」听起来是安全操作，实际会丢掉最后一次写入的供应商改动。
    expect(llmView).toMatch(/dialog\.warning\([\s\S]{0,800}restoreBackends/)
    expect(llmView).toMatch(/最后一次(?:保存|写入)/)
  })

  it('回滚后重新拉取供应商列表', () => {
    expect(llmView).toMatch(/restoreBackends\([\s\S]{0,500}fetchAdapters\(\)/)
  })

  it('没有备份时的 404 要说成「还没有备份」', () => {
    // 没有可恢复的东西是一种正常状态，不是错误。
    expect(llmView).toMatch(/还没有可恢复的备份/)
  })

  it('提示里报出恢复了几个、加载成功几个', () => {
    // 两个数字不同就意味着有后端恢复了但起不来（Key 失效、地址不通），
    // 只报一个数字会让那种情况看起来完全成功。
    expect(llmView).toMatch(/restored_count/)
    expect(llmView).toMatch(/loaded_count/)
  })
})
