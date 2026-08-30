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
/**
 * 上游实现自身的扫码登录生命周期。
 *
 * 与 `IMAdapterHealth.status` 分属两个问题：后者说的是「Kirara 与 OneBot 实现
 * 之间的连接」，这里说的是「OneBot 实现与 QQ 之间的登录」。二维码过期要重新扫码，
 * 凭据被拒要改 Token——混成一个状态会把两种完全不同的处置指向同一个方向。
 *
 * 只在适配器配置了上游日志路径时才有值；未配置为 `null`（而不是空对象）。
 * 全部字段脱敏：日志里的账号、设备标识、昵称与头像地址都不会出现在这里。
 */
export interface QRLoginSnapshot {
  state:
    | 'unknown'
    | 'pending'
    | 'waiting_scan'
    | 'scanned'
    | 'expired'
    | 'succeeded'
    | 'failed'
    | 'unavailable'
    | 'quick_login'
  generated_at: string | null
  expires_at: string | null
  validity_seconds: number | null
  /** 距失效剩余秒数；已失效为 0，无二维码为 null。 */
  remaining_seconds: number | null
  latest_qr_path: string | null
  refresh_count: number
  failure_reason:
    | 'qr_code_unavailable'
    | 'no_saved_credential'
    | 'login_failed'
    | 'expired_without_scan'
    | null
  last_event_at: string | null
  /** 可直接展示的一句处置建议。 */
  remediation: string | null
}

export interface IMAdapterHealth {
  status:
    | 'connected'
    | 'waiting'
    | 'disconnected'
    | 'stale'
    | 'initializing'
    | 'credential_rejected'
    | 'upstream_refused'
    /**
     * 链路正常，但适配器的持久化目录已不可写（只读重挂、卷写满）。
     *
     * 与其他状态的区别在于处置方向：这一类要查数据卷和磁盘，
     * 而不是查 Token 或去扫码。此前这种情况会显示为「已连接」，
     * 于是「面板一切正常但消息在丢」无法从界面上看出来。
     */
    | 'storage_unavailable'
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
    | 'data_directory_unwritable'
    | null
  outbox?: Record<string, number> | null
  qr_login?: QRLoginSnapshot | null
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
