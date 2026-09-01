import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * 侧栏暴露的入口不能落在占位页。
 *
 * 之前 `/memory`、`/memory/search` 在侧栏以「记忆管理 / 记忆检索」出现，
 * 点进去却是 `ComingSoon.vue`（「努力建设中... 喵~」）。而记忆本身早已是资源类型之一，
 * 走 `GET /api/resources?type=memory` —— 能力一直在，只是入口指错了地方。
 * 用户看到的是「这个功能没做」，实际是「这个链接接错了」，两者对使用者的含义完全不同。
 *
 * 这些断言读的是路由与侧栏源码本身，不渲染组件：
 * 死链是一条静态的连线错误，用源码断言能在改坏的那一刻就报出来。
 */

const root = resolve(__dirname, '..')
const routerSource = readFileSync(resolve(root, 'src/router/index.ts'), 'utf-8')
const secondarySidebar = readFileSync(
  resolve(root, 'src/components/layout/SecondarySidebar.vue'),
  'utf-8'
)
const mainSidebar = readFileSync(resolve(root, 'src/components/layout/MainSidebar.vue'), 'utf-8')

/** 取出侧栏里所有 `path: '/...'` 字面量，这些是用户真能点到的入口。 */
function sidebarPaths(source: string): string[] {
  return [...source.matchAll(/path:\s*'(\/[^']*)'/g)].map((match) => match[1])
}

/** 一条路由记录是否把 ComingSoon 当组件。块以 `path:` 起、到下一个 `path:` 或结尾止。 */
function placeholderPaths(source: string): string[] {
  const blocks = source.split(/(?=\n\s*\{\s*\n\s*path:)/)
  const found: string[] = []
  for (const block of blocks) {
    const path = block.match(/path:\s*'([^']+)'/)?.[1]
    if (path && /ComingSoon/.test(block)) found.push(path)
  }
  return found
}

describe('路由不留死链', () => {
  it('侧栏暴露的每个路径都不指向占位页', () => {
    const placeholders = new Set(placeholderPaths(routerSource))
    const exposed = sidebarPaths(secondarySidebar)

    const dead = exposed.filter((path) => placeholders.has(path))
    expect(dead, `这些侧栏入口点进去是占位页：${dead.join(', ')}`).toEqual([])
  })

  it('记忆两条入口指向资源管理的 memory 视图，而不是占位页', () => {
    // 记忆能力的真实载体是资源管理里的 memory 类型；入口应当把人送到那里。
    expect(routerSource).toMatch(/path:\s*'\/memory'/)
    const memoryBlock = routerSource
      .split(/(?=\n\s*\{\s*\n\s*path:)/)
      .find((block) => /path:\s*'\/memory'/.test(block))
    expect(memoryBlock).toBeDefined()
    expect(memoryBlock).not.toMatch(/ComingSoon/)
    expect(memoryBlock).toMatch(/redirect/)
    // 落点必须带上类型，否则跳过去看到的是全部资源，等于没筛。
    expect(memoryBlock).toMatch(/type:\s*'memory'/)
  })

  it('二级侧栏的每个一级分支都对应 MainSidebar 里真实存在的一级项', () => {
    // `memory` 曾作为二级分支存在，但 MainSidebar 没有 memory 一级项，
    // 一级 activeKey 取 path.split('/')[1]，于是那两条永远高亮不到任何一级菜单。
    const mainKeys = new Set(
      [...mainSidebar.matchAll(/key:\s*'([a-z-]+)'/g)].map((match) => match[1])
    )
    const branchKeys = [...secondarySidebar.matchAll(/case\s+'([a-z-]+)':/g)].map(
      (match) => match[1]
    )

    const orphans = [...new Set(branchKeys)].filter((key) => !mainKeys.has(key))
    expect(orphans, `二级分支没有对应的一级菜单项：${orphans.join(', ')}`).toEqual([])
  })

  it('activeKey 感知 query，否则同 path 不同 query 的条目永远高亮不到', () => {
    // 「资源列表」与「记忆资源」都指向 /resources，只有 query 不同。
    // activeKey 若只看 route.path，后者点进去高亮仍停在前者，看起来像点击没生效。
    expect(secondarySidebar).toMatch(/route.query/)
  })

  it('资源管理有二级菜单，因为它下面确实挂着多个子入口', () => {
    // 资源管理带依赖面板、备份恢复等子入口，却没有二级分支；
    // 而记忆（本身只是资源的一个类型）反倒有。这个错位要一起纠正。
    expect(secondarySidebar).toMatch(/case\s+'resources':/)
  })
})
