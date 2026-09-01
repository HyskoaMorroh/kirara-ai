import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * skills.sh 是活的远程来源（GET https://skills.sh/api/search），内置目录是一份 41 条的本地清单。
 * 两者不重叠：目录给的是挑好的常用件，skills.sh 才能搜到清单之外的长尾技能。
 * 后端路由与 API 封装都早就在，界面却只接了目录搜索 —— 能力可达但没有入口，
 * 从界面上看就是「搜不到的技能就是没有」。
 */
const root = resolve(__dirname, '..')
const resourceView = readFileSync(resolve(root, 'src/views/resources/ResourceView.vue'), 'utf-8')
const resourceApi = readFileSync(resolve(root, 'src/api/resource.ts'), 'utf-8')

describe('skills.sh 搜索必须有界面入口', () => {
  it('API 封装存在，这是前提', () => {
    expect(resourceApi).toMatch(/export async function searchSkills\(/)
    expect(resourceApi).toMatch(/resources\/skills-sh\/search/)
  })

  it('ResourceView 真的引入并调用了 searchSkills，而不是只留个封装', () => {
    expect(resourceView).toMatch(/\bsearchSkills\b/)
    // 引入之外还要有调用：只 import 不用会被 lint 拦掉，但也可能被写成死代码。
    const calls = [...resourceView.matchAll(/searchSkills\(/g)]
    expect(calls.length, 'searchSkills 被引入却没有调用点').toBeGreaterThan(0)
  })

  it('两个来源要能分别选择，否则用户无从知道自己搜的是哪一份数据', () => {
    // 目录搜索仍在（不能为了加新入口把旧的挤掉）。
    expect(resourceView).toMatch(/searchResourceCatalog\(/)
    // 来源切换控件。
    expect(resourceView).toMatch(/data-test="discover-source"/)
  })

  it('skills.sh 的结果能直接安装，搜到装不了等于没搜', () => {
    expect(resourceView).toMatch(/data-test="install-skills-sh-result"/)
  })
})
