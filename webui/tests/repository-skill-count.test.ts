// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 仓库列表要显示「识别到几个技能」。
 *
 * 参考界面的仓库管理页每一行是「仓库全名 + 分支 + 灰底徽章 `识别到 N 个技能`」
 * （`docs/superpowers/plans/ccs-ui-inventory.md` 4.2.2，截图里是 864 / 22 / 20 / 11）。
 *
 * 这个数字不是装饰：注册一个仓库之后，界面上此前完全看不出它有没有用——
 * 一个 owner/name 拼错、分支写错、或压根不含 `SKILL.md` 的仓库，与一个装着
 * 几百个技能的仓库长得一模一样，都只是「已启用」。用户要点进「发现」才知道，
 * 而那要出一次网、下载整个仓库归档。
 *
 * 三条边界钉在这里：类型里有这一项且可为 `null`、`null` 与 `0` 在界面上是两种
 * 不同的显示（「还没发现过」与「发现过、一个都没有」）、`0` 用告警色标出——
 * 它是「这个仓库配错了」唯一的线索。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('类型声明', () => {
  it('`ResourceRepository` 带 discovered_skills', () => {
    expect(apiSource).toMatch(/discovered_skills:\s*number\s*\|\s*null/)
  })
})

describe('仓库表格', () => {
  it('有「技能数」这一列', () => {
    expect(viewSource).toMatch(/title:\s*'技能数'/)
  })

  it('列绑到 discovered_skills 字段', () => {
    expect(viewSource).toMatch(/title:\s*'技能数'[\s\S]{0,200}key:\s*'discovered_skills'/)
  })

  it('「还没发现过」与「发现过是 0 个」显示不同', () => {
    // 合成一个数会让每个刚注册的仓库看起来都是配错的。
    expect(viewSource).toMatch(/未发现过/)
    expect(viewSource).toMatch(/识别到 \$\{row\.discovered_skills\} 个/)
  })

  it('`null` 判断同时覆盖 undefined', () => {
    // 后端旧注册表补的是 `null`，而一次字段名改动会让它变成 undefined——
    // 只判 `null` 时那种情况会渲染出 "识别到 undefined 个"。
    expect(viewSource).toMatch(
      /discovered_skills === null \|\| row\.discovered_skills === undefined/
    )
  })

  it('0 用告警色，非 0 用成功色', () => {
    // 0 是「这个仓库配错了」唯一的线索，与「有 864 个」同色等于没说。
    expect(viewSource).toMatch(/discovered_skills === 0 \? 'warning' : 'success'/)
  })

  it('表格宽度容得下新增的列', () => {
    // 不加宽时 naive-ui 会按容器宽度压缩每一列，先被吃掉的是最右侧的操作按钮。
    const declared = viewSource.match(/:data="repositories"[\s\S]{0,240}?:scroll-x="(\d+)"/)
    expect(declared, '仓库表没有声明 scroll-x').not.toBeNull()
    expect(Number(declared![1])).toBeGreaterThanOrEqual(760)
  })
})
