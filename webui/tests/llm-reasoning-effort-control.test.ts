// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { resilienceDefaults } from '../src/api/llm'

/**
 * 供应商级「推理强度」的 UI 契约（需求 8「Tool Search 最大强度思考」的落地形式）。
 *
 * 一条容易被做错的边界：**「不指定」必须是一个可表达的状态**。
 * 后端默认值是 `None`，不支持扩展思考的模型收到 `thinking` / `thinkingConfig`
 * 会直接报错。因此：
 *
 * - 该字段**不能**进 `resilienceDefaults()`：那个表的作用是「新建时带齐字段」，
 *   替用户填一个具体档位就把「不指定」这个状态抹掉了；
 * - 下拉必须 clearable，且选项里不能出现「默认」——把默认做成一个选项会让人
 *   以为默认也是一档具体强度；
 * - 「恢复默认」要把它清掉，否则按钮的字面意思与实际行为不符。
 */

const here = dirname(fileURLToPath(import.meta.url))
const configSource = readFileSync(
  resolve(here, '../src/components/llm/LLMAdapterConfig.vue'),
  'utf-8'
)

describe('provider reasoning effort control', () => {
  it('is not part of the resilience defaults table', () => {
    expect(Object.keys(resilienceDefaults())).not.toContain('reasoning_effort')
  })

  it('exposes a clearable selector so "unset" stays expressible', () => {
    expect(configSource).toContain('data-test="reasoning-effort"')
    const block = configSource.slice(configSource.indexOf('data-test="reasoning-effort"') - 400)
    expect(block.slice(0, 800)).toContain('clearable')
  })

  it('offers exactly the four supported tiers and no synthetic default option', () => {
    const options = configSource.match(/reasoningEffortOptions\s*=\s*\[([\s\S]*?)\]/)
    expect(options, 'reasoningEffortOptions 未定义').not.toBeNull()
    const body = options![1]
    for (const tier of ['low', 'medium', 'high', 'max']) {
      expect(body).toContain(`'${tier}'`)
    }
    // 「默认」不能是一个选项：清空才是默认。
    expect(body).not.toContain('默认')
  })

  it('deletes the key when cleared instead of sending an empty value', () => {
    // 发空串会被后端当成一个真实档位并触发校验失败。
    expect(configSource).toMatch(/delete nextAdapter\.reasoning_effort/)
  })

  it('reset-to-defaults clears the tier as well', () => {
    const reset = configSource.match(/const resetResilience[\s\S]*?\n}/)
    expect(reset, 'resetResilience 未找到').not.toBeNull()
    expect(reset![0]).toContain('delete nextAdapter.reasoning_effort')
  })
})
