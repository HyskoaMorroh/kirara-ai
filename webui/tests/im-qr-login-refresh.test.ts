import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 扫码状态必须有一个「刷新」动作（需求 18.4 逐项点名的六项之一）。
 *
 * 二维码有效期实测 120 秒，远短于「看一眼、去拿手机、回来扫」这个动作序列。
 * 没有刷新入口时，面板上的剩余秒数是上一次列表轮询的快照，用户因此总在扫
 * 一张屏幕上还在、上游其实已经换掉的码——这正是「二维码总是过期，无法登录」
 * 这个报障的形态。
 *
 * 同时钉住三条边界：
 * - 按钮只在真的有扫码环节的适配器上出现；
 * - 刷新只更新这一行的 qr_login，不重取整份列表（否则会冲掉正在编辑的表单）；
 * - 文案不能承诺「重新生成二维码」——生成方是上游容器，不是 Kirara。
 *
 * 以源码为断言对象：组件依赖 naive-ui 与路由，单测里挂载成本远高于收益，
 * 而这里要验的是契约而不是渲染细节。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/im.ts')
const viewSource = read('../src/views/im/IMAdapterDetail.vue')

describe('刷新扫码状态的 API 客户端', () => {
  it('声明了 refreshQRLogin', () => {
    expect(apiSource).toContain('refreshQRLogin')
  })

  it('打到后端的专用路由', () => {
    expect(apiSource).toContain('/qr-login')
  })

  it('用 POST 而不是 GET', () => {
    // 它在服务器上真的做事（读文件），且不该被浏览器或中间层缓存——
    // 一个被缓存的「刷新」会返回上一次的结果，那比没有按钮更糟。
    expect(apiSource).toMatch(/refreshQRLogin[\s\S]{0,200}http\.post/)
  })

  it('返回类型区分「不支持」与「读不到」', () => {
    expect(apiSource).toMatch(/supported:\s*boolean/)
    expect(apiSource).toMatch(/qr_login:\s*QRLoginSnapshot \| null/)
  })
})

describe('界面上的刷新入口', () => {
  it('提供刷新按钮', () => {
    expect(viewSource).toContain('刷新扫码状态')
  })

  it('按钮只在该适配器有扫码环节时出现', () => {
    // 给 Telegram 放一个永远无事可做的按钮，比没有按钮更让人困惑。
    expect(viewSource).toMatch(/v-if="adapter\.health\?\.qr_login"/)
  })

  it('按名字跟踪加载态，多个实例不会一起转圈', () => {
    expect(viewSource).toContain('qrRefreshing')
    expect(viewSource).toMatch(/qrRefreshing === adapter\.name/)
  })

  it('只更新这一行的 qr_login，不重取整份列表', () => {
    // 整表刷新会把用户正在编辑的配置表单冲掉，而他此刻只是想知道
    // 那张码还能不能扫。
    expect(viewSource).toMatch(/adapter\.health\.qr_login = result\.qr_login/)
  })

  it('读不到时给出后端的处置建议而不是一句通用失败', () => {
    expect(viewSource).toContain('result.remediation')
  })
})

describe('文案不谎报所有权', () => {
  it('不承诺重新生成二维码', () => {
    // 二维码由 LLOneBot / PMHQ 在自己的容器里生成并在过期时自行重新请求。
    // 写成「重新生成」时，点了没反应的用户会去排查 Kirara，而要看的是上游容器。
    expect(viewSource).not.toContain('重新生成二维码')
    expect(viewSource).not.toContain('生成新二维码')
  })

  it('注释说明它只重读上游日志', () => {
    expect(apiSource).toContain('只重读')
  })
})
