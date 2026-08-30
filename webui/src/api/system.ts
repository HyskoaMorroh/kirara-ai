import { http } from '@/utils/http'

/**
 * 就绪检查。
 *
 * 后端 `GET /system/readiness` 一直提供 7 项检查，每项都带 `summary` 与
 * **可执行的 `remediation`**。此前前端一行都没有引用它：那份诊断只能靠
 * `curl` 看到，而最需要它的正是刚部署完、还没配好任何东西的人。
 */
export type ReadinessStatus = 'pass' | 'warn' | 'fail' | 'skip'

export interface ReadinessCheck {
  id: string
  status: ReadinessStatus
  /** 一句话说明当前状态。 */
  summary: string
  /** 下一步该做什么。`pass` 时是「无需处理」。 */
  remediation: string
  /** 有界的结构化证据（计数、路径条数等），不含密钥与消息正文。 */
  evidence: Record<string, unknown>
}

export interface ReadinessResponse {
  /** 任一 `fail` 即为 false；`warn` 不阻塞。 */
  ready: boolean
  timestamp: string
  checks: ReadinessCheck[]
}

/** 每项检查的中文标题。后端只给稳定 id，避免把展示文案锁进 API。 */
export const READINESS_CHECK_LABELS: Record<string, string> = {
  data_directories_writable: '数据目录可写',
  configuration_parseable: '配置文件可解析',
  workflows_valid: '工作流有效',
  dispatch_targets_exist: '调度目标存在',
  im_available: '聊天平台已连接',
  llm_available: '模型后端可用',
  mcp_health: 'MCP 服务健康'
}

export const systemApi = {
  /**
   * 读取就绪状态。需要鉴权，且**不改变任何服务器状态**。
   */
  getReadiness(): Promise<ReadinessResponse> {
    return http.get<ReadinessResponse>('/system/readiness')
  }
}
