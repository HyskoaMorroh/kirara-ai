import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 「导入已有」必须覆盖「包已经在服务器上」这个处境。
 *
 * 需求 10 把五项 Skills 管理能力并列，其中「从ZIP安装」与「导入已有」此前
 * 都是浏览器上传，机制上是同一件事，只有审计口不同。真正缺的那半边是：
 * 运维用 scp 把一批包放进了服务器，手里没有可上传的文件，他要的是
 * 「服务器上已经有的那些，列出来让我选」。
 *
 * 钉住四条边界：
 * - 列举是只读的（GET），安装是写操作（POST）；
 * - 只传文件名，不传路径；
 * - 已装过的包标出来而不是从列表里消失；
 * - 坏包只影响自己那一行。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('API 客户端', () => {
  it('列举走 GET /resources/imports', () => {
    expect(apiSource).toContain('listImportableArchives')
    expect(apiSource).toMatch(/listImportableArchives[\s\S]{0,200}http\.get/)
  })

  it('安装走 POST /resources/imports/install', () => {
    expect(apiSource).toContain('installImportableArchive')
    expect(apiSource).toContain('/resources/imports/install')
  })

  it('安装只提交文件名，不提交路径', () => {
    // 允许路径就等于把一个只读列举接口变成任意文件安装接口。
    expect(apiSource).toMatch(/file_name:\s*fileName/)
    expect(apiSource).not.toMatch(/installImportableArchive[\s\S]{0,300}path:/)
  })

  it('返回类型区分「已装」「可更新」「坏包」', () => {
    expect(apiSource).toMatch(/installed:\s*boolean/)
    expect(apiSource).toMatch(/installed_version:\s*string \| null/)
    expect(apiSource).toMatch(/is_upgrade:\s*boolean/)
    expect(apiSource).toMatch(/error:\s*string \| null/)
  })

  it('返回类型里没有宿主机路径', () => {
    // 路径不该经由接口流出去。
    expect(apiSource).not.toMatch(/ImportableArchive[\s\S]{0,400}absolute_path/)
    expect(apiSource).not.toMatch(/ImportableArchive[\s\S]{0,400}imports_path/)
  })
})

describe('界面入口', () => {
  it('提供「服务器上的包」按钮', () => {
    expect(viewSource).toContain('data-test="staged-archives"')
    expect(viewSource).toContain('服务器上的包')
  })

  it('与两个上传按钮并存而不是替换它们', () => {
    // 三条路径解决三种处境，谁也不能顶替谁。
    expect(viewSource).toContain('data-test="install-archive"')
    expect(viewSource).toContain('data-test="import-archive"')
  })

  it('已安装且不是升级的包不能再点安装', () => {
    expect(viewSource).toMatch(/entry\.installed && !entry\.is_upgrade/)
  })

  it('坏包不能点安装，并单独显示原因', () => {
    expect(viewSource).toMatch(/!!entry\.error/)
    expect(viewSource).toContain('无法读取')
  })

  it('可更新的包按钮文案是「更新」而不是「安装」', () => {
    // 「已装 1.0.0、盘上有 2.0.0」与「已装 2.0.0」处置不同。
    expect(viewSource).toMatch(/entry\.is_upgrade \? '更新' : '安装'/)
  })

  it('空目录给出「把包放哪里」而不是只说没有数据', () => {
    expect(viewSource).toContain('resources/imports')
  })

  it('提供重新扫描，且按名字跟踪单行忙态', () => {
    expect(viewSource).toContain('重新扫描')
    expect(viewSource).toMatch(/stagedBusyFile === entry\.file_name/)
  })

  it('安装成功后同时刷新已装列表与待导入列表', () => {
    // 只刷一边会让刚装好的包在另一份列表里仍显示为「未安装」。
    expect(viewSource).toMatch(/loadResources\(\), loadStagedArchives\(\)/)
  })
})
