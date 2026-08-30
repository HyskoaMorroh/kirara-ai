// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 请求详情页必须显示后端已经返回的全部计量字段。
 *
 * `getDetailFields()` 在 view model 里定义得很完整（首字节、尝试次数、失败类型、
 * 用量来源、成本快照…），但它**没有任何消费者**：详情页自己硬编码了一套
 * `n-descriptions`，比那份定义少了六七项。字段在、后端返回也在，
 * 只有渲染那一跳断了——界面上的表现是「详情页信息比列表还少」，
 * 而没有任何报错提示这件事。
 *
 * 这些用例钉住两件事：详情页真的消费那份定义；定义本身覆盖需求 22.1 点名的项。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const viewModel = read('../src/views/tracing/llm/llm-tracing.vm.ts')
const detailView = read('../src/views/tracing/llm/LLMTraceDetail.vue')

describe('the detail field definition is actually consumed', () => {
  it('exposes detailFields from the view model', () => {
    expect(viewModel).toMatch(/detailFields/)
    expect(viewModel).toMatch(/getDetailFields\(\)/)
  })

  it('renders them in the detail view instead of a second hardcoded list', () => {
    expect(detailView).toContain('detailFields')
    expect(detailView).toMatch(/v-for="\(field, index\) in detailFields"/)
  })

  it('shows --- rather than a blank cell for a missing value', () => {
    // 空白会被读成「这项是 0」。0 与「没有数据」在计量上是两件事。
    expect(detailView).toContain("'---'")
  })
})

describe('the definition covers what requirement 22.1 names', () => {
  const required = [
    ['首字节', 'ttft_ms'],
    ['尝试次数', 'attempt_count'],
    ['重试次数', 'retry_count'],
    ['故障转移次数', 'failover_count'],
    ['失败类型', 'error_category'],
    ['用量来源', 'usage_source'],
    ['缓存写入Token', 'cache_write_tokens']
  ] as const

  for (const [label, key] of required) {
    it(`includes ${label}`, () => {
      expect(viewModel).toContain(label)
      expect(viewModel).toContain(key)
    })
  }

  it('separates retry from failover instead of only reporting attempts', () => {
    // 同一家重试 3 次与切换 3 家在 `attempt_count` 上完全一样，
    // 而处置相反：前者调超时，后者查供应商健康。
    expect(viewModel).toContain('retry_count')
    expect(viewModel).toContain('failover_count')
  })

  it('surfaces which price version produced the cost', () => {
    // 改价之后回看历史账单时，这是唯一能回答「为什么这两条价格不同」的字段。
    expect(viewModel).toContain('price_version_id')
    expect(viewModel).toContain('定价版本')
  })
})
