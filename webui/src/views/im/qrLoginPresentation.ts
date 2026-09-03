/**
 * QQ 扫码登录状态的呈现逻辑。
 *
 * 抽成独立模块，是因为这里的判断**是**这个功能：二维码只有 120 秒有效期，
 * 而「看一眼面板、拿手机、解锁、打开扫一扫」轻易花掉一半。算错倒计时或者
 * 在归零后仍显示「待扫码」，用户会去扫一张已经过期的码——
 * 而他此刻真正该做的是点「刷新扫码状态」取新的一张。
 *
 * 此前这些判断只被源码 grep「测过」：`im-qr-countdown.test.ts` 与
 * `im-qr-login-status.test.ts` 的断言形如
 * `expect(viewSource).toMatch(/qr\.state === 'waiting_scan' \? qrRemainingSeconds/)`。
 * 那条断言把一行代码的写法钉住，却不检查 `<= 0` 有没有写成 `< 0`、
 * 除以 1000 有没有写成乘以 1000。
 */

export type StatusTagType = 'default' | 'error' | 'info' | 'success' | 'warning'

export interface QRSnapshot {
  state: string
  expires_at?: string | null
  validity_seconds?: number | null
  remediation?: string | null
  latest_qr_path?: string | null
  refresh_count?: number
}

/**
 * 每种扫码状态的文案与档位。
 *
 * `age_unknown` 与 `waiting_scan` 分开：前者是「读不到时效」，
 * 那时不能拼任何秒数——一个编出来的倒计时说的谎恰好是「还来得及」。
 */
export const QR_STATE_TEXT: Record<string, { label: string; type: StatusTagType }> = {
  pending: { label: '等待二维码', type: 'default' },
  waiting_scan: { label: '待扫码', type: 'warning' },
  age_unknown: { label: '二维码时效未知', type: 'warning' },
  scanned: { label: '已扫码待确认', type: 'info' },
  expired: { label: '二维码已过期', type: 'error' },
  succeeded: { label: 'QQ 已登录', type: 'success' },
  failed: { label: '登录失败', type: 'error' },
  unavailable: { label: '二维码暂不可用', type: 'default' },
  quick_login: { label: '免扫码登录', type: 'success' }
}

/**
 * 二维码还剩多少秒。无法判断时返回 `null`，**绝不返回一个编出来的数字**。
 *
 * 按 `expires_at` 与传入的「现在」算，而不用后端的 `remaining_seconds`：
 * 后者是取快照那一刻的读数，在 120 秒这个尺度上一次性渲染必然说谎。
 *
 * 下界钳到 0：过期多久不是用户要的信息，「已过期」才是。
 */
export function qrRemainingSeconds(qr: QRSnapshot, now: number): number | null {
  if (!qr.expires_at) return null
  const expiresAt = Date.parse(qr.expires_at)
  if (Number.isNaN(expiresAt)) return null
  return Math.max(0, (expiresAt - now) / 1000)
}

export interface QRTag {
  label: string
  type: StatusTagType
  title: string
}

/**
 * 一枚扫码状态标签，或 `null`（不显示任何扫码信息）。
 *
 * 返回 `null` 的两种情形都是「没有结论」：后端没配上游日志路径时给不出快照，
 * 以及 `state` 为 `unknown`。显示成「未知」会让用户以为出了问题。
 *
 * 倒计时归零后**改口为已过期**：继续显示「待扫码（剩 0 秒）」是把过期说成可扫。
 */
export function qrLoginTag(qr: QRSnapshot | null | undefined, now: number): QRTag | null {
  if (!qr || qr.state === 'unknown') return null

  const remaining = qr.state === 'waiting_scan' ? qrRemainingSeconds(qr, now) : null
  const state = remaining !== null && remaining <= 0 ? 'expired' : qr.state
  const preset = QR_STATE_TEXT[state]
  if (!preset) return null

  // 「还剩多久」而不是绝对时刻：用户要判断的是「现在扫还来不来得及」。
  const countdown =
    state === 'waiting_scan' && remaining !== null ? `（剩 ${Math.round(remaining)} 秒）` : ''

  const details: string[] = []
  if (state === 'expired' && qr.state === 'waiting_scan') {
    details.push('这张二维码已超过有效期，请点「刷新扫码状态」取最新一张。')
  } else if (qr.remediation) {
    details.push(qr.remediation)
  }
  if (qr.validity_seconds) details.push(`有效期 ${qr.validity_seconds} 秒`)
  if (qr.latest_qr_path) details.push(`最新二维码：${qr.latest_qr_path}`)
  if ((qr.refresh_count ?? 0) > 0) details.push(`已刷新 ${qr.refresh_count} 次`)

  return { label: `${preset.label}${countdown}`, type: preset.type, title: details.join('\n') }
}
