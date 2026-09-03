// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { matchesResourceKeyword } from '../src/views/resources/resourceFilter'

/**
 * 搜索框承诺的每一个匹配面都必须真的能命中。
 *
 * 发现过程：`resourceFilter.ts` 读 `resource.name` / `resource.description`，
 * 而 `ManagedResource` 此前**没有这两个字段**——服务端把它们放在
 * `source_metadata` 里（`author_document`、`install_skill`），目录安装
 * （`_install_builtin`）连放都没放。于是输入框写着「搜索名称、ID 或描述」，
 * 实际只有 ID 那一面在工作，另外两面从未命中过任何东西。
 *
 * 这个缺陷躲过了类型检查：谓词的入参类型把 `name` / `description` 声明成可选，
 * 传一个没有它们的对象完全合法；也躲过了既有测试，因为
 * `resource-installed-search.test.ts` 手写的对象**带**这两个字段，
 * 那是一个真实响应里不存在的形状。
 *
 * 因此这里断言的不是谓词本身（那条已经有测试），而是**谓词读的字段真的存在**，
 * 以及正文这一面按需求 10 接上了服务器。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('ManagedResource 声明了谓词读的字段', () => {
  const managedResource = apiSource.slice(
    apiSource.indexOf('export interface ManagedResource'),
    apiSource.indexOf('export interface ResourceRepository')
  )

  it('name 与 description 在类型里', () => {
    // 缺了这两行，谓词读到的永远是 undefined，而 tsc 不会报任何错。
    expect(managedResource).toMatch(/\bname\?: string \| null/)
    expect(managedResource).toMatch(/\bdescription\?: string \| null/)
  })

  it('允许 null——「没有名字」与「读不到」不是一回事', () => {
    // 服务端总会给出这个键；`null` 表示这条资源确实没有名字，界面回落到 ID。
    expect(managedResource).toContain('name?: string | null')
  })
})

describe('正文这一面走服务器', () => {
  it('listResources 能带 query', () => {
    // 正文只在服务器上：`GET /resources` 不返回正文，前端无从匹配。
    expect(apiSource).toMatch(/export async function listResources\(\s*type\?: ResourceType,\s*query\?: string/)
  })

  it('查询参数用 URLSearchParams 拼，而不是手拼字符串', () => {
    // 关键词是用户输入，会含 `&`、`#`、空格与中文。
    expect(apiSource).toContain('new URLSearchParams()')
    expect(apiSource).toMatch(/search\.set\('query', query\)/)
  })

  it('type 与 query 能同时提交——搜索与类型筛选是两个维度', () => {
    expect(apiSource).toMatch(/if \(type\) search\.set\('type', type\)/)
    expect(apiSource).toMatch(/if \(query !== undefined\) search\.set\('query', query\)/)
  })
})

describe('输入框的承诺与实现一致', () => {
  it('占位符把正文这一面写进去', () => {
    // 用户不会去猜搜索框搜不搜正文。少写一个词，等于这条特性对他不存在。
    expect(viewSource).toContain('搜索名称、ID、描述或正文')
  })

  it('正文搜索在途时显示 loading，而不是先报「没有匹配」', () => {
    expect(viewSource).toMatch(/:loading="searchingBody"/)
    expect(viewSource).toMatch(/visibleResources\.value\.length === 0 && !searchingBody\.value/)
  })
})

describe('前后端两条匹配面合起来用', () => {
  it('可见集合是「前端元数据命中」并上「服务器命中」', () => {
    // 服务器返回的是元数据命中 ∪ 正文命中，是前端结果的超集：
    // 请求在途时先按元数据过滤只会少显示几行，不会显示错的行。
    expect(viewSource).toMatch(
      /matchesResourceKeyword\(item, needle\) \|\| ids\?\.has\(item\.resource_id\) === true/
    )
  })

  it('请求节流，且乱序返回不覆盖新关键词', () => {
    // 正文命中要在服务器上逐条读文件并校验摘要，每敲一个字符发一次
    // 会把「边打边看」变成一串重复的全文件哈希。
    expect(viewSource).toMatch(/setTimeout\(/)
    expect(viewSource).toMatch(/token !== bodySearchToken/)
  })

  it('清空关键词立刻丢掉正文命中', () => {
    // 留一份属于上一个关键词的集合，会让清空之后仍然多出几行。
    expect(viewSource).toMatch(/if \(!needle\)[\s\S]{0,200}bodyMatchIds\.value = null/)
  })

  it('换类型后重搜——正文命中属于上一个类型', () => {
    expect(viewSource).toMatch(/resourceType\.value = value[\s\S]{0,200}runBodySearch\(\)/)
  })

  it('搜索失败只丢正文这一面，不清空列表', () => {
    expect(viewSource).toMatch(/catch \{[\s\S]{0,300}bodyMatchIds\.value = null/)
  })

  it('组件卸载时清掉节流中的定时器', () => {
    expect(viewSource).toMatch(/onBeforeUnmount\([\s\S]{0,400}clearTimeout\(bodySearchTimer\)/)
  })
})

describe('列表显示名称', () => {
  it('有名字时显示名字，没有时回落到 ID', () => {
    expect(viewSource).toMatch(/row\.name \|\| row\.resource_id/)
  })

  it('ID 不因为有名字就消失', () => {
    // 每个确认框、每条审计记录都按 ID 称呼这条资源。
    // 界面上只给名字，会让「确认删除 prompt.office-research」对不上任何一行。
    expect(viewSource).toMatch(/row\.name\s*\?\s*`\$\{row\.resource_id\}/)
  })
})

describe('谓词对真实形状仍然正确', () => {
  // 这几条用**真实响应的形状**（名称来自服务端投影）复核谓词，
  // 与既有那份手写对象的测试互补。
  const real = {
    resource_id: 'prompt.office-research',
    name: 'Office and Research Assistant',
    description: '办公、邮件、会议、表格和学术研究场景的中文行为提示词。',
    source_metadata: { provider: 'catalog', catalog_id: 'prompt:office-research' }
  }

  it('按投影上来的名称命中', () => {
    expect(matchesResourceKeyword(real, 'Office and Research')).toBe(true)
  })

  it('按描述里的中文子串命中', () => {
    expect(matchesResourceKeyword(real, '学术研究')).toBe(true)
  })

  it('名称为 null 的资源不抛错，仍能按 ID 命中', () => {
    const bare = { resource_id: 'prompt.bare', name: null, description: null }
    expect(matchesResourceKeyword(bare, 'bare')).toBe(true)
    expect(matchesResourceKeyword(bare, '任何名字')).toBe(false)
  })
})
