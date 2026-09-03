// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'

import {
  QR_STATE_TEXT,
  qrLoginTag,
  qrRemainingSeconds
} from '../src/views/im/qrLoginPresentation'

/**
 * QQ 扫码状态按**行为**验证。
 *
 * 替换的是 `im-qr-countdown.test.ts` 与 `im-qr-login-status.test.ts` 里那些
 * 源码 grep，例如：
 *
 *     expect(viewSource).toMatch(/qr\.state === 'waiting_scan' \? qrRemainingSeconds/)
 *     expect(viewSource).toMatch(/qrCountdownExpired|remaining\w*\s*<=\s*0/)
 *
 * 第二条尤其能说明问题：它用 `|` 接受两种写法，于是把 `<= 0` 改成 `< 0`
 * 仍然匹配——而那正是「归零后还显示待扫码」这个 bug 的形态。
 *
 * 这个功能的后果很具体：二维码只有 120 秒有效期。算错倒计时或归零后不改口，
 * 用户会去扫一张已经过期的码，而他此刻该做的是点「刷新扫码状态」。
 */

/** 固定的「现在」，让断言不依赖真实时钟。 */
const NOW = Date.parse('2026-09-03T12:00:00Z')
const inSeconds = (seconds: number) =>
  new Date(NOW + seconds * 1000).toISOString()

const snapshot = (over: Record<string, unknown> = {}) => ({
  state: 'waiting_scan',
  expires_at: inSeconds(90),
  validity_seconds: 120,
  refresh_count: 0,
  ...over
})

describe('剩余秒数', () => {
  it('按 expires_at 与当前时刻算', () => {
    expect(qrRemainingSeconds(snapshot(), NOW)).toBe(90)
  })

  it('单位是秒，不是毫秒', () => {
    // 除以 1000 写成乘以 1000 会得到 9e7，界面显示「剩 90000000 秒」。
    // 源码 grep 看不出这种错。
    expect(qrRemainingSeconds(snapshot({ expires_at: inSeconds(30) }), NOW)).toBe(30)
  })

  it('已经过期时钳到 0，而不是给出负数', () => {
    // 过期多久不是用户要的信息，「已过期」才是。
    expect(qrRemainingSeconds(snapshot({ expires_at: inSeconds(-45) }), NOW)).toBe(0)
  })

  it('没有 expires_at 时返回 null，不编一个数字', () => {
    expect(qrRemainingSeconds(snapshot({ expires_at: null }), NOW)).toBeNull()
    expect(qrRemainingSeconds(snapshot({ expires_at: '' }), NOW)).toBeNull()
  })

  it('时间戳解析不了时返回 null', () => {
    expect(qrRemainingSeconds(snapshot({ expires_at: 'soon' }), NOW)).toBeNull()
  })
})

