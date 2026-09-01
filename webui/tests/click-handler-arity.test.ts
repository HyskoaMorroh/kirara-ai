/**
 * 带可选首参的函数不能裸绑到 @click 上。
 *
 * `@click="createRule"` 会把 MouseEvent 当第一个实参传进去。函数签名是
 * `createRule(workflowId = '')` 时，默认值**不生效**——默认值只在实参为
 * `undefined` 时才顶上，而 MouseEvent 是个真对象。结果新建的规则里
 * `workflow_id` 成了一个 MouseEvent，落到后端就是一条 workflow_id 不合法的规则，
 * 而界面上完全看不出哪里错了：用户只看到「创建规则」点了之后保存失败。
 *
 * `vue-tsc` 能报这个（TS2322），但它此前被 TopBar.vue 的 TS6504 挡住了——
 * TS6504 是致命错，类型检查停在那一步就没往下走。所以这里补一条独立的文本规则：
 * 即使将来 typecheck 又被别的致命错拦住，这条也照样能拦下同类写法。
 *
 * 只查带可选首参的函数（`(x?: T)` 或 `(x = ...)`）。零参函数裸绑是安全且惯用的，
 * 不该被这条规则牵连——把它们一起禁掉只会逼着大家加一堆无意义的 `()`。
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', 'src')

const vueFiles = (dir: string): string[] =>
  readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) return vueFiles(full)
    return full.endsWith('.vue') ? [full] : []
  })

/** 收集该文件里「首参可选」的顶层函数名。 */
const optionalFirstParamFns = (source: string): Set<string> => {
  const names = new Set<string>()
  // const fn = (a?: T) => / const fn = (a = x) =>；只认 const 箭头函数，
  // 这是本仓库 <script setup> 里定义处理函数的唯一写法。
  const pattern = /const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\(\s*([A-Za-z_$][\w$]*)\s*(\?:|=)/g
  for (const match of source.matchAll(pattern)) names.add(match[1])
  return names
}

/** 收集裸绑到 @click 的处理函数名（`@click="fn"`，不带括号）。 */
const bareClickHandlers = (source: string): { name: string; line: number }[] => {
  const found: { name: string; line: number }[] = []
  source.split('\n').forEach((text, index) => {
    for (const match of text.matchAll(/@click="([A-Za-z_$][\w$]*)"/g)) {
      found.push({ name: match[1], line: index + 1 })
    }
  })
  return found
}

describe('@click 处理函数入参', () => {
  it('首参可选的函数不能裸绑到 @click，否则 MouseEvent 会顶掉默认值', () => {
    const offenders: string[] = []

    for (const file of vueFiles(SRC)) {
      const source = readFileSync(file, 'utf8')
      const risky = optionalFirstParamFns(source)
      if (risky.size === 0) continue

      for (const handler of bareClickHandlers(source)) {
        if (!risky.has(handler.name)) continue
        offenders.push(
          `${relative(SRC, file).replace(/\\/g, '/')}:${handler.line} @click="${handler.name}" ` +
            `→ 改成 @click="${handler.name}()"`
        )
      }
    }

    expect(
      offenders,
      `这些处理函数首参可选，裸绑会把 MouseEvent 当实参传进去：\n${offenders.join('\n')}`
    ).toEqual([])
  })

  it('规则本身认得出危险签名，不是空跑一遍就绿', () => {
    // 防止上面那条因为正则写坏而永远匹配不到、变成一条无效的绿灯。
    const sample = `
      const createRule = (workflowId = '') => {}
      const openPreview = (draftRule?: DispatchRule) => {}
      const reload = async () => {}
    `
    const risky = optionalFirstParamFns(sample)

    expect(risky.has('createRule')).toBe(true)
    expect(risky.has('openPreview')).toBe(true)
    // 零参函数裸绑是安全的，不该被这条规则牵连。
    expect(risky.has('reload')).toBe(false)
  })

  it('规则认得出裸绑与带括号调用的区别', () => {
    const bare = bareClickHandlers('<n-button @click="createRule">x</n-button>')
    const called = bareClickHandlers('<n-button @click="createRule()">x</n-button>')

    expect(bare.map((entry) => entry.name)).toEqual(['createRule'])
    expect(called).toEqual([])
  })
})
