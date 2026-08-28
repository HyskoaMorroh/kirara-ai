import { http } from '@/utils/http'

export interface IMPlatform {
  name: string
  adapter: string
  config: Record<string, any>
  enable: boolean
  accounts: IMAccount[]
}

export interface IMAccount {
  id: string
  platform: string
  name: string
  status: 'online' | 'offline'
  config: Record<string, any>
}

export interface IMAdapterInfo {
  name: string
  localized_name: string | null
  localized_description: string | null
  detail_info_markdown: string | null
}

export interface UserProfile {
  user_id: string
  username: string
  display_name: string
  description: string
  avatar_url: string
}

export interface IMAdapter {
  name: string
  adapter: string
  is_running: boolean
  enable: boolean
  config: Record<string, any>
  bot_profile: UserProfile | null
  health: IMAdapterHealth | null
}

/**
 * 适配器连接健康。
 *
 * `status` 把重启周期里看起来一样、原因完全不同的几种情况分开：
 * `initializing` 表示进程已起但适配器还没启动完，此时并没有出错；
 * `waiting` 表示已挂载、上游实现尚未接入；
 * `credential_rejected` 表示上游确实接进来了但凭据不对，重试无用；
 * `upstream_refused` 表示上游握手不被接受（角色缺失或账号标识缺失）；
 * `stale` 表示曾经连上但心跳停了；`disconnected` 表示适配器已停止。
 *
 * `last_disconnect_reason` 是固定的原因码，不含任何凭据或账号信息。
 */
export interface IMAdapterHealth {
  status:
    | 'connected'
    | 'waiting'
    | 'disconnected'
    | 'stale'
    | 'initializing'
    | 'credential_rejected'
    | 'upstream_refused'
  connected_account_count: number
  last_heartbeat_age_seconds: number | null
  adapter_started?: boolean | null
  websocket_connected?: boolean | null
  external_login_status?:
    | 'unknown'
    | 'upstream_reported_online'
    | 'upstream_reported_offline'
    | null
  last_disconnect_reason?:
    | 'access_token_missing'
    | 'access_token_mismatch'
    | 'invalid_client_role'
    | 'missing_self_id'
    | 'heartbeat_timeout'
    | 'upstream_lifecycle_disconnect'
    | 'adapter_stopped'
    | null
  outbox?: Record<string, number> | null
}

// 适配器类型枚举
export enum AdapterType {
  // 主动类 - 一个配置项对应一个bot实例
  ACTIVE = 'active',
  // 被动类 - 1对多，一个配置项对应多个bot实例
  PASSIVE_MANY = 'passive_many',
  // 被动类 - 1对1，一个配置项对应一个bot实例
  PASSIVE_ONE = 'passive_one'
}

// 适配器实例接口
export interface IMAdapterInstance {
  id: string
  adapter_name: string
  name: string
  status: 'online' | 'offline'
  config: Record<string, any>
  created_at: string
  updated_at: string
}

// 适配器详情接口
export interface IMAdapterDetail {
  name: string
  adapter: string
  type: AdapterType
  is_running: boolean
  config: Record<string, any>
  bot_profile: UserProfile | null
  health: IMAdapterHealth | null
}

export interface ConfigSchema {
  title: string
  type: string
  properties: Record<
    string,
    {
      title: string
      type: string
      description?: string
      default?: any
      minimum?: number
      maximum?: number
      enum?: any[]
      enumNames?: string[]
      readonly?: boolean
    }
  >
  required?: string[]
}

export const imApi = {
  /**
   * 获取适配器类型列表
   */
  getAdapterTypes() {
    return http.get<{ types: string[]; adapters: Record<string, IMAdapterInfo> }>('/im/types')
  },

  /**
   * 获取适配器列表
   */
  getAdapters() {
    return http.get<{ adapters: IMAdapter[] }>('/im/adapters')
  },

  /**
   * 获取适配器详情
   */
  getAdapter(adapterId: string) {
    return http.get<{ adapter: IMAdapter }>(`/im/adapters/${adapterId}`)
  },

  /**
   * 获取适配器详细信息（包含类型和实例）
   */
  getAdapterDetail(adapterId: string) {
    return http.get<{ adapter: IMAdapterDetail }>(`/im/adapters/${adapterId}`)
  },

  /**
   * 创建适配器
   */
  createAdapter(adapter: Omit<IMAdapter, 'is_running'>) {
    return http.post<{ adapter: IMAdapter }>('/im/adapters', adapter)
  },

  /**
   * 更新适配器
   */
  updateAdapter(adapterId: string, adapter: Omit<IMAdapter, 'is_running'>) {
    return http.put<{ adapter: IMAdapter }>(`/im/adapters/${adapterId}`, adapter)
  },

  /**
   * 删除适配器
   */
  deleteAdapter(adapterId: string) {
    return http.delete<void>(`/im/adapters/${adapterId}`)
  },

  /**
   * 启动适配器
   */
  startAdapter(adapterId: string) {
    return http.post<void>(`/im/adapters/${adapterId}/start`)
  },

  /**
   * 停止适配器
   */
  stopAdapter(adapterId: string) {
    return http.post<void>(`/im/adapters/${adapterId}/stop`)
  },

  /**
   * 获取适配器配置模式
   */
  getAdapterConfigSchema(adapterType: string) {
    return http.get<{ configSchema: ConfigSchema }>(`/im/types/${adapterType}/config-schema`)
  }
}
