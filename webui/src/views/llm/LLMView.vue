<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount } from 'vue'
import { useMessage, NModal, NCard } from 'naive-ui'
import { llmApi, resilienceDefaults } from '@/api/llm'
import type { LLMBackend, ConfigSchema } from '@/api/llm'
import type { ModelInfo } from '@/components/form/types'
import { useLatestRequest } from '@/composables/useLatestRequest'

// 导入组件
import LLMAdapterList from '@/components/llm/LLMAdapterList.vue'
import LLMAdapterConfig from '@/components/llm/LLMAdapterConfig.vue'
import LLMEmptyState from '@/components/llm/LLMEmptyState.vue'
import LLMModelForm from '@/components/llm/LLMModelForm.vue'
import LLMConfirmContent from '@/components/llm/LLMConfirmContent.vue'

const $message = useMessage()
const isAutoDetectModelsSupported = ref(false)
const showConfirmModal = ref(false)
const autoDetectLoading = ref(false)
const selectedAdapter = ref('')
const adapters = ref<LLMBackend[]>([])
const adapterTypes = ref<string[]>([])
const configSchema = ref<ConfigSchema | null>(null)
const loading = ref(false)
const adapterListRequests = useLatestRequest()
const schemaRequests = useLatestRequest()
const autoDetectSupportRequests = useLatestRequest()
const modelDetectionRequests = useLatestRequest()

const isAbortError = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

// 模型编辑相关
const showModelModal = ref(false)
const modelEditMode = ref<'add' | 'edit'>('add')
const modelEditIndex = ref(-1)
const currentModel = ref<ModelInfo>({
  id: '',
  type: '',
  ability: 0
})

// 根据模型类型定义不同的能力选项
const modelAbilities: Record<string, { label: string; value: number }[]> = {
  llm: [
    { label: '聊天对话', value: (1 << 1) + (1 << 2) + (1 << 3) },
    { label: '图像输入', value: 1 << 4 },
    { label: '图像输出', value: 1 << 5 },
    { label: '音频输入', value: 1 << 6 },
    { label: '音频输出', value: 1 << 7 },
    { label: '函数调用', value: 1 << 8 }
  ],
  embedding: [
    { label: '文本嵌入', value: 1 << 1 },
    { label: '图像嵌入', value: 1 << 2 },
    { label: '音频嵌入', value: 1 << 3 },
    { label: '视频嵌入', value: 1 << 4 },
    { label: '批量调用', value: 1 << 5 }
  ],
  image_generation: [
    { label: '文生图', value: 1 << 1 },
    { label: '图生图', value: 1 << 2 },
    { label: '局部重绘', value: 1 << 3 },
    { label: '图像扩展', value: 1 << 4 },
    { label: '图像放大', value: 1 << 5 }
  ],
  audio: [
    { label: '音频输入', value: 1 << 6 },
    { label: '音频输出', value: 1 << 7 }
  ]
}

// 当前选中的适配器实例
const currentAdapter = ref<LLMBackend | null>(null)

// 保存原始适配器名称
const originalAdapterName = ref('')

// 删除确认模态框
const showDeleteConfirmModal = ref(false)

// 获取适配器类型和实例
const fetchAdapters = async () => {
  const request = adapterListRequests.begin()
  try {
    const [typesResponse, adaptersResponse] = await Promise.all([
      llmApi.getAdapterTypes(request.signal),
      llmApi.getBackends(request.signal)
    ])
    if (!adapterListRequests.isCurrent(request.generation)) return

    adapterTypes.value = Array.isArray(typesResponse) ? typesResponse : typesResponse.types
    adapters.value = Array.isArray(adaptersResponse)
      ? adaptersResponse
      : adaptersResponse.data.backends
  } catch (error: unknown) {
    if (isAbortError(error) || !adapterListRequests.isCurrent(request.generation)) return
    const errorMessage = error instanceof Error ? error.message : error
    $message.error(`加载适配器失败: ${errorMessage}`)
  }
}

