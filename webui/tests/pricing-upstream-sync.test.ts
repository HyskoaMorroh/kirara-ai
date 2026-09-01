/**
 * 定价页的上游同步入口。
 *
 * 后端已经能拉公开定价目录并只改自己写过的版本，但如果页面上没有入口，用户
 * 仍然只能一个模型一个模型地手敲四个数字。这里锁住三件事：同步按钮存在、
 * 结果里「被手工价保护住多少条」要如实告诉用户、以及自动同步间隔可以在界面
 * 上关掉（0 天）。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const pricingView = readFileSync(
  resolve(__dirname, '../src/views/llm/PricingView.vue'),
  'utf8'
)
const llmApi = readFileSync(resolve(__dirname, '../src/api/llm.ts'), 'utf8')

describe('定价上游同步', () => {
  it('API 层暴露同步与间隔设置两个动作', () => {
    expect(llmApi).toMatch(/syncPricing\(\)/)
    expect(llmApi).toMatch(/updatePricingSyncSchedule\(intervalDays: number\)/)
    expect(llmApi).toMatch(/'\/llm\/pricing\/sync'/)
    expect(llmApi).toMatch(/'\/llm\/pricing\/sync-schedule'/)
  })

  it('页面上有同步按钮，否则这条接口等于只能 curl', () => {
    expect(pricingView).toMatch(/data-test="sync-pricing"/)
    expect(pricingView).toMatch(/llmApi\.syncPricing\(\)/)
  })

  it('同步结果要报出被手工价保护住的条目数', () => {
    // 只报「导入 N 条」会让用户以为自己改过的价格也被覆盖了。
    expect(pricingView).toMatch(/skipped_manual/)
  })

  it('自动同步间隔可以在界面上改，包括关掉', () => {
    expect(pricingView).toMatch(/data-test="pricing-sync-interval"/)
    expect(pricingView).toMatch(/updatePricingSyncSchedule/)
  })

  it('同步后要刷新列表，否则页面还显示旧价格', () => {
    const syncBody = pricingView.slice(
      pricingView.indexOf('async function syncFromUpstream')
    )
    const bodyEnd = syncBody.indexOf('\n}')
    // 这里原本钉的是 `load()`——一个组件里并不存在的名字。同步成功后那行必抛
    // ReferenceError，被 catch 捕成「定价同步失败」：价格其实已经写盘了，界面却报错。
    // 所以断言要落在真实存在的加载函数上，否则测试是在替一个运行时错误打绿灯。
    expect(syncBody.slice(0, bodyEnd)).toMatch(/loadCatalog\(\)/)
  })
})
