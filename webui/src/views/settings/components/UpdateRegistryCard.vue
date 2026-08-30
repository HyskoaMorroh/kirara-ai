<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  NCard,
  NSpace,
  NButton,
  NForm,
  NFormItem,
  NSelect,
  NSpin,
  NSwitch,
  NText
} from 'naive-ui'
import { useUpdateViewModel } from '../viewmodels/update.vm'
import UpdateChecker from '@/components/UpdateChecker.vue'

const {
  loading,
  formData,
  rules,
  pypiRegistryOptions,
  npmRegistryOptions,
  renderLabel,
  fetchConfig,
  handleSubmit
} = useUpdateViewModel()

/**
 * 手动检查更新的入口。
 *
 * 「禁用自动检查」的说明里承诺「手动检查照常可用」，那句话此前指向一个界面上
 * 并不存在的按钮：唯一写着「检查更新」的 `VersionCard.vue` 零挂载点，
 * `AboutView.vue` 还是占位页。把入口放在开关旁边，用户读到那句承诺时
 * 就能当场用到它，而不必去别处找。
 *
 * 复用 `UpdateChecker`（而不是自己再调一次接口）：发现新版本后的
 * 「跳过此版本 / 稍后提醒 / 立即更新」整套流程都在它里面，
 * 自己实现一遍就会与状态栏那份产生行为差异。
 */
const updateCheckerRef = ref<InstanceType<typeof UpdateChecker> | null>(null)
const checking = ref(false)

const handleManualCheck = async () => {
  checking.value = true
  try {
    // 传 true：后端把不带 `manual` 的调用当成自动检查，
    // 在开关打开时直接返回不外呼——那会把手动按钮一起挡掉。
    await updateCheckerRef.value?.checkUpdate(true)
  } finally {
    checking.value = false
  }
}

onMounted(() => {
  fetchConfig()
})
</script>

<template>
  <n-card title="更新源配置" class="settings-card">
    <!-- 手动检查更新走这套弹窗，与状态栏共用一份「跳过/稍后/立即更新」流程 -->
    <update-checker ref="updateCheckerRef" />
    <div style="margin-bottom: 16px">
      <n-text>
        这里配置的镜像源地址会影响插件的安装和项目本体的更新检查。
        请根据你的网络环境选择合适的镜像源，以获得更快的下载速度和更好的使用体验。
      </n-text>
    </div>
    <n-spin :show="loading">
      <n-form
        :model="formData"
        :rules="rules"
        label-placement="left"
        label-width="120"
        require-mark-placement="right-hanging"
      >
        <n-form-item label="PyPI镜像源" path="pypi_registry">
          <n-select
            v-model:value="formData.pypi_registry"
            :options="pypiRegistryOptions"
            :render-label="renderLabel"
            filterable
            tag
            placeholder="请选择或输入PyPI镜像源地址"
          />
          <template #feedback>
            <n-text depth="3"
              >用于下载Python包的镜像源，国内用户可以选择阿里云或清华镜像以提高下载速度</n-text
            >
          </template>
        </n-form-item>
        <n-form-item label="NPM镜像源" path="npm_registry">
          <n-select
            v-model:value="formData.npm_registry"
            :options="npmRegistryOptions"
            :render-label="renderLabel"
            filterable
            tag
            placeholder="请选择或输入NPM镜像源地址"
          />
          <template #feedback>
            <n-text depth="3"
              >用于下载前端依赖的镜像源，国内用户可以选择淘宝镜像以提高下载速度</n-text
            >
          </template>
        </n-form-item>
        <!--
          放在镜像源之后：这三项都属于「启动时要不要去外网、去哪里」这一件事。
          默认关闭——静默停掉版本检查会让用户长期停在旧版本而不自知。
        -->
        <n-form-item label="禁用自动检查更新" path="disable_auto_check">
          <n-switch v-model:value="formData.disable_auto_check" data-test="disable-auto-check" />
          <template #feedback>
            <n-text depth="3">
              打开后不再自动向 PyPI 与 npm 询问版本：启动时不问，打开页面时也不问。
              离线或内网部署既查不到注册表，又要为此等一次超时，这个开关把那些等待一起去掉。
              这不是「禁用升级」——下方「立即检查更新」由你主动发起，任何时候都照常可用。
            </n-text>
          </template>
        </n-form-item>
        <!--
          禁用自动升级：`entry.py::check_update` 打开时**完全不发起请求**。
          说明里必须写清「检查更新按钮仍可用」——不写，用户会把
          「禁用自动升级」读成「禁用升级」，从而不敢开这个他最需要的开关。
        -->
        <n-form-item label="禁用自动检查" path="disable_auto_check">
          <n-switch
            v-model:value="formData.disable_auto_check"
            data-test="disable-auto-check"
          />
          <template #feedback>
            <n-text depth="3">
              开启后启动时不再探测新版本，且完全不发起请求。适合离线或内网部署——
              这类环境既查不到注册表，又要为此等一次超时。
              设置页与关于页的「检查更新」按钮不受影响，仍可随时手动检查
            </n-text>
          </template>
        </n-form-item>
      </n-form>
      <div style="margin-top: 24px">
        <n-space justify="end">
          <n-button
            :loading="checking"
            data-test="check-update-now"
            @click="handleManualCheck"
          >
            立即检查更新
          </n-button>
          <n-button type="primary" :loading="loading" @click="handleSubmit"> 保存配置 </n-button>
        </n-space>
      </div>
    </n-spin>
  </n-card>
</template>

<style scoped>
.settings-card {
  max-width: 800px;
  margin: 0 auto;
}
</style>