// 获取适配器配置模式
const fetchAdapterConfigSchema = async (adapterType: string, overrideConfig: boolean = false) => {
  const request = schemaRequests.begin()
  try {
    loading.value = true
    const { configSchema: configSchemaData } = await llmApi.getAdapterConfigSchema(
      adapterType,
      request.signal
    )
    if (
      !schemaRequests.isCurrent(request.generation) ||
      currentAdapter.value?.adapter !== adapterType
    ) {
      return
    }
    if (currentAdapter.value && overrideConfig) {
      currentAdapter.value!!.config = {}
    }
    configSchema.value = configSchemaData
  } catch (error: unknown) {
    if (isAbortError(error) || !schemaRequests.isCurrent(request.generation)) return
    const errorMessage = error instanceof Error ? error.message : error
    $message.error(`获取适配器配置模式失败: ${errorMessage}`)
    configSchema.value = null
  } finally {
    if (schemaRequests.isCurrent(request.generation)) {
      loading.value = false
    }
  }
}

// 处理适配器选择
const handleAdapterSelect = async (adapter: LLMBackend) => {
  selectedAdapter.value = adapter.name
  currentAdapter.value = {
    name: '',
    adapter: '',
    config: {},
    enable: true,
    models: []
  }
  // 后端返回的字段优先；只对旧数据缺失的容错字段补默认值，
  // 避免保存时把未声明的字段提交成 undefined 而被后端重置。
  currentAdapter.value = { ...resilienceDefaults(), ...adapter }
  originalAdapterName.value = adapter.name
}

// 创建新配置
const handleCreateAdapter = async (adapter: string | null = null) => {
  currentAdapter.value = {
    name: '',
    adapter: adapter ?? '',
    config: {},
    enable: true,
    models: [],
    ...resilienceDefaults()
  }
  // 创建新配置时，清空原始名称
  originalAdapterName.value = ''
}

const isCreating = computed(() => {
  const existingAdapter = adapters.value.find((a) => a.name === originalAdapterName.value)
  return !existingAdapter
})

// 保存配置
const handleSave = async (
  adapter: LLMBackend | null = currentAdapter.value,
  persistedName: string = originalAdapterName.value
) => {
  try {
    if (!adapter?.name || !adapter?.adapter) {
      throw new Error('请输入完整的配置信息')
    }

    const existingAdapter = adapters.value.find((item) => item.name === persistedName)
    if (!existingAdapter) {
      await llmApi.createBackend(adapter)
      $message.success('创建成功')
    } else {
      await llmApi.updateBackend(persistedName, adapter)
      $message.success('保存成功')
    }
    await fetchAdapters()
    // 保存期间切换到其他卡片时，旧请求不能改写新卡片的持久化身份。
    if (currentAdapter.value === adapter) {
      originalAdapterName.value = adapter.name
    }
    return true
  } catch (error: unknown) {
    if (isAbortError(error)) return false
    const errorMessage = error instanceof Error ? error.message : '未知错误'
    $message.error(`保存失败: ${errorMessage}`)
    return false
  }
}

// 切换启用状态
const toggleEnable = async () => {
  const adapter = currentAdapter.value
  if (!adapter) {
    throw new Error('当前配置为空')
  }

  adapter.enable = !adapter.enable
  if (!(await handleSave(adapter, originalAdapterName.value))) {
    adapter.enable = !adapter.enable // 恢复状态
  }
}

// 自动检测模型
const handleAutoDetectModels = async () => {
  showConfirmModal.value = true
}

