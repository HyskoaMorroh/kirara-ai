import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http } from '../src/utils/http'

const response = (body: string | null, init: ResponseInit = {}) =>
  new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init
  })

describe('http response handling', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn()
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('parses JSON responses without changing the public request contract', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response('{"ok":true}'))

    await expect(http.get<{ ok: boolean }>('/health')).resolves.toEqual({ ok: true })
  })

  it('reports the HTTP status for an empty error response instead of JSON syntax noise', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response('', { status: 502, statusText: 'Bad Gateway' }))

    await expect(http.get('/health')).rejects.toThrow('请求失败 (HTTP 502 Bad Gateway)')
  })

  it('accepts a no-content response for mutations', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(null, { status: 204 }))

    await expect(http.delete('/im/adapters/example')).resolves.toBeUndefined()
  })

  it('includes a bounded text summary for non-JSON error responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response('<html>upstream unavailable</html>', {
        status: 503,
        statusText: 'Service Unavailable',
        headers: { 'Content-Type': 'text/html' }
      })
    )

    await expect(http.get('/health')).rejects.toThrow(
      '请求失败 (HTTP 503 Service Unavailable): upstream unavailable'
    )
  })

  it('preserves a structured backend error message when the body is JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      response('{"error":"adapter unavailable"}', { status: 503, statusText: 'Service Unavailable' })
    )

    await expect(http.get('/health')).rejects.toThrow('adapter unavailable')
  })
})
