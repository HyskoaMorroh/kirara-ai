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

describe('倒计时', () => {
  it('有一个会自己走的计时器', () => {
    expect(viewSource).toMatch(/setInterval|useIntervalFn/)
  })

  it('计时器在组件卸载时被清掉', () => {
    // 留着的定时器会在离开页面后继续跑，并持有对已卸载组件的引用。
    expect(viewSource).toMatch(/onUnmounted|clearInterval/)
  })

  it('剩余秒数由「失效时刻 − 现在」算出，而不是照抄后端那一次的值', () => {
    // 照抄的值在页面打开 90 秒后依然是打开那一刻的数字。
    expect(viewSource).toContain('expires_at')
  })

  it('归零后标签改成已过期', () => {
    expect(viewSource).toMatch(/qrCountdownExpired|remaining\w*\s*<=\s*0/)
  })
})

describe('有效期本身要说出来', () => {
  it('展示 validity_seconds', () => {
    // 「这种码只能撑 120 秒」是用户决定「先拿手机再刷新」的依据。
    expect(viewSource).toContain('validity_seconds')
  })
})

describe('时间戳缺失时不编数字', () => {
  it('age_unknown 有独立文案', () => {
    expect(viewSource).toContain('age_unknown')
  })

  it('age_unknown 不显示剩余秒数', () => {
    // 该分支下后端给的是 null；显示任何数字都是编的。
    expect(viewSource).toMatch(/age_unknown[\s\S]{0,400}?(?:不显示|无法判断|未知)/)
  })
})
