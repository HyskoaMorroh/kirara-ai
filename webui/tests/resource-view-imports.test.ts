/**
 * `ResourceView.vue` 里用到的 API 函数与类型必须真的 import 进来。
 *
 * `installRemoteSkill` 在「发现并安装」的确认回调里被调用，但从未 import——
 * 点「安装」时抛 `ReferenceError: installRemoteSkill is not defined`，
 * 资源一个都装不上。`ImportableArchive` 同理（类型缺失只影响编译期）。
 *
 * 这类错误 `vue-tsc` 报得出来（TS2304），但此前被 `TopBar.vue` 的 TS6504 挡住了：
 * TS6504 是致命错，类型检查停在那一步就没往下走，78 条既有错误一条都没露出来。
 * 所以这里补一条不依赖 typecheck 是否被拦住的独立检查。
 *
 * 只查 `@/api/resource` 这一个模块：它是本页所有后端调用的唯一出口，
 * 漏 import 的后果就是整块功能点下去直接炸。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const view = readFileSync(
  join(__dirname, '..', 'src', 'views', 'resources', 'ResourceView.vue'),
  'utf8'
)
const api = readFileSync(join(__dirname, '..', 'src', 'api', 'resource.ts'), 'utf8')

/** `@/api/resource` 导出的全部函数名与类型名。 */
const exportedNames = (): Set<string> => {
  const names = new Set<string>()
  for (const match of api.matchAll(
    /export\s+(?:async\s+)?(?:function|interface|type|const)\s+([A-Za-z_$][\w$]*)/g
  )) {
    names.add(match[1])
  }
  return names
}

/** `ResourceView.vue` 从 `@/api/resource` 实际 import 进来的名字。 */
const importedNames = (): Set<string> => {
  const names = new Set<string>()
  for (const match of view.matchAll(
    /import\s+(?:type\s+)?\{([^}]+)\}\s+from\s+'@\/api\/resource'/g
  )) {
    for (const raw of match[1].split(',')) {
      const name = raw.trim().split(/\s+as\s+/)[0].trim()
      if (name) names.add(name)
    }
  }
  return names
}

/** 去掉 template、注释与字符串字面量后的 script 正文——避免把文案里的词当标识符。 */
const scriptBody = (): string => {
  const scriptEnd = view.lastIndexOf('</script>')
  return view
    .slice(0, scriptEnd === -1 ? view.length : scriptEnd)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/'[^'\n]*'/g, "''")
    .replace(/"[^"\n]*"/g, '""')
    .replace(/`[^`]*`/g, '``')
}

describe('ResourceView 的 API import', () => {
  it('用到的 @/api/resource 导出都 import 了，否则点下去是 ReferenceError', () => {
    const exported = exportedNames()
    const imported = importedNames()
    const body = scriptBody()

    const missing = [...exported].filter((name) => {
      if (imported.has(name)) return false
      // 作为独立标识符出现（不是别的名字的一截）才算用到了。
      return new RegExp(`\\b${name}\\b`).test(body)
    })

    expect(
      missing.sort(),
      `这些名字在 ResourceView.vue 里用到但没 import：${missing.join(', ')}`
    ).toEqual([])
  })

  it('规则真的读到了两份文件，不是空集合互相比较', () => {
    // 防止路径写错时两个集合都为空、这条测试永远绿。
    expect(exportedNames().size).toBeGreaterThan(20)
    expect(importedNames().size).toBeGreaterThan(20)
    expect(scriptBody()).toContain('const')
  })

  it('规则不会把文案里的词误判成标识符', () => {
    // scriptBody 已抹掉字符串字面量，所以确认文案词不会残留。
    expect(scriptBody()).not.toContain('资源会从该仓库下载')
  })
})
