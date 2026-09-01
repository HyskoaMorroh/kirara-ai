/**
 * `getAutoDetectSchedule()` 的响应是平铺对象,没有 `data` 包装。
 *
 * 后端是 `jsonify(scheduler.get_status())`(`llm/routes.py`),直接吐
 * `{running, backends, price_sync}`。`AutoDetectScheduleView.vue` 一直读
 * `response.backends` / `response.running`,是对的。
 *
 * 但 `PricingView.vue` 的 `loadSyncState()` 写成了 `response.data.running` ——
 * `response.data` 是 `undefined`,读它的属性抛 TypeError,而那一段套在
 * `try { ... } catch { syncState.value = null }` 里,异常被吞掉。
 *
 * 后果最坏的地方在于它**完全静默**:定价页不报错、不提示,只是同步状态标签永远不
 * 出现,间隔输入框也永远停在硬编码的初值 7。用户改成 30 天、刷新页面看回 7,
 * 只会以为"没保存住"。控制台干净,后端日志干净。
 *
 * 判据:**同一个接口在两个页面必须用同一种读法。** 一处加 `.data` 一处不加,
 * 至少有一处是错的,而错的那处如果被 catch 兜住就永远查不出来。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')
const pricingView = readFileSync(resolve(root, 'src/views/llm/PricingView.vue'), 'utf-8')
const scheduleView = readFileSync(
  resolve(root, 'src/views/llm/AutoDetectScheduleView.vue'),
  'utf-8'
)
const llmApi = readFileSync(resolve(root, 'src/api/llm.ts'), 'utf-8')

/** 取某个文件里 `getAutoDetectSchedule()` 调用之后的一段,用来看它怎么读返回值。 */
function readerBody(source: string): string {
  const at = source.indexOf('getAutoDetectSchedule()')
  expect(at, '找不到 getAutoDetectSchedule 调用').toBeGreaterThan(-1)
  return source.slice(at, at + 600)
}

describe('自动检测计划响应的读法', () => {
  it('声明的返回类型上没有 data 字段', () => {
    const at = llmApi.indexOf('interface AutoDetectScheduleResponse')
    const body = llmApi.slice(at, llmApi.indexOf('\n}', at))
    expect(body).toMatch(/running/)
    expect(body).toMatch(/backends/)
    expect(body, '类型里出现了 data,与后端的平铺响应不符').not.toMatch(/^\s*data\??\s*:/m)
  })

  it('定价页按平铺字段读，不走 response.data', () => {
    const body = readerBody(pricingView)
    expect(
      body,
      'PricingView 读了 response.data —— 后端没有这一层，取属性会抛 TypeError 并被 catch 吞掉'
    ).not.toMatch(/response\.data\./)
  })

  it('定价页真的取到了 running 与 price_sync', () => {
    const body = readerBody(pricingView)
    expect(body).toMatch(/response\.running/)
    expect(body).toMatch(/response\.price_sync/)
  })

  it('两个页面的读法一致，避免一处对一处错', () => {
    const usesDataWrapper = (body: string) => /response\.data\./.test(body)
    expect(usesDataWrapper(readerBody(pricingView))).toBe(
      usesDataWrapper(readerBody(scheduleView))
    )
  })

  it('取同步状态失败时不能静默,要留下可诊断的痕迹', () => {
    // 裸 `catch {}` 把类型层没拦住的错误也一并吞掉,正是这个缺陷藏了一整轮的原因。
    const at = pricingView.indexOf('async function loadSyncState')
    const fn = pricingView.slice(at, pricingView.indexOf('\n}', at))
    expect(fn, 'loadSyncState 的 catch 没有捕获错误对象,失败时无从排查').toMatch(
      /catch\s*\(\s*\w+\s*\)/
    )
  })
})
