// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 8 末句点名的整流器，界面这一跳。
 *
 * 后端有四个开关（`rectifier_enabled` + 三个子项），运行时也真的读它们
 * （`LLMBackendConfig.build_rectifier_config()` → `LLMChatRequest.rectifier` →
 * 适配器失败分支）。但类型与面板里没有这四个键时，用户只能改 config.yaml，
 * 而这正是本轮反复在修的那类缺陷：后端读得到、前端到不了。
 *
 * 一个必须能单独关掉的项：`rectify_media_fallback` **会改变模型看到的内容**
 * （图片被换成占位文本）。把它和另外两项绑在一个开关上，等于要求用户
 * 「要么接受改写图片，要么放弃修签名」。
 */

const here = dirname(fileURLToPath(import.meta.url))
const apiSource = readFileSync(resolve(here, '../src/api/llm.ts'), 'utf-8')
const configSource = readFileSync(
  resolve(here, '../src/components/llm/LLMAdapterConfig.vue'),
  'utf-8'
)

/** `resilienceDefaults()` 函数体，锚在 `export const` 上而不是第一次提及处。 */
const resilienceDefaultsBody = apiSource.match(
  /export const resilienceDefaults[\s\S]*?\n\}\)/
)

const SWITCHES = [
  'rectifier_enabled',
  'rectify_thinking_signature',
  'rectify_thinking_budget',
  'rectify_media_fallback'
]

describe('rectifier controls', () => {
  it('declares all four switches on the backend type', () => {
    // 类型里没有这个键时，编辑表单提交的 payload 里也不会有它。
    for (const key of SWITCHES) {
      expect(apiSource, `${key} 未声明`).toContain(key)
    }
  })

  it('keeps them out of the resilience defaults table', () => {
    // 进表就是「每次保存无条件补齐」。后端默认全开，而前端补一份 true
    // 会让用户在 YAML 里关掉的那次被一次无关的编辑重新打开——
    // 后端的 `exclude_unset=True` 只能救「没发这个键」。
    expect(resilienceDefaultsBody).not.toBeNull()
    for (const key of SWITCHES) {
      expect(resilienceDefaultsBody?.[0], `${key} 不该进默认值表`).not.toContain(key)
    }
  })

  it('exposes a control for each switch in the provider panel', () => {
    for (const key of SWITCHES) {
      expect(configSource, `${key} 没有控件`).toContain(
        `data-test="${key.replace(/_/g, '-')}"`
      )
    }
  })

  it('explains that the media fallback changes what the model sees', () => {
    // 不说清这一点，用户会以为它和另外两项一样只是「修参数」。
    const index = configSource.indexOf('data-test="rectify-media-fallback"')
    expect(index).toBeGreaterThan(-1)
    const block = configSource.slice(Math.max(0, index - 900), index + 900)
    expect(block).toMatch(/图片/)
    expect(block).toMatch(/占位|替换/)
  })

  it('says rectification only happens after a real upstream rejection', () => {
    // 「会自动改写我的请求」听起来比实际危险得多：只有上游真的拒绝、
    // 且错误命中白名单时才改，且每类只改一次。
    const index = configSource.indexOf('data-test="rectifier-enabled"')
    expect(index).toBeGreaterThan(-1)
    const block = configSource.slice(index, index + 1400)
    expect(block).toMatch(/拒绝|失败/)
    expect(block).toMatch(/重试/)
  })
})
