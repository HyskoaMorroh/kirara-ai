import { compare as semverCompare, valid as semverValid } from 'semver'

const PEP_440_PRERELEASE = /^(\d+\.\d+\.\d+)(a|b|rc)(\d+)$/

//: 预发布序号在比较时必须成为**独立的数字标识符**。
//:
//: semver 只在标识符「全是数字」时按数值比较；`bNN` 这种「字母 + 两位数」是一个
//: 字母数字标识符，于是按字典序把 `b11` 排在 `b8` 之前。序号进入两位数后，
//: 比较一个 `b8` 与一个 `b11` 会返回 1——装着 `b11` 的用户被提示「升级」到 `b8`，
//: 而 `b10` 这类真的新版本反而不会被提示。PEP 440 的序号是数字，
//: 比较必须保持同一个序。
//:
//: 只在比较时插入这个点，**不改变** `normalizeAppVersion` 的输出：那个值会
//: 直接显示在版本卡片上，也要和 npm 上的包版本（`X.Y.Z-bNN` 形态）对得上。
//: 展示形态与排序形态是两件事，混在一起才会出现「为了排序而改标签」。
//:
//: 本文件刻意不写任何具体版本号：版本只由 `pyproject.toml` 推导，
//: 源码里出现字面版本串会让它变成一个需要跟着发布一起改的版本载体。
const COMPARABLE_PRERELEASE = /^(\d+\.\d+\.\d+)-(a|b|rc)(\d+)$/

export function normalizeAppVersion(rawVersion: string): string {
  const identity = rawVersion.trim().replace(/^v(?=\d)/, '')
  if (!identity) return 'unknown'
  if (identity === 'unknown' || identity.startsWith('dev-')) return identity

  const normalized = identity.replace(PEP_440_PRERELEASE, '$1-$2$3')
  return semverValid(normalized) ?? 'unknown'
}

/** 把规范化后的版本改写成序号可按数值比较的形态，仅供 `compare` 使用。 */
function toComparableVersion(normalizedVersion: string): string {
  const comparable = normalizedVersion.replace(COMPARABLE_PRERELEASE, '$1-$2.$3')
  // 插点后仍必须是合法 semver；不合法就退回原值，让调用方按「无法比较」处理。
  return semverValid(comparable) ? comparable : normalizedVersion
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
      const result = semverCompare(
        toComparableVersion(normalized1),
        toComparableVersion(normalized2)
      )
      return result === null ? 0 : result
    } catch (error) {
      console.error('版本号格式错误', error)
      return 0
    }
  }
}
