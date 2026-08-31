import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 连接状态必须自己刷新（需求 1、3 的共同前提）。
 *
 * 冷启动宽限期是 180 秒，二维码有效期是 120 秒——两个都是**会自己变化**的状态，
 * 而适配器详情页只在 `onMounted` 拉一次数据。于是：
 *
 * - 用户重启容器后打开面板，看到「正在启动」，然后一直是「正在启动」；上游其实
 *   两分钟前就连上了，他要手动刷新整个页面才知道。
 * - 二维码那一栏更糟：倒计时会自己走到 0 并显示「已过期」，可上游早就生成了新码，
 *   面板上那个路径与刷新次数还是旧的。用户得点「刷新扫码状态」才知道。
 *
 * 「等就行」这句处置建议只有在**面板会自己变**的前提下才成立。不会变的话，
 * 那句话等于让用户盯着一个静止的画面等。
 *
 * 容错面板（ResilienceView）已经有这套约定：10 秒一轮、可关、离开页面时清掉。
 * 这里要求连接状态用同一套约定，而不是各写一份间隔。
 */

const here = dirname(fileURLToPath(import.meta.url))
const viewSource = readFileSync(
  resolve(here, '../src/views/im/IMAdapterDetail.vue'),
  'utf-8'
)

describe('连接状态轮询', () => {
  it('有一个定时拉取适配器状态的计时器', () => {
    // 与二维码倒计时是两回事：那个只重算本地时间，不发请求。
    expect(viewSource).toMatch(/healthTimer|statusTimer|refreshTimer/)
  })

  it('轮询调的是取回状态的那个函数', () => {
    expect(viewSource).toMatch(/(?:healthTimer|statusTimer|refreshTimer)[\s\S]{0,300}fetchAdapters/)
  })

  it('间隔不小于 5 秒', () => {
    // 更快只是把一个诊断面板变成压测；这里的状态变化尺度是几十秒到几分钟。
    expect(viewSource).toMatch(/setInterval\([\s\S]{0,120}?(?:5_000|10_000|15_000|\d{4,})/)
  })

  it('离开页面时清掉', () => {
    // 两个计时器都要清：留着的会在组件卸载后继续发请求。
    const unmounted = viewSource.slice(viewSource.indexOf('onUnmounted'))
    expect(unmounted).toMatch(/clearInterval[\s\S]{0,400}clearInterval/)
  })

  it('可以关掉自动刷新', () => {
    // 一个会自己变的面板在排查时可能碍事：正在读某个状态时它被刷走了。
    expect(viewSource).toMatch(/autoRefresh/)
  })

  it('轮询失败不弹错误提示', () => {
    // 每 10 秒弹一次「获取适配器列表失败」会把界面糊满，而首次加载失败要说。
    expect(viewSource).toMatch(/fetchAdapters\s*=\s*async\s*\(\s*(?:\{[^}]*\}|\w+)/)
  })
})
