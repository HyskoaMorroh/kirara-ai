/**
 * `ConfigurationList.vue` 的表单读写必须用属性名，而不是数组下标。
 *
 * 这个组件把配置分组渲染成表单。分组是 `Array<Configuration>`，`v-for` 拿到的
 * `j` 是**数组下标**（number）。但配置值 `editableConfigurationValue` 是
 * `Record<string, any>` —— 键是属性名（字符串），不是下标。
 *
 * 于是整份表单的读写全部错位：
 *
 *     v-model:value="editableConfigurationValue[j]"      // 读的是 "0" / "1" / "2"
 *     removeObjectKey(j, keyName)                        // 传下标给声明为 string 的参数
 *
 * 后果不是"某个字段串了"，是**整个组件读不到也写不回任何真实配置**：
 * 界面显示空值（`record["0"]` 不存在），用户填的内容写进 `record["0"]`，
 * 保存时后端拿到一份键名为 "0"/"1"/"2" 的对象，与它期望的属性名毫无关系。
 *
 * 对照同仓已在服役的 `DynamicConfigForm.vue`：它用
 * `for (const key in props.schema.properties)` 与 `props.modelValue[key]`，
 * 全程按属性名索引 —— 那份是对的写法。
 *
 * 这个组件当前没有引用方，所以缺陷没有外显。但它留在仓库里，任何人接上就是
 * 一个"能打开、能填、保存后配置全丢"的表单。修它比留着更省事。
 *
 * 判据：**索引类型必须与容器类型一致。** `Record<string, T>` 只能用字符串键索引；
 * 数组下标与属性名是两种不同的东西，恰好都能写在方括号里不代表它们等价。
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')
const raw = readFileSync(resolve(root, 'src/components/ConfigurationList.vue'), 'utf-8')

/** 剥掉注释：本文件的说明里就引用了错误写法作为反例。 */
const source = raw
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/.*$/gm, '$1')

/** 模板段（`<template>` 到最后一个 `</template>`）。 */
const template = source.slice(0, source.indexOf('<script'))

describe('ConfigurationList 的索引一致性', () => {
  it('自检：配置值容器是按属性名索引的 Record', () => {
    expect(source).toMatch(/editableConfigurationValue\s*=\s*ref<Record<string,/)
  })

  it('模板不用数组下标索引配置值', () => {
    // `[j]` 是 `v-for (config, j) in group.properties` 的下标，是 number；
    // 而容器的键是属性名。用它索引读到的永远是 undefined。
    const hits = [...template.matchAll(/editableConfigurationValue\[\s*j\s*\]/g)]
    expect(
      hits.length,
      `模板里有 ${hits.length} 处用数组下标 j 索引 Record —— 读不到也写不回真实配置`
    ).toBe(0)
  })

  it('v-for 取出配置项的键名，而不是只取下标', () => {
    // 正确做法与 DynamicConfigForm.vue 一致：遍历时拿到属性名。
    expect(
      template,
      'v-for 只取了下标，没有属性名；无法按键索引配置值'
    ).toMatch(/v-for="\(\s*(config|property)\s*,\s*[A-Za-z_$][\w$]*Key\b/)
  })

  it('对象操作函数收到的是属性名而非下标', () => {
    // 四个函数的首参声明为 `arr: number`，但它们要索引的是 Record。
    for (const fn of [
      'addObjectArrayItem',
      'removeObjectArrayItem',
      'removeObjectKey',
      'renameObjectKey'
    ]) {
      const at = source.indexOf(`const ${fn} = (`)
      expect(at, `找不到 ${fn}`).toBeGreaterThan(-1)
      const signature = source.slice(at, source.indexOf(')', at))
      expect(
        signature,
        `${fn} 的首参声明为 number（数组下标），但它索引的是按属性名的 Record`
      ).not.toMatch(/\(\s*\w+\s*:\s*number/)
    }
  })
})
