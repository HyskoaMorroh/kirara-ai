// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * MCP 新增表单要给出与内置目录一致的一组预设模板。
 *
 * 参考界面的「新增 MCP」页第一步就是选类型：自定义、fetch、time、memory、
 * sequential-thinking、context7 六个快捷标签，「类型快捷标签应能填充合理模板」
 * （`docs/superpowers/plans/ccs-ui-notes.md` 的 `Image_2026-08-23_033301_565`
 * 与 `Image_2026-08-24_214356_226` 两节）。
 *
 * 本项目的内置目录（`resource_catalog.py` 的 `_BUILTINS`）里有**八个** stdio MCP
 * 预设：context7、fetch、time、memory、sequential-thinking、filesystem、
 * chrome-devtools、playwright。它们各自声明了 `command` / `args` 与
 * `runtime_dependency`（靠 `npx` 还是 `uvx` 拉起）。
 *
 * 而 MCP 页此前只有一个「Context7 模板」按钮。缺的不是七个按钮，是**这条链路的
 * 对称性**：同样八个预设，从「资源管理 → 发现并安装」进去装得到，从「MCP → 添加
 * 服务器」进去只有一个。用户在 MCP 页找不到 `fetch`，会得出「这个项目不支持它」
 * 这个错误结论——而它就在另一个页面的目录里。
 *
 * 这组用例钉住行为：预设是一份**表驱动**的清单（不是八个复制粘贴的函数）、
 * 每一条都带命令与参数、每一条都说明靠什么运行时拉起，且 id 与内置目录对得上——
 * 对不上时用户从两个入口装同一个 MCP 会得到两个不同 id 的服务器。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const vmSource = read('../src/views/mcp/mcp.vm.ts')
const viewSource = read('../src/views/mcp/MCPList.vue')

/** 与 `kirara_ai/plugin_manager/resource_catalog.py` 的 `_BUILTINS` 一致。 */
const CATALOG_PRESETS = [
  'context7',
  'fetch',
  'time',
  'memory',
  'sequential-thinking',
  'filesystem',
  'chrome-devtools',
  'playwright'
] as const

describe('预设清单', () => {
  it('导出一份可枚举的预设表', () => {
    // 表驱动而不是八个 openXxxTemplate 函数：后者每加一个预设就要改三处
    // （函数、按钮、导出），而漏掉任何一处都不会报错。
    expect(vmSource).toMatch(/MCP_PRESETS|mcpPresets/)
  })

  it.each(CATALOG_PRESETS)('包含内置目录里的 %s', (id) => {
    expect(vmSource, `预设表里没有 ${id}`).toContain(`'${id}'`)
  })

  it('每条预设都带命令与参数', () => {
    // 没有命令的预设填不出可用配置，点了等于开一个空表单。
    const table = vmSource.slice(vmSource.indexOf('MCP_PRESETS'))
    const commands = table.match(/command:\s*'(npx|uvx)'/g) || []
    expect(commands.length).toBeGreaterThanOrEqual(CATALOG_PRESETS.length)
  })

  it('每条预设都说明靠什么运行时拉起', () => {
    // `npx` 与 `uvx` 都不是本项目的依赖，运行时镜像两个都没装。
    // 不说明的话，用户点了启用只会看到「连接失败 / 工具数 0」。
    const table = vmSource.slice(vmSource.indexOf('MCP_PRESETS'))
    const runtimes = table.match(/runtime:\s*'(npx|uvx)'/g) || []
    expect(runtimes.length).toBeGreaterThanOrEqual(CATALOG_PRESETS.length)
  })

  it('fetch 与 time 走 uvx，其余走 npx', () => {
    // 与 `_BUILTINS` 的 `runtime_dependency` 对齐。搞反了会让界面上的
    // 依赖提示指向一个装了也没用的工具。
    const table = vmSource.slice(vmSource.indexOf('MCP_PRESETS'))
    expect(table).toMatch(/'fetch'[\s\S]{0,400}?uvx/)
    expect(table).toMatch(/'time'[\s\S]{0,400}?uvx/)
    expect(table).toMatch(/'playwright'[\s\S]{0,400}?npx/)
  })
})

describe('界面入口', () => {
  it('预设按表渲染，而不是写死一个按钮', () => {
    // 钉行为不钉写法：菜单项必须**由预设表派生**（`presets.map(...)`），
    // 而具体用 `v-for` 还是 naive-ui 的 `:options` 是实现选择。
    // 钉住 `v-for` 会让一次正当的实现变更变成红灯，而改断言让它变绿又什么都没验证。
    expect(viewSource).toMatch(/presets\s*\.\s*map\(/)
    expect(viewSource).toMatch(/:options="presetMenuOptions"|v-for=".*preset/i)
  })

  it('保留「自定义」这条空白起点', () => {
    // 参考界面把自定义与快捷类型并列。没有它时，想从零写一个配置的人
    // 得先选一个预设再删干净。
    expect(viewSource).toMatch(/自定义/)
  })

  it('应用预设的处理函数只有一个', () => {
    // 八个预设八个 handler 是同一段逻辑抄八遍；只留一个按 id 取表的入口。
    const handlers = vmSource.match(/const open\w*Template\s*=/g) || []
    expect(handlers.length).toBeLessThanOrEqual(1)
  })

  it('预设填出的 id 与目录一致', () => {
    // 两个入口装出两个不同 id 的同一个 MCP，之后「为什么有两个 context7」
    // 无从解释；`refresh_managed_servers` 也按 id 对账。
    const table = vmSource.slice(vmSource.indexOf('MCP_PRESETS'))
    for (const id of CATALOG_PRESETS) {
      expect(table).toMatch(new RegExp(`id:\\s*'${id}'`))
    }
  })
})
