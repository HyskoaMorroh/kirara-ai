import { describe, expect, it } from 'vitest'

import {
  createWorkflowCanvasInitializationGuard,
  getWorkflowCanvasInitializationKey
} from '../src/components/workflow/workflow-canvas-initialization'

describe('workflow canvas initialization identity', () => {
  it('reinitializes a reused canvas for each loading generation', () => {
    const guard = createWorkflowCanvasInitializationGuard()
    const workflowA = getWorkflowCanvasInitializationKey('user:a', 1)
    const workflowB = getWorkflowCanvasInitializationKey('user:b', 2)

    expect(guard.shouldInitialize(workflowA)).toBe(true)
    expect(guard.shouldInitialize(workflowA)).toBe(false)
    expect(guard.shouldInitialize(workflowB)).toBe(true)
  })

  it('distinguishes repeated empty-workflow loads', () => {
    const guard = createWorkflowCanvasInitializationGuard()
    const firstEmptyWorkflow = getWorkflowCanvasInitializationKey(':', 3)
    const secondEmptyWorkflow = getWorkflowCanvasInitializationKey(':', 4)

    expect(guard.shouldInitialize(firstEmptyWorkflow)).toBe(true)
    expect(guard.shouldInitialize(secondEmptyWorkflow)).toBe(true)
  })

  it('uses the workflow id as a compatibility fallback without a generation', () => {
    const guard = createWorkflowCanvasInitializationGuard()
    const workflowA = getWorkflowCanvasInitializationKey('user:a')
    const workflowB = getWorkflowCanvasInitializationKey('user:b')

    expect(guard.shouldInitialize(workflowA)).toBe(true)
    expect(guard.shouldInitialize(workflowB)).toBe(true)
    expect(guard.shouldInitialize(workflowB)).toBe(false)
  })
})
