/**
 * `n-alert` 的重试 / 确认按钮必须真的渲染出来。
 *
 * 两处代码把按钮放在 `<template #action>` 里：
 *   - `LLMStatistics.vue` 统计加载失败时的「重试」
 *   - `LLMView.vue` 供应商导入冲突时的「确认覆盖 / 取消」
 *
 * 但 naive-ui 的 `AlertSlots` 只声明 `default | icon | header`（`Alert.d.ts:219`），
 * 运行时 `Alert.mjs` 也只消费这三个。实测确认：真实 NAlert 下 `#action` 的内容
 * **完全不渲染**——正文出现，按钮不出现。
 *
 * 这不是样式偏差，是功能缺失：
 *   - 统计加载失败后没有任何重试入口，用户只能刷新整页；
 *   - 导入冲突时看不到「确认覆盖」，整条导入流程在这一步断掉。
 *
 * 而它长期没被发现，是因为两处的测试都 mock 了 naive-ui，stub 模板里手写了
 * `<slot name="action" />`——mock 比真实组件更宽容，于是测试点得到按钮、
 * 断言通过，真实界面上什么都没有。
 *
 * 判据：**stub 不能比被替代的真实组件更宽容。** 多渲染一个 slot 就等于
 * 给一个不存在的能力打绿灯。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const alertDts = readFileSync(
  resolve(root, 'node_modules/naive-ui/es/alert/src/Alert.d.ts'),
  'utf-8'
)
const statistics = readFileSync(resolve(root, 'src/components/LLMStatistics.vue'), 'utf-8')
const llmView = readFileSync(resolve(root, 'src/views/llm/LLMView.vue'), 'utf-8')

/** 取出 `<n-alert ...>...</n-alert>` 的所有片段，并剥掉注释。 */
function alertBlocks(source: string): string[] {
  return [...source.matchAll(/<n-alert[\s\S]*?<\/n-alert>/g)].map((match) =>
    // 必须剥注释：修复时写的说明里就含「`#action`」这个词，
    // 不剥的话断言会把解释缺陷的注释当成缺陷本身（上一处 ConfigurationList 已踩过）。
    match[0].replace(/<!--[\s\S]*?-->/g, '')
  )
}

describe('n-alert 的 action slot', () => {
  it('自检：naive-ui 确实没有声明 action slot', () => {
    const at = alertDts.indexOf('interface AlertSlots')
    expect(at, '找不到 AlertSlots').toBeGreaterThan(-1)
    const body = alertDts.slice(at, alertDts.indexOf('}', at))
    expect(body).toMatch(/default\?/)
    expect(
      body,
      'naive-ui 已支持 action slot —— 请移除本测试并恢复 #action 写法'
    ).not.toMatch(/\baction\?/)
  })

  it('LLMStatistics 的重试按钮不放在不存在的 action slot 里', () => {
    for (const block of alertBlocks(statistics)) {
      expect(
        block,
        'n-alert 里用了 #action —— 该 slot 不被渲染，重试按钮不会出现在界面上'
      ).not.toMatch(/#action|v-slot:action|name="action"/)
    }
  })

  it('LLMView 的导入冲突按钮同样不用 action slot', () => {
    for (const block of alertBlocks(llmView)) {
      expect(
        block,
        'n-alert 里用了 #action —— 「确认覆盖」不会渲染，导入流程会在这一步断掉'
      ).not.toMatch(/#action|v-slot:action|name="action"/)
    }
  })

  it('两个按钮仍然存在，只是改到会渲染的位置', () => {
    // 修复不能是删掉按钮。
    expect(statistics).toMatch(/data-test="retry-statistics"/)
    expect(llmView).toMatch(/data-test="confirm-overwrite"/)
    expect(llmView).toMatch(/data-test="cancel-overwrite"/)
  })

  it('测试 stub 不再伪造 action slot', () => {
    // stub 里手写 `<slot name="action" />` 会让这类缺陷继续通过测试。
    const statisticsTest = readFileSync(resolve(root, 'tests/llm-statistics.test.ts'), 'utf-8')
    const at = statisticsTest.indexOf("vi.mock('naive-ui'")
    if (at === -1) return
    const mockBlock = statisticsTest.slice(at, at + 900)
    const alertStub = /NAlert\s*:\s*passthrough/.test(mockBlock)
    if (!alertStub) return
    expect(
      mockBlock,
      'naive-ui stub 渲染了 action slot，比真实 NAlert 宽容 —— 会给不存在的能力打绿灯'
    ).not.toMatch(/slot name="action"/)
  })
})
