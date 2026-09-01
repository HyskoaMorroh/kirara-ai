/**
 * 每个被 import 的第三方包都必须在 package.json 里直接声明。
 *
 * 起因是 `ConfigurationList.vue` 里 `import Markdown from 'vue3-markdown-it'` —— 这个包
 * 既没声明也没安装。它至今没炸,只因为那个组件已经没人引用了;一旦有人重新引它,Vite
 * 解析失败,页面直接打不开,不是类型警告。
 *
 * 另有五个包(highlight.js、semver、date-fns、vscode-languageclient、
 * @codingame/monaco-vscode-configuration-service-override)是靠别的依赖间接带进
 * node_modules 才能解析的。今天能跑,上游哪天不再传递它就断,而且断在构建期。
 *
 * 这里同时锁两件事:声明齐全,以及确实装得上。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { resolve, join } from 'node:path'

const root = resolve(__dirname, '..')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf-8')) as {
  dependencies?: Record<string, string>
  devDependencies?: Record<string, string>
}
const declared = new Set([
  ...Object.keys(pkg.dependencies ?? {}),
  ...Object.keys(pkg.devDependencies ?? {})
])

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.(ts|vue)$/.test(entry.name) ? [full] : []
  })
}

/** 包名根:`@scope/name/sub` 取 `@scope/name`,`name/sub` 取 `name`。 */
function packageRoot(specifier: string): string {
  return specifier.startsWith('@')
    ? specifier.split('/').slice(0, 2).join('/')
    : specifier.split('/')[0]
}

const IMPORT_STATEMENT = /^\s*import\s+(?:[^;\n]*?\s+from\s+)?["']([^"']+)["']/gm

const imported = new Map<string, string[]>()
for (const file of sourceFiles(join(root, 'src'))) {
  const source = readFileSync(file, 'utf-8')
  for (const match of source.matchAll(IMPORT_STATEMENT)) {
    const specifier = match[1]
    if (/^[./]/.test(specifier) || specifier.startsWith('@/') || specifier.startsWith('node:')) {
      continue
    }
    const name = packageRoot(specifier)
    const seen = imported.get(name) ?? []
    seen.push(file.slice(root.length + 1))
    imported.set(name, seen)
  }
}

describe('第三方 import 与依赖声明', () => {
  it('自检:确实扫到了一批裸包 import', () => {
    expect(imported.size).toBeGreaterThan(5)
    expect([...imported.keys()]).toContain('naive-ui')
  })

  it('每个被 import 的包都在 package.json 里直接声明', () => {
    const undeclaredList = [...imported.entries()]
      .filter(([name]) => !declared.has(name))
      .map(([name, files]) => `${name} <- ${files.join(', ')}`)

    expect(undeclaredList, `未声明的依赖：\n${undeclaredList.join('\n')}`).toEqual([])
  })

  it('每个被 import 的包都真的装在 node_modules 里', () => {
    const notInstalled = [...imported.keys()].filter(
      (name) => !existsSync(join(root, 'node_modules', name))
    )

    expect(notInstalled, `声明了但装不到：${notInstalled.join(', ')}`).toEqual([])
  })
})
