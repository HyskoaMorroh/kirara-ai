import { http } from '@/utils/http'

export interface Workflow {
  group_id: string
  workflow_id: string
  name: string
  description: string
  blocks: any[]
  wires: any[]
  metadata?: Record<string, any>
  config: WorkflowConfig
}

export interface WorkflowInfo {
  group_id: string
  workflow_id: string
  name: string
  description: string
  block_count: number
  metadata?: Record<string, any>
}

export interface WorkflowConfig {
  max_execution_time: number
}

export interface WorkflowListResponse {
  workflows: WorkflowInfo[]
}

export interface WorkflowResponse {
  workflow: Workflow
}

export interface BlockInstance {
  type_name: string
  name: string
  config: Record<string, any>
  position?: {
    x: number
    y: number
  } | null
}

export interface Wire {
  source_block: string
  source_output: string
  target_block: string
  target_input: string
}

export interface WorkflowDefinition extends WorkflowInfo {
  blocks: BlockInstance[]
  wires: Wire[]
}

export interface WorkflowValidationIssue {
  severity: 'error' | 'warning'
  code: string
  message: string
  node_name?: string | null
  port_name?: string | null
}

export interface WorkflowValidationResponse {
  errors: WorkflowValidationIssue[]
  warnings: WorkflowValidationIssue[]
}

export async function listWorkflows() {
  return http.get<WorkflowListResponse>('/workflow')
}

export async function getWorkflow(groupId: string, workflowId: string, signal?: AbortSignal) {
  return http.get<WorkflowResponse>(`/workflow/${groupId}/${workflowId}`, { signal })
}

export async function createWorkflow(
  groupId: string,
  workflowId: string,
  data: Workflow,
  signal?: AbortSignal
) {
  return http.post<WorkflowResponse>(`/workflow/${groupId}/${workflowId}`, data, { signal })
}

export async function updateWorkflow(
  groupId: string,
  workflowId: string,
  data: Workflow,
  signal?: AbortSignal
) {
  return http.put<WorkflowResponse>(`/workflow/${groupId}/${workflowId}`, data, { signal })
}

export async function deleteWorkflow(groupId: string, workflowId: string) {
  return http.delete(`/workflow/${groupId}/${workflowId}`)
}

/** 仅预检草稿结构，不保存、执行或修改当前工作流。 */
export async function validateWorkflow(data: Workflow) {
  return http.post<WorkflowValidationResponse>('/workflow/validate', data)
}
