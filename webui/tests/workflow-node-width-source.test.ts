import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  CODE_NODE_MAX_WIDTH,
  CODE_NODE_MIN_WIDTH,
  NODE_MAX_WIDTH,
  NODE_MIN_WIDTH
} from '../src/components/workflow/useLayout'

/**
 * 节点宽度只能有一处真值。
 *
 * `useLayout.ts` 用这些常量估算未测量节点的盒子并交给 dagre 排布。组件既内联
 * 绑定常量（运行时真值），也在 CSS 里保留同样的数值作为回退（内联样式缺失时
 * 节点不至于塌成一条）。回退值与常量漂移不会报错，只会表现为「节点间距忽大
 * 忽小」——这条测试断言两者始终相等。
 *
 * CustomNode 早已收敛到常量绑定，CodeNode 曾遗漏，两者在这里一起被钉住。
 */

const here = dirname(fileURLToPath(import.meta.url))
const nodeSource = (name: string) =>
  readFileSync(resolve(here, `../src/components/workflow/nodes/${name}`), 'utf-8')

const rootRule = (source: string, selector: string) => {
  const styleBlock = source.slice(source.indexOf('<style'))
  const match = styleBlock.match(new RegExp(`\\${selector}\\s*\\{[^}]*\\}`))
  expect(match, `${selector} 根规则未找到`).not.toBeNull()
  return match![0]
}

const declaredPx = (rule: string, property: string) => {
  const match = rule.match(new RegExp(`${property}:\\s*(\\d+)px`))
  expect(match, `${property} 回退值未声明`).not.toBeNull()
  return Number(match![1])
}

describe('node width single source of truth', () => {
  it.each([
    ['CustomNode.vue', '.custom-node', NODE_MIN_WIDTH, NODE_MAX_WIDTH],
    ['CodeNode.vue', '.code-node', CODE_NODE_MIN_WIDTH, CODE_NODE_MAX_WIDTH]
  ])(
    '%s CSS fallback matches the layout constants',
    (file, selector, expectedMin, expectedMax) => {
      const rule = rootRule(nodeSource(file as string), selector as string)

      expect(declaredPx(rule, 'min-width')).toBe(expectedMin)
      expect(declaredPx(rule, 'max-width')).toBe(expectedMax)
    }
  )

  it.each(['CustomNode.vue', 'CodeNode.vue'])(
    '%s binds its runtime width from the layout constants',
    (file) => {
      const source = nodeSource(file)

      expect(source).toMatch(/from '\.\.\/useLayout'/)
      expect(source).toMatch(/nodeWidthStyle/)
      expect(source).toMatch(/:style="nodeWidthStyle"/)
    }
  )

  it('keeps the code-node band narrower than the regular band', () => {
    expect(CODE_NODE_MIN_WIDTH).toBeLessThanOrEqual(NODE_MIN_WIDTH)
    expect(CODE_NODE_MAX_WIDTH).toBeLessThanOrEqual(NODE_MAX_WIDTH)
    expect(CODE_NODE_MIN_WIDTH).toBeLessThan(CODE_NODE_MAX_WIDTH)
  })
})
