import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import { describe, expect, it } from 'vitest'

import { palettes } from '../src/theme/palettes'

const WORKFLOW_CANVAS_PATH = fileURLToPath(
  new URL('../src/components/workflow/WorkflowCanvas.vue', import.meta.url)
)
const NODE_PANEL_PATH = fileURLToPath(
  new URL('../src/components/workflow/NodeConfigPanel.vue', import.meta.url)
)
const MAIN_CSS_PATH = fileURLToPath(new URL('../src/assets/main.css', import.meta.url))

const workflowCanvasSource = readFileSync(WORKFLOW_CANVAS_PATH, 'utf-8')
const nodePanelSource = readFileSync(NODE_PANEL_PATH, 'utf-8')
const mainCssSource = readFileSync(MAIN_CSS_PATH, 'utf-8')

const SEMANTIC_TOKEN_KEYS = [
  'focus',
  'overlay',
  'selection',
  'muted',
  'minimap',
  'nodeAccents'
] as const

const TEXT_TOKEN_KEYS = [
  'text',
  'textSecondary',
  'textTertiaryText',
  'primaryText',
  'successText',
  'warningText',
  'errorText',
  'infoText'
] as const

type Rgb = [number, number, number]

function parseColor(value: string): Rgb {
  const trimmed = value.trim()
  const hex = trimmed.replace(/^#/, '')
  if (/^[\da-f]{6}$/i.test(hex)) {
    return [
      Number.parseInt(hex.slice(0, 2), 16),
      Number.parseInt(hex.slice(2, 4), 16),
      Number.parseInt(hex.slice(4, 6), 16)
    ]
  }

  const shortHex = trimmed.replace(/^#/, '')
  if (/^[\da-f]{3}$/i.test(shortHex)) {
    return [...shortHex].map((channel) => Number.parseInt(`${channel}${channel}`, 16)) as Rgb
  }

  const rgb = trimmed.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i)
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])]

  throw new Error(`测试不支持的颜色格式: ${value}`)
}

function relativeLuminance(value: string): number {
  const channels = parseColor(value).map((channel) => channel / 255)
  const linear = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  )
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
}

function contrastRatio(foreground: string, background: string): number {
  const foregroundLuminance = relativeLuminance(foreground)
  const backgroundLuminance = relativeLuminance(background)
  const lighter = Math.max(foregroundLuminance, backgroundLuminance)
  const darker = Math.min(foregroundLuminance, backgroundLuminance)
  return (lighter + 0.05) / (darker + 0.05)
}

describe('工作流主题语义令牌', () => {
  it.each(palettes.flatMap((palette) =>
    (['light', 'dark'] as const).map((scheme) => [palette.key, scheme, palette[scheme]] as const)
  ))('%s/%s 定义完整语义令牌', (_paletteKey, _scheme, seed) => {
    for (const key of SEMANTIC_TOKEN_KEYS) {
      expect(seed[key], `缺少语义令牌 ${key}`).toBeTruthy()
    }

    expect(seed.nodeAccents.custom).toBeTruthy()
    expect(seed.nodeAccents.code).toBeTruthy()
  })

  it.each(palettes.flatMap((palette) =>
    (['light', 'dark'] as const).map((scheme) => [palette.key, scheme, palette[scheme]] as const)
  ))('%s/%s 的正文与状态文本满足 WCAG AA', (_paletteKey, _scheme, seed) => {
    for (const key of TEXT_TOKEN_KEYS) {
      expect(
        contrastRatio(seed[key], seed.bg),
        `${key}=${seed[key]} 在 ${seed.bg} 上的对比度不足 4.5:1`
      ).toBeGreaterThanOrEqual(4.5)
    }
  })
})

describe('工作流画布无障碍与响应式合同', () => {
  it('每个图标工具栏操作都有 aria-label 和对应 tooltip 文本', () => {
    const buttonLabels = [...workflowCanvasSource.matchAll(/aria-label="([^"]+)"/g)].map(
      (match) => match[1]
    )
    expect(buttonLabels.length).toBeGreaterThanOrEqual(10)
    expect(workflowCanvasSource).toContain('<NTooltip')
    expect(workflowCanvasSource).toContain('title="自动排布')
    expect(workflowCanvasSource).toContain('title="查找节点')
  })

  it('画布操作保持原生键盘可达，并为窄屏提供受限工具栏与面板宽度', () => {
    expect(workflowCanvasSource).toContain('<NButton')
    expect(workflowCanvasSource).toContain('aria-label="保存工作流"')
    expect(workflowCanvasSource).toContain('aria-label="快捷键说明"')
    expect(workflowCanvasSource).toContain('max-width: calc(100vw - 48px)')
    expect(workflowCanvasSource).toContain('@media (max-width: 640px)')
    expect(workflowCanvasSource).toContain('<NDropdown')
    expect(workflowCanvasSource).toContain('trigger="click"')
    expect(workflowCanvasSource).toContain('title="更多画布操作"')
    expect(workflowCanvasSource).toContain("key: 'layout-lr'")
    expect(workflowCanvasSource).toContain("key: 'layout-tb'")
    expect(workflowCanvasSource).toContain("'layout-lr': () => setLayoutDirection('LR')")
    expect(workflowCanvasSource).toContain("'layout-tb': () => setLayoutDirection('TB')")
    expect(workflowCanvasSource).toContain('class="toolbar-secondary"')
    expect(workflowCanvasSource).toContain('class="toolbar-overflow"')
    expect(workflowCanvasSource).toContain('class="node-config-panel-wrapper"')
    expect(workflowCanvasSource).toContain('class="node-list-panel-wrapper"')
    expect(workflowCanvasSource).toMatch(
      /\.vue-flow__panel\.node-config-panel-wrapper\s*\{[\s\S]*?z-index:\s*120;/
    )
    expect(workflowCanvasSource).toMatch(
      /@media \(max-width: 640px\)[\s\S]*?\.toolbar-secondary\s*\{[\s\S]*?display:\s*none;/
    )
    expect(nodePanelSource).toContain('calc(100vw - 16px)')
    expect(nodePanelSource).not.toMatch(/\.node-config-panel\s*\{[\s\S]*?width:\s*100vw;/)
  })

  it('为焦点、选择态和减少动态效果提供全局 CSS 合同', () => {
    expect(mainCssSource).toContain('--focus-color')
    expect(mainCssSource).toContain('--selection-bg-color')
    expect(mainCssSource).toContain('::selection')
    expect(mainCssSource).toContain('prefers-reduced-motion: reduce')
    expect(mainCssSource).toContain(':focus-visible')
  })
})
