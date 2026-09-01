<template>
  <div class="topbar">
    <p>{{ props.title.toLocaleUpperCase() }}</p>
    <n-space>
      <slot name="tools"></slot>
      <n-button quaternary @click="resetForm">
        <template #icon>
          <n-icon>
            <reload-icon />
          </n-icon>
        </template>
      </n-button>
      <n-button quaternary @click="saveToServer">
        <template #icon>
          <n-icon>
            <save-icon />
          </n-icon>
        </template>
      </n-button>
    </n-space>
  </div>
  <n-scrollbar style="max-height: 90vh">
    <div style="max-width: 66%; margin: 0 auto; padding-top: 16px" class="configuration-container">
      <n-form ref="formRef" label-placement="left" v-model:value="editableConfigurationValue">
        <div v-for="(group, i) in configurationGroups" :key="i">
          <h2 style="text-align: left; padding: 16px 0">{{ group.title }}</h2>
          <div v-if="group.description" class="markdown-content" v-html="md.render(group.description)" />
          <n-divider></n-divider>

          <!--
            `configKey` 是配置项在 `configurationValue` 里的键名。
            此前用的是 `v-for (config, j)` 的数组下标 `j` 去索引
            `editableConfigurationValue`（一个 Record<string, any>）——
            读到的永远是 undefined，用户填的内容也写进 "0"/"1"/"2" 这种键，
            保存后与后端期望的属性名毫无关系。与 DynamicConfigForm.vue 一致，
            改为按属性名索引。
          -->
          <div
            style="margin-bottom: 20px"
            v-for="(config, configKey) in groupProperties(group)"
            :key="configKey"
          >
            <n-form-item :label="config.title" v-if="config.type == 'boolean'">
              <n-switch v-model:value="editableConfigurationValue[configKey]">
                <template #checked-icon> 😁 </template>
                <template #unchecked-icon> 🤔 </template>
              </n-switch>
            </n-form-item>

            <template v-else-if="config.type == 'object'">
              <p style="padding: 10px 0">{{ config.title }}</p>

              <template v-for="(_, keyName) in editableConfigurationValue[configKey]" :key="keyName">
                <p style="padding: 10px 0">
                  <span contenteditable="true" @input="renameObjectKey(configKey, String(keyName), $event)">{{
                    keyName
                  }}</span>
                  <n-button style="margin-left: 12px" @click="removeObjectKey(configKey, String(keyName))">
                    删除
                  </n-button>
                  <n-button attr-type="button" @click="addObjectArrayItem(configKey, String(keyName))">
                    增加
                  </n-button>
                </p>

                <n-form-item
                  v-for="(__, childIndex) in editableConfigurationValue[configKey][keyName]"
                  :key="childIndex"
                  :label="`${childIndex}`"
                >
                  <n-input
                    v-model:value="editableConfigurationValue[configKey][keyName][childIndex]"
                    clearable
                    style="min-width: 25%"
                  />
                  <n-button
                    style="margin-left: 12px"
                    @click="removeObjectArrayItem(configKey, String(keyName), childIndex)"
                  >
                    删除
                  </n-button>
                </n-form-item>
              </template>

              <n-form-item>
                <n-space>
                  <n-button attr-type="button" @click="addObjectKey(configKey, '请输入 AI 名')">
                    增加
                  </n-button>
                </n-space>
              </n-form-item>
            </template>

            <template v-else-if="config.type == 'array'">
              <p style="padding: 10px 0">{{ config.title }}</p>
              <n-form-item
                v-for="(item, index) in editableConfigurationValue[configKey]"
                :key="index"
                :label="`第${index + 1}项`"
              >
                <n-input
                  v-model:value="editableConfigurationValue[configKey][index]"
                  clearable
                  style="min-width: 25%"
                />
                <n-button style="margin-left: 12px" @click="removeArrayItem(configKey, index)">
                  删除
                </n-button>
              </n-form-item>

              <n-form-item>
                <n-space>
                  <n-button attr-type="button" @click="addArrayItem(configKey)"> 增加 </n-button>
                </n-space>
              </n-form-item>
            </template>

            <n-form-item
              :label="config.title"
              path="inputValue"
              v-else-if="config.type == 'integer'"
            >
              <n-input-number
                v-model:value="editableConfigurationValue[configKey]"
                :placeholder="'' + (config.default || '请输入……')"
                style="min-width: 25%"
              />
            </n-form-item>
            <n-form-item
              :label="config.title"
              path="inputValue"
              v-else-if="config.form_type == 'password'"
            >
              <n-input
                v-model:value="editableConfigurationValue[configKey]"
                type="password"
                :placeholder="'' + (config.default || '请输入……')"
                style="min-width: 25%"
              />
            </n-form-item>
            <n-form-item :label="config.title" path="inputValue" v-else>
              <n-input
                v-model:value="editableConfigurationValue[configKey]"
                type="text"
                :placeholder="'' + (config.default || '请输入……')"
                style="min-width: 25%"
              />
            </n-form-item>
            <div v-if="config.description" class="markdown-content" v-html="md.render(config.description)" />

            <n-divider></n-divider>
          </div>
        </div>
      </n-form>
    </div>
  </n-scrollbar>
