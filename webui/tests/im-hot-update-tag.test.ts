import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * QQ 自身热更新必须在界面上是一个独立可见的状态（需求 18.4、19.5）。
 *
 * 现场日志里热更新与一次真实对话**重叠**：`07:56:20` 开始下载 → `07:56:56` 下载完
 * （36 秒窗口），而 `[收-私] 写一个回火算法` 恰好落在 `07:56:56`。运维事后问
 * 「那条为什么慢」时，如果面板上没有热更新这回事，唯一能得到的结论是「QQ 慢」，
 * 而真正的原因是上游正在后台拉一个几十 MB 的包。
 *
 * 这些用例钉住三件事：类型与后端字段对齐、它是**另一枚**标签而不是挤进扫码标签、
 * 以及不显示此刻不影响任何事的状态（常驻「已就绪」只会挤占状态区）。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/im.ts')
const viewSource = read('../src/views/im/IMAdapterDetail.vue')

/** 后端 `HotUpdateSnapshot` 的字段。 */
const BACKEND_FIELDS = [
  'target_version',
  'started_at',
  'completed_at',
  'duration_seconds',
  'remediation'
]

/** 后端 `HotUpdateState` 的六个取值。 */
const BACKEND_STATES = [
  'checking',
  'up_to_date',
  'downloading',
  'downloaded',
  'ready',
  'failed'
]

describe('hot update type binding', () => {
  it('hangs the snapshot off the QR login payload', () => {
    // 后端把它放在 `qr_login.hot_update` 里（同一份日志解析出来的两条线）。
    expect(apiSource).toMatch(/hot_update:\s*HotUpdateSnapshot \| null/)
    expect(apiSource).toContain('export interface HotUpdateSnapshot')
  })

  it('types every field the backend returns', () => {
    for (const field of BACKEND_FIELDS) {
      expect(apiSource, `HotUpdateSnapshot 缺少字段 ${field}`).toContain(field)
    }
  })

  it('types every state the backend can emit', () => {
    for (const state of BACKEND_STATES) {
      expect(apiSource, `HotUpdateSnapshot 缺少状态 ${state}`).toContain(`'${state}'`)
    }
  })

  it('documents that duration is null while downloading, not zero', () => {
    // 0 会被读成「瞬间完成」，正好与「它正在占着带宽」相反。
    expect(apiSource).toMatch(/duration_seconds:\s*number \| null/)
    expect(apiSource).toContain('瞬间完成')
  })

  it('warns that the upstream timestamps carry no date', () => {
    // 那些日志行只有 HH:MM:SS.mmm；格式化成某年某月某日是给出没有依据的精确。
    expect(apiSource).toContain('不承诺是墙上时间')
  })
})

describe('hot update presentation', () => {
  it('renders a tag of its own rather than folding into the QR tag', () => {
    // 合并会让「正在下载更新」顶掉「等待扫码」，而后者才是操作者此刻要看的。
    expect(viewSource).toContain('data-test="hot-update-tag"')
    expect(viewSource).toMatch(/hotUpdateTag\s*\(/)
    expect(viewSource).toContain('不影响这张码能不能扫')
  })

  it('only surfaces the states that affect something right now', () => {
    // downloading 占带宽、failed 会反复重试并占带宽；其余三个此刻不影响任何事,
    // 常驻一枚「已就绪」只会挤占状态区，让真正需要注意的标签更难被看见。
    const table = viewSource.slice(
      viewSource.indexOf('const HOT_UPDATE_TAG'),
      viewSource.indexOf('const hotUpdateTag')
    )
    expect(table).toContain('downloading')
    expect(table).toContain('failed')
    expect(table).not.toContain('ready:')
    expect(table).not.toContain('up_to_date')
    expect(table).not.toContain('checking:')
  })

  it('returns null when the log has no hot-update lines at all', () => {
    // 后端在这种情况下给 `null`（与 up_to_date 是两件事：可能只是日志没挂全）。
    const helper = viewSource.slice(viewSource.indexOf('const hotUpdateTag'))
    expect(helper.slice(0, 400)).toContain('if (!hot) return null')
  })

  it('shows the download window length but not a fabricated wall-clock time', () => {
    const helper = viewSource.slice(viewSource.indexOf('const hotUpdateTag'))
    const block = helper.slice(0, 1200)
    expect(block).toContain('本轮下载耗时')
    // started_at / completed_at 不得被格式化展示。
    expect(block).not.toMatch(/toLocale\w*String\(\)/)
  })

  it('checks duration against null explicitly so a real 0 still renders', () => {
    // `if (hot.duration_seconds)` 会把「测了，0 秒」也吞掉。
    const helper = viewSource.slice(viewSource.indexOf('const hotUpdateTag'))
    expect(helper.slice(0, 1200)).toContain('hot.duration_seconds !== null')
  })
})
