// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  canInstallStaged,
  stagedActionLabel,
  stagedStatus,
  type StagedArchive
} from '../src/views/resources/stagedArchives'

/**
 * 服务器待安装包（`resources/imports`）的三个判断，按**行为**验证。
 *
 * `stagedArchives.ts` 早已是纯函数，但没有一条测试调用过它——
 * `resource-staged-imports.test.ts` 的 24 条断言全是源码 grep，其中三条
 * 直接把表达式当字符串钉住：
 *
 *     toMatch(/entry\.installed && !entry\.is_upgrade/)
 *     toMatch(/!!entry\.error/)
 *     toMatch(/entry\.is_upgrade \? '更新' : '安装'/)
 *
 * 这三条既拦不住错、又拦得住对：把 `&&` 写成 `||`（于是**任何**已安装的包
 * 都点不动，包括可升级的）字符串还在；而把 `!!entry.error` 重构成
 * `Boolean(entry.error)`——一次完全等价的改写——测试反而红。
 *
 * 这一页错了的后果很具体：按钮该不该能点、点了做什么。禁错了用户装不上，
 * 放开了他会拿到一句「版本必须递增」，而两者都读不出真实原因。
 */

const entry = (over: Partial<StagedArchive> = {}): StagedArchive => ({
  file_name: 'demo-1.0.0.zip',
  ...over
})

describe('能不能点安装', () => {
  it('全新的包可以装', () => {
    expect(canInstallStaged(entry())).toBe(true)
    expect(canInstallStaged(entry({ installed: false }))).toBe(true)
  })

  it('已装且不是升级时禁用', () => {
    // 装一遍只会得到「版本必须递增」——那句话与用户所做无关。
    expect(canInstallStaged(entry({ installed: true, is_upgrade: false }))).toBe(false)
  })

  it('已装但盘上是更高版本时**可以**点', () => {
    // 这一条区分得出 `&&` 与 `||`：写成 `||` 时它红。
    // 而那正是最要紧的处境——运维 scp 上来一个新版本就是为了升级。
    expect(canInstallStaged(entry({ installed: true, is_upgrade: true }))).toBe(true)
  })

  it('读不出来的包禁用', () => {
    // 后端连清单都没解析成功，点下去必定失败，
    // 且失败信息会指向解包而不是「这个文件坏了」。
    expect(canInstallStaged(entry({ error: 'manifest is invalid' }))).toBe(false)
  })

  it('坏包即使标着可升级也禁用', () => {
    // 两个条件的**优先级**：错误压过升级。反过来会让一个坏包看着能点。
    expect(
      canInstallStaged(entry({ error: 'bad zip', installed: true, is_upgrade: true }))
    ).toBe(false)
  })

  it('空字符串的 error 不算错误', () => {
    // 后端对「没有错误」返回 `null`；某些序列化会给空串。
    // 把空串当错误会让所有正常包都禁用。
    expect(canInstallStaged(entry({ error: '' }))).toBe(true)
    expect(canInstallStaged(entry({ error: null }))).toBe(true)
  })
})

describe('按钮文案', () => {
  it('升级写「更新」', () => {
    // 写「安装」会让人以为装出第二份，而实际是给同一个资源加一个新版本
    // 并自动备份旧版。
    expect(stagedActionLabel(entry({ is_upgrade: true }))).toBe('更新')
  })

  it('其余情况写「安装」', () => {
    expect(stagedActionLabel(entry())).toBe('安装')
    expect(stagedActionLabel(entry({ is_upgrade: false }))).toBe('安装')
    expect(stagedActionLabel(entry({ installed: true }))).toBe('安装')
  })
})

describe('状态标签', () => {
  it('四种状态各自区分', () => {
    expect(stagedStatus(entry())).toBe('new')
    expect(stagedStatus(entry({ installed: true }))).toBe('installed')
    expect(stagedStatus(entry({ installed: true, is_upgrade: true }))).toBe('upgradable')
    expect(stagedStatus(entry({ error: 'bad' }))).toBe('error')
  })

  it('可升级优先于已安装', () => {
    // 一个可升级的包**也**是已安装的。判断顺序写反会让所有可升级的包
    // 都显示「已安装」，用户于是不会去点。
    expect(stagedStatus(entry({ installed: true, is_upgrade: true }))).toBe('upgradable')
  })

  it('错误优先于其余一切', () => {
    expect(
      stagedStatus(entry({ error: 'bad', installed: true, is_upgrade: true }))
    ).toBe('error')
  })

  it('标了可升级但没标已安装时仍算可升级', () => {
    // 后端只在有已装版本时才置 `is_upgrade`，所以这是不该出现的组合。
    // 真出现时按可升级处理——那时按钮可点，比显示成「全新」更接近事实。
    expect(stagedStatus(entry({ is_upgrade: true }))).toBe('upgradable')
  })
})

describe('三个判断彼此一致', () => {
  it('禁用的行不会给出「更新」这种诱导性文案之外的矛盾', () => {
    // 遍历八种组合，锁住一条不变式：
    // 状态为 installed 或 error 时按钮必须禁用；其余必须可点。
    for (const installed of [true, false]) {
      for (const isUpgrade of [true, false]) {
        for (const error of ['', 'boom']) {
          const item = entry({ installed, is_upgrade: isUpgrade, error })
          const status = stagedStatus(item)
          const clickable = canInstallStaged(item)
          const expected = status === 'upgradable' || status === 'new'
          expect(clickable, `${JSON.stringify(item)} 状态 ${status}`).toBe(expected)
        }
      }
    }
  })
})
