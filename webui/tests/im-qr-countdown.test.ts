import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 二维码有效期只有 120 秒，界面必须让用户判断「现在扫还来不来得及」（需求 3）。
 *
 * 现场报障是「二维码总是过期，无法登录」。后端 `QRLoginSnapshot` 一直返回
 * `validity_seconds`、`generated_at`、`expires_at` 与 `remaining_seconds`，
 * 而界面上只渲染一个静态的「剩 N 秒」——**没有倒计时、没有轮询**。用户走开去
 * 拿手机再回来，屏幕上还写着「剩 92 秒」，而那张码早已失效。
 *
 * 三件事因此必须钉住：
 *
 * - **倒计时要自己走。** 一次性渲染的数字在 120 秒的尺度上必然说谎，
 *   而它说的谎恰好是「还来得及」。
 * - **归零后要改口。** 数字走到 0 时标签必须从「待扫码」变成「已过期」，
 *   并告诉用户去刷新——继续显示「待扫码（剩 0 秒）」是把过期说成可扫。
 * - **`age_unknown` 不能显示成剩余时间。** 上游日志没有时间戳时后端返回
 *   `remaining_seconds: null`，此时任何数字都是编的。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/im.ts')
const viewSource = read('../src/views/im/IMAdapterDetail.vue')

describe('类型声明', () => {
  it('包含 age_unknown 状态', () => {
    // 后端会返回它；类型里没有，编译期就到不了渲染层。
    expect(apiSource).toContain("'age_unknown'")
  })
})

/**
 * 倒计时的**算法**由 `im-qr-login-logic.test.ts` 调用函数验证：单位是秒不是毫秒、
 * 恰好归零算过期、`age_unknown` 不拼数字、同一份快照在不同时刻给出不同秒数。
 *
 * 本文件只留「必须写在组件里才成立」的两件事：会自己走的计时器，以及卸载时清掉它。
 * 这两条无法靠纯函数覆盖——它们是组件生命周期。
 *
 * 原来这里有 `toMatch(/qrCountdownExpired|remaining\w*\s*<=\s*0/)`：
 * 用 `|` 接受两种写法，于是把 `<= 0` 改成 `< 0` 仍然匹配——
 * 而那正是「归零后还显示待扫码」这个 bug 的形态。
 */
describe('倒计时的组件部分', () => {
  it('有一个会自己走的计时器', () => {
    expect(viewSource).toMatch(/setInterval|useIntervalFn/)
  })

  it('计时器在组件卸载时被清掉', () => {
    // 留着的定时器会在离开页面后继续跑，并持有对已卸载组件的引用。
    expect(viewSource).toMatch(/onUnmounted|clearInterval/)
  })

  it('每一拍推进的是那个「现在」，而不是重新拉接口', () => {
    // 倒计时靠本地时钟走：每秒打一次后端只为了刷新一个数字，
    // 会把一个诊断页变成压测。
    expect(viewSource).toMatch(/now\.value = Date\.now\(\)/)
  })

  it('倒计时与标签接在那份逻辑上', () => {
    expect(viewSource).toMatch(/from '\.\/qrLoginPresentation'/)
    expect(viewSource).toMatch(/computeQrRemainingSeconds\(qr, now\.value\)/)
    expect(viewSource).toMatch(/buildQrLoginTag\(adapter\.health\?\.qr_login, now\.value\)/)
  })
})
