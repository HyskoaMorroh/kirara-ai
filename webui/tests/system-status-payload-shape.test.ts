/**
 * 状态栏的 HTTP 响应类型必须描述后端真正发出的形状。
 *
 * 后端 `SystemStatus`(kirara_ai/web/api/system/models.py)全字段是 snake_case,
 * 而 store 里的 `SystemStatus` 是转换之后的 camelCase 内部形状。二者同名但不同形。
 *
 * `fetchStatus` 曾把响应标成 `http.get<{ status: SystemStatus }>`,借的是 store 那个
 * camelCase 类型 —— 函数体读 `data.status.cpu_usage` 全部落在类型之外,报 12 条
 * TS2551。运行时其实是对的(函数体做的正是 snake→camel 转换),错的是拿内部形状去
 * 描述外部载荷:一旦后端加字段或改名,类型这层照不出来,而且真出问题时这 12 条噪声
 * 会把真实的属性名错误埋掉。
 *
 * 所以这里锁两件事:响应类型独立于 store 类型,且字段与后端逐一对齐。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const statusBar = readFileSync(
  resolve(__dirname, '../src/components/layout/StatusBar.vue'),
  'utf-8'
)

/** 后端 models.py 里 SystemStatus 的字段,顺序无关。 */
const BACKEND_FIELDS = [
  'version',
  'uptime',
  'active_adapters',
  'active_backends',
  'loaded_plugins',
  'workflow_count',
  'memory_usage',
  'cpu_usage',
  'cpu_info',
  'python_version',
  'platform',
  'has_proxy'
]

describe('系统状态响应形状', () => {
  it('自检:确实读到了 StatusBar', () => {
    expect(statusBar).toContain('fetchStatus')
    expect(statusBar).toContain('/system/status')
  })

  it('响应类型不复用 store 的 camelCase SystemStatus', () => {
    // `http.get<{ status: SystemStatus }>` 是当初那处错标。
    expect(statusBar).not.toMatch(/http\s*\n?\s*\.get<\{\s*status:\s*SystemStatus\s*\}>/)
  })

  it('声明了一个专门描述后端载荷的 snake_case 类型', () => {
    expect(statusBar).toMatch(/interface\s+SystemStatusPayload/)
  })

  it('载荷类型覆盖后端每一个字段', () => {
    const match = statusBar.match(/interface\s+SystemStatusPayload\s*\{([\s\S]*?)\n\}/)
    expect(match, '找不到 SystemStatusPayload 定义').toBeTruthy()
    const body = match![1]

    const missing = BACKEND_FIELDS.filter(
      (field) => !new RegExp(`\\b${field}\\??\\s*:`).test(body)
    )
    expect(missing, `载荷类型缺少后端字段：${missing.join(', ')}`).toEqual([])
  })

  it('载荷类型不混入 camelCase 字段', () => {
    const match = statusBar.match(/interface\s+SystemStatusPayload\s*\{([\s\S]*?)\n\}/)
    const body = match![1]

    // snake_case 与 camelCase 混在一个类型里,说明又把两种形状搅到一起了。
    const camel = [...body.matchAll(/^\s*([a-z]+[A-Z][A-Za-z]*)\??\s*:/gm)].map((m) => m[1])
    expect(camel, `载荷类型混入了 camelCase 字段：${camel.join(', ')}`).toEqual([])
  })

  it('转换后仍然交给 store 的 camelCase 形状', () => {
    // 运行时行为不能改:updateSystemStatus 收的还是 camelCase。
    expect(statusBar).toMatch(/updateSystemStatus\(\{/)
    expect(statusBar).toMatch(/cpuUsage:\s*data\.status\.cpu_usage/)
    expect(statusBar).toMatch(/pythonVersion:\s*data\.status\.python_version/)
  })
})
