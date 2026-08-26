// import { useMessage } from 'naive-ui'

const BASE_URL = '/backend-api/api'
const MAX_ERROR_BODY_LENGTH = 240

export class HttpRequestError extends Error {
  readonly status: number
  readonly data: unknown

  constructor(message: string, status: number, data: unknown) {
    super(message)
    this.name = 'HttpRequestError'
    this.status = status
    this.data = data
  }
}

function compactResponseText(text: string): string {
  return text
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, MAX_ERROR_BODY_LENGTH)
}

function responseStatus(response: Response): string {
  const statusText = response.statusText.trim()
  return statusText ? `HTTP ${response.status} ${statusText}` : `HTTP ${response.status}`
}

function getBackendErrorMessage(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null

  const record = value as Record<string, unknown>
  for (const key of ['error', 'message', 'detail']) {
    const candidate = record[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }

  return null
}

class Http {
  //   private message = useMessage()

  async fetch(path: string, config: RequestInit): Promise<Response> {
    try {
      let actualPath = path
      let headers: Record<string, any> = {}
      if (!path.startsWith('http://') && !path.startsWith('https://')) {
        actualPath = `${BASE_URL}${path}`
        headers = {
          'Content-Type': 'application/json',
          ...headers,
          ...config.headers,
          ...this.getAuthHeader()
        }
        if (typeof FormData !== 'undefined' && config.body instanceof FormData) {
          delete headers['Content-Type']
        }
        config = {
          ...config,
          credentials: 'include'
        }
      }
      return await fetch(actualPath, {
        ...config,
        headers: headers
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '请求失败'
      //   this.message.error(message)
      throw error
    }
  }

  private async request<T>(path: string, config: RequestInit): Promise<T> {
    try {
      let actualPath = path
      let headers: Record<string, any> = {}
      if (!path.startsWith('http://') && !path.startsWith('https://')) {
        actualPath = `${BASE_URL}${path}`
        headers = {
          'Content-Type': 'application/json',
          ...headers,
          ...config.headers,
          ...this.getAuthHeader()
        }
        if (typeof FormData !== 'undefined' && config.body instanceof FormData) {
          delete headers['Content-Type']
        }
        config = {
          ...config,
          credentials: 'include'
        }
      }
      const response = await fetch(actualPath, {
        ...config,
        headers: headers
      })

      // Read once so empty, 204, truncated, and non-JSON proxy responses can be
      // reported with their HTTP status instead of leaking a JSON parser error.
      const body = await response.text()
      let data: unknown = undefined
      if (body.trim()) {
        try {
          data = JSON.parse(body)
        } catch {
          if (!response.ok) {
            const summary = compactResponseText(body)
            throw new Error(
              `请求失败 (${responseStatus(response)})${summary ? `: ${summary}` : ''}`
            )
          }
          throw new Error(`响应不是有效的 JSON (${responseStatus(response)})`)
        }
      }

      if (!response.ok) {
        const backendMessage = getBackendErrorMessage(data)
        throw new HttpRequestError(
          backendMessage || `请求失败 (${responseStatus(response)})`,
          response.status,
          data
        )
      }

      return data as T
    } catch (error) {
      const message = error instanceof Error ? error.message : '请求失败'
      //   this.message.error(message)
      throw error
    }
  }

  getAuthToken(): string | null {
    return localStorage.getItem('token')
  }

  private getAuthHeader(): HeadersInit {
    const token = this.getAuthToken()
    return token ? { Authorization: `Bearer ${token}` } : {}
  }

  get<T>(path: string, config: Omit<RequestInit, 'method'> = {}) {
    return this.request<T>(path, { ...config, method: 'GET' })
  }

  post<T>(path: string, data?: any, config: Omit<RequestInit, 'method' | 'body'> = {}) {
    return this.request<T>(path, {
      ...config,
      method: 'POST',
      body: JSON.stringify(data)
    })
  }

  postForm<T>(path: string, data: FormData, config: Omit<RequestInit, 'method' | 'body'> = {}) {
    return this.request<T>(path, {
      ...config,
      method: 'POST',
      body: data
    })
  }

  put<T>(path: string, data?: any, config: Omit<RequestInit, 'method' | 'body'> = {}) {
    return this.request<T>(path, {
      ...config,
      method: 'PUT',
      body: JSON.stringify(data)
    })
  }

  delete<T>(path: string, config: Omit<RequestInit, 'method'> = {}) {
    return this.request<T>(path, { ...config, method: 'DELETE' })
  }

  url(path: string) {
    return `${BASE_URL}${path}`
  }

  ws(path: string) {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = new URL(`${wsProtocol}//${window.location.host}`)
    wsUrl.pathname = `${BASE_URL}${path}`
    return new WebSocket(wsUrl.toString())
  }

}

export const http = new Http()
