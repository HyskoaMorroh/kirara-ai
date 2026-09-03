// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

/**
 * 定价表要有「显示名称」这一列身份，而它绝不能代替模型标识。
 *
 * 参考界面的定价表有两列身份：模型标识（等宽、稳定的上游 ID）与显示名称
 * （可读化展示）。笔记里专门写了那条边界：「模型标识使用稳定的上游模型 ID，
 * 显示名称单独保存，**不能用显示名称代替路由匹配键**」
 * （`docs/superpowers/plans/ccs-ui-notes.md` 的 `Image_2026-08-23_032940_110`）。
 *
 * 存在的理由是价目表到几十条时 `claude-sonnet-5` 与
 * `claude-sonnet-5-20260514` 在一屏里只差一个后缀，而它们的单价可能不同。
 * 要挑出「我在用的那个」，唯一可读的抓手就是这个名字；上游目录每个模型
 * 本来就带 `name`。
 *
 * 这组用例钉住三件事：表单能填、列表两个身份都看得见、以及**提交时空值转
 * `null`**——后端拒绝空白标签（那会在表格里留下一行没有身份的价格），
 * 而输入框里的「没填」天然是空串。
 */

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative: string) => readFileSync(resolve(here, relative), 'utf-8')

const apiSource = read('../src/api/llm.ts')
const viewSource = read('../src/views/llm/PricingView.vue')

describe('类型声明', () => {
  it('`PricingVersion` 带可选的 display_name', () => {
    expect(apiSource).toMatch(/display_name\?:\s*string\s*\|\s*null/)
  })

  it('它是可选的，不是必填', () => {
    // 做成必填会让老价目文件（没有这个字段）在类型层就对不上，
    // 而后端明确允许缺省。
    expect(apiSource).not.toMatch(/\n\s*display_name:\s*string\n/)
  })
})

describe('编辑表单', () => {
  it('有独立的显示名称输入框', () => {
    expect(viewSource).toMatch(/v-model="form\.display_name"/)
  })

  it('与模型标识是两个输入框，不是一个', () => {
    expect(viewSource).toMatch(/v-model="form\.model"/)
    expect(viewSource).toMatch(/v-model="form\.display_name"/)
  })

  it('说明留空时的行为', () => {
    // 「可选」两个字不够：用户要知道留空之后表格里显示什么。
    expect(viewSource).toMatch(/留空则显示模型标识|留空/)
  })

  it('长度有上界', () => {
    // 标签进表格单元格，无界长度会把列宽撑破；后端上限 200。
    expect(viewSource).toMatch(/name="display_name"[\s\S]{0,120}maxlength="200"/)
  })
})

describe('提交时的空值处理', () => {
  it('空串与纯空白转成 null', () => {
    // 后端对空白标签直接拒绝。前端原样提交空串等于把一个必然的 400
    // 留给用户去撞，而错误文案对填表的人没有可操作性。
    // 转换规则本身（空串/空白/null 都到 null、填了则 trim 后保留）由
    // `pricing-form-values.test.ts` 调用函数验证。
    // 原来这里钉的是那一行的写法，而它无法区分「改好了」与「改坏了」：
    // 重构成 `label || null` 它红，把条件写反成 `label ? null : label` 它也红。
    expect(viewSource).toMatch(/from '\.\/pricingForm'/)
    expect(viewSource).toMatch(/copyVersion\(form\.value\)/)
  })

  it('转换发生在 copyVersion 里，两条提交路径共用', () => {
    // 新建走 createPricing、编辑走 updatePricing；只在一条上做归一化，
    // 另一条就会漏。
    expect(viewSource).toMatch(/createPricing\([\s\S]{0,200}copyVersion\(form\.value\)/)
    expect(viewSource).toMatch(/updatePricing\([\s\S]{0,200}copyVersion\(form\.value\)/)
  })
})

describe('列表里的两列身份', () => {
  it('优先显示可读名，没填时回落到模型标识', () => {
    // 回落规则由逻辑测试覆盖（含「纯空白也回落」这一条，
    // `||` 写法对空白字符串是不成立的）。
    expect(viewSource).toMatch(/pricingLabel\(version\)/)
  })

  it('有可读名时模型标识仍然可见', () => {
    // 只显示标签会让「计价真正用的键」从界面上消失，
    // 而那正是这条边界要防的：标签不能代替匹配键。
    expect(viewSource).toMatch(/v-if="version\.display_name"[\s\S]{0,160}version\.model/)
  })

  it('模型标识用等宽字体', () => {
    // 相近标识要逐字符核对，比例字体下很难分辨。
    expect(viewSource).toMatch(/class="mono model-id"|class="model-id mono"/)
    expect(viewSource).toMatch(/\.mono\s*\{[^}]*font-family/)
  })
})
