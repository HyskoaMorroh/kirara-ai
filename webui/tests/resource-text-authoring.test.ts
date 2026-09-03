// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 提示词必须能从界面创建，而不是只能上传一个手工打包的 ZIP。
 *
 * 参考界面的「提示词管理」页有一个「+ 添加提示词」按钮：填名称、描述、正文，保存
 * （`docs/superpowers/plans/ccs-ui-inventory.md` 的「五、提示词管理」）。
 * 本项目此前唯一的写入路径是 multipart ZIP 上传——用户得自己按 `manifest.json`
 * 的八个必填字段手算 `content_sha256`（`path:size:sha256` 逐行拼接再哈希）、
 * 打包、再上传。提示词这个类型的**全部内容就是正文**，要求为一段纯文本走这一遍，
 * 等于把它最主要的用法排除在产品之外。
 *
 * 这组用例锁住的是界面侧的四条边界：
 *
 * 1. 入口存在，且与「发现并安装」分开——后者从外部拿现成的包，前者写自己的内容；
 * 2. 编辑走**新版本**而不是就地改：`content_sha256` 把清单与文件绑在一起，
 *    就地改的后果不是「改了没生效」而是下一次载入直接失败；
 * 3. 「编辑正文」只对纯文本类型出现——skill 的正文是给模型的行为说明，
 *    hook 是能起进程的命令声明；
 * 4. 前端不提交 `content_sha256`：摘要由服务器算，自带摘要等于让调用方自己
 *    决定「校验通过」。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('API 客户端', () => {
  it('声明创建与新版本两个函数', () => {
    expect(apiSource).toContain('authorResourceDocument')
    expect(apiSource).toContain('authorResourceDocumentVersion')
  })

  it('创建用 POST /resources/documents', () => {
    expect(apiSource).toMatch(/authorResourceDocument\([\s\S]{0,400}?'\/resources\/documents'/)
  })

  it('新版本用 PUT，落在资源自己的路径下', () => {
    expect(apiSource).toMatch(
      /authorResourceDocumentVersion\([\s\S]{0,600}?http\.put[\s\S]{0,200}?\/documents/
    )
  })

  it('只允许纯文本三个类型', () => {
    // skill / hook / mcp 的正文会被执行或解析成行为声明，不能从输入框创建。
    expect(apiSource).toMatch(/type:\s*'prompt'\s*\|\s*'memory'\s*\|\s*'session'/)
  })

  it('不提交 content_sha256', () => {
    // 摘要由服务器算。请求方自带摘要等于让调用方自己决定「校验通过」。
    //
    // 断言钉在**载荷字段**上而不是「文件里不出现这个词」：注释里说明为什么不传它
    // 是应该的，钉字符串会把一条正确的注释判成缺陷。
    const block = apiSource.slice(
      apiSource.indexOf('export async function authorResourceDocument'),
      apiSource.indexOf('export async function listResourceBackups')
    )
    expect(block.length).toBeGreaterThan(200)
    expect(block).not.toMatch(/content_sha256\s*[:?]/)
  })
})

describe('界面入口', () => {
  it('工具栏有新建入口', () => {
    expect(viewSource).toContain('data-test="author-document"')
    expect(viewSource).toContain('新建提示词')
  })

  it('新建与「发现并安装」是两个按钮', () => {
    // 两件不同的事：一个从外部拿现成的包，一个写自己的内容。
    expect(viewSource).toContain("openPanel('discover')")
    expect(viewSource).toContain('openAuthoring')
  })

  it('表单收名称、描述、正文与版本', () => {
    for (const test of [
      'authoring-resource-id',
      'authoring-type',
      'authoring-version',
      'authoring-content',
      'authoring-save'
    ]) {
      expect(viewSource, `缺 data-test="${test}"`).toContain(`data-test="${test}"`)
    }
  })

  it('正文是多行输入而不是单行', () => {
    // 提示词按行读；单行输入框里改一段十行的规则等于盲改。
    expect(viewSource).toMatch(/authoring-content[\s\S]{0,400}type="textarea"|type="textarea"[\s\S]{0,400}authoring-content/)
  })

  it('编辑正文的入口只对纯文本类型出现', () => {
    // 表格列用渲染函数，`data-test` 是对象键（`'data-test': 'edit-document'`）
    // 而不是模板属性。只匹配 `data-test="..."` 会漏掉渲染函数写法——
    // 这类「断言钉在写法上」的失败此前反复出现过。两种形态都接受。
    expect(viewSource).toMatch(/data-test["']?\s*[:=]\s*["']edit-document["']/)
    expect(viewSource).toMatch(/isAuthorable\(row\)/)
  })

  it('可创建类型只有三个', () => {
    expect(viewSource).toMatch(/AUTHORABLE_TYPES\s*=\s*\['prompt',\s*'memory',\s*'session'\]/)
  })

  it('编辑时 ID 与类型不可改', () => {
    // 改 ID 等于建一个新资源，改类型会让已有绑定指向一个不同语义的东西。
    expect(viewSource).toMatch(/authoring-resource-id[\s\S]{0,300}:disabled="Boolean\(authoringTarget\)"/)
  })

  it('编辑时预填当前正文', () => {
    // 留空会让用户以为旧内容已经没了；这是编辑而不是重写。
    expect(viewSource).toMatch(/openAuthoringForEdit[\s\S]{0,900}getResourceContent/)
  })

  it('编辑时预填一个递增的版本号', () => {
    // 后端要求严格递增。让用户自己猜下一个版本号是把一个必然的约束留给他去撞。
    expect(viewSource).toContain('suggestNextVersion')
  })

  it('保存前在前端就拦下空正文与非法 ID', () => {
    // 校验规则本身在 `documentAuthoring.ts` 里，由
    // `resource-authoring-validation.test.ts` **调用函数**验证行为——
    // 曾经这里只 grep 源码字符串，而那两条正则丢了反斜杠
    // （`/^d+.d+.d+/`，合法但永远匹配不上），字符串在、行为不在，测试照样绿。
    // 这一条只确认组件真的接上了那个校验，且拦得住时不发请求。
    expect(viewSource).toMatch(/authoringFormError\(authoringForm\.value/)
    expect(viewSource).toMatch(/if \(authoringError\.value\)[\s\S]{0,200}return/)
  })

  it('保存成功后说明还需确认才生效', () => {
    // 装完保持停用是刻意的：提示词会进系统提示词、改变每一轮回复。
    expect(viewSource).toMatch(/请确认后再启用/)
  })

  it('保存后重新拉取列表', () => {
    expect(viewSource).toMatch(/saveAuthoredDocument[\s\S]{0,1800}loadResources\(\)/)
  })
})