const confirmAutoDetect = async () => {
  const adapter = currentAdapter.value
  const persistedName = originalAdapterName.value
  const request = modelDetectionRequests.begin()
  if (!adapter) return

  autoDetectLoading.value = true
  try {
    if (await handleSave(adapter, persistedName)) {
      if (
        !modelDetectionRequests.isCurrent(request.generation) ||
        currentAdapter.value !== adapter
      ) {
        return
      }
      // 后端已经与定时任务共用模型目录规范化逻辑。这里仅替换当前后端的
      // 最新可发现模型；工作流中的主模型和四个备用模型从未参与本次请求。
      const detected = await llmApi.getBackendModels(adapter.name, request.signal)
      if (
        !modelDetectionRequests.isCurrent(request.generation) ||
        currentAdapter.value !== adapter
      ) {
        return
      }
      adapter.models = detected.models as ModelInfo[]
      await handleSave(adapter, adapter.name) // 保存检测到的模型列表
    }
  } catch (error: unknown) {
    if (isAbortError(error) || !modelDetectionRequests.isCurrent(request.generation)) return
    const errorMessage = error instanceof Error ? error.message : error
    $message.error(`自动检测模型失败: ${errorMessage}`)
  } finally {
    if (modelDetectionRequests.isCurrent(request.generation)) {
      autoDetectLoading.value = false
      showConfirmModal.value = false
    }
  }
}

const cancelAutoDetect = () => {
  showConfirmModal.value = false
}

// 处理添加模型
const handleAddModel = () => {
  modelEditMode.value = 'add'
  modelEditIndex.value = -1
  currentModel.value = {
    id: '',
    type: 'llm',
    ability: 0
  }
  showModelModal.value = true
}

// 处理编辑模型
const handleEditModel = (index: number, model: ModelInfo) => {
  modelEditMode.value = 'edit'
  modelEditIndex.value = index
  currentModel.value = { ...model }
  showModelModal.value = true
}

// 处理关闭模型模态框
const handleModelModalCancel = () => {
  showModelModal.value = false
}

// 保存模型
const saveModel = (model: ModelInfo) => {
  if (!currentAdapter.value?.models) {
    currentAdapter.value!!.models = []
  }

  if (modelEditMode.value === 'add') {
    currentAdapter.value!!.models.push(model)
  } else {
    currentAdapter.value!!.models[modelEditIndex.value] = model
  }

  showModelModal.value = false
  $message.success(`${modelEditMode.value === 'add' ? '添加' : '编辑'}模型成功`)
}

// 删除配置
const handleDelete = () => {
  showDeleteConfirmModal.value = true
}

const confirmDelete = async () => {
  const adapter = currentAdapter.value
  try {
    if (!adapter?.name) {
      throw new Error('当前配置为空')
    }
    await llmApi.deleteBackend(adapter.name)
    $message.success('删除成功')
    if (currentAdapter.value === adapter) {
      currentAdapter.value = null
    }
  } catch (error: unknown) {
    const errorMessage = error instanceof Error ? error.message : '未知错误'
    $message.error(`删除失败: ${errorMessage}`)
  } finally {
    await fetchAdapters()
    showDeleteConfirmModal.value = false
  }
}

const cancelDelete = () => {
  showDeleteConfirmModal.value = false
}

// 监听适配器类型变化
watch(
  () => currentAdapter.value?.adapter,
  async (newAdapter) => {
    if (newAdapter) {
      await fetchAdapterConfigSchema(newAdapter)
    } else {
      schemaRequests.cancel()
      configSchema.value = null
      loading.value = false
    }
  }
)

watch(
  () => currentAdapter.value?.adapter,
  async (adapterType) => {
    const request = autoDetectSupportRequests.begin()
    if (!adapterType) {
      isAutoDetectModelsSupported.value = true
      return
    }
    try {
      const { supportsAutoDetectModels } =
        await llmApi.getAdapterSupportsAutoDetectModels(adapterType, request.signal)
      // 切换后端时，忽略较慢旧请求的返回，避免错误覆盖当前卡片状态。
      if (
        autoDetectSupportRequests.isCurrent(request.generation) &&
        currentAdapter.value?.adapter === adapterType
      ) {
        isAutoDetectModelsSupported.value = supportsAutoDetectModels
      }
    } catch (error: unknown) {
      if (
        !isAbortError(error) &&
        autoDetectSupportRequests.isCurrent(request.generation) &&
        currentAdapter.value?.adapter === adapterType
      ) {
        isAutoDetectModelsSupported.value = false
      }
    }
  }
)

