/**
 * MCP 服务器列表的筛选参数必须真的进到请求 URL。
 *
 * `mcp.vm.ts` 原先这样调用：
 *
 *     http.get<PagedResponse<MCPServer>>('/mcp/servers', { params })
 *
 * 但 `http.get` 的第二个参数是 `Omit<RequestInit, 'method'>`——原生 fetch 配置，
 * **没有 `params` 这一项**（全文件 grep 不到 params）。传进去的对象被
 * `{ ...config, method: 'GET' }` 原样展开给 fetch，fetch 忽略未知字段。
 *
 * 后果是分页与筛选完全失效，但界面上看不出报错：
 * - 后端 `page` 默认 1、`page_size` 默认 20（`mcp/routes.py`），所以永远返回第一页；
 * - `type` / `status` / `query` 全部为 None，所以筛选和搜索一律返回全量；
 * - 前端读到的 `total` / `total_pages` 是全量的，分页控件显示得像在工作。
 *
 * 用户翻到第 2 页看到的还是第 1 页的内容，输入关键词搜索没有任何变化 ——
 * 而请求成功、控制台干净、后端日志也干净。
 *
 * 判据：**查询参数要么进 URL，要么就不该存在。** 传给一个不认识它的配置对象，
 * 等于静默丢弃。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const httpUtil = readFileSync(resolve(root, 'src/utils/http.ts'), 'utf-8')
const mcpVm = readFileSync(resolve(root, 'src/views/mcp/mcp.vm.ts'), 'utf-8')

describe('MCP 列表查询参数', () => {
  it('自检：http 工具确实不支持 params 配置项', () => {
    // 前提。若哪天 http.get 真的实现了 params 序列化，下面的断言就该重新评估。
    expect(
      /\bparams\b/.test(httpUtil),
      'http.ts 出现了 params —— 请确认它是否已实现查询串序列化，并更新本测试的理由'
    ).toBe(false)
  })

  it('不把 params 塞给 http.get 的 RequestInit 配置', () => {
    // 覆盖简写 `{ params }` 与显式 `{ params: x }` 两种形态：
    // 只查简写时，把它改成 `{ params: search }` 仍会漏过去（反向自证时踩到过）。
    //
    // 泛型段用 `[^(]*` 而不是 `[^>]*`：`PagedResponse<MCPServer>` 里含 `>`，
    // 用 `[^>]*` 会在第一个 `>` 处停住，永远匹配不到后面的参数列表 —— 那样这条
    // 断言就成了一个永远为假的空壳。
    expect(
      /http\.(get|post|put|delete)<[^(]*\([^)]*\{[^}]*\bparams\b/.test(mcpVm),
      'mcp.vm.ts 把 params 传给了 http 配置对象 —— fetch 会忽略它，分页与筛选静默失效'
    ).toBe(false)
  })

  it('筛选参数通过查询串进入 URL', () => {
    expect(mcpVm).toMatch(/URLSearchParams|\?\$\{|search\.toString\(\)/)
  })

  it('后端读取的五个参数都被真正拼进请求', () => {
    // 与 kirara_ai/web/api/mcp/routes.py 的 request.args.get 一一对应。
    // 键可能写成 URLSearchParams 的对象字面量键（`page:`）或 set 调用（`set('page'`），
    // 两种都算拼进去了；只查带引号的形式会漏掉前者。
    const at = mcpVm.indexOf('/mcp/servers')
    const block = mcpVm.slice(Math.max(0, at - 1400), at + 200)
    for (const key of ['page', 'page_size', 'type', 'status', 'query']) {
      expect(block, `查询串缺少参数 ${key}`).toMatch(
        new RegExp(`(['"\`]${key}['"\`]|\\b${key}\\s*:)`)
      )
    }
  })

  it('空筛选项不拼成字面量 undefined', () => {
    // `String(undefined)` 会得到 "undefined" 这个字符串，后端会把它当成有效筛选值。
    const at = mcpVm.indexOf('/mcp/servers')
    const around = mcpVm.slice(Math.max(0, at - 1200), at + 200)
    expect(around).toMatch(/if\s*\(|\?\?|&&|filter|Boolean/)
  })
})
