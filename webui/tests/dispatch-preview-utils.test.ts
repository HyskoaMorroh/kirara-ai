import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import { describe, expect, it } from 'vitest'

import {
  DISPATCH_PREVIEW_DECISIONS,
  getDispatchPreviewDecisionLabel,
  getDispatchPreviewDecisionType
} from '../src/views/workflow/dispatch-preview-utils'
import type { DispatchPreviewDecision } from '../src/api/dispatch'

/**
 * 判定类型定义在后端（`DispatchPreviewRuleResult.decision` 的 Literal），
 * 前端只负责给它们配中文标签与颜色。逐个写死字符串相等的断言无法发现
 * “后端新增了一种判定、前端忘了加标签”——界面会直接渲染出 undefined。
 *
 * 因此本测试直接以后端源码里的 Literal 取值集合为准，校验映射的完整性。
 */
const BACKEND_MODELS_PATH = fileURLToPath(
  new URL('../../kirara_ai/web/api/dispatch/models.py', import.meta.url)
)

function readBackendDecisionValues(): string[] {
  const source = readFileSync(BACKEND_MODELS_PATH, 'utf-8')
  const match = source.match(/decision:\s*Literal\[([^\]]*)\]/)
  if (!match) {
    throw new Error('未能在后端 models.py 中找到 decision 的 Literal 定义，契约已变化')
  }
  const values = [...match[1].matchAll(/"([^"]+)"/g)].map((item) => item[1])
  if (values.length === 0) {
    throw new Error('后端 decision Literal 为空，契约已变化')
  }
  return values
}

describe('dispatch preview decision labels', () => {
  const backendDecisions = readBackendDecisionValues()

  it('mirrors exactly the decision values the backend can return', () => {
    expect([...DISPATCH_PREVIEW_DECISIONS].sort()).toEqual([...backendDecisions].sort())
  })

  it('maps every backend decision to a non-empty Chinese label', () => {
    const missing = backendDecisions.filter((decision) => {
      const label = getDispatchPreviewDecisionLabel(decision as DispatchPreviewDecision)
      return typeof label !== 'string' || label.length === 0
    })

    expect(missing).toEqual([])
  })

  it('maps every backend decision to a renderable tag type', () => {
    const allowedTypes = ['success', 'warning', 'info', 'default', 'error']
    const missing = backendDecisions.filter(
      (decision) =>
        !allowedTypes.includes(
          getDispatchPreviewDecisionType(decision as DispatchPreviewDecision) as string
        )
    )

    expect(missing).toEqual([])
  })

  it('gives each decision a distinct label so the table stays readable', () => {
    const labels = backendDecisions.map((decision) =>
      getDispatchPreviewDecisionLabel(decision as DispatchPreviewDecision)
    )

    expect(new Set(labels).size).toBe(labels.length)
  })

  it('distinguishes the selected rule from later matching but shadowed rules', () => {
    expect(getDispatchPreviewDecisionLabel('selected')).toBe('将执行')
    expect(getDispatchPreviewDecisionLabel('shadowed')).toBe('匹配但被前序规则截断')
    expect(getDispatchPreviewDecisionType('selected')).toBe('success')
    expect(getDispatchPreviewDecisionType('shadowed')).toBe('warning')
  })

  it('uses clear labels for non-matches and indeterminate conditions', () => {
    expect(getDispatchPreviewDecisionLabel('not_matched')).toBe('未命中')
    expect(getDispatchPreviewDecisionLabel('indeterminate')).toBe('无法确定')
    expect(getDispatchPreviewDecisionLabel('disabled')).toBe('已禁用')
  })
})
