/**
 * 撤销栈不许用超出构建基线的内置方法。
 *
 * `vite.config.ts` 没有设 `build.target`,走 Vite 默认的 `'modules'`
 * ——Chrome 87 / Firefox 78 / Safari 14 / Edge 88。而 `Array.prototype.at`
 * 要 Chrome 92 / Firefox 90 / Safari 15.4。
 *
 * 关键在于 Vite/esbuild **只降级语法,不给内置方法注入 polyfill**：
 * `undoStack.at(-1)` 会原样出现在产物里,在基线浏览器上抛
 * `TypeError: undoStack.at is not a function`。而 `pushHistoryState` 是画布
 * 每一次改动的必经路径,所以后果不是某个边角功能失效,是工作流画布整体打不开。
 *
 * `tsconfig` 里 `lib` 是 ES2020（`@vue/tsconfig` 特意设的,注释说明更新的内置
 * 方法留给用户自行 polyfill）,所以 `vue-tsc` 本来就报了这 4 条 TS2550。
 * 把 lib 抬到 es2022 能让错误消失,但那是把真实的兼容性问题消音 —— 产物不会
 * 因此多出 polyfill。这里改为锁住「不要用它」。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

/** 剥掉注释：本文件说明里就写着 `.at(` 这个模式,不能让它算进命中。 */
function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

describe('构建基线内置方法', () => {
  it('撤销栈取栈顶不使用 Array.prototype.at', () => {
    const source = stripComments(
      readFileSync(resolve(root, 'src/store/workflow-editor.ts'), 'utf-8')
    )

    const hits = [...source.matchAll(/\.at\(\s*-?\d/g)].map((match) => match[0])
    expect(
      hits,
      `workflow-editor.ts 仍在用 .at()，基线浏览器（Safari 14）上会抛 TypeError：${hits.join(', ')}`
    ).toEqual([])
  })

  it('vite 构建目标若已显式抬高，本约束可以放宽', () => {
    // 这条是活文档：一旦有人显式设了 build.target，说明基线被重新声明过，
    // 上面那条断言的前提就要重新评估，而不是继续盲目禁用 .at()。
    const viteConfig = stripComments(readFileSync(resolve(root, 'vite.config.ts'), 'utf-8'))

    const hasExplicitTarget = /build\s*:[\s\S]{0,400}?\btarget\s*:/.test(viteConfig)
    expect(
      hasExplicitTarget,
      'vite.config.ts 出现了 build.target；请重新核对基线并更新上一条断言的理由'
    ).toBe(false)
  })
})