</template>

<script setup lang="ts">
import { SaveOutline as SaveIcon, ReloadOutline as ReloadIcon } from '@vicons/ionicons5'
import {
  NDivider,
  NInput,
  NFormItem,
  NForm,
  NSpace,
  NButton,
  NIcon,
  NScrollbar,
  NSwitch,
  NInputNumber
} from 'naive-ui'
import MarkdownIt from 'markdown-it'

import CryptoJS from 'crypto-js'
import { ref, watch } from 'vue'
import { deepClone } from '@/utils/deep-clone'

/**
 * 配置项描述用 markdown 渲染。
 *
 * 原先用的是 `vue3-markdown-it`——一个既没在 package.json 声明、也没装进
 * node_modules 的包。这个组件目前没有引用方,所以一直没炸;一旦被重新引用,
 * Vite 解析不到就是构建失败,不是类型告警。改用仓库已声明的 `markdown-it`,
 * 与 `IMAdapterDetail.vue` 渲染适配器说明走同一个库。
 *
 * 描述里的链接一律新窗口打开:配置页往往填了一半,原地跳走等于丢掉未保存的输入。
 */
const md = new MarkdownIt()
const defaultLinkRender =
  md.renderer.rules.link_open ||
  function (tokens, idx, options, env, self) {
    return self.renderToken(tokens, idx, options)
  }
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  return defaultLinkRender(tokens, idx, options, env, self)
}

export type Configuration = {
  title: string
  isRequired: boolean
  value: any
  description: string
  default?: any
  type: string
  form_type?: string
}
export type ConfigurationGroup = {
  title: string
  description?: string
  /**
   * 配置项，按属性名索引。
   *
   * 与 `configurationValue`（`Record<string, any>`）同构：遍历它拿到的键
   * 可以直接用来索引配置值。此前声明为 `Array<Configuration>`，于是模板
   * 只能拿到数组下标，索引 Record 时读到的永远是 undefined。
   * 这与 `DynamicConfigForm.vue` 的 `schema.properties` 形态一致。
   */
  properties: Record<string, Configuration>
}

const props = defineProps({
  title: {
    type: String,
    required: true
  },
  configurationGroups: {
    type: Array as () => Array<ConfigurationGroup>,
    required: true
  },
  configurationValue: {
    type: Object,
    required: true
  }
})

const emit = defineEmits<{
  (e: 'reset'): void
  (e: 'save', configurationValue: any): void
}>()

/**
 * 取分组的配置项映射。
 *
 * 兼容仍以数组形态传入的旧调用方：把数组按 `title` 收成键值对，让模板始终
 * 拿到属性名。数组形态下 `title` 就是后端 schema 的属性名（见
 * `saveToServer` 里按 title 查找配置项那段）。
 */
const groupProperties = (group: ConfigurationGroup): Record<string, Configuration> => {
  const properties = group.properties as unknown
  if (Array.isArray(properties)) {
    return Object.fromEntries(
      (properties as Configuration[]).map((item) => [item.title, item])
    )
  }
  return (properties || {}) as Record<string, Configuration>
}

const editableConfigurationValue = ref<Record<string, any>>({})

watch(
  () => props.configurationValue,
  (value) => {
    editableConfigurationValue.value = deepClone(value)
  },
  { immediate: true, deep: true }
)

function generateSalt() {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
  let salt = ''
  for (let i = 0; i < 10; i++) {
    salt += chars.charAt(Math.floor(Math.random() * chars.length))
  }
  return salt
}

/**
 * 按算法名取哈希函数。
 *
 * `CryptoJS[name]` 在类型上是 `unknown`（算法是运行时动态选的，vendor shim
 * 用索引签名表达这一点）。这里在一处收窄成"可调用的哈希函数"，
 * 而不是把 shim 整体放宽成 any —— 那会让 CryptoJS 的其余误用也失去检查。
 */
