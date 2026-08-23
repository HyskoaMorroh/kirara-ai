export type WorkflowCanvasInitializationKey = string | number

export const getWorkflowCanvasInitializationKey = (
  workflowId?: string,
  initializationKey?: WorkflowCanvasInitializationKey
) => {
  const source =
    initializationKey === undefined
      ? `workflow:${workflowId ?? ''}`
      : `generation:${typeof initializationKey}:${String(initializationKey)}`
  return `${source}\u0000${workflowId ?? ''}`
}

export const createWorkflowCanvasInitializationGuard = () => {
  let initializedKey: string | null = null

  return {
    shouldInitialize(key: string) {
      if (initializedKey === key) return false
      initializedKey = key
      return true
    },
    reset() {
      initializedKey = null
    }
  }
}
