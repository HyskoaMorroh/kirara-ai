// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 登记过的技能仓库必须能从界面删掉。
 *
 * 参考界面的仓库管理页每一行右侧有两个图标按钮：打开仓库与**删除仓库**，
 * 笔记还写明「删除属于有影响的操作，应有确认与失败反馈」
 * （`docs/superpowers/plans/ccs-ui-notes.md` 的 `Image_2026-08-23_033146_102`）。
 *
 * 本项目此前只有「登记」与「启停」。「停用就够了」不成立：停用表达的是
 * 「这个来源暂时不用」，删除表达的是「这个来源是错的 / 不再存在」。一个拼错的
 * 坐标（`anthropcis/skills`）可以被停用，但那条记录再也去不掉——仓库表上永远
 * 多一行说明不了任何事的死项，想清掉只能登服务器手改 JSON。
 *
 * 这组用例钉住的是行为：有删除入口、要二次确认、确认文案说清**不动已装资源**
 * （「删除仓库」这四个字读起来像会一起删掉从它装过的东西，而那是用户按这个按钮
 * 之前最想知道的事）、以及删完刷新列表。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('API 客户端', () => {
  it('声明了 removeRepository', () => {
    expect(apiSource).toMatch(/export async function removeRepository/)
  })

  it('打到按坐标寻址的 DELETE 路由', () => {
    expect(apiSource).toMatch(
      /removeRepository[\s\S]{0,400}http\.delete[\s\S]{0,200}\/resources\/repositories\//
    )
  })

  it('三段坐标都做 URL 编码', () => {
    // 分支名可以含 `/`（例如 `release/1.x`）。不编码时它会被当成路径分隔符，
    // 请求打到一个不存在的路由上，错误信息与「仓库不存在」无法区分。
    const block = apiSource.slice(apiSource.indexOf('export async function removeRepository'))
    const encoded = block.slice(0, 500).match(/encodeURIComponent/g) || []
    expect(encoded.length).toBeGreaterThanOrEqual(3)
  })

  it('提交显式确认标记', () => {
    // 后端在 `confirmed !== true` 时返回 400。前端不传就等于这个按钮永远失败，
    // 而错误信息（「需要确认」）对用户毫无意义——他明明点了确认框。
    expect(apiSource).toMatch(/removeRepository[\s\S]{0,500}confirmed:\s*true/)
  })
})

describe('界面入口', () => {
  it('仓库行有移除按钮', () => {
    // 这一列是 `h()` 渲染函数产出的，`data-test` 以对象属性形式出现
    // （`'data-test': 'remove-repository'`），不是模板里的 `data-test="..."`。
    // 钉住模板写法会让这条断言在渲染函数实现下恒假。
    expect(viewSource).toMatch(/'data-test':\s*'remove-repository'/)
  })

  it('按钮用危险色，与「停用」区分开', () => {
    // 两个按钮相邻且都改变来源可用性，同色会让不可逆的那个看起来和可逆的一样。
    expect(viewSource).toMatch(/remove-repository[\s\S]{0,200}|type: 'error'[\s\S]{0,200}remove-repository/)
    const block = viewSource.slice(viewSource.indexOf("'data-test': 'remove-repository'") - 300)
    expect(block.slice(0, 400)).toMatch(/type: 'error'/)
  })

  it('走二次确认而不是直接删', () => {
    expect(viewSource).toMatch(/removeRepositoryRow[\s\S]{0,200}ask\(/)
  })

  it('确认文案说清不会动已装资源', () => {
    // 「删除仓库」读起来像会一起删掉装过的东西——这是按下按钮前最需要澄清的一点。
    expect(viewSource).toMatch(/已经装好的资源不受影响/)
  })

  it('确认文案带上分支', () => {
    // 同一个 owner/name 的不同分支是两条独立记录，不说分支等于让用户猜删的是哪条。
    expect(viewSource).toMatch(/removeRepositoryRow[\s\S]{0,600}repository\.branch/)
  })

  it('删完刷新仓库列表', () => {
    expect(viewSource).toMatch(/removeRepository\([\s\S]{0,400}loadRepositories\(\)/)
  })

  it('失败时有提示而不是静默', () => {
    expect(viewSource).toMatch(/removeRepositoryRow[\s\S]{0,600}'移除仓库失败'/)
  })
})
