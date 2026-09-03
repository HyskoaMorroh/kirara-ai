import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 模型目录自动检测计划必须有 WebUI 界面。
 *
 * 后端一直提供三个接口，但前端此前**零调用点**，QUICKSTART 里明写着「这三个接口
 * 没有对应的 WebUI 界面，只能用 API 调用」。后果不是「少一个页面」：
 * 「模型目录会定期自动刷新」这件事在产品上完全不可见——运维无法回答「下一轮什么
 * 时候跑」「上一轮成功了吗」「这个后端到底开没开」，改一个间隔要手改
 * `data/config.yaml` 再重启整个进程（会中断所有正在进行的对话）。
 *
 * 这些用例钉住四件事：三个接口都有调用点、类型与后端字段对齐、页面可达（路由 +
 * 侧边栏）、以及三条呈现原则真的落在界面上。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/AutoDetectScheduleView.vue')
const routerSource = read('../src/router/index.ts')
const sidebarSource = read('../src/components/layout/SecondarySidebar.vue')

/** 后端 `TaskScheduler.get_status()` 平铺返回的字段。 */
const BACKEND_ROW_FIELDS = ['name', 'interval_days', 'last_run', 'model_count']

describe('auto-detect schedule API binding', () => {
  it('exposes a call site for all three endpoints', () => {
    expect(apiSource).toContain("'/llm/auto-detect-schedule'")
    expect(apiSource).toContain('/auto-detect-schedule`')
    expect(apiSource).toContain("'/llm/auto-detect-schedule/run'")
    expect(apiSource).toMatch(/getAutoDetectSchedule\s*\(/)
    expect(apiSource).toMatch(/updateAutoDetectSchedule\s*\(/)
    expect(apiSource).toMatch(/runAutoDetectNow\s*\(/)
  })

  it('types every field the backend actually returns', () => {
    for (const field of BACKEND_ROW_FIELDS) {
      expect(apiSource, `AutoDetectScheduleRow 缺少字段 ${field}`).toContain(field)
    }
    // `running` 决定「间隔配置到底会不会触发」，漏掉它页面就无法区分
    // 「配了 5 天」和「配了 5 天但调度器没跑」。
    expect(apiSource).toMatch(/running:\s*boolean/)
  })

  it('sends the interval as a number under interval_days', () => {
    // 后端读 `data.get("interval_days")` 并 `int(...)`；字段名写错会静默变成 0，
    // 也就是「关闭自动检测」——一个看起来成功的请求把功能关掉了。
    expect(apiSource).toMatch(/interval_days:\s*intervalDays/)
  })

  it('encodes the backend name into the path', () => {
    // 后端名允许中文与空格。不编码时带斜杠的名字会打到另一条路由上。
    expect(apiSource).toMatch(/encodeURIComponent\(backendName\)/)
  })
})

describe('auto-detect schedule page reachability', () => {
  it('registers a route', () => {
    expect(routerSource).toContain("path: '/llm/auto-detect'")
    expect(routerSource).toContain('AutoDetectScheduleView.vue')
    expect(routerSource).toContain("name: 'llm-auto-detect'")
  })

  it('appears in the LLM sidebar', () => {
    // 有路由但没有入口等于只能手敲 URL——那与「只能 curl」的差别很小。
    expect(sidebarSource).toContain("path: '/llm/auto-detect'")
    expect(sidebarSource).toContain('自动检测计划')
  })

  it('requires authentication', () => {
    const routeBlock = routerSource.slice(
      routerSource.indexOf("path: '/llm/auto-detect'")
    )
    expect(routeBlock.slice(0, 400)).toContain('requiresAuth: true')
  })
})

describe('auto-detect schedule presentation', () => {
  // 时刻计算、校验、汇总这几条**规则**由
  // `llm-auto-detect-schedule-logic.test.ts` 调用函数验证。
  // 本文件只留「必须写在模板里才有意义」的那些：可访问性属性、
  // 确认框、data-test 钩子、以及组件与那份逻辑的接线。
  //
  // 为什么要分开：源码 grep 断言对行为改变不敏感。
  // 原来这里有 `toContain("if (!row.last_run) return '—'")`——
  // 把一整行代码当字符串钉住，重构成等价写法它就红，而把
  // `86_400_000` 写错、把 `<= 0` 写成 `< 0`，它照样绿。
  it('never-run 与算错时刻这两件事由逻辑测试覆盖，这里只确认接线', () => {
    expect(viewSource).toMatch(/from '\.\/autoDetectSchedule'/)
    expect(viewSource).toMatch(/formatNextRun\(row\)/)
    expect(viewSource).toMatch(/formatLastRun\(row\)/)
  })

  it('warns prominently when the scheduler loop is not running', () => {
    // running=false 时所有间隔都不会触发；逐行显示「每 5 天」是一句谎话。
    expect(viewSource).toContain('data-test="scheduler-stopped"')
    expect(viewSource).toContain('不会自动触发')
  })

  it('does not guess a next-run time without a last run', () => {
    // 首轮延迟带 0–300 秒随机抖动，编一个「大约 X」会让人按那个时间去等。
    expect(viewSource).not.toMatch(/Date\.now\(\)\s*\+\s*interval/)
  })

  it('confirms the forced run and states its blast radius', () => {
    // 这个动作访问每一个上游并可能改写模型目录，不是一次只读查询。
    expect(viewSource).toContain('n-popconfirm')
    expect(viewSource).toContain('data-test="run-auto-detect"')
    expect(viewSource).toContain('会访问上游')
    expect(viewSource).toContain('data/config.yaml')
  })

  it('says that saving writes the config file', () => {
    expect(viewSource).toContain('data/config.yaml')
    expect(viewSource).toContain('不需要重启进程')
  })

  it('explains that zero means disabled', () => {
    expect(viewSource).toContain('0 = 关闭')
  })

  it('keeps a per-row draft so one failed save cannot corrupt the display', () => {
    expect(viewSource).toContain('draftIntervals')
    expect(viewSource).toMatch(/draftIntervals\.value\[row\.name\]\s*=\s*row\.interval_days/)
  })

  it('tracks the saving backend by name rather than a shared boolean', () => {
    // 共享布尔会让所有行一起转圈，看起来像整页都在保存。
    expect(viewSource).toContain("savingBackend = ref('')")
    expect(viewSource).toMatch(/savingBackend === row\.name/)
  })

  it('does not overwrite a row being edited on each poll', () => {
    expect(viewSource).toContain('if (!(row.name in draftIntervals.value))')
  })

  it('保存前先过校验，拦下时不发请求', () => {
    // 具体拒哪些值由逻辑测试覆盖（负数、非数字、非有限值、小数截断）。
    // 这里钉住「校验真的接在保存路径上」——只测规则不测接线，
    // 等于验证了一个没人调用的函数。
    expect(viewSource).toMatch(/checkInterval\(draftIntervals\.value\[row\.name\]\)/)
    expect(viewSource).toMatch(/if \(!checked\.ok\)[\s\S]{0,120}return/)
  })

  it('逐后端结果接到汇总逻辑上', () => {
    // `results` 是 {后端名: 成功与否}。三种处境（一个没跑 / 全成功 / 有失败）
    // 的措辞由逻辑测试覆盖。
    expect(viewSource).toContain('lastRunResults')
    expect(viewSource).toMatch(/runSummary\(lastRunResults\.value\)/)
    expect(viewSource).toMatch(/message\[summary\.level\]\(summary\.text\)/)
  })

  it('gives the table an accessible caption and row headers', () => {
    expect(viewSource).toContain('<caption')
    expect(viewSource).toContain('scope="row"')
    expect(viewSource).toContain('scope="col"')
  })

  it('labels the interval input for screen readers', () => {
    // 输入框只有一个数字，没有可见文字标签；读屏用户听到的是「编辑框 5」——
    // 哪一行的 5 无从判断。标签必须带上后端名。
    expect(viewSource).toMatch(/:aria-label="`\$\{row\.name\}[^`]*检测间隔天数/)
  })

  it('clears its polling timer on unmount', () => {
    // 不清的话离开页面后仍在每 60 秒打一次接口。
    expect(viewSource).toContain('onBeforeUnmount')
    expect(viewSource).toContain('clearInterval(timer)')
  })
})
