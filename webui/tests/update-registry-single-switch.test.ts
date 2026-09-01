/**
 * 「禁用自动检查」在更新设置卡里只能出现一次（需求 8 的「禁用自动升级」）。
 *
 * 现状是同一个 `disable_auto_check` 字段被渲染了两遍：两个 `n-form-item`、
 * 两个 `data-test="disable-auto-check"` 的开关，标签还不一样（「禁用自动检查更新」
 * 与「禁用自动检查」）。两个开关绑同一个 `v-model`，所以拨动其中一个，另一个
 * 会跟着动 —— 用户看到的是「我关了一个，另一个自己也变了」，很自然地读成
 * 界面有 bug 或者配置没保存住。
 *
 * 同时这让任何按 `data-test` 取开关的测试都取到长度为 2 的集合，
 * `.trigger()` 只作用在第一个上，断言却是对的 —— 一个会掩盖真问题的假绿。
 *
 * 判据：**一个配置字段对应一个控件。** 说明文字可以长，控件不能有两份。
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const source = (): string =>
  readFileSync(
    resolve(__dirname, '../src/views/settings/components/UpdateRegistryCard.vue'),
    'utf-8'
  )

/** 统计非重叠出现次数。 */
const countOf = (haystack: string, needle: string): number =>
  haystack.split(needle).length - 1

describe('UpdateRegistryCard 的自动检查开关', () => {
  it('data-test="disable-auto-check" 只出现一次', () => {
    expect(countOf(source(), 'data-test="disable-auto-check"')).toBe(1)
  })

  it('disable_auto_check 只绑定一次 v-model', () => {
    expect(countOf(source(), 'v-model:value="formData.disable_auto_check"')).toBe(1)
  })

  it('path="disable_auto_check" 的表单项只有一个', () => {
    expect(countOf(source(), 'path="disable_auto_check"')).toBe(1)
  })

  it('保留的那一份说明里写清了手动检查仍然可用', () => {
    // 这句不是文案偏好：不写，「禁用自动检查」会被读成「禁用检查」，
    // 于是最需要这个开关的离线部署反而不敢打开它。
    const text = source()
    expect(text).toMatch(/手动检查|仍可|不受影响/)
  })
})
