// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve, sep } from 'node:path'

/**
 * 模板里用到的每一个 naive-ui 组件都必须真的被 import。
 *
 * 这个项目没有全局注册 naive-ui（`main.ts` 只 `use` 了 pinia 与 router），也没有装
 * 自动导入解析器。于是 `<n-list>` 这种没 import 的标签会被 Vue 当成**原生元素**
 * 渲染：内容照样出现在 DOM 里，只是完全没有 naive-ui 的样式与行为——
 * `bordered` 不画边框、`n-list-item` 之间没有分隔线、`n-icon` 不做尺寸与对齐。
 * 控制台里有一条 `Failed to resolve component`，而生产构建把它去掉了。
 *
 * 这类缺陷的形态是「看起来像 CSS 没写好」：不报错、不白屏，只是排版不对。
 * 而真正的原因在 import 清单里，离出问题的那一行几百行远。
 *
 * 断言按**行为**写：从 `<template>` 里收集 `<n-xxx>` 标签，逐个要求对应的
 * PascalCase 名字出现在文件里（`script setup` 的 import 是唯一可能的来源）。
 * 不钉某一个具体文件，也不钉 import 的写法——新增一个页面时这条护栏自动覆盖它。
 */

const here = dirname(fileURLToPath(import.meta.url))
const SRC = resolve(here, '../src')

function vueFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) return vueFiles(full)
    return full.endsWith('.vue') ? [full] : []
  })
}

const pascal = (tag: string) =>
  tag
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')

interface Unresolved {
  file: string
  tags: string[]
}

function scan(): Unresolved[] {
  const offenders: Unresolved[] = []
  for (const file of vueFiles(SRC)) {
    const source = readFileSync(file, 'utf-8')
    const templateAt = source.indexOf('<template>')
    if (templateAt < 0) continue
    const template = source.slice(templateAt)
    const used = new Set([...template.matchAll(/<(n-[a-z0-9-]+)/g)].map((m) => m[1]))
    if (!used.size) continue
    // 整份文件里出现过这个 PascalCase 名字就算已解析：import 是它唯一可能的来源，
    // 而只扫 import 块会漏掉 `import { NList as List }` 之外的各种合法写法。
    const declared = new Set([...source.matchAll(/\b(N[A-Z][A-Za-z0-9]*)\b/g)].map((m) => m[1]))
    const missing = [...used].filter((tag) => !declared.has(pascal(tag))).sort()
    if (missing.length) {
      offenders.push({ file: file.split(sep).join('/').slice(file.split(sep).join('/').indexOf('/src/') + 1), tags: missing })
    }
  }
  return offenders
}

describe('naive-ui 组件解析', () => {
  it('自检：确实扫到了文件与标签', () => {
    // 没有这条时，一次路径写错会让整组断言在空集合上恒真。
    const files = vueFiles(SRC)
    expect(files.length).toBeGreaterThan(20)
    const anyTag = files.some((file) => /<n-[a-z]/.test(readFileSync(file, 'utf-8')))
    expect(anyTag).toBe(true)
  })

  it('模板里的每个 n-* 标签都有对应 import', () => {
    const offenders = scan()
    const detail = offenders.map((item) => `${item.file}: ${item.tags.join(', ')}`).join('\n')
    expect(detail, `以下组件没有 import，会被当成原生元素渲染（无样式、无行为）：\n${detail}`).toBe('')
  })
})
