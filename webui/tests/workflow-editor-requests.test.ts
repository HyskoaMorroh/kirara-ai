// @vitest-environment happy-dom

import { mount, flushPromises } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkflowEditor from '../src/views/workflow/WorkflowEditor.vue'

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
}

const deferred = <T>(): Deferred<T> => {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const route = reactive<{ params: { id?: string } }>({ params: { id: 'user:a' } })
const routerPush = vi.fn()
const messageError = vi.fn()
const messageSuccess = vi.fn()
const dialogWarning = vi.fn()
let leaveGuard: (() => unknown) | undefined

const getWorkflow = vi.fn()
const updateWorkflow = vi.fn()
const createWorkflow = vi.fn()
const listBlockTypes = vi.fn()

vi.mock('vue-router', async () => {
  const actual = await vi.importActual<typeof import('vue-router')>('vue-router')
  return {
    ...actual,
    useRoute: () => route,
    useRouter: () => ({ push: routerPush }),
    onBeforeRouteLeave: (guard: () => unknown) => {
      leaveGuard = guard
    }
  }
})

vi.mock('naive-ui', () => ({
  useMessage: () => ({ error: messageError, success: messageSuccess }),
  useDialog: () => ({ warning: dialogWarning }),
  NButton: { template: '<button><slot /></button>' },
  NResult: { template: '<div><slot name="footer" /></div>' },
  NSpin: { template: '<div data-test="loading" />' }
}))

vi.mock('../src/api/workflow', () => ({
  getWorkflow: (...args: unknown[]) => getWorkflow(...args),
  updateWorkflow: (...args: unknown[]) => updateWorkflow(...args),
  createWorkflow: (...args: unknown[]) => createWorkflow(...args)
}))

vi.mock('../src/api/block', () => ({
  listBlockTypes: (...args: unknown[]) => listBlockTypes(...args)
}))

const workflowResponse = (id: string) => ({
  workflow: {
    group_id: 'user',
    workflow_id: id,
    name: `Workflow ${id.toUpperCase()}`,
    description: `${id} description`,
    blocks: [],
    wires: [],
    config: { max_execution_time: 120 }
  }
})

const mountEditor = () =>
  mount(WorkflowEditor, {
    global: {
      stubs: {
        WorkflowCanvas: {
          name: 'WorkflowCanvas',
          props: [
            'blocks',
            'wires',
            'blockTypes',
            'initialName',
            'initialDescription',
            'initialWorkflowId',
            'initialConfig',
            'loading'
          ],
          emits: ['update:blocks', 'update:wires', 'update:config', 'save'],
          template: '<div data-test="canvas" />'
        }
      }
    }
  })

describe('WorkflowEditor request ordering', () => {
  beforeEach(() => {
    route.params.id = 'user:a'
    routerPush.mockReset().mockResolvedValue(undefined)
    messageError.mockReset()
    messageSuccess.mockReset()
    dialogWarning.mockReset()
    getWorkflow.mockReset()
    updateWorkflow.mockReset().mockResolvedValue(workflowResponse('saved'))
    createWorkflow.mockReset()
    listBlockTypes.mockReset().mockResolvedValue({ types: [] })
    leaveGuard = undefined
  })

  it('keeps the newest workflow, loading state, and dirty state when the old request resolves last', async () => {
    const requestA = deferred<ReturnType<typeof workflowResponse>>()
    const requestB = deferred<ReturnType<typeof workflowResponse>>()
    getWorkflow.mockImplementation((_group: string, workflow: string) =>
      workflow === 'a' ? requestA.promise : requestB.promise
    )

    const wrapper = mountEditor()
    await nextTick()
    route.params.id = 'user:b'
    await nextTick()

    requestB.resolve(workflowResponse('b'))
    await flushPromises()
    await nextTick()

    const canvas = wrapper.findComponent({ name: 'WorkflowCanvas' })
    expect(canvas.props('initialName')).toBe('Workflow B')
    canvas.vm.$emit('update:blocks', [{ name: 'edited' }])
    await nextTick()

    requestA.resolve(workflowResponse('a'))
    await flushPromises()
    await nextTick()

    expect(wrapper.findComponent({ name: 'WorkflowCanvas' }).props('initialName')).toBe('Workflow B')
    expect(wrapper.find('[data-test="loading"]').exists()).toBe(false)
    expect(leaveGuard).toBeTypeOf('function')
    leaveGuard?.()
    expect(dialogWarning).toHaveBeenCalledOnce()
  })

  it('binds save to the loaded identity even when route params change before the watcher runs', async () => {
    getWorkflow.mockResolvedValue(workflowResponse('a'))
    const wrapper = mountEditor()
    await flushPromises()
    await nextTick()

    route.params.id = 'user:b'
    wrapper.findComponent({ name: 'WorkflowCanvas' }).vm.$emit(
      'save',
      'Renamed',
      'description',
      'user:renamed'
    )
    await flushPromises()

    expect(updateWorkflow).toHaveBeenCalled()
    expect(updateWorkflow.mock.calls[0].slice(0, 2)).toEqual(['user', 'a'])
  })

  it('aborts outstanding requests on unmount', async () => {
    const pending = deferred<ReturnType<typeof workflowResponse>>()
    let signal: AbortSignal | undefined
    getWorkflow.mockImplementation((_group: string, _workflow: string, requestSignal: AbortSignal) => {
      signal = requestSignal
      return pending.promise
    })

    const wrapper = mountEditor()
    await nextTick()
    expect(signal?.aborted).toBe(false)

    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
