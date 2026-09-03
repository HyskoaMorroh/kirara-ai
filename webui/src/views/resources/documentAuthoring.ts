/**
 * 从纯文本创建 / 编辑资源的表单规则。
 *
 * 抽成独立模块而不是留在 `ResourceView.vue` 里，是因为这两条规则是**纯逻辑**，
 * 而留在组件里只能靠 grep 源码来「验证」——那种测试看得见字符串、
 * 看不见行为。这个缺陷正是这样溜过去的：
 *
 * 两处版本号正则曾写成 `/^d+.d+.d+/`（丢了反斜杠）。它是合法正则，
 * 不报任何错，只是永远匹配不上：`/^d+.d+.d+/.test('1.0.0') === false`。
 * 后果不是「校验松了」而是**表单永远存不下去**——`authoringError` 对任何
 * 输入都返回「版本号需形如 1.0.0」，于是整条「从纯文本创建提示词」的路
 * 在界面上完全不可用，而源码 grep 测试全绿。
 */

/** 后端 `_ID_PATTERN`，逐字符同一套：资源 ID 会成为磁盘路径的一段。 */
export const RESOURCE_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/

/** 语义化版本的前三段。后端还要求严格递增，那一条只有服务器判得了。 */
export const VERSION_PREFIX_PATTERN = /^(\d+)\.(\d+)\.(\d+)/

/**
 * 建议下一个版本号：patch 位 +1。
 *
 * 后端要求版本严格递增，让用户自己猜下一个号，是把一个必然会撞上的约束
 * 留给他去撞。解析不出来时退回 `1.0.1`——比留空好，最终由后端校验。
 */
export function suggestNextVersion(current: string): string {
  const match = VERSION_PREFIX_PATTERN.exec(String(current ?? ''))
  if (!match) return '1.0.1'
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`
}

export interface AuthoringFormValues {
  resource_id: string
  content: string
  version: string
}

/**
 * 表单第一个阻止提交的理由，没有则返回空串。
 *
 * `editing` 为真时不校验 ID：改一条已装资源的正文时 ID 不可改，
 * 那时报「资源 ID 不能为空」指向一个用户改不了的字段。
 */
export function authoringFormError(
  form: AuthoringFormValues,
  { editing }: { editing: boolean }
): string {
  if (!String(form.content ?? '').trim()) return '正文不能为空'
  if (!VERSION_PREFIX_PATTERN.test(String(form.version ?? '').trim())) return '版本号需形如 1.0.0'
  if (editing) return ''
  const id = String(form.resource_id ?? '').trim()
  if (!id) return '资源 ID 不能为空'
  if (!RESOURCE_ID_PATTERN.test(id)) return '资源 ID 只能含字母、数字、点、下划线与连字符'
  return ''
}
