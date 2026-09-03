// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 「发现并安装资源」的结果必须是响应式卡片网格，两个来源同一种形态。
 *
 * 参考界面把发现结果做成三列网格卡片，每张卡片带名称、来源坐标、描述摘要与
 * 查看/安装操作区，窄屏降列（`docs/superpowers/plans/ccs-ui-notes.md` 的
 * `Image_2026-08-23_033110_034` 与 `Image_2026-08-23_033131_088` 两条）。
 * 此前这里是单列的分隔行：一屏只能比较两三个候选，而「发现」这件事的本质就是
 * **横向比较**——同一个关键词返回十几个来源不同的同名技能时，逐行下拉看不出差别。
 *
 * 另一半是两个来源形态不一致：内置目录用 `article` 行，skills.sh 用 `n-list`。
 * 同一个面板里切换来源时布局整体换一次，读起来像换了一个页面，
 * 而两者的操作（查看、安装）其实完全相同。
 *
 * 这组用例钉住的是**形态与响应式**，不是具体的像素值：
 * 网格由 `auto-fill` + `minmax` 决定列数（而不是写死 3 列——写死会在窄屏留下
 * 挤成一条的卡片），且窄屏有降列断点。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const viewSource = read('../src/views/resources/ResourceView.vue')

describe('发现结果的卡片网格', () => {
  it('用 auto-fill + minmax 决定列数，而不是写死列数', () => {
    // 写死 3 列会在窄屏把卡片挤成一条，操作区先被压掉——
    // 而操作区正是这个面板唯一的目的（装还是不装）。
    expect(viewSource).toMatch(/\.remote-results\s*\{[^}]*repeat\(auto-fill,\s*minmax\(/)
  })

  it('卡片有边框与内边距，不是靠分隔线区分的行', () => {
    // 分隔行在两列以上时读不出「哪几段属于同一个候选」：
    // 横向相邻的两张卡之间没有任何视觉边界。
    expect(viewSource).toMatch(/\.remote-result\s*\{[^}]*border:/)
    expect(viewSource).toMatch(/\.remote-result\s*\{[^}]*padding:/)
  })

  it('卡片内竖向排布，操作区在底部', () => {
    // 名称、描述与操作区在一张窄卡里必须竖排；横排会让描述只剩一两个字。
    expect(viewSource).toMatch(/\.remote-result\s*\{[^}]*flex-direction:\s*column/)
  })

  it('窄屏降到单列', () => {
    expect(viewSource).toMatch(/@media[^{]*max-width:\s*(?:6|7)\d\dpx[^{]*\{[\s\S]{0,900}?\.remote-results\s*\{[^}]*1fr/)
  })

  it('两个来源用同一个卡片组件形态', () => {
    // skills.sh 此前用 `n-list`：同一个面板里切来源时布局整体换一次，
    // 读起来像换了一个页面，而两者的操作完全相同。
    expect(viewSource).not.toMatch(/skillsShResults[\s\S]{0,200}<n-list\b/)
    const cardOpenings = viewSource.match(/class="remote-result"/g) || []
    expect(cardOpenings.length).toBeGreaterThanOrEqual(2)
  })

  it('skills.sh 卡片保留安装入口与下载量', () => {
    expect(viewSource).toContain('data-test="install-skills-sh-result"')
    expect(viewSource).toMatch(/skillsShResults[\s\S]{0,1500}次安装/)
  })

  it('目录卡片保留查看、安装与「已安装未启用」的启用入口', () => {
    expect(viewSource).toContain('data-test="catalog-enable"')
    expect(viewSource).toMatch(/remoteResults[\s\S]{0,2000}aria-label="查看目录资源"/)
  })

  it('描述摘要限高截断，卡片高度不被一段长描述拉开', () => {
    // 网格里一张卡变高会把整行拉高，其余卡片下方留出大片空白。
    expect(viewSource).toMatch(/-webkit-line-clamp|line-clamp/)
  })
})
