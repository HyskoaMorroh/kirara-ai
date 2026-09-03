/**
 * 资源正文与已注册版本的摘要核对。
 *
 * 抽成纯函数，是因为「你看到的正文与运行时载入的是同一份」这个论断**就是**
 * 这段代码，而它此前只被 `expect(viewSource).toContain('entryDigestMatches')`
 * 覆盖——那只证明这个名字存在。
 *
 * 判断错的两个方向都很糟：
 *
 * - 该不匹配时说匹配 → 用户以为看到的就是运行时那份，而磁盘上的文件已被篡改；
 * - 该匹配时说不匹配 → 一个完好的资源被显示成可疑，用户去排查一个不存在的问题。
 *
 * 因此这里的默认答案是**「不成立」**：缺少任何一个比较所需的输入时返回 false，
 * 而不是「没发现问题所以算通过」。
 */

export interface VersionRecord {
  version: string
  content_sha256: string
}

export interface EntryContent {
  version: string
  content_sha256: string
}

/**
 * 读到的正文是否与注册表里那一版的摘要一致。
 *
 * 必须按**版本号**取记录再比摘要，不能只看「有没有哪一版的摘要等于它」：
 * 后者在多版本资源上会把「读到的是旧版正文」误判成一致。
 */
export function entryDigestMatches(
  entry: EntryContent | null | undefined,
  versions: readonly VersionRecord[] | null | undefined
): boolean {
  if (!entry || !versions) return false
  const record = versions.find((item) => item.version === entry.version)
  if (!record) return false
  // 空摘要不算一致：两边都空会让 `===` 成立，而那意味着两边都不知道摘要。
  if (!record.content_sha256 || !entry.content_sha256) return false
  return record.content_sha256 === entry.content_sha256
}
