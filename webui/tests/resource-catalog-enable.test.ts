import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 目录页装完之后必须能就地启用（需求 10）。
 *
 * 现场报障是「Agent 编辑器里 Prompt/Skill/Memory/MCP/Hook 五个添加按钮全是灰的」。
 * 链路一段都没断——真正的断点是导航：
 *
 * 1. `resource_lifecycle.install_archive` 一律写入
 *    `enabled=false, confirmation_required=true`。这是刻意的安全设计：装包不自动
 *    跑脚本，启用需要显式确认。
 * 2. Agent 绑定区的判据是 `resource.enabled && !resource.confirmation_required`
 *    （`AgentView.vue`），因此刚装好的资源一律不可绑定——按钮灰是这条设计的
 *    正常投影，不是缺陷。
 * 3. 而目录页装完只把按钮置灰改成「已安装」，**不提供任何跳转或动作**；
 *    「请先在资源管理中启用并确认」那句提示又出现在另一个页面上。
 *
 * 两处不连通，用户就卡在第 2 步与第 3 步之间，并把它读成「功能没做」。
 * 这些用例钉住那个动作确实存在、且不会退回成一个灰按钮。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const viewSource = read('../src/views/resources/ResourceView.vue')
const apiSource = read('../src/api/resource.ts')

describe('catalog enable action', () => {
  it('offers an enable button for installed-but-disabled items', () => {
    expect(viewSource).toContain('data-test="catalog-enable"')
    expect(viewSource).toMatch(/enableInstalledCatalogItem\s*\(/)
  })

  it('shows it exactly when the item is installed and not yet enabled', () => {
    // 三个条件都必要：未安装的显示「安装」；已启用的没有下一步；
    // 缺 installed_resource_id 时无从调用启用接口（那是服务端给的 id）。
    expect(viewSource).toContain(
      'v-if="item.installed && !item.enabled && item.installed_resource_id"'
    )
  })

  it('never leaves an installed-but-disabled item with only a greyed button', () => {
    // 回归点：此前 `:disabled="item.installed"` 让这一档完全没有动作。
    // 现在 disabled 那一支只负责「已启用」——即真的没有下一步可做的那一档。
    const fallback = viewSource.slice(viewSource.indexOf('v-else type="primary"'))
    expect(fallback.slice(0, 400)).toContain(':disabled="item.installed"')
    expect(fallback.slice(0, 400)).toContain('已启用')
    // 「已安装」不再作为终态文案出现在这个按钮上——它现在意味着「还差一步」。
    expect(fallback.slice(0, 400)).not.toContain("'已安装'")
  })

  it('goes through the same confirmation as the resource list', () => {
    // 启用会让 Agent 在后续对话里读取这个资源的固定版本与权限，
    // 因此与资源列表同一道确认，不能因为入口不同而少一次。
    const handler = viewSource.slice(viewSource.indexOf('const enableInstalledCatalogItem'))
    expect(handler.slice(0, 900)).toContain('ask({')
    expect(handler.slice(0, 900)).toContain('enableResource(resourceId, true)')
  })

  it('refetches from the server rather than flipping a local boolean', () => {
    // `installed` 与 `enabled` 都来自服务端。本地改布尔会让界面与真实状态短暂
    // 不符，而那个不符恰好发生在用户最想确认「到底成没成」的时刻。
    const handler = viewSource.slice(viewSource.indexOf('const enableInstalledCatalogItem'))
    expect(handler.slice(0, 900)).toContain('loadResources()')
    expect(handler.slice(0, 900)).toContain('searchRemote(')
  })

  it('tells the user what became possible', () => {
    // 「资源已启用」只说明发生了什么，没说明下一步在哪。
    const handler = viewSource.slice(viewSource.indexOf('const enableInstalledCatalogItem'))
    expect(handler.slice(0, 900)).toContain('可以在 Agent 里绑定')
  })

  it('types installed_resource_id so the action can address the resource', () => {
    expect(apiSource).toMatch(/installed_resource_id\?:\s*string \| null/)
  })

  it('shows a per-item busy state rather than a shared spinner', () => {
    // 用 resource id 而不是布尔：多张卡片才不会一起转圈。
    const button = viewSource.slice(
      viewSource.indexOf('v-if="item.installed && !item.enabled'),
      viewSource.indexOf('v-else type="primary"')
    )
    expect(button).toContain('busyResourceId === item.installed_resource_id')
  })
})