// 初始化加载
onMounted(() => {
  void fetchAdapters()
})

onBeforeUnmount(() => {
  adapterListRequests.cancel()
  schemaRequests.cancel()
  autoDetectSupportRequests.cancel()
  modelDetectionRequests.cancel()
})
</script>

<template>
  <div class="llm-container">
    <!-- 适配器列表 -->
    <LLMAdapterList
      :adapters="adapters"
      :selectedAdapter="selectedAdapter"
      @select="handleAdapterSelect"
      @create="handleCreateAdapter"
    />

    <!-- 主内容区域 -->
    <template v-if="currentAdapter">
      <LLMAdapterConfig
        :adapter="currentAdapter"
        :adapterTypes="adapterTypes"
        :configSchema="configSchema"
        :loading="loading"
        :isCreating="isCreating"
        :isAutoDetectModelsSupported="isAutoDetectModelsSupported"
        :modelAbilities="modelAbilities"
        @update:adapter="currentAdapter = $event"
        @save="handleSave"
        @delete="handleDelete"
        @add-model="handleAddModel"
        @edit-model="handleEditModel"
        @auto-detect-models="handleAutoDetectModels"
      />
    </template>
    <template v-else>
      <LLMEmptyState :adapterTypes="adapterTypes" @create="handleCreateAdapter" />
    </template>
  </div>

  <!-- 自动检测确认模态框 -->
  <n-modal v-model:show="showConfirmModal" class="custom-modal">
    <n-card style="width: 400px" :bordered="false" size="huge" role="dialog" aria-modal="true">
      <LLMConfirmContent
        title="确认"
        content="自动检测前会保存当前配置，并仅刷新此后端当前可发现的模型目录；不会改写工作流中的主模型或备用模型。已下线模型在工作流的对应下拉槽位会显示为空，等待你手动选择替代项。"
        confirmText="继续"
        :loading="autoDetectLoading"
        @confirm="confirmAutoDetect"
        @cancel="cancelAutoDetect"
      />
    </n-card>
  </n-modal>

  <!-- 删除配置确认模态框 -->
  <n-modal v-model:show="showDeleteConfirmModal" class="custom-modal">
    <n-card style="width: 400px" :bordered="false" size="huge" role="dialog" aria-modal="true">
      <LLMConfirmContent
        title="确认删除"
        content="确定要删除此配置吗？删除后将无法恢复。"
        confirmText="删除"
        confirmType="error"
        @confirm="confirmDelete"
        @cancel="cancelDelete"
      />
    </n-card>
  </n-modal>

  <!-- 添加/编辑模型模态框 -->
  <n-modal v-model:show="showModelModal" preset="card" style="width: 800px" class="custom-modal">
    <template #header>
      {{ modelEditMode === 'add' ? '添加模型' : '编辑模型' }}
    </template>
    <LLMModelForm
      :modelInfo="currentModel"
      :modelEditMode="modelEditMode"
      :modelAbilities="modelAbilities"
      @save="saveModel"
      @cancel="handleModelModalCancel"
    />
  </n-modal>
</template>

<style scoped>
.llm-container {
  display: grid;
  grid-template-columns: 280px 1fr;
  height: calc(100vh - 28px);
  background-color: var(--bg-color);
  transition: all var(--transition-duration) var(--transition-timing-function);
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.custom-modal .n-card {
  /* 模态内的卡片就是模态自身的表面，与 .n-modal 同为大型表面档 */
  border-radius: var(--radius-lg);
  box-shadow: var(--box-shadow);
}

/* 响应式调整 */
@media (max-width: 768px) {
  .llm-container {
    grid-template-columns: 1fr;
  }
}
</style>
