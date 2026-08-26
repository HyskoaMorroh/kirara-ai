import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cancelDependencyTask,
  enableResource,
  getDependencyTask,
  installResource,
  installSystemDependency,
  listDependencyTasks,
  listResourceAudit,
  listResources,
  listSystemDependencies,
  probeSystemDependency,
  retryDependencyTask,
  restoreResource,
  updateResource
} from '../src/api/resource'
import { http } from '../src/utils/http'

describe('resource API client', () => {
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

  it('uses resource type filters and the expected endpoint', async () => {
    const request = vi.spyOn(http, 'get').mockResolvedValue([])

    await listResources('skill')

    expect(request).toHaveBeenCalledWith('/resources?type=skill')
  })

  it('uploads FormData without forcing a JSON content type', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"resource_id":"demo"}', {
        status: 201,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    const file = new File(['manifest'], 'resource.zip', { type: 'application/zip' })

    await installResource(file)

    const [, request] = fetchMock.mock.calls[0]
    expect((request as RequestInit).body).toBeInstanceOf(FormData)
    expect(new Headers((request as RequestInit).headers).has('Content-Type')).toBe(false)
  })

  it('sends confirmation for enable and restore actions', async () => {
    const post = vi.spyOn(http, 'post').mockResolvedValue({})

    await enableResource('scope:demo', true)
    await restoreResource('scope:demo', '1.0.0', true)

    expect(post).toHaveBeenNthCalledWith(1, '/resources/scope%3Ademo/enable', { confirmed: true })
    expect(post).toHaveBeenNthCalledWith(2, '/resources/scope%3Ademo/restore', {
      version: '1.0.0',
      confirmed: true
    })
  })

  it('builds paginated audit queries', async () => {
    const request = vi.spyOn(http, 'get').mockResolvedValue({ items: [] })

    await listResourceAudit('scope:demo', 20, 10)

    expect(request).toHaveBeenCalledWith(
      '/resources/audit?offset=20&limit=10&resource_id=scope%3Ademo'
    )
  })

  it('updates a specific resource through its version endpoint', async () => {
    const postForm = vi.spyOn(http, 'postForm').mockResolvedValue({})
    const file = new File(['manifest'], 'resource-v2.zip', { type: 'application/zip' })

    await updateResource('scope:demo', file)

    expect(postForm).toHaveBeenCalledWith('/resources/scope%3Ademo/versions', expect.any(FormData))
  })

  it('uses controlled system dependency endpoints without accepting command input', async () => {
    const get = vi.spyOn(http, 'get').mockResolvedValue([])
    const post = vi.spyOn(http, 'post').mockResolvedValue({})

    await listSystemDependencies()
    await probeSystemDependency('agent-browser-cli')
    await installSystemDependency('agent-browser-cli', true)

    expect(get).toHaveBeenCalledWith('/resources/dependencies')
    expect(post).toHaveBeenNthCalledWith(
      1,
      '/resources/dependencies/agent-browser-cli/probe',
      {}
    )
    expect(post).toHaveBeenNthCalledWith(
      2,
      '/resources/dependencies/agent-browser-cli/install',
      { confirmed: true }
    )
  })

  it('lists, reads, retries and cancels dependency tasks by server task ID', async () => {
    const get = vi.spyOn(http, 'get').mockResolvedValue([])
    const post = vi.spyOn(http, 'post').mockResolvedValue({})

    await listDependencyTasks('agent-browser-cli')
    await getDependencyTask('dep-123')
    await retryDependencyTask('dep-123', true)
    await cancelDependencyTask('dep-456')

    expect(get).toHaveBeenNthCalledWith(
      1,
      '/resources/dependency-tasks?dependency_id=agent-browser-cli'
    )
    expect(get).toHaveBeenNthCalledWith(2, '/resources/dependency-tasks/dep-123')
    expect(post).toHaveBeenNthCalledWith(1, '/resources/dependency-tasks/dep-123/retry', {
      confirmed: true
    })
    expect(post).toHaveBeenNthCalledWith(2, '/resources/dependency-tasks/dep-456/cancel', {})
  })
})
