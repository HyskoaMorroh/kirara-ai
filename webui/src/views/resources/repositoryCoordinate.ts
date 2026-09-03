/**
 * 把用户粘贴进来的东西解析成一个仓库坐标。
 *
 * 为什么需要它：登记仓库此前是三个独立输入框（所有者 / 仓库 / 分支），而用户
 * 手上拿到的东西**一定**是一个 URL——从浏览器地址栏或 `git clone` 命令里复制的。
 * 要求他把 URL 拆成三段再分别填，是把一次粘贴变成三次手抄，而手抄正是拼错坐标
 * 的来源。
 *
 * **解析放在前端，后端校验一个字不放宽。** 后端那三个字段的正则是安全边界：
 * 它们会拼进 GitHub 归档 URL 与磁盘路径。放宽它等于把「URL 解析写错」升级成
 * 「一次路径穿越的机会」。这里解析完仍然提交三个干净字段，两层校验形成双重
 * 保险而不是互相替代——所以下面这些正则与后端的 `_GITHUB_PART` / `_BRANCH_PART`
 * 是刻意重复的。
 */

/** 一个已解析的坐标。`branch` 为 `null` 表示输入里没有分支信息。 */
export interface RepositoryCoordinate {
  owner: string
  name: string
  /**
   * `null` 表示「输入里没给」，由调用方决定缺省值。
   *
   * 不在这一层写死 `main`：表单里的分支输入框可能已经填了别的，
   * 替它填一个默认值会把用户的输入覆盖掉。
   */
  branch: string | null
}

/** 与后端 `_GITHUB_PART` 同一套：字母数字加 `.`/`_`/`-`，不含斜杠。 */
const OWNER_OR_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$/

/** 与后端 `_BRANCH_PART` 同一套：允许斜杠（`release/1.x`），但不含可疑字符。 */
const BRANCH = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}$/

/** 只认 github.com 及其 www 前缀：后端只会去这一个主机拉归档。 */
const GITHUB_HOSTS = new Set(['github.com', 'www.github.com'])

function isSafeSegment(value: string, pattern: RegExp): boolean {
  if (!pattern.test(value)) return false
  // `..` 在正则里是合法的（`.` 被允许），但它是路径穿越的形状，单独挡掉。
  if (value === '.' || value === '..' || value.includes('..')) return false
  return true
}

function normalizeBranch(segments: string[]): string | null {
  if (!segments.length) return null
  const branch = segments.join('/')
  if (!isSafeSegment(branch, BRANCH)) return null
  if (branch.endsWith('/') || branch.includes('//')) return null
  return branch
}

/**
 * 解析 `owner/name`、完整 GitHub URL、或 `git@github.com:owner/name.git`。
 *
 * 认不出来时返回 `null`——调用方据此提示用户，而不是提交一个猜出来的坐标。
 * 猜错的坐标会以「仓库不存在」失败，而那条错误指向的原因是错的。
 */
export function parseRepositoryCoordinate(input: string): RepositoryCoordinate | null {
  let value = String(input ?? '').trim()
  if (!value) return null

  // `git@github.com:owner/name.git` —— `git clone` 的 SSH 形态。
  const sshMatch = /^git@([^:]+):(.+)$/.exec(value)
  if (sshMatch) {
    if (!GITHUB_HOSTS.has(sshMatch[1].toLowerCase())) return null
    value = sshMatch[2]
  } else {
    // 协议可省略（`github.com/owner/name`）。补一个才能交给 URL 解析器，
    // 而补之前要先确认它看起来像个主机——否则 `anthropics/skills` 会被
    // 解析成主机 `anthropics`。
    const withProtocol = /^https?:\/\//i.test(value)
      ? value
      : /^(?:www\.)?github\.com\//i.test(value)
        ? `https://${value}`
        : null
    if (withProtocol) {
      let url: URL
      try {
        url = new URL(withProtocol)
      } catch {
        return null
      }
      if (!GITHUB_HOSTS.has(url.hostname.toLowerCase())) return null
      // 查询串与锚点不是坐标的一部分（`?tab=readme` 之类）。
      value = url.pathname
    }
  }

  value = value.replace(/\.git$/i, '').replace(/\/+$/, '').replace(/^\/+/, '')
  if (!value) return null
  // 查询串与锚点在非 URL 形态下也可能被粘进来。
  if (/[?#\\]/.test(value)) return null

  const segments = value.split('/')
  if (segments.length < 2) return null
  const [owner, name, ...rest] = segments
  if (!isSafeSegment(owner, OWNER_OR_NAME) || !isSafeSegment(name, OWNER_OR_NAME)) {
    return null
  }

  // 只有 `/tree/<branch>` 表示分支。`/blob/...`、子目录深链等一律忽略——
  // 它们描述的是仓库里的位置，不是坐标。
  let branch: string | null = null
  if (rest[0] === 'tree') {
    branch = normalizeBranch(rest.slice(1))
    if (branch === null && rest.length > 1) return null
  }

  return { owner, name, branch }
}