type HashFn = (message: string, key?: string) => { toString: (encoder?: unknown) => string }

function resolveHashFn(name: string): HashFn {
  const candidate = (CryptoJS as unknown as Record<string, unknown>)[name]
  if (typeof candidate !== 'function') {
    throw new Error(`不支持的哈希算法：${name}`)
  }
  return candidate as HashFn
}

function createHash(originalStr: string, method: string) {
  const hashFunc = method
  const hashFn = resolveHashFn(hashFunc)
  if (hashFunc.startsWith('Hmac')) {
    const key = generateSalt()
    const saltedData = `${originalStr}`
    const hash = hashFn(saltedData, key)
    return `${hashFunc.replace('Hmac', '').toLocaleLowerCase()}$${key}$${hash.toString(
      CryptoJS.enc.Hex
    )}`
  }
  const hash = hashFn(originalStr)
  return `${hashFunc}$${hash.toString(CryptoJS.enc.Hex)}`
}

const resetForm = () => {
  // Reset form fields to their original values
  emit('reset')
}

/**
 * 按属性名在所有分组里找配置项。
 *
 * 原先写的是 `configurationGroups[0].properties[property]` —— `properties` 是数组,
 * 拿对象键去索引它恒为 `undefined`,于是下面那个 `form_type == 'password'` 判断
 * **永远不成立,所有密码字段都以明文提交**。界面上看不出异常:保存成功、字段有值。
 * 而且它只看第 0 组,后面分组的密码字段本来也轮不到。
 */
function findConfiguration(name: string): Configuration | undefined {
  for (const group of props.configurationGroups) {
    const hit = groupProperties(group)[name]
    if (hit) return hit
  }
  return undefined
}

/** 密码字段的哈希算法。原先取的是 `Configuration.password`——这个字段并不存在。 */
const PASSWORD_HASH_METHOD = 'SHA256'

const saveToServer = () => {
  try {
    for (const property of Object.keys(editableConfigurationValue.value)) {
      if (findConfiguration(property)?.form_type !== 'password') continue
      const raw = editableConfigurationValue.value[property]
      // 空值不哈希:哈希一个空串会写出一个看起来已设置、实际锁死账号的凭据。
      if (typeof raw !== 'string' || raw === '') continue
      editableConfigurationValue.value[property] = createHash(raw, PASSWORD_HASH_METHOD)
    }
  } catch (e) {
    console.error(e)
  }

  emit('save', editableConfigurationValue.value)
}

const removeArrayItem = (name: string, index: number) => {
  editableConfigurationValue.value[name].splice(index, 1)
}
const addArrayItem = (name: string) => {
  editableConfigurationValue.value[name].push('')
}

const addObjectArrayItem = (name: string, keyName: string) => {
  editableConfigurationValue.value[name][keyName].push('')
}
const removeObjectArrayItem = (name: string, keyName: string, index: number) => {
  editableConfigurationValue.value[name][keyName].splice(index, 1)
}
const removeObjectKey = (name: string, keyName: string) => {
  delete editableConfigurationValue.value[name][keyName]
}

const addObjectKey = (name: string, keyName: string) => {
  editableConfigurationValue.value[name][keyName] = []
}

const changeObjectKey = (name: string, keyName: string, newKeyName: string) => {
  editableConfigurationValue.value[name][newKeyName] = editableConfigurationValue.value[name][keyName]
  delete editableConfigurationValue.value[name][keyName]
}

/**
 * 从 contenteditable 的输入事件里取新键名。
 *
 * 模板里原先直接写 `$event.target.innerText`：`target` 可能为 null,而 `EventTarget`
 * 上也没有 `innerText`。这里在一处收窄类型,模板不再碰 DOM 细节。
 *
 * 空白键名直接忽略：把一个键改成空串会让它在表单上彻底消失,而用户只是删光了字符
 * 准备重打。
 */
const renameObjectKey = (name: string, keyName: string, event: Event) => {
  const target = event.target
  if (!(target instanceof HTMLElement)) return
  const newKeyName = target.innerText.trim()
  if (!newKeyName || newKeyName === keyName) return
  changeObjectKey(name, keyName, newKeyName)
}
</script>

<style scoped>
.topbar {
  background-color: var(--panel-bg-color, var(--vt-c-white-mute));
  color: var(--text-primary, #111);
  height: 50px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 20px;
}

ul {
  list-style: none;
  display: flex;
}

li {
  margin-right: 20px;
}

a {
  color: var(--text-primary, #111);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

/* 链接需要可见的键盘聚焦环 */
a:focus-visible {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 2px;
}
</style>
