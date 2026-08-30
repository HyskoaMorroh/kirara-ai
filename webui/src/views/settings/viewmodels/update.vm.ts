import { ref, h } from 'vue'
import { http } from '@/utils/http'
import { useMessage } from 'naive-ui'
import type { FormRules, SelectOption } from 'naive-ui'

export interface UpdateForm {
  pypi_registry: string
  npm_registry: string
  /**
   * 禁用启动时的自动版本检查。
   *
   * 打开后启动路径上**完全不发起请求**——离线或内网部署既查不到注册表，
   * 又要为此等一次超时。默认 `false`：静默停掉版本检查会让用户长期停在
   * 旧版本而不自知，那不该是出厂状态。
   *
   * 与「禁用升级」无关：「检查更新」按钮是用户主动发起的，不受这里影响。
   */
  disable_auto_check: boolean
}

export interface UpdateConfig {
  update: UpdateForm
}

export function useUpdateViewModel() {
  const loading = ref(false)
  const message = useMessage()

  const formData = ref<UpdateForm>({
    pypi_registry: 'https://pypi.org/simple',
    npm_registry: 'https://registry.npmjs.org',
    disable_auto_check: false
  })

  const rules: FormRules = {
    pypi_registry: {
      required: true,
      message: '请选择或输入PyPI镜像源',
      trigger: 'blur'
    },
    npm_registry: {
      required: true,
      message: '请选择或输入NPM镜像源',
      trigger: 'blur'
    }
  }

  // 预设的镜像源选项
  const pypiRegistryOptions: SelectOption[] = [
    {
      label: 'PyPI 官方',
      value: 'https://pypi.org/simple',
      description: 'https://pypi.org/simple'
    },
    {
      label: '阿里云镜像',
      value: 'https://mirrors.aliyun.com/pypi/simple',
      description: 'https://mirrors.aliyun.com/pypi/simple'
    },
    {
      label: '清华镜像',
      value: 'https://pypi.tuna.tsinghua.edu.cn/simple',
      description: 'https://pypi.tuna.tsinghua.edu.cn/simple'
    }
  ]

  const npmRegistryOptions: SelectOption[] = [
    {
      label: 'NPM 官方',
      value: 'https://registry.npmjs.org',
      description: 'https://registry.npmjs.org'
    },
    {
      label: '淘宝镜像',
      value: 'https://registry.npmmirror.com',
      description: 'https://registry.npmmirror.com'
    }
  ]

  const fetchConfig = async () => {
    loading.value = true
    try {
      const response = await http.get<UpdateConfig>('/system/config')
      // 不直接整体赋值：后端若没返回 `disable_auto_check`（旧版本后端），
      // 整体赋值会把它变成 `undefined`，开关渲染成关闭、保存时又把
      // `undefined` 发回去。用默认值兜住缺键，只覆盖真的收到的值。
      formData.value = {
        ...formData.value,
        ...(response.update ?? {}),
        disable_auto_check: response.update?.disable_auto_check === true
      }
    } catch (error: any) {
      if (error.response?.data?.message) {
        message.error(error.response.data.message)
      } else {
        message.error('获取配置失败')
      }
    } finally {
      loading.value = false
    }
  }

  const handleSubmit = async () => {
    loading.value = true
    try {
      await http.post('/system/config/update', formData.value)
      message.success('保存配置成功')
    } catch (error: any) {
      if (error.response?.data?.message) {
        message.error(error.response.data.message)
      } else {
        message.error('保存配置失败')
      }
    } finally {
      loading.value = false
    }
  }

  const renderLabel = (option: SelectOption) => {
    return h('div', {}, `${option.label} (${option.description || '自定义'})`)
  }

  return {
    loading,
    formData,
    rules,
    pypiRegistryOptions,
    npmRegistryOptions,
    renderLabel,
    fetchConfig,
    handleSubmit
  }
}