describe('状态标签', () => {
  it('待扫码时带上倒计时', () => {
    const tag = qrLoginTag(snapshot(), NOW)
    expect(tag?.label).toBe('待扫码（剩 90 秒）')
    expect(tag?.type).toBe('warning')
  })

  it('倒计时归零后改口为已过期', () => {
    // 这是这组测试的核心断言。继续显示「待扫码（剩 0 秒）」是把过期说成可扫。
    const tag = qrLoginTag(snapshot({ expires_at: inSeconds(-1) }), NOW)
    expect(tag?.label).toBe('二维码已过期')
    expect(tag?.type).toBe('error')
  })

  it('恰好归零的那一刻就算过期', () => {
    // `<= 0` 与 `< 0` 的分界。写成 `< 0` 时这一条红，而源码 grep 测不出来。
    const tag = qrLoginTag(snapshot({ expires_at: inSeconds(0) }), NOW)
    expect(tag?.label).toBe('二维码已过期')
  })

  it('过期后给出可执行的下一步', () => {
    // 「已过期」本身不告诉用户该做什么。
    const tag = qrLoginTag(snapshot({ expires_at: inSeconds(-5) }), NOW)
    expect(tag?.title).toContain('刷新扫码状态')
  })

  it('age_unknown 不拼任何秒数', () => {
    // 读不到时效时，一个编出来的倒计时说的谎恰好是「还来得及」。
    const tag = qrLoginTag(snapshot({ state: 'age_unknown', expires_at: null }), NOW)
    expect(tag?.label).toBe('二维码时效未知')
    expect(tag?.label).not.toMatch(/\d/)
  })

  it('待扫码但读不到 expires_at 时也不拼数字', () => {
    const tag = qrLoginTag(snapshot({ expires_at: null }), NOW)
    expect(tag?.label).toBe('待扫码')
  })

  it('非待扫码状态不显示倒计时', () => {
    // 已扫码之后剩余秒数无意义——码已经用掉了。
    const tag = qrLoginTag(snapshot({ state: 'scanned' }), NOW)
    expect(tag?.label).toBe('已扫码待确认')
  })

  it('没有快照或状态未知时不显示任何标签', () => {
    // 显示「未知」会让用户以为出了问题，而真实原因是没配上游日志路径。
    expect(qrLoginTag(null, NOW)).toBeNull()
    expect(qrLoginTag(undefined, NOW)).toBeNull()
    expect(qrLoginTag(snapshot({ state: 'unknown' }), NOW)).toBeNull()
  })

  it('无法识别的状态不显示标签，而不是显示原始字符串', () => {
    expect(qrLoginTag(snapshot({ state: 'brand_new_state' }), NOW)).toBeNull()
  })

  it('每种已知状态都有中文文案与档位', () => {
    for (const state of [
      'pending',
      'waiting_scan',
      'age_unknown',
      'scanned',
      'expired',
      'succeeded',
      'failed',
      'unavailable',
      'quick_login'
    ]) {
      expect(QR_STATE_TEXT[state], `状态 ${state} 缺少文案`).toBeTruthy()
      expect(QR_STATE_TEXT[state].label).not.toBe('')
    }
  })

  it('登录成功是 success 档，失败是 error 档', () => {
    // 档位决定颜色。把失败画成绿色比没有标签更糟。
    expect(QR_STATE_TEXT.succeeded.type).toBe('success')
    expect(QR_STATE_TEXT.failed.type).toBe('error')
    expect(QR_STATE_TEXT.expired.type).toBe('error')
  })
})

describe('标签详情', () => {
  it('带上有效期、路径与刷新次数', () => {
    const tag = qrLoginTag(
      snapshot({ latest_qr_path: '/data/qr.png', refresh_count: 3 }),
      NOW
    )
    expect(tag?.title).toContain('有效期 120 秒')
    expect(tag?.title).toContain('/data/qr.png')
    expect(tag?.title).toContain('已刷新 3 次')
  })

  it('没刷新过时不显示「已刷新 0 次」', () => {
    const tag = qrLoginTag(snapshot({ refresh_count: 0 }), NOW)
    expect(tag?.title).not.toContain('已刷新')
  })

  it('过期时的处置建议顶替普通 remediation', () => {
    // 过期这件事有唯一正确的下一步，比后端给的通用建议更具体。
    const tag = qrLoginTag(
      snapshot({ expires_at: inSeconds(-10), remediation: '请检查上游日志路径' }),
      NOW
    )
    expect(tag?.title).toContain('刷新扫码状态')
    expect(tag?.title).not.toContain('请检查上游日志路径')
  })

  it('未过期时显示后端给的 remediation', () => {
    const tag = qrLoginTag(snapshot({ remediation: '请检查上游日志路径' }), NOW)
    expect(tag?.title).toContain('请检查上游日志路径')
  })
})

describe('倒计时随时间推进', () => {
  it('同一份快照在不同时刻给出不同秒数', () => {
    // 后端的 `remaining_seconds` 是取快照那一刻的读数；界面必须自己走。
    const qr = snapshot({ expires_at: inSeconds(100) })
    expect(qrLoginTag(qr, NOW)?.label).toBe('待扫码（剩 100 秒）')
    expect(qrLoginTag(qr, NOW + 60_000)?.label).toBe('待扫码（剩 40 秒）')
    expect(qrLoginTag(qr, NOW + 101_000)?.label).toBe('二维码已过期')
  })
})
