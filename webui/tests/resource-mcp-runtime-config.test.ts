// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 受管 MCP 资源必须有一个能配置的界面入口。
 *
 * 发现过程：`mcp:filesystem` 的描述写着「启用前必须在 args 末尾追加允许访问的目录」。
 * 而受管 MCP 资源住在资源注册表里，唯一的编辑路由 `PUT /mcp/servers/<id>` 只在
 * `config.mcp.servers` 里查找——对受管资源一律 404，尽管 `MCPList.vue` 给每一行
 * 都渲染了「编辑」按钮。所以那条描述要求用户做一件产品里做不到的事。
 *
 * 这组用例锁住界面侧的边界：
 *
 * 1. 入口只对 mcp 类型出现；
 * 2. 表单**不给** command / args / 传输类型的输入框——那是摘要保护的身份，
 *    给了输入框等于让人以为可以改，而提交会被后端拒；
 * 3. 环境变量只提交改过的键：读回来的值是掩码，回传等于把凭据写成掩码；
 * 4. 超时边界在前端先拦一次，与后端逐字一致；
 * 5. 保存后重取列表——不重取的话界面显示的还是旧覆盖。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/resource.ts')
const viewSource = read('../src/views/resources/ResourceView.vue')

describe('API 客户端', () => {
  it('声明写入运行时配置的函数与类型', () => {
    expect(apiSource).toContain('setResourceRuntime')
    expect(apiSource).toContain('interface ResourceRuntimeOverrides')
  })

  it('走 PUT /resources/<id>/runtime', () => {
    expect(apiSource).toMatch(/\/resources\/\$\{encodeURIComponent\(resourceId\)\}\/runtime/)
  })

  it('类型里只有部署相关的键', () => {
    const block = apiSource.slice(
      apiSource.indexOf('export interface ResourceRuntimeOverrides'),
      apiSource.indexOf('export async function setResourceRuntime')
    )
    for (const key of ['extra_args', 'env', 'headers', 'cwd', 'roots', 'startup_timeout_ms']) {
      expect(block, `${key} 应可配置`).toContain(key)
    }
    // 身份字段不该出现在这个类型里：它们由 content_sha256 保护。
    for (const key of ['command', 'url:', 'type:']) {
      expect(block, `${key} 不该可配置`).not.toContain(key)
    }
  })

  it('ManagedResource 声明 runtime_overrides', () => {
    // 不声明的话，读回来的覆盖在前端是 undefined，表单永远从空开始——
    // 用户每次打开都看不到自己上次配的目录。
    expect(apiSource).toMatch(/runtime_overrides\?: ResourceRuntimeOverrides \| null/)
  })
})

describe('界面入口', () => {
  it('只对 mcp 类型出现', () => {
    expect(viewSource).toMatch(/isRuntimeConfigurable\s*=\s*\(resource: ManagedResource\)\s*=>\s*resource\.type === 'mcp'/)
    expect(viewSource).toMatch(/isRuntimeConfigurable\(row\)/)
  })

  it('入口按钮存在', () => {
    expect(viewSource).toMatch(/data-test["']?\s*[:=]\s*["']edit-runtime["']/)
  })

  it('打开时预填已保存的覆盖', () => {
    // 留空会让用户以为之前配的目录没保存上。
    expect(viewSource).toMatch(/resource\.runtime_overrides \|\| \{\}/)
  })
})

describe('表单不提供身份字段', () => {
  const modal = viewSource.slice(
    viewSource.indexOf(`:show="panel === 'runtime'"`),
    viewSource.indexOf(`:show="panel === 'backups'"`)
  )

  it('没有 command / 传输类型 / URL 的输入框', () => {
    expect(modal.length).toBeGreaterThan(200)
    for (const forbidden of ['runtime-command', 'runtime-type', 'runtime-url', 'runtime-args']) {
      expect(modal, `${forbidden} 不该有输入框`).not.toContain(forbidden)
    }
  })

  it('说明命令来自资源包、不可改', () => {
    // 不写这一句，用户会去找那个输入框，找不到就以为界面缺功能。
    expect(modal).toMatch(/命令与传输类型来自已签名的资源包/)
  })

  it('提供目录、环境变量、roots、工作目录与超时', () => {
    for (const field of [
      'runtime-extra-arg',
      'runtime-add-env',
      'runtime-root',
      'runtime-cwd',
      'runtime-timeout'
    ]) {
      expect(modal, `${field} 缺失`).toContain(field)
    }
  })

  it('说明启动参数与 roots 是两套机制', () => {
    // 两者都叫「目录」，混为一谈会让人以为配了一处就够了。
    expect(modal).toMatch(/与上面的启动参数是两套机制/)
  })
})

describe('提交语义', () => {
  it('只提交改过的环境变量键', () => {
    // 读回来的值是掩码（`********`）。把未修改的键连掩码一起回传，
    // 会把真实凭据覆盖成八个星号。
    expect(viewSource).toMatch(/stored\.env && stored\.env\[name\] === value/)
  })

  it('被删掉的键显式清空', () => {
    // 后端按键合并：不发就是保留，于是删一行在界面上消失、在服务器上还在。
    expect(viewSource).toMatch(/!form\.env\.some\([\s\S]{0,120}env\[name\] = ''/)
  })

  it('超时边界与后端一致', () => {
    expect(viewSource).toMatch(/value < 1000 \|\| value > 600000/)
  })

  it('超时留空时不提交这个键', () => {
    // 提交 `undefined` 会被 JSON 丢掉，但显式 delete 让意图明确：
    // 「留空」是不动，不是清零。
    expect(viewSource).toMatch(/delete payload\.startup_timeout_ms/)
  })

  it('保存后重取列表', () => {
    expect(viewSource).toMatch(/setResourceRuntime\([\s\S]{0,400}loadResources\(\)/)
  })

  it('保存提示说明已启用的服务器会重连', () => {
    // 用户需要知道这次保存是否立即影响正在跑的进程。
    expect(viewSource).toMatch(/已启用的服务器会按新配置重连/)
  })
})
