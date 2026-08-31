import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 扫码登录状态必须与适配器连接状态分开呈现。
 *
 * 「上游还没接进来」（waiting）和「上游接进来了但它自己还没登录 QQ」
 * （waiting_scan）的处置完全不同：一个查地址与 Token，一个去扫码。
 * 合并成一枚标签会让「只差扫码」被误读成「连不上」——这正是「重启后显示
 * 未连接」这类报障里最常见的误诊方向。
 *
 * 这里以源码为断言对象（组件依赖 naive-ui 与路由，单测里挂载成本远高于收益），
 * 覆盖的是「契约是否成立」：类型是否声明、状态是否全部有文案、是否独立成标签。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/im.ts')
const viewSource = read('../src/views/im/IMAdapterDetail.vue')

/** 后端 QRLoginState 的全部取值，必须与 kirara_ai/im/qr_login.py 一致。 */
const BACKEND_STATES = [
  'unknown',
  'pending',
  'waiting_scan',
  'scanned',
  'expired',
  'succeeded',
  'failed',
  'unavailable',
  'quick_login'
]

const BACKEND_FAILURE_REASONS = [
  'qr_code_unavailable',
  'no_saved_credential',
  'login_failed',
  'expired_without_scan'
]

describe('QR login snapshot typing', () => {
  it('declares every backend state', () => {
    for (const state of BACKEND_STATES) {
      expect(apiSource, `缺少状态 ${state}`).toContain(`'${state}'`)
    }
  })

  it('declares every backend failure reason', () => {
    for (const reason of BACKEND_FAILURE_REASONS) {
      expect(apiSource, `缺少失败原因 ${reason}`).toContain(`'${reason}'`)
    }
  })

  it('exposes qr_login on the adapter health type', () => {
    expect(apiSource).toMatch(/qr_login\?:\s*QRLoginSnapshot \| null/)
  })

  it('keeps every snapshot field the backend returns', () => {
    for (const field of [
      'generated_at',
      'expires_at',
      'validity_seconds',
      'remaining_seconds',
      'latest_qr_path',
      'refresh_count',
      'failure_reason',
      'last_event_at',
      'remediation'
    ]) {
      expect(apiSource, `缺少字段 ${field}`).toContain(field)
    }
  })
})

describe('QR login rendering', () => {
  it('gives every actionable state its own label', () => {
    // `unknown` 故意没有文案：未配置日志路径时不该显示任何扫码信息。
    for (const state of BACKEND_STATES.filter((s) => s !== 'unknown')) {
      expect(viewSource, `状态 ${state} 缺少展示文案`).toContain(`${state}:`)
    }
  })

  it('renders the QR state as a separate tag from the connection state', () => {
    expect(viewSource).toContain('qrLoginTag')
    expect(viewSource).toContain('status-tag qr-login')
    // 连接状态标签必须仍然存在且独立。
    expect(viewSource).toContain("['status-tag', adapterStatus(adapter).className]")
  })

  it('shows nothing when the snapshot is absent or unknown', () => {
    expect(viewSource).toMatch(/if \(!qr \|\| qr\.state === 'unknown'\) return null/)
  })

  it('surfaces the remediation text rather than only the state name', () => {
    expect(viewSource).toContain('qr_login?.remediation')
  })

  it('shows remaining seconds only while the code is still scannable', () => {
    // 剩余秒数由 `expires_at` 自行倒数（见 im-qr-countdown.test.ts），
    // 但门槛不变：只有 `waiting_scan` 才拼这个数字。
    expect(viewSource).toMatch(/qr\.state === 'waiting_scan' \? qrRemainingSeconds/)
    expect(viewSource).toMatch(/state === 'waiting_scan' && remaining !== null/)
  })
})
