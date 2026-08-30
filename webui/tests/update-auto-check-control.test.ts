// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 需求 8「禁用自动升级」的界面这一跳。
 *
 * `update.disable_auto_check` 有真实消费点：`entry.py::check_update` 打开时
 * **完全不发起请求**——离线或内网部署既查不到注册表又要等一次超时，
 * 而「禁用」如果只是不打印结果，那条等待依然在启动路径上。
 *
 * 但通往它的路此前只有手改 config.yaml：`GET /system/config` 的 `update` 段
 * 不返回它，`POST /system/config/update` 收到也丢掉，前端 `UpdateForm`
 * 更没有这个键。后端两处已修（见
 * `tests/web/api/system/test_update_auto_check_config.py`），这里补界面。
 *
 * 一个必须写进说明的边界：关掉自动检查**不等于**关掉更新能力。
 * 「检查更新」按钮是用户主动发起的，不受这个开关影响；
 * 不写清楚，用户会以为自己再也收不到新版本了而不敢开。
 */

const here = dirname(fileURLToPath(import.meta.url))
const vmSource = readFileSync(
  resolve(here, '../src/views/settings/viewmodels/update.vm.ts'),
  'utf-8'
)
const cardSource = readFileSync(
  resolve(here, '../src/views/settings/components/UpdateRegistryCard.vue'),
  'utf-8'
)

describe('disable-auto-check control', () => {
  it('declares the field on the form type so it reaches the request body', () => {
    // `handleSubmit` 提交的就是 `formData` 本身；类型里没这个键，payload 里也不会有。
    const form = vmSource.match(/interface UpdateForm \{[\s\S]*?\n\}/)
    expect(form).not.toBeNull()
    expect(form?.[0]).toContain('disable_auto_check')
  })

  it('defaults to enabled auto-check', () => {
    // 默认值必须是「不禁用」：静默停掉版本检查会让用户长期停在旧版而不自知。
    const initial = vmSource.match(/ref<UpdateForm>\(\{[\s\S]*?\n\s*\}\)/)
    expect(initial).not.toBeNull()
    expect(initial?.[0]).toMatch(/disable_auto_check:\s*false/)
  })

  it('exposes a switch in the update-source card', () => {
    expect(cardSource).toContain('data-test="disable-auto-check"')
  })

  it('says the manual check button still works', () => {
    // 不说这句，用户会把「禁用自动升级」读成「禁用升级」。
    const index = cardSource.indexOf('data-test="disable-auto-check"')
    expect(index).toBeGreaterThan(-1)
    const block = cardSource.slice(index, index + 1200)
    expect(block).toContain('检查更新')
    expect(block).toMatch(/离线|内网/)
  })
})

/**
 * 上一组测的是「开关能填」。这一组测的是「开关的承诺成立」。
 *
 * 后端已按「自动不外呼、`?manual=1` 照常外呼」实现（见
 * `tests/web/api/system/test_update_auto_check_config.py::TestAutomaticChecksActuallyStop`）。
 * 前端若不带那个参数，手动按钮就会被当成自动调用挡掉——承诺当场作废。
 *
 * 另一半是那颗按钮本身。仓库里唯一写着「检查更新」的按钮在 `VersionCard.vue`，
 * 而它 **零挂载点**；`AboutView.vue` 是「施工中」占位页。也就是说
 * `global_config.py` 里「WebUI 的检查更新按钮仍然可用」这句话，
 * 在修好之前指向一个界面上并不存在的按钮。
 */
const systemVmSource = readFileSync(
  resolve(here, '../src/views/system/update.vm.ts'),
  'utf-8'
)

describe('manual update check', () => {
  it('passes manual=1 so the backend does not treat a click as an auto check', () => {
    expect(systemVmSource).toMatch(/manual=1/)
  })

  it('distinguishes "did not check" from "checked, no update"', () => {
    // 两者都让 backend_update_available 为 false，但只有后者能说「已是最新版本」。
    expect(systemVmSource).toContain('checked')
  })

  it('gives the manual click visible feedback when already up to date', () => {
    // 手动点一下什么都不发生，用户无法区分「已最新」与「按钮坏了」。
    expect(systemVmSource).toMatch(/已是最新版本/)
  })

  it('mounts a reachable manual-check button next to the switch', () => {
    // 按钮存在但没有挂载点，等于承诺了一个界面上不存在的入口。
    expect(cardSource).toContain('data-test="check-update-now"')
    expect(cardSource).toContain('UpdateChecker')
  })
})
