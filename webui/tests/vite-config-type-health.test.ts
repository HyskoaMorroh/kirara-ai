/**
 * `vite.config.ts` 必须能通过类型检查。
 *
 * 这个文件此前从未被检查过：`npm run type-check` 是裸 `vue-tsc --noEmit`,在
 * solution-style 根配置下零文件输入,恒绿。修好门禁后 `tsconfig.node.json` 暴露 3 条错误。
 *
 * 它不是普通源文件——构建入口写错会让 `vite build` 在 CI 上失败,而本地开发可能
 * 因为 esbuild 的宽松解析照样跑得起来。所以这份配置的类型健康度需要单独钉住。
 *
 * 三条错误各自的性质:
 * - `resolveJsonModule`: 配置缺失,不是代码问题。`import packageJson from './package.json'`
 *   是版本号注入的唯一来源(`VITE_APP_VERSION` 与 `version.json` 都从它取)。
 * - `this.emitFile`: rollup 插件的 `generateBundle` 里 `this` 是 PluginContext,
 *   但对象字面量没有类型标注时 TS 推不出来。缺它则 `version.json` 不会产出。
 * - esbuild Plugin 类型冲突: 顶层装了 0.25.3(被 @codingame 插件要求),
 *   vite 4 内嵌 0.18.20,两份 `Plugin` 类型不兼容。这是真实的版本分裂,
 *   不能靠 `as any` 假装同一个类型——那会让将来真正的签名变化也一起被吞掉。
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const nodeTsconfig = JSON.parse(
  readFileSync(resolve(root, 'tsconfig.node.json'), 'utf-8')
) as { include?: string[]; compilerOptions?: Record<string, unknown> }
const viteConfig = readFileSync(resolve(root, 'vite.config.ts'), 'utf-8')

describe('vite.config.ts 类型健康度', () => {
  it('自检:vite.config.ts 在 node 项目的检查范围内', () => {
    expect(nodeTsconfig.include?.some((glob) => glob.startsWith('vite.config'))).toBe(true)
  })

  it('resolveJsonModule 已开启,package.json 才能作为版本号来源导入', () => {
    expect(
      nodeTsconfig.compilerOptions?.resolveJsonModule,
      'vite.config.ts 从 ./package.json 读版本号；不开 resolveJsonModule 会报 TS2732'
    ).toBe(true)
  })

  it('版本元数据插件声明了 rollup Plugin 类型,this.emitFile 才可用', () => {
    // 不标类型时 `generateBundle` 里的 `this` 推不出 PluginContext，
    // `this.emitFile` 报 TS2339，而缺它 version.json 就不会产出。
    const at = viteConfig.indexOf('function versionMetadataPlugin')
    expect(at, '找不到 versionMetadataPlugin').toBeGreaterThan(-1)
    const body = viteConfig.slice(at, viteConfig.indexOf('\n}', at))
    expect(body, '插件返回值没有 rollup Plugin 类型标注').toMatch(/:\s*Plugin\b/)
  })

  it('esbuild 插件的类型冲突有显式说明,不是无声的 as any', () => {
    // 顶层 esbuild 与 vite 内嵌 esbuild 是两个大版本，Plugin 类型不兼容。
    // 断言要求：转换点必须带注释说明版本分裂，否则将来真的签名变化也会被吞掉。
    const at = viteConfig.indexOf('importMetaUrlPlugin')
    const region = viteConfig.slice(at)
    const usage = region.indexOf('plugins: [importMetaUrlPlugin')
    expect(usage, '找不到 importMetaUrlPlugin 的使用点').toBeGreaterThan(-1)
    const around = region.slice(Math.max(0, usage - 600), usage + 200)
    expect(around, 'esbuild 插件类型转换缺少说明注释').toMatch(/esbuild/i)
  })

  it('自检:两份 esbuild 确实并存,上一条断言的前提成立', () => {
    // 若哪天版本对齐了，转换和注释都该删掉，而不是留着。
    const top = resolve(root, 'node_modules/esbuild/package.json')
    const nested = resolve(root, 'node_modules/vite/node_modules/esbuild/package.json')
    if (!existsSync(top) || !existsSync(nested)) return
    const topVersion = JSON.parse(readFileSync(top, 'utf-8')).version as string
    const nestedVersion = JSON.parse(readFileSync(nested, 'utf-8')).version as string
    expect(
      topVersion.split('.')[1] !== nestedVersion.split('.')[1],
      `两份 esbuild 已对齐（${topVersion} / ${nestedVersion}），请移除类型转换与相关注释`
    ).toBe(true)
  })
})
