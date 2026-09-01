import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 界面上的「自动检测」必须让后端**自己**保证保存（需求 7）。
 *
 * 第 7 条原文前半句是「模型管理无法实现自动定期监测更新模型**并保存配置**」。
 * 后台调度器那条链一直是完整的，缺口在界面侧：`GET .../auto-detect-models` 只
 * 返回结果，前端拿到之后再打一次 `PUT /backends/<name>` 间接落盘。
 *
 * 那条旧路径的问题不是「不能保存」，而是**把保存挂在前端多走一步上**：
 * 异常、切页、请求被新一代取代——少走那一步就只刷新了界面而没落盘，
 * 而用户看到模型列表变了，以为已经存好，重启进程后全没。
 *
 * 这些用例钉住：调用点存在、走的是 apply 而不是「只读 + PUT」、
 * `changed=false` 不被报成保存成功。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/LLMView.vue')

describe('auto-detect apply API binding', () => {
  it('exposes the apply endpoint', () => {
    expect(apiSource).toContain('/auto-detect-models/apply')
    expect(apiSource).toMatch(/applyBackendModels\s*\(/)
  })

  it('sends the confirmation the backend requires', () => {
    // 后端缺 `confirmed` 返回 400：这个动作改写 data/config.yaml，
    // 不接受「顺手点一下」。
    const block = apiSource.slice(apiSource.indexOf('applyBackendModels(backendName'))
    expect(block.slice(0, 400)).toContain('confirmed: true')
  })

  it('encodes the backend name into the path', () => {
    // 后端名允许中文与空格；不编码时带斜杠的名字会打到另一条路由上。
    const block = apiSource.slice(apiSource.indexOf('applyBackendModels(backendName'))
    expect(block.slice(0, 400)).toContain('encodeURIComponent(backendName)')
  })

  it('types saved and changed separately', () => {
    // 两者含义不同：`saved=false, changed=false` 是「本来就没变」，不是失败。
    const block = apiSource.slice(apiSource.indexOf('applyBackendModels(backendName'))
    expect(block.slice(0, 400)).toMatch(/saved:\s*boolean/)
    expect(block.slice(0, 400)).toMatch(/changed:\s*boolean/)
  })

  it('keeps the read-only detection available for preview', () => {
    // 预览这一步本身有意义：先看清将要保存什么，再决定是否保存。
    expect(apiSource).toMatch(/getBackendModels\s*\(/)
  })
})

describe('auto-detect flow in the model view', () => {
  it('calls apply rather than read-only detect plus a separate save', () => {
    expect(viewSource).toContain('applyBackendModels')
    // 回归点：旧路径是 getBackendModels + handleSave，保存挂在前端多走一步上。
    const flow = viewSource.slice(
      viewSource.indexOf('const confirmAutoDetect'),
      viewSource.indexOf('const cancelAutoDetect')
    )
    expect(flow).not.toContain('getBackendModels')
    expect(flow).toContain('applyBackendModels')
  })

  it('does not report a save when nothing changed', () => {
    const flow = viewSource.slice(
      viewSource.indexOf('const confirmAutoDetect'),
      viewSource.indexOf('const cancelAutoDetect')
    )
    expect(flow).toContain('applied.changed')
    expect(flow).toContain('无需改动')
  })

  it('refreshes the local list after the backend persisted and reloaded', () => {
    const flow = viewSource.slice(
      viewSource.indexOf('const confirmAutoDetect'),
      viewSource.indexOf('const cancelAutoDetect')
    )
    expect(flow).toContain('fetchAdapters()')
  })

  it('still guards against a superseded request generation', () => {
    // 切到另一张卡片时，旧请求不得改写新卡片的模型列表。
    const flow = viewSource.slice(
      viewSource.indexOf('const confirmAutoDetect'),
      viewSource.indexOf('const cancelAutoDetect')
    )
    expect(flow).toContain('modelDetectionRequests.isCurrent(request.generation)')
    expect(flow).toContain('currentAdapter.value !== adapter')
  })
})
