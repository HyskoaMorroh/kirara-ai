// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 「重置熔断」必须在容错面板上真的存在。
 *
 * `POST /llm/backends/<name>/circuit/reset` 已经落地（创建者身份 + 显式确认 +
 * 同时撤销持久化隔离），`docs/PRACTICAL_PLAN_AND_TUTORIAL.md` 第 4.0 节也把
 * 「容错面板每一行都有『重置熔断』」写成了产品行为。但界面上没有任何代码
 * 调用它——面板显示 `已熔断`，而用户看得到状态却没有任何动作能改变它。
 *
 * 没有这个按钮时唯一的办法是等满恢复窗口，或者重启整个进程；而重启会一并
 * 中断所有正在进行的对话。文档承诺了一个不存在的按钮，比没有文档更糟：
 * 用户会去面板上找它，找不到之后怀疑自己看错了版本。
 *
 * 四条边界必须成立：
 * - 只在真的熔断（`open` / `half-open`）时可点：`closed` 上重置无事可做；
 * - 必须显式确认——它把一个刚被判定不健康的上游放回真实流量；
 * - 只重置那一家，不提供「全部重置」；
 * - 成功后立刻刷新状态，而不是等下一次轮询。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/ResilienceView.vue')

describe('API 客户端', () => {
  it('声明了 resetProviderCircuit', () => {
    expect(apiSource).toContain('resetProviderCircuit')
  })

  it('打到后端的专用路由', () => {
    expect(apiSource).toContain('/circuit/reset')
  })

  it('提交显式确认标记', () => {
    // 后端在 `confirmed !== true` 时返回 400。前端不传就等于这个按钮永远失败，
    // 而错误信息（「需要确认」）对用户毫无意义——他明明点了确认框。
    expect(apiSource).toMatch(/resetProviderCircuit[\s\S]{0,300}confirmed:\s*true/)
  })
})

describe('面板上的入口', () => {
  it('提供重置按钮', () => {
    expect(viewSource).toContain('data-test="reset-circuit"')
    expect(viewSource).toContain('重置熔断')
  })

  it('只在熔断或半开时可点', () => {
    // `closed` 上重置无事可做；给一个永远无效的按钮会让人怀疑它是不是坏了。
    expect(viewSource).toMatch(/row\.state !== 'closed'/)
  })

  it('要求显式确认后才发出请求', () => {
    expect(viewSource).toMatch(/dialog|popconfirm|n-popconfirm/i)
  })

  it('确认文案说明它会把上游放回真实流量', () => {
    expect(viewSource).toContain('真实流量')
  })

  it('按名字跟踪忙态，多行不会一起转圈', () => {
    expect(viewSource).toContain('resettingProvider')
  })

  it('成功后立刻刷新状态', () => {
    // 等下一次轮询（10 秒）会让用户以为按钮没生效——那正是这个按钮要解决的
    // 问题。刷新走面板既有的 `load()`，不新开一条取数路径。
    expect(viewSource).toMatch(/resetCircuit[\s\S]{0,600}await load\(/)
  })

  it('不提供「全部重置」', () => {
    // 「取消所有隔离」会把其余正因真实故障被隔离的上游一起放行。
    expect(viewSource).not.toContain('全部重置')
    expect(viewSource).not.toContain('取消所有隔离')
  })
})
