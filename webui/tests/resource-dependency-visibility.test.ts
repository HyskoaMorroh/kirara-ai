/**
 * 「这台机器缺 uvx」必须在界面上说得出来（需求 10）。
 *
 * 后端已经把四个字段投影在每个目录项与已安装资源上（`project_dependencies`）：
 *   dependency_ids       这个资源需要哪些系统依赖
 *   system_dependencies  每个依赖的探测结果（含 status / ready / version）
 *   dependencies_ready   全部就绪与否
 *   dependency_status    汇总档位：not_required / ready / missing / failed / cancelled / unknown
 *
 * 前端从不渲染它们，`CatalogItem` 的 TS 类型里也没有这四个键。
 *
 * 后果：用户装 `mcp:fetch`（它靠 `uvx` 启动），安装成功、启用成功、绑定成功，
 * 而这台机器没有 uvx。界面上唯一的线索是 MCP 面板显示「连接失败 / 工具数 0」——
 * 没有一处说缺什么。用户会去查网络、查配置、查 API Key，而真实原因是一个
 * 没装的命令行工具。
 *
 * 关键区分：`unknown` 不等于 `missing`。
 * 前者是「还没探测过」，后者是「探测过、确实没有」。把它们显示成同一种状态，
 * 会让刚装完还没探测的资源看起来像是坏的，用户于是去装一个本来就在的东西。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const api = readFileSync(resolve(root, 'src/api/resource.ts'), 'utf-8')
const view = readFileSync(resolve(root, 'src/views/resources/ResourceView.vue'), 'utf-8')

describe('资源的系统依赖状态', () => {
  it('自检：SystemDependency 类型本来就有，不需要新造', () => {
    expect(api).toMatch(/export interface SystemDependency\b/)
    expect(api).toMatch(/export type SystemDependencyStatus\b/)
  })

  it('依赖汇总档位有独立类型，六个取值与后端一一对应', () => {
    // 后端 `_dependency_status` 返回这六种；写成宽 string 会让拼错的档位
    // 在编译期通过，然后在界面上落进「其他」分支。
    const at = api.indexOf('ResourceDependencyStatus')
    expect(at, '缺少 ResourceDependencyStatus 类型').toBeGreaterThan(-1)
    const declaration = api.slice(at, api.indexOf('\n\n', at))
    for (const status of [
      'not_required',
      'ready',
      'missing',
      'failed',
      'cancelled',
      'unknown'
    ]) {
      expect(declaration, `档位 ${status} 未声明`).toContain(status)
    }
  })

  it('四个字段在一处声明，两个接口都能拿到', () => {
    // 断言的是「两个接口都带上这四个字段」这个结果，不是某一种写法。
    // 逐字段写进各自 body 与抽成共享接口再 extends 是等价的，后者更不容易漂移；
    // 只查 body 会把后者判成失败。
    const projection = api.indexOf('export interface ResourceDependencyProjection')
    expect(projection, '缺少 ResourceDependencyProjection').toBeGreaterThan(-1)
    const body = api.slice(projection, api.indexOf('\n}', projection))
    for (const field of [
      'dependency_ids',
      'system_dependencies',
      'dependencies_ready',
      'dependency_status'
    ]) {
      expect(body, `依赖投影缺字段 ${field}`).toMatch(new RegExp(`\\b${field}\\??:`))
    }

    for (const name of ['CatalogItem', 'ManagedResource']) {
      const at = api.indexOf(`export interface ${name}`)
      expect(at, `找不到 ${name}`).toBeGreaterThan(-1)
      const declaration = api.slice(at, api.indexOf('{', at))
      expect(
        declaration,
        `${name} 没有继承 ResourceDependencyProjection，拿不到依赖字段`
      ).toContain('ResourceDependencyProjection')
    }
  })

  it('界面渲染依赖状态，而不是只把字段接进类型', () => {
    expect(view).toMatch(/dependency_status|dependenciesLabel|dependencyHint/)
    // 状态列用渲染函数（h()）而非模板，data-test 是对象字面量的键：
    // `'data-test': 'resource-dependency-hint'`。原先只查 HTML 属性形态
    // （`data-test="..."`），匹配不到这种写法。
    expect(view).toMatch(/['"]data-test['"]\s*:\s*['"]resource-dependency-hint['"]/)
  })

  it('提示里说得出缺哪一个依赖的名字', () => {
    // 只说「依赖未就绪」等于没说：用户需要知道去装什么。
    expect(view).toMatch(/system_dependencies|dependencyNames|missingDependencies/)
  })

  it('unknown 与 missing 分开表达', () => {
    // 「还没探测过」与「探测过、确实没有」是两种处境，处置不同。
    const at = view.indexOf('dependency')
    const region = view.slice(Math.max(0, at - 200))
    expect(region).toMatch(/'unknown'|"unknown"/)
    expect(region).toMatch(/'missing'|"missing"/)
  })
})
