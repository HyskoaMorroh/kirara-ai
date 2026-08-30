// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 8：「隐藏 AI 署名」必须能在模型管理里填。
 *
 * 后端一直有 `hide_ai_attribution`，也一直有真实消费点（`llm_manager` 在非流式与
 * 流式聚合后各执行一次署名清理）。但 `webui/src` 下 grep 这个字段**零命中**：
 * 类型里没有、面板里没有控件。于是这个开关只能改 config.yaml，
 * 而需求原文要求的是「在模型管理添加多个上游的同时完全实现」。
 *
 * 比「填不了」更糟的一层：`LLMBackendUpdateRequest` 继承 `LLMBackendConfig`，
 * 前端不提交该字段时 pydantic 会补默认值 `False`——用户在 YAML 里开了它，
 * 之后在 WebUI 改任何一项都会把它静默关掉。后端已按「未提交的字段保留原值」
 * 修正（见 `test_update_backend_keeps_fields_the_client_never_sent`），
 * 这里补上界面这一跳。
 *
 * 这是一个**会改写模型输出**的开关，因此：
 * - 默认必须关闭；
 * - 说明文字要讲清「只删署名句、不动答案」，否则没人敢开它。
 */

const here = dirname(fileURLToPath(import.meta.url))
const configSource = readFileSync(
  resolve(here, '../src/components/llm/LLMAdapterConfig.vue'),
  'utf-8'
)
const apiSource = readFileSync(resolve(here, '../src/api/llm.ts'), 'utf-8')

/**
 * `resilienceDefaults()` 函数体。
 *
 * 必须锚在 `export const` 上：文件里第一次出现 `resilienceDefaults` 是
 * 类型注释里的 `{@link resilienceDefaults}`，从那里开始的懒惰匹配会把整段
 * JSDoc 当成默认值表——而那段注释里恰好提到 `hide_ai_attribution`，
 * 于是「这个键在不在默认值表里」的断言会永远命中注释、永远给出错答案。
 */
const resilienceDefaultsBody = apiSource.match(
  /export const resilienceDefaults[\s\S]*?\n\}\)/
)

describe('provider hide-AI-attribution control', () => {
  it('declares the field on the backend type so edits do not drop it', () => {
    // 类型里没有这个键时，表单提交的 payload 里也不会有它。
    expect(apiSource).toContain('hide_ai_attribution')
  })

  it('exposes a switch in the provider panel', () => {
    expect(configSource).toContain('data-test="hide-ai-attribution"')
  })

  it('describes what it removes and what it leaves alone', () => {
    // 一个会改写模型输出的开关，不说清边界就没人敢开。
    const index = configSource.indexOf('data-test="hide-ai-attribution"')
    expect(index).toBeGreaterThan(-1)
    const block = configSource.slice(index, index + 1200)
    expect(block).toContain('署名')
    expect(block).toContain('答案')
  })

  it('defaults to off rather than silently rewriting replies', () => {
    // 默认改写输出是最坏的默认值：用户没要求过，回复却变了。
    //
    // `\??` 是必需的：声明写作 `hide_ai_attribution?: boolean`，
    // 可选标记就在冒号前面，不放过它的正则会漏掉真实声明并误判为「没声明」。
    const match = apiSource.match(/hide_ai_attribution\??\s*:\s*([^\n,]+)/)
    expect(match).not.toBeNull()
    // 类型声明为可选布尔即可；关键是默认值表里若出现它，必须是 false。
    if (resilienceDefaultsBody?.[0].includes('hide_ai_attribution')) {
      expect(resilienceDefaultsBody[0]).toMatch(/hide_ai_attribution\??\s*:\s*false/)
    }
  })

  it('keeps the switch out of the resilience defaults table', () => {
    // `resilienceDefaults()` 的语义是「保存时无条件补齐」，任何进表的键
    // 都会被写进每次 payload。改写输出的开关一旦进表，就等于宣布
    // 「这个值我们替你决定」——即便补的是 false，也会覆盖用户在 YAML 里开的那次。
    // 后端的 `exclude_unset=True` 只能救「前端没发这个键」，救不了「前端补发了 false」。
    expect(resilienceDefaultsBody).not.toBeNull()
    expect(resilienceDefaultsBody?.[0]).not.toContain('hide_ai_attribution')
  })
})
