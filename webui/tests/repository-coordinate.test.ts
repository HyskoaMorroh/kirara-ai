// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

import { parseRepositoryCoordinate } from '../src/views/resources/repositoryCoordinate'

const here = dirname(fileURLToPath(import.meta.url))

/**
 * 登记仓库要接受粘贴进来的 GitHub URL，而不是只接受拆好的三个字段。
 *
 * 参考界面只有一个输入框，占位符是 `owner/name 或 https://github.com/owner/name`
 * （`docs/superpowers/plans/ccs-ui-inventory.md` 4.2.1）。本项目此前是三个独立
 * 输入框（所有者 / 仓库 / 分支），而用户手上拿到的东西**一定**是一个 URL——
 * 从浏览器地址栏复制的。要求他把 URL 拆成三段再分别填进去，是把一次粘贴
 * 变成三次手抄，而手抄正是拼错坐标的来源（而拼错的坐标此前删都删不掉）。
 *
 * 解析放在前端而不是放宽后端校验：后端那三个字段的正则是安全边界
 * （它们会拼进 GitHub 归档 URL 与磁盘路径），放宽它等于把 URL 解析的错误
 * 变成一次路径穿越的机会。前端解析完仍然提交三个干净字段，后端一个字不改。
 *
 * 这组用例覆盖真实会被粘贴进来的形态，以及必须被拒绝的形态。
 */

describe('owner/name 简写', () => {
  it('接受最简形态', () => {
    expect(parseRepositoryCoordinate('anthropics/skills')).toEqual({
      owner: 'anthropics',
      name: 'skills',
      branch: null
    })
  })

  it('容忍首尾空白', () => {
    expect(parseRepositoryCoordinate('  anthropics/skills  ')?.owner).toBe('anthropics')
  })

  it('拒绝只有一段', () => {
    expect(parseRepositoryCoordinate('anthropics')).toBeNull()
  })

  it('拒绝空输入', () => {
    for (const value of ['', '   ', '/', '/skills', 'anthropics/']) {
      expect(parseRepositoryCoordinate(value), `${value} 不该被接受`).toBeNull()
    }
  })
})

