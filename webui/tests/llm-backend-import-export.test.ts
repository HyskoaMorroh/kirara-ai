// @vitest-environment happy-dom

import { describe, expect, it, vi } from 'vitest'

/**
 * 需求 8 的「编辑导入供应商」里，**导入 / 导出**在后端早已实现并鉴权
 * （`GET /llm/backends/export`、`POST /llm/backends/import`，含整份校验、
 * 空凭据保留现有值、同名冲突 409），但前端既没有 API 封装也没有调用方：
 * 从产品角度看这条能力只能 curl。定价目录的同类能力则一直有按钮。
 *
 * 这些用例钉住客户端封装的三件事：
 *
 * 1. 导出走 `http.fetch`（返回 `Response`，才能取 `Content-Disposition` 命名文件），
 *    而不是走会把响应体当 JSON 解析的 `http.get`；
 * 2. 导入 body 只带 `document` 与 `overwrite`——后端对未知字段直接 400；
 * 3. `overwrite` 必须显式传，默认 `false`：同名后端静默覆盖会让用户丢掉
 *    自己填好的凭据与容错参数。
 */

const { post, fetchMock } = vi.hoisted(() => ({
  post: vi.fn(),
  fetchMock: vi.fn()
}))

vi.mock('../src/utils/http', () => ({
  http: { post, fetch: fetchMock, get: vi.fn(), put: vi.fn(), delete: vi.fn() },
  HttpRequestError: class extends Error {}
}))

async function api() {
  const module = await import('../src/api/llm')
  return module.llmApi
}

describe('provider configuration import and export client', () => {
  it('exports through http.fetch so the filename header survives', async () => {
    fetchMock.mockReset()
    fetchMock.mockResolvedValue(new Response('{}'))
    const llmApi = await api()

    await llmApi.exportBackends()

    expect(fetchMock).toHaveBeenCalledWith('/llm/backends/export', { method: 'GET' })
  })

  it('sends only document and overwrite on import', async () => {
    post.mockReset()
    post.mockResolvedValue({ data: { imported_count: 2, overwritten: [] } })
    const llmApi = await api()

    await llmApi.importBackends({ document: { version: 1, backends: [] } })

    const [path, body] = post.mock.calls.at(-1) as [string, Record<string, unknown>]
    expect(path).toBe('/llm/backends/import')
    expect(Object.keys(body).sort()).toEqual(['document', 'overwrite'])
  })

  it('defaults overwrite to false so a name clash is reported, not applied', async () => {
    post.mockReset()
    post.mockResolvedValue({ data: { imported_count: 0, overwritten: [] } })
    const llmApi = await api()

    await llmApi.importBackends({ document: { version: 1, backends: [] } })

    const [, body] = post.mock.calls.at(-1) as [string, Record<string, unknown>]
    expect(body.overwrite).toBe(false)
  })

  it('forwards an explicit overwrite request', async () => {
    post.mockReset()
    post.mockResolvedValue({ data: { imported_count: 1, overwritten: ['openai'] } })
    const llmApi = await api()

    await llmApi.importBackends({
      document: { version: 1, backends: [] },
      overwrite: true
    })

    const [, body] = post.mock.calls.at(-1) as [string, Record<string, unknown>]
    expect(body.overwrite).toBe(true)
  })
})

describe('provider import and export are reachable from the UI', () => {
  it('LLMView wires both actions to the client', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, resolve } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(resolve(here, '../src/views/llm/LLMView.vue'), 'utf-8')

    expect(source).toContain('exportBackends')
    expect(source).toContain('importBackends')
    expect(source, '导出按钮缺失').toContain('data-test="export-backends"')
    expect(source, '导入入口缺失').toContain('data-test="import-backends"')
  })

  it('surfaces the 409 conflict instead of silently overwriting', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, resolve } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(resolve(here, '../src/views/llm/LLMView.vue'), 'utf-8')

    // 冲突必须让用户确认后才重发 overwrite:true，不能自动覆盖。
    expect(source).toContain('overwrite')
    expect(source).toMatch(/409|conflict|冲突/)
  })
})
