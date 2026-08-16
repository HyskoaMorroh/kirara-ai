import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import { describe, expect, it } from 'vitest'

import { palettes } from '../src/theme/palettes'

/**
 * 色板值在三处重复：palettes.ts（唯一真源）、assets/main.css、index.html 里
 * 手抄的首屏防白屏（FOUC）启动表。启动表不能删除，也不能 import palettes.ts，
 * 否则会把入口 chunk 拉回首屏。本测试因此负责检测两者漂移。
 *
 * 契约：index.html 中的启动表是一个可机械解析的 JS 对象字面量，紧跟在
 * 名为 THEME_BOOT_TABLE 的标记注释之后（形如 block 注释包裹该标记），形如
 *   { classic: { light: ['背景色', '前景色'], dark: [...] }, ... }
 * 其中背景色对应 ThemeSeed.bg，前景色对应 ThemeSeed.textTertiary。
 */

const BOOT_TABLE_MARKER = '/* THEME_BOOT_TABLE */'

const indexHtml = readFileSync(
  fileURLToPath(new URL('../index.html', import.meta.url)),
  'utf-8'
)

/** 从标记（或退化到 bootPalettes 赋值）处截取配平的花括号块 */
function extractBootTableSource(html: string): string {
  let searchFrom = html.indexOf(BOOT_TABLE_MARKER)
  if (searchFrom === -1) {
    // 标记尚未落地时退回按变量名定位，保证测试在过渡期仍然有效
    searchFrom = html.search(/var\s+bootPalettes\s*=/)
  }
  expect(
    searchFrom,
    `index.html 未找到启动色板表：既没有 ${BOOT_TABLE_MARKER} 标记，也没有 bootPalettes 赋值`
  ).toBeGreaterThan(-1)

  const start = html.indexOf('{', searchFrom)
  expect(start, 'index.html 启动色板表缺少 { 起始花括号').toBeGreaterThan(-1)

  let depth = 0
  for (let index = start; index < html.length; index += 1) {
    const char = html[index]
    if (char === '{') depth += 1
    if (char === '}') {
      depth -= 1
      if (depth === 0) {
        return html.slice(start, index + 1)
      }
    }
  }
  throw new Error('index.html 启动色板表花括号未配平，无法解析')
}

/** 把 JS 对象字面量（单引号、裸键、尾逗号）规整成 JSON 再解析，避免依赖格式 */
function parseBootTable(source: string): Record<string, Record<string, string[]>> {
  const json = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/'/g, '"')
    .replace(/([{,]\s*)([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":')
    .replace(/,(\s*[}\]])/g, '$1')
  return JSON.parse(json)
}

describe('index.html 首屏启动色板表', () => {
  const bootTable = parseBootTable(extractBootTableSource(indexHtml))

  it('覆盖 palettes.ts 中的每一个色板，且不含多余条目', () => {
    const sourceKeys = palettes.map((palette) => palette.key).sort()
    const bootKeys = Object.keys(bootTable).sort()

    expect(
      bootKeys,
      `启动表色板集合与 palettes.ts 不一致：palettes.ts=${sourceKeys.join(', ')}，` +
        `index.html=${bootKeys.join(', ')}。新增色板后必须同步 index.html 的启动表。`
    ).toEqual(sourceKeys)
  })

  it.each(palettes.map((palette) => [palette.key, palette] as const))(
    '色板 %s 的浅色/深色背景与前景值与 palettes.ts 一致',
    (key, palette) => {
      const entry = bootTable[key]
      expect(entry, `启动表缺少色板 ${key}`).toBeTruthy()

      for (const scheme of ['light', 'dark'] as const) {
        const pair = entry[scheme]
        expect(
          Array.isArray(pair) && pair.length === 2,
          `启动表 ${key}.${scheme} 应为 ['背景色', '前景色'] 两元数组，实际为 ${JSON.stringify(pair)}`
        ).toBe(true)

        const expected = [palette[scheme].bg, palette[scheme].textTertiary]
        expect(
          pair.map((value) => value.trim().toLowerCase()),
          `色板 ${key} 的 ${scheme} 值已漂移：palettes.ts 为 ${JSON.stringify(expected)}，` +
            `index.html 启动表为 ${JSON.stringify(pair)}。` +
            '请修改 palettes.ts 后同步 index.html 的 THEME_BOOT_TABLE。'
        ).toEqual(expected.map((value) => value.trim().toLowerCase()))
      }
    }
  )
})
