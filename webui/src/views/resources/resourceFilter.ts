/**
 * 已安装资源的关键词过滤谓词。
 *
 * 类型筛选走服务端（改 type 会重新 `GET /resources?type=`），关键词过滤留在前端：
 * 列表已经在手里，为了搜一个词再往服务器跑一趟只会让输入框卡顿，
 * 也会让「边打边看」这个最自然的用法变成每敲一个字符发一次请求。
 *
 * 匹配面覆盖用户实际记得住的三个字段。只匹配 `name` 不够——
 * 装过 `agent-browser` 的人往往记得的是 id 片段，不是显示名「Agent Browser」。
 */

/** 过滤只需要这三个字段，不要求调用方传完整的 ManagedResource。 */
export interface ResourceKeywordFields {
  resource_id?: string | null
  name?: string | null
  description?: string | null
}

/**
 * 资源是否命中关键词。
 *
 * 空关键词（含纯空白）放行所有资源——「没在搜」不等于「搜不到」。
 * 大小写不敏感；中文按子串匹配，不做分词，避免把「浏览器」切成搜不到的碎片。
 */
export function matchesResourceKeyword(resource: ResourceKeywordFields, keyword: string): boolean {
  const needle = String(keyword ?? '').trim().toLowerCase()
  if (!needle) return true

  return [resource.resource_id, resource.name, resource.description].some((field) =>
    String(field ?? '').toLowerCase().includes(needle)
  )
}

/**
 * 已知资源类型的展示顺序，与「资源类型」下拉保持一致。
 *
 * 摘要条和筛选器读的是同一批类型，顺序不同会变成两套心智模型：
 * 用户在摘要里第三个看到 Hooks，去下拉里却要往下翻到第五个。
 */
export const RESOURCE_TYPE_ORDER = ['skill', 'prompt', 'session', 'memory', 'hook', 'mcp'] as const

/** 没带 `type` 的条目归到这里，而不是悄悄消失。 */
const UNKNOWN_TYPE = 'unknown'

/**
 * 按类型统计已安装资源。
 *
 * 两点刻意为之：
 *
 * 1. **装了 0 个的类型也要出现，值为 0。** 把 0 项省掉等于把「一个都没装」和
 *    「装了但没显示出来」画成同一个界面。绑定 Agent 时挑不到 Hook，
 *    绝大多数时候原因就是压根没装——这个 0 是诊断信息，不是噪声。
 * 2. **未知类型不丢弃。** 后端加一种资源类型时，前端宁可多出一行陌生名字，
 *    也不要让分项之和跟总数对不上——数字打架比多一行更难排查。
 */
export function countResourcesByType(
  resources: readonly { type?: string | null }[]
): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const type of RESOURCE_TYPE_ORDER) counts[type] = 0

  for (const resource of resources) {
    const key = String(resource?.type ?? '').trim() || UNKNOWN_TYPE
    counts[key] = (counts[key] ?? 0) + 1
  }

  return counts
}
