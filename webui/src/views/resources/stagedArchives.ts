/**
 * 服务器上待安装包（`resources/imports`）的三个显示判断。
 *
 * 抽成纯函数，是因为这三件事决定用户**能不能点、点了做什么**，而它们此前只被
 * 源码 grep 覆盖（`toMatch(/entry\.installed && !entry\.is_upgrade/)` 之类）。
 * 那种断言检查模板里有没有那个表达式，不检查三态组合下的结论对不对。
 *
 * 三个状态两两不同，混淆任意一对都会误导：
 *
 * - 「盘上有 2.0.0、已装 1.0.0」→ 该点「更新」；
 * - 「已装 2.0.0」→ 什么都不用做，按钮该禁用；
 * - 「读不出这个包」→ 按钮该禁用，且要说清是读取失败而不是已安装。
 */

export interface StagedArchive {
  file_name: string
  installed?: boolean
  installed_version?: string | null
  is_upgrade?: boolean
  error?: string | null
}

/**
 * 这个包能不能安装。
 *
 * 读不出来时禁用（`error` 非空）：那时后端连清单都没解析成功，
 * 点下去必定失败，而失败信息会指向解包而不是「这个文件坏了」。
 *
 * 已装且不是升级时禁用：那时装一遍只会得到「版本必须递增」。
 */
export function canInstallStaged(entry: StagedArchive): boolean {
  if (entry.error) return false
  return !(entry.installed && !entry.is_upgrade)
}

/**
 * 按钮上写什么。
 *
 * 升级写「更新」而不是「安装」：后者让人以为会装出第二份，
 * 而实际行为是给同一个资源加一个新版本并自动备份旧版。
 */
export function stagedActionLabel(entry: StagedArchive): '更新' | '安装' {
  return entry.is_upgrade ? '更新' : '安装'
}

/**
 * 这一行该显示哪种状态标签。
 *
 * `upgradable` 优先于 `installed`：一个可升级的包**也**是已安装的，
 * 而这两种处境的处置不同（点更新 / 什么都不用做）。
 * 判断顺序写反会让所有可升级的包都显示成「已安装」，用户于是不会去点。
 */
export function stagedStatus(
  entry: StagedArchive
): 'error' | 'upgradable' | 'installed' | 'new' {
  if (entry.error) return 'error'
  if (entry.is_upgrade) return 'upgradable'
  if (entry.installed) return 'installed'
  return 'new'
}
