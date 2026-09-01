import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * 发现面板有两个来源，翻页和「装完刷新」必须跟着当前来源走。
 * 写死 searchRemote 的话，在 skills.sh 来源下点翻页会去翻内置目录，
 * 结果区却渲染 skillsShResults —— 看起来就是「点了没反应」。
 */
const root = resolve(__dirname, '..')
const view = readFileSync(resolve(root, 'src/views/resources/ResourceView.vue'), 'utf-8')

// 页面上有多个分页控件（审计日志也有一个），按 remoteOffset 认出发现面板那一个。
const discoverPagination = [...view.matchAll(/<n-pagination[\s\S]*?\/>/g)]
  .map((match) => match[0])
  .find((tag) => tag.includes('remoteOffset'))

describe('发现面板的来源一致性', () => {
  it('翻页走统一入口，而不是写死内置目录', () => {
    expect(discoverPagination, '找不到发现面板的分页控件').toBeTruthy()
    expect(discoverPagination!).toMatch(/runDiscoverSearch\(/)
    expect(discoverPagination!).not.toMatch(/searchRemote\(/)
  })

  it('总数按来源取，否则 skills.sh 的分页页数是内置目录的', () => {
    expect(view).toMatch(/const discoverTotal = computed\(/)
    expect(discoverPagination!, '分页控件仍在用 remoteTotal，skills.sh 结果数对不上').toMatch(
      /discoverTotal/
    )
    expect(discoverPagination!).not.toMatch(/remoteTotal/)
  })

  it('安装或启用后的刷新也跟着来源，否则刚装的技能不会从列表里更新', () => {
    // searchRemote 只剩一处出现：runDiscoverSearch 里的分派（它自己的定义是
    // `const searchRemote = async`，不带调用括号，不计入）。
    const calls = [...view.matchAll(/searchRemote\(/g)]
    expect(
      calls.length,
      `searchRemote 仍有 ${calls.length} 处直接调用，应改走 runDiscoverSearch`
    ).toBe(1)
  })
})
