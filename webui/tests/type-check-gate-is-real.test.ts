/**
 * `npm run type-check` 必须真的检查文件。
 *
 * 起因:`tsconfig.json` 是 solution-style 配置(`"files": []` + `references`),
 * 而 `type-check` 脚本写的是裸 `vue-tsc --noEmit`——不带 `-p`、也不带 `--build`。
 * 这种组合下 TS 只加载根配置,`files: []` 意味着**零个输入文件**,references 不会被
 * 跟随。于是命令秒退、退出码 0、什么都没检查。
 *
 * 实测确认:在 `src/` 下写入 `const probe: number = 'definitely not a number'`,
 * `npm run type-check` 通过;`npx vue-tsc --noEmit -p tsconfig.app.json` 报 TS2322。
 *
 * 后果不是"少检查了一点":`.github/workflows/release-preflight.yml` 与
 * `quickstart-windows.yml` 都把 `yarn type-check` 当作发布门禁。一个恒绿的门禁
 * 比没有门禁更糟——它让每次发布都带着"类型检查已通过"的记录,而实际从未执行。
 * 本仓库真实存在的 37 条类型错误(含两个会在运行时抛 ReferenceError 的未定义名)
 * 就是这样长期通过发布检查的。
 *
 * 判据:**门禁必须能拒绝。** 一个永远不会失败的检查不是检查。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const pkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf-8')) as {
  scripts: Record<string, string>
}
const rootTsconfig = JSON.parse(
  readFileSync(resolve(root, 'tsconfig.json'), 'utf-8')
) as { files?: unknown[]; references?: unknown[] }

describe('type-check 门禁有效性', () => {
  it('自检:根 tsconfig 确实是 solution-style,自身不含输入文件', () => {
    // 这是前提。若哪天根配置改成直接 include src,下面的断言就该重新评估。
    expect(rootTsconfig.files).toEqual([])
    expect(Array.isArray(rootTsconfig.references)).toBe(true)
  })

  it('type-check 脚本存在', () => {
    expect(pkg.scripts['type-check']).toBeTypeOf('string')
  })

  it('type-check 不能是裸 vue-tsc --noEmit——那样零文件被检查', () => {
    const script = pkg.scripts['type-check']
    const isBareNoEmit =
      /\bvue-tsc\b/.test(script) && !/-p\b|--project\b|--build\b|-b\b/.test(script)
    expect(
      isBareNoEmit,
      `type-check 是 "${script}"：根 tsconfig 的 files 为空且不跟随 references，` +
        '这条命令一个文件都不会检查，却始终返回 0。' +
        '需要显式指定项目（-p tsconfig.app.json）或走 --build。'
    ).toBe(false)
  })

  it('应用代码的那份 tsconfig 被真正检查到', () => {
    const script = pkg.scripts['type-check']
    // 应用源码在 tsconfig.app.json 的 include 里；门禁必须覆盖它。
    expect(
      script,
      'type-check 没有覆盖 tsconfig.app.json，src/ 下的代码不在检查范围内'
    ).toMatch(/tsconfig\.app\.json|--build|-b\b/)
  })
})
