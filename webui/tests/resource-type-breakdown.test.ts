/**
 * 摘要条要能看出各类型各装了几个（需求 10 的「已安装数量按类型统计」）。
 *
 * 现状：摘要条只有总数 / 已启用 / 需要确认三个数字。类型下拉停在「全部资源」时，
 * 用户看到的是「装了 37 个」——但 37 里有几个 Skill、几个 MCP、Hook 是不是一个都没装，
 * 只能一个个改下拉去数。而「某类型为 0」恰恰是最需要看见的信号：
 * 绑定 Agent 时找不到可选 Hook，原因往往就是压根没装，不是绑定坏了。
 *
 * 锁两件事：
 * 1. 计数覆盖**全部**已知类型，装了 0 个的类型也要出现在结果里并且值为 0——
 *    把 0 项静默省掉，等于把「没装」和「装了但没显示」混成同一种画面；
 * 2. 未知类型不静默丢弃。后端将来加一种资源类型时，前端不该假装它不存在。
 *
 * 用纯函数而不是挂载 2000 行组件：这条规则的实质是一次分组统计，
 * 挂载只会把 naive-ui 的渲染细节混进断言。
 */

import { describe, expect, it } from 'vitest'
import { RESOURCE_TYPE_ORDER, countResourcesByType } from '../src/views/resources/resourceFilter'

const item = (type: string, over: Record<string, unknown> = {}) => ({
  resource_id: `${type}-demo`,
  type,
  enabled: true,
  ...over
})

describe('已安装资源按类型计数', () => {
  it('装了 0 个的类型也要出现，值为 0', () => {
    // 只装了 skill。其余类型必须显式为 0，而不是从结果里消失。
    const counts = countResourcesByType([item('skill'), item('skill')])

    expect(counts.skill).toBe(2)
    expect(counts.mcp).toBe(0)
    expect(counts.hook).toBe(0)
    expect(counts.prompt).toBe(0)
  })

  it('每种已知类型都有一个计数位，顺序与类型下拉一致', () => {
    const counts = countResourcesByType([])

    // 顺序要跟下拉一致，否则摘要条和筛选器读起来是两套心智模型。
    expect(Object.keys(counts)).toEqual([...RESOURCE_TYPE_ORDER])
  })

  it('混合列表按类型分别累加', () => {
    const counts = countResourcesByType([
      item('skill'),
      item('mcp'),
      item('mcp'),
      item('hook'),
      item('prompt'),
      item('memory'),
      item('session')
    ])

    expect(counts.skill).toBe(1)
    expect(counts.mcp).toBe(2)
    expect(counts.hook).toBe(1)
    expect(counts.prompt).toBe(1)
    expect(counts.memory).toBe(1)
    expect(counts.session).toBe(1)
  })

  it('后端新增的未知类型不被静默丢弃', () => {
    // 前端不该假装后端没有这种类型；数字对不上总数比少一行更难查。
    const counts = countResourcesByType([item('skill'), item('agent')])

    expect(counts.agent).toBe(1)
    expect(counts.skill).toBe(1)
  })

  it('缺少 type 字段的条目归到 unknown，不计入任何已知类型', () => {
    const counts = countResourcesByType([{ resource_id: 'broken' }, item('skill')])

    expect(counts.skill).toBe(1)
    expect(counts.unknown).toBe(1)
  })

  it('计数之和等于列表长度，摘要条的总数不会和分项打架', () => {
    const list = [item('skill'), item('mcp'), { resource_id: 'broken' }, item('agent')]
    const counts = countResourcesByType(list)

    const sum = Object.values(counts).reduce((acc, n) => acc + n, 0)
    expect(sum).toBe(list.length)
  })
})
