/**
 * 已安装资源要能按关键词过滤（需求 10 的 Skills 管理可操作度）。
 *
 * 现状：整页只有一个「资源类型」下拉，`visibleResources` 就是未过滤的 `resources`。
 * 类型筛选是服务端行为（改 type 会重新 `GET /resources?type=`），
 * 所以装了几十个 Skill 之后，想找某一个只能靠眼睛扫表格。
 *
 * 这里锁两件事：
 * 1. 过滤在**前端**做——已经取回来的列表不该为了搜一个词再往服务器跑一趟；
 * 2. 匹配要覆盖用户实际记得的字段：名称、资源 ID、描述。只匹配 name 不够，
 *    用户常常只记得 `agent-browser` 这种 id 片段。
 *
 * 用纯函数而不是挂载整个 2000 行组件：这条规则的实质是过滤谓词，
 * 挂载只会把 naive-ui 的渲染细节混进断言里。
 */

import { describe, expect, it } from 'vitest'
import { matchesResourceKeyword } from '../src/views/resources/resourceFilter'

const item = (over: Record<string, unknown> = {}) => ({
  resource_id: 'skill-agent-browser',
  name: 'Agent Browser',
  description: '浏览器自动化技能',
  ...over
})

describe('matchesResourceKeyword', () => {
  it('空关键词放行所有资源', () => {
    expect(matchesResourceKeyword(item(), '')).toBe(true)
    expect(matchesResourceKeyword(item(), '   ')).toBe(true)
  })

  it('按名称匹配，且忽略大小写', () => {
    expect(matchesResourceKeyword(item(), 'agent browser')).toBe(true)
    expect(matchesResourceKeyword(item(), 'AGENT')).toBe(true)
  })

  it('按资源 ID 片段匹配——用户往往只记得 id', () => {
    expect(matchesResourceKeyword(item(), 'agent-browser')).toBe(true)
    expect(matchesResourceKeyword(item(), 'skill-')).toBe(true)
  })

  it('按描述匹配，中文不被切碎', () => {
    expect(matchesResourceKeyword(item(), '浏览器')).toBe(true)
    expect(matchesResourceKeyword(item(), '自动化')).toBe(true)
  })

  it('不匹配时返回 false', () => {
    expect(matchesResourceKeyword(item(), 'telegram')).toBe(false)
  })

  it('关键词首尾空格不影响匹配', () => {
    expect(matchesResourceKeyword(item(), '  浏览器  ')).toBe(true)
  })

  it('description 缺失不会抛错', () => {
    expect(matchesResourceKeyword(item({ description: null }), '浏览器')).toBe(false)
    expect(matchesResourceKeyword(item({ description: undefined }), 'agent')).toBe(true)
  })
})
