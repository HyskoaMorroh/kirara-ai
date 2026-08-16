import { http } from '@/utils/http'
import { ref, computed } from 'vue'

export interface BlockInput {
  name: string
  label: string
  description: string
  type: string
  required: boolean
  default?: any
}

export interface BlockOutput {
  name: string
  label: string
  description: string
  type: string
}

export interface BlockConfig {
  label: string
  name: string
  description: string
  type: string
  required: boolean
  default?: any
  has_options?: boolean
  options?: string[]
}

export interface BlockType {
  type_name: string
  name: string
  label: string
  description: string
  /** 画布上节点标题栏颜色，后端未配置时为空字符串，前端回退到主题色 */
  color?: string
  inputs: BlockInput[]
  outputs: BlockOutput[]
  configs: BlockConfig[]
}

export interface BlockTypeListResponse {
  types: BlockType[]
}

export interface BlockTypeResponse {
  type: BlockType
}

export async function listBlockTypes() {
  return http.get<BlockTypeListResponse>('/block/types')
}

export async function getBlockType(typeName: string) {
  return http.get<BlockTypeResponse>(`/block/types/${typeName}`)
}

export async function getTypeCompatibility() {
  return http.get<Record<string, Record<string, boolean>>>('/block/types/compatibility')
}
