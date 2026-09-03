import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 资源正文必须能在界面上看到（需求 10 的「提示词管理」）。
 *
 * prompt 这个类型的全部内容就是正文，而此前界面上只能看到安装 / 启用 / 停用 /
 * 版本 / 备份这些元数据——「提示词管理」回答不了它唯一要回答的问题
 * 「现在生效的提示词到底写了什么」。后端的 `read_entry_metadata()` 早就存在
 * 且返回的正是这些，但零调用点。
 *
 * 三条边界一并钉住：
 * - **只读**：正文不可就地编辑（摘要与清单绑定，就地改会让资源在下一次载入
 *   时失败），改正文走「上传 ZIP 升级」；
 * - **摘要一起显示**：「你看到的」与「运行时载入的」是同一份必须可自证；
 * - **可切版本**：回退前想先看看旧版写了什么，是这个入口最实际的用途之一。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('API 客户端', () => {
  it('声明了 getResourceContent', () => {
    expect(apiSource).toContain('getResourceContent')
  })

  it('打到只读的 content 路由', () => {
    expect(apiSource).toContain('/content')
    expect(apiSource).toMatch(/getResourceContent[\s\S]{0,300}http\.get/)
  })

  it('可以指定版本', () => {
    expect(apiSource).toMatch(/getResourceContent\([^)]*version\??:\s*string/)
  })

  it('返回类型带已校验摘要与权限', () => {
    expect(apiSource).toMatch(/content_sha256:\s*string/)
    expect(apiSource).toMatch(/permissions:\s*string\[\]/)
  })

  it('没有写入正文的接口', () => {
    // 就地改正文会让资源在下一次载入时失败（不是「改了没生效」）。
    expect(apiSource).not.toMatch(/updateResourceContent|setResourceContent/)
  })
})

describe('详情弹窗里的正文', () => {
  it('有正文区块', () => {
    expect(viewSource).toContain('data-test="entry-content"')
    expect(viewSource).toContain('入口正文')
  })

  it('打开详情时就把正文取回来', () => {
    expect(viewSource).toMatch(/openDetail[\s\S]{0,400}loadEntryContent/)
  })

  it('显示摘要是否与版本记录一致', () => {
    // 不显示这一行时用户只能靠信任，而这正是完整性校验存在的理由。
    expect(viewSource).toContain('data-test="entry-digest"')
    // 比较逻辑（按版本号取记录再比摘要、空摘要不算一致、大小写敏感）由
    // `resource-entry-digest.test.ts` 调用函数验证。这里只确认接线。
    expect(viewSource).toMatch(/compareEntryDigest\(entryContent\.value/)
    expect(viewSource).toMatch(/from '\.\/entryDigest'/)
  })

  it('多版本时可以切换看旧版正文', () => {
    expect(viewSource).toMatch(/entryContentVersion/)
    expect(viewSource).toMatch(/selectedResource\.versions\.length > 1/)
  })

  it('说明正文为什么不能就地编辑', () => {
    expect(viewSource).toContain('不可就地编辑')
    // 并指出受支持的改法，否则这段说明读起来像「功能缺失」。
    expect(viewSource).toContain('上传 ZIP 升级')
  })

  it('读取失败只影响这一块，不影响详情其余字段', () => {
    expect(viewSource).toContain('entryContentError')
  })

  it('正文保留换行与缩进', () => {
    // 提示词的段落与缩进是它语义的一部分；按普通段落折行会让
    // 「先给结论，再给依据」这类结构读不出来。
    expect(viewSource).toContain('white-space: pre-wrap')
  })
})
