import { ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { http } from '@/utils/http'
import { version } from '@/utils/version'

export interface UpdateResponse {
  current_backend_version: string
  latest_backend_version: string
  backend_update_available: boolean
  backend_download_url: string | null
  latest_webui_version: string
  webui_download_url: string | null
  /**
   * 这次到底有没有真的去问注册表。
   *
   * `update.disable_auto_check` 打开时自动检查不外呼，此时
   * `backend_update_available` 为 `false` 的含义是「没问」，而不是「已是最新」。
   * 少了这个字段，界面只能在两种说法里挑一个谎报。
   *
   * 声明为可选：旧后端不返回它，那时按「查过了」处理即可。
   */
  checked?: boolean
}

export interface UpdateInfo {
  current_backend_version: string
  latest_backend_version: string
  current_webui_version: string
  latest_webui_version: string
  backend_update_available: boolean
  webui_update_available: boolean
  backend_download_url: string | null
  webui_download_url: string | null
}

export interface UpdateProgress {
  step: string
  percentage: number
}

export function useUpdateViewModel() {
  const appStore = useAppStore()
  const message = useMessage()

  const showUpdateModal = ref(false)
  const updateInProgress = ref(false)
  const updateProgress = ref<UpdateProgress>({
    step: '',
    percentage: 0
  })

  // 处理错误信息
  const handleError = (error: any, defaultMessage: string) => {
    console.error(defaultMessage + ':', error)
    if (error.response?.data?.message) {
      message.error(error.response.data.message)
    } else if (error instanceof Error) {
      message.error(error.message)
    } else {
      message.error(defaultMessage)
    }
  }

  // 检查更新
  //
  // `manual` 区分「页面挂载时自动查」与「用户点了按钮」。后端在
  // `update.disable_auto_check` 打开时会挡掉自动的那一次（离线部署每开一次页面
  // 都要等两次注册表超时），但手动点的照常外呼。不带这个参数，手动按钮会被
  // 一起挡掉——那就等于把「禁用自动检查」偷偷变成了「禁用检查」。
  const checkUpdate = async (manual = false) => {
    try {
      const data = await http.get<UpdateResponse>(
        manual ? '/system/check-update?manual=1' : '/system/check-update'
      )

      if (data.checked === false) {
        // 没查过就不能说「已是最新」，也不能弹「有更新」——两种说法都是编的。
        //
        // 只有自动检查会走到这里：后端仅在 `disable_auto_check` 打开且请求
        // 不带 `manual=1` 时返回 `checked: false`，手动点的那次一定是 `true`。
        // 而自动检查被禁用是用户自己开的开关，不需要每次打开页面再提醒一遍。
        return
      }

      if (data.latest_backend_version == '0.0.0') {
        message.error('无法从服务端配置的更新源获取后端版本')
        data.backend_update_available = false
      }

      // 获取当前前端版本
      const current_webui_version = version.getCurrentVersion()

      // 判断前端是否需要更新
      const webui_update_available =
        version.compare(data.latest_webui_version, current_webui_version) > 0

      const updateInfo: UpdateInfo = {
        ...data,
        current_webui_version,
        webui_update_available
      }

      if (updateInfo.backend_update_available || updateInfo.webui_update_available) {
        showUpdateModal.value = appStore.setUpdateInfo(updateInfo)
        console.log('showUpdateModal', showUpdateModal.value)
      } else if (manual) {
        // 手动点一下什么都不发生，用户无法区分「已是最新」与「按钮坏了」。
        // 自动检查不提示：那会变成每次打开页面弹一次的噪音。
        message.success('已是最新版本')
      }
    } catch (error: any) {
      handleError(error, '检查更新失败')
    }
  }

  // 开始更新
  const startUpdate = async () => {
    updateInProgress.value = true
    updateProgress.value = { step: '准备更新...', percentage: 0 }

    try {
      // 开始更新
      updateProgress.value = { step: '下载更新包...', percentage: 30 }

      await http.post('/system/update', {
        update_backend: appStore.updateInfo?.backend_update_available ?? false,
        update_webui: appStore.updateInfo?.webui_update_available ?? false
      })

      updateProgress.value = { step: '安装更新...', percentage: 70 }

      // 重启系统
      updateProgress.value = { step: '重启系统...', percentage: 90 }

      try {
        await http.post('/system/restart')
        throw new Error('重启系统失败')
      } catch (error: any) {
        // 这个请求不可能成功
        updateProgress.value = {
          step: '更新完成，等待系统启动...（若无响应，请手动刷新页面）',
          percentage: 100
        }

        // 10秒后刷新页面
        setTimeout(() => {
          // 重新加载页面， 设置一个 query t=来重置缓存
          window.location.href = window.location.href.split('?')[0] + '?t=' + Date.now()
        }, 10000)
      }
    } catch (error: any) {
      handleError(error, '更新失败')
      updateInProgress.value = false
    }
  }

  // 稍后提醒
  const remindLater = () => {
    showUpdateModal.value = false
    appStore.setUpdateRemindLater()
  }

  // 跳过此版本
  const skipVersion = () => {
    showUpdateModal.value = false
    appStore.setSkipVersion()
  }

  return {
    showUpdateModal,
    updateInProgress,
    updateProgress,
    checkUpdate,
    startUpdate,
    remindLater,
    skipVersion
  }
}
