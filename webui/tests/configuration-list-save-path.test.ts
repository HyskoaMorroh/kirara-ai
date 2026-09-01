/**
 * `ConfigurationList.vue` 的保存路径不能引用不存在的字段。
 *
 * 这个组件目前没有引用方,但它在仓库里、随时可能被重新接上,而它的保存路径有四处真实缺陷:
 *
 * 1. `for (let property: string in ...)` —— `for...in` 的左侧不允许类型注解,这是语法错。
 * 2. `properties[property].password` —— `Configuration` 类型里没有 `password` 字段,取到
 *    `undefined` 后作为 `method` 传进 `createHash`,而 `createHash` 用它索引 `CryptoJS`。
 *    结果是把密码字段"加密"成 `undefined$...` 或直接抛错 —— 密码保存这条路必然不对。
 * 3. `properties[property]` 用字符串索引一个数组 —— `properties` 是 `Array<Configuration>`,
 *    而 `property` 是对象键。数组按键名索引恒为 `undefined`,于是上面那个 `.form_type`
 *    判断永远走不进去:**所有密码字段实际都以明文提交**。
 * 4. `$event.target.innerText` —— target 可能为 null,且 `EventTarget` 没有 `innerText`。
 *
 * 第 3 条是这里最严重的:它让整个"密码字段加密后再提交"的设计静默失效,而界面上看不出
 * 任何异常 —— 保存成功、字段有值,只是值是明文。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const raw = readFileSync(resolve(__dirname, '../src/components/ConfigurationList.vue'), 'utf-8')

/**
 * 剥掉注释再匹配。
 *
 * 修好之后这些断言仍然全红,原因不在产品代码：新写的注释里引用了被替换掉的旧写法
 * （`properties[property]`、`.password`）作为「原先错在哪」的说明,正则把注释也扫进去了。
 * 检查「代码里还有没有这个写法」就必须只看代码。
 */
const source = raw
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/(^|[^:])\/\/.*$/gm, '$1')

describe('ConfigurationList 保存路径', () => {
  it('自检:确实读到了保存函数', () => {
    expect(source).toContain('const saveToServer')
    expect(source).toContain('createHash')
  })

  it('for...in 左侧不带类型注解', () => {
    expect(source).not.toMatch(/for\s*\(\s*(?:let|const|var)\s+\w+\s*:\s*\w+\s+in\b/)
  })

  it('不引用 Configuration 上不存在的 password 字段', () => {
    // 类型里只有 title/isRequired/value/description/default/type/form_type。
    expect(source).not.toMatch(/\.password\b/)
  })

  it('按键名查找配置项时不拿字符串索引数组', () => {
    // properties 是数组,用对象键索引它恒为 undefined,密码判断永远不成立。
    expect(source).not.toMatch(/properties\[\s*property\s*\]/)
  })

  it('保存时能按属性名找到对应配置项', () => {
    // 断言的是「按属性名查」这个行为，而不是某一种实现手法。
    // 起初钉的是 `find(` —— 那是 properties 还是数组时的写法；后来 properties
    // 改成 Record，按键直接索引（`groupProperties(group)[name]`）比 find 更直接，
    // 旧断言会把这个更正确的实现判成失败。
    const at = source.indexOf('function findConfiguration')
    expect(at, '找不到 findConfiguration').toBeGreaterThan(-1)
    const body = source.slice(at, source.indexOf('\n}', at))
    expect(body, 'findConfiguration 没有按属性名查找').toMatch(
      /groupProperties\(group\)\[name\]|find\(/
    )
  })

  it('读取 contenteditable 文本时先收窄事件目标', () => {
    expect(source).not.toMatch(/\$event\.target\.innerText/)
  })
})