describe('完整 GitHub URL', () => {
  it('接受仓库主页', () => {
    expect(parseRepositoryCoordinate('https://github.com/anthropics/skills')).toEqual({
      owner: 'anthropics',
      name: 'skills',
      branch: null
    })
  })

  it('接受 http 与省略协议两种写法', () => {
    for (const value of [
      'http://github.com/anthropics/skills',
      'github.com/anthropics/skills',
      'www.github.com/anthropics/skills'
    ]) {
      expect(parseRepositoryCoordinate(value)?.name, `${value} 解析失败`).toBe('skills')
    }
  })

  it('去掉 .git 后缀与末尾斜杠', () => {
    // `git clone` 用的地址就带 `.git`，而那是最常被粘贴进来的一种。
    for (const value of [
      'https://github.com/anthropics/skills.git',
      'https://github.com/anthropics/skills/',
      'git@github.com:anthropics/skills.git'
    ]) {
      expect(parseRepositoryCoordinate(value), `${value} 解析失败`).toMatchObject({
        owner: 'anthropics',
        name: 'skills'
      })
    }
  })

  it('从 /tree/<branch> 里取出分支', () => {
    // 浏览一个非默认分支时地址栏就是这个形态。丢掉分支会让登记落在 `main` 上，
    // 而用户看到的是他刚刚浏览过的那个分支——两者不是同一份内容。
    expect(parseRepositoryCoordinate('https://github.com/anthropics/skills/tree/master')).toEqual({
      owner: 'anthropics',
      name: 'skills',
      branch: 'master'
    })
  })

  it('分支名含斜杠时完整取出', () => {
    // `release/1.x` 是合法分支名。只取第一段会登记成一个不存在的 `release`。
    expect(
      parseRepositoryCoordinate('https://github.com/anthropics/skills/tree/release/1.x')?.branch
    ).toBe('release/1.x')
  })

  it('忽略 /tree 之外的路径段', () => {
    // 深链到某个目录时地址里还有 `/blob/...` 或子目录，那些不是坐标的一部分。
    expect(
      parseRepositoryCoordinate('https://github.com/anthropics/skills/blob/main/README.md')
    ).toMatchObject({ owner: 'anthropics', name: 'skills' })
  })

  it('拒绝非 GitHub 主机', () => {
    // 后端只会去 github.com 拉归档。接受别的主机等于给出一个必定失败的登记，
    // 而失败信息会指向「仓库不存在」，与真正的原因无关。
    for (const value of [
      'https://gitlab.com/anthropics/skills',
      'https://example.com/anthropics/skills',
      'https://github.evil.com/anthropics/skills'
    ]) {
      expect(parseRepositoryCoordinate(value), `${value} 不该被接受`).toBeNull()
    }
  })

  it('拒绝路径里的穿越尝试', () => {
    // 解析结果会提交给后端并拼进 URL 与磁盘路径。前端先挡一层，
    // 与后端的正则形成双重保险而不是替代它。
    //
    // 只列**不经过 URL 规范化**的形态：`owner/name` 简写这一路原样进分段器，
    // `..` 会作为一个段出现并被段校验挡掉。
    for (const value of ['anthropics/../skills', '../skills', 'anthropics/..']) {
      expect(parseRepositoryCoordinate(value), `${value} 不该被接受`).toBeNull()
    }
  })

  it('URL 里的 `..` 由 URL 规范化解掉，解析的是它真正指向的坐标', () => {
    // `new URL()` 在我们分段之前就把 `..` 解析完了：
    // `https://github.com/../../etc/passwd` 的 pathname 是 `/etc/passwd`。
    // 那**就是**这个 URL 指向的地址（浏览器同样这么解），两段都是普通的合法名字，
    // 没有任何穿越形状会到达后端。因此这里断言的是「解析成它真正指向的坐标」，
    // 而不是「拒绝」——后者会把一个正确的规范化说成漏洞。
    expect(parseRepositoryCoordinate('https://github.com/../../etc/passwd')).toEqual({
      owner: 'etc',
      name: 'passwd',
      branch: null
    })
    // 规范化之后只剩一段的，仍然认不出坐标。
    expect(parseRepositoryCoordinate('https://github.com/anthropics/../skills')).toBeNull()
  })

  it('拒绝含非法字符的坐标', () => {
    for (const value of ['anth ropics/skills', 'anthropics/sk ills', 'anthropics/skills?x=1#y']) {
      expect(parseRepositoryCoordinate(value), `${value} 不该被接受`).toBeNull()
    }
  })
})

describe('分支缺省的表达', () => {
  it('没给分支时返回 null，而不是替用户填 main', () => {
    // `null` 让调用方自己决定缺省值（表单里的分支输入框可能已经填了别的）。
    // 在解析这一层写死 `main` 会覆盖掉用户填的分支。
    expect(parseRepositoryCoordinate('anthropics/skills')?.branch).toBeNull()
  })
})

describe('表单接线', () => {
  const viewSource = readFileSync(
    resolve(here, '../src/views/resources/ResourceView.vue'),
    'utf-8'
  )

  it('坐标输入框的占位符说清接受两种形态', () => {
    // 只写 `owner` 的话，没人会想到可以直接粘 URL——而那正是最常见的输入。
    expect(viewSource).toMatch(/owner\/name 或 https:\/\/github\.com\/owner\/name/)
  })

  it('提交前先解析，解析失败不发请求', () => {
    expect(viewSource).toMatch(/parseRepositoryCoordinate\(form\.coordinate\)/)
    expect(viewSource).toMatch(/parsed === null[\s\S]{0,200}return/)
  })

  it('坐标里带的分支优先于分支输入框', () => {
    // 用户粘的是 `/tree/master`，他要的就是那个分支；让输入框的 `main` 覆盖它
    // 会登记出一份他没看过的内容。
    expect(viewSource).toMatch(/parsed\.branch \|\| form\.branch\.trim\(\) \|\| 'main'/)
  })

  it('提交的是解析后的干净字段，而不是原始输入', () => {
    // 后端那三个字段的正则是安全边界，前端不放宽它，只负责把 URL 拆好。
    expect(viewSource).toMatch(/addRepository\(parsed\.owner, parsed\.name, branch\)/)
  })

  it('失败提示说明接受什么，而不是只说格式不对', () => {
    expect(viewSource).toMatch(/请填写 owner\/name 或 GitHub 仓库地址/)
  })
})
