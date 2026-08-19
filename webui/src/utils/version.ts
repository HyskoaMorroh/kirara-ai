import { compare as semverCompare, valid as semverValid } from 'semver'

const PEP_440_PRERELEASE = /^(\d+\.\d+\.\d+)(a|b|rc)(\d+)$/

export function normalizeAppVersion(rawVersion: string): string {
  const identity = rawVersion.trim().replace(/^v(?=\d)/, '')
  if (!identity) return 'unknown'
  if (identity === 'unknown' || identity.startsWith('dev-')) return identity

  const normalized = identity.replace(PEP_440_PRERELEASE, '$1-$2$3')
  return semverValid(normalized) ?? 'unknown'
}

export const version = {
  /**
   * 获取当前前端版本
   */
  getCurrentVersion(): string {
    return normalizeAppVersion(import.meta.env.VITE_APP_VERSION || '')
  },

  /**
   * 比较两个版本号
   * @param version1 版本号1
   * @param version2 版本号2
   * @returns 如果 version1 > version2 返回 1，如果 version1 < version2 返回 -1，如果相等返回 0
   */
  compare(version1: string, version2: string): number {
    const normalized1 = normalizeAppVersion(version1)
    const normalized2 = normalizeAppVersion(version2)
    if (!semverValid(normalized1) || !semverValid(normalized2)) return 0
    try {
      const result = semverCompare(normalized1, normalized2)
      return result === null ? 0 : result
    } catch (error) {
      console.error('版本号格式错误', error)
      return 0
    }
  }
}
