// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 10 点名的 Skills 管理动作必须**在界面上够得到**，不能只有后端与
 * 客户端封装（那等于「只能 curl」）。
 *
 * 一次审计发现四条能力属于「后端有、封装有、界面无调用方」：
 *
 * | 能力 | 客户端封装 | 需求原文 |
 * | --- | --- | --- |
 * | 从 ZIP 安装（新资源） | `installResource` | 「从ZIP安装」 |
 * | 上传 ZIP 升级到新版本 | `updateResource` | 「检查更新」的离线对应动作 |
 * | 回滚到历史版本 | `restoreResource` | 22.3 的「可回滚机制」 |
 * | 仓库直查（列出某仓库下的 Skill） | `discoverRepository` | 「发现技能」 |
 *
 * 注意与「导入已有」的区别：`importResource`（`/resources/imports`）走的是
 * 服务器暂存目录，语义是「把一个已经准备好的包纳管」；`installResource`
 * （`POST /resources`）是直接安装一个新资源。两者后端是两个端点、两套语义，
 * 界面此前只接了前者，于是「从 ZIP 安装」这条需求实际落在了「导入」按钮上。
 *
 * 这些用例断言界面源码里真的调用了这四个封装，并且危险动作带确认。
 */

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(
  resolve(here, '../src/views/resources/ResourceView.vue'),
  'utf-8'
)

describe('Skills management actions are reachable from the UI', () => {
  it('imports every action it is supposed to offer', () => {
    for (const symbol of [
      'installResource',
      'importResource',
      'updateResource',
      'restoreResource',
      'discoverRepository'
    ]) {
      expect(view, `${symbol} 未被界面引用`).toContain(symbol)
    }
  })

  it('calls install and import through separate entry points', () => {
    // 两个后端端点语义不同（直接安装 vs 纳管暂存包），界面不能只接一个。
    expect(view).toMatch(/installResource\(/)
    expect(view).toMatch(/importResource\(/)
  })

  it('offers an upload-a-new-version action', () => {
    expect(view).toMatch(/updateResource\(/)
    expect(view).toContain('data-test="upload-version"')
  })

  it('offers a rollback action for an installed resource', () => {
    expect(view).toMatch(/restoreResource\(/)
    expect(view).toContain('data-test="rollback-version"')
  })

  it('offers repository discovery separate from catalog search', () => {
    expect(view).toMatch(/discoverRepository\(/)
    // 仓库表格的操作列是 `h()` 渲染函数，标记写成对象属性
    // （`'data-test': 'discover-repository'`）而不是模板属性，
    // 因此这里同时接受两种写法，只断言标记存在。
    expect(view).toMatch(/data-test["']?\s*[:=]\s*["']discover-repository["']/)
  })
})

describe('an enabled resource that nothing binds is marked as not in effect', () => {
  it('shows a distinct badge instead of only 已启用', () => {
    // 需求 22.3：装好并启用之后界面显示「已启用」，但一个 Skill 只有被绑定到
    // 某个 Agent 之后才会进入 LLM 请求。不说出这件事的话，用户看到「已启用」
    // 却得到「什么都没变」，然后去怀疑模型或提示词。
    expect(view).toContain('in_effect')
    // 状态列是 `h()` 渲染函数，标记写成对象属性而不是模板属性
    // （与 discover-repository 同一形态）。
    expect(view).toMatch(/data-test["']?\s*[:=]\s*["']not-in-effect["']/)
    expect(view).toContain('未生效')
    // 提示必须给出下一步，而不只是标一个状态。
    expect(view).toMatch(/没有任何 Agent 绑定/)
  })

  it('says nothing when the binding state is unknown', () => {
    // `in_effect` 缺失表示「读不到 Agent 注册表」。把「不知道」显示成「未生效」
    // 等于给出一个没有依据的论断，因此判断必须是严格的 `=== false`。
    expect(view).toMatch(/row\.in_effect === false/)
    expect(view).not.toMatch(/!row\.in_effect/)
  })
})

describe('destructive resource actions ask first', () => {  it('confirms before installing an uploaded archive', () => {
    const block = view.slice(view.indexOf('installResource('))
    // 安装会把包解到服务器磁盘上，必须先确认。
    expect(view).toMatch(/ask\(\{[\s\S]*?installResource/)
    expect(block.length).toBeGreaterThan(0)
  })

  it('confirms before rolling a resource back', () => {
    // 回滚会改变当前生效版本，且资源随后被停用等待再确认。
    expect(view).toMatch(/ask\(\{[\s\S]*?restoreResource/)
  })

  it('confirms before uploading a new version', () => {
    expect(view).toMatch(/ask\(\{[\s\S]*?updateResource/)
  })
})
