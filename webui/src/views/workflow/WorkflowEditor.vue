<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue'
import { routerKey, useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { useMessage, useDialog, NButton, NResult, NSpin } from 'naive-ui'
import {
  getWorkflow,
  createWorkflow,
  updateWorkflow,
  type BlockInstance,
  type Wire,
  type WorkflowConfig
} from '@/api/workflow'
import { listBlockTypes, type BlockType } from '@/api/block'
import WorkflowCanvas from '@/components/workflow/WorkflowCanvas.vue'
import { mergeWorkflowConfig } from '@/components/workflow/workflow-data'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const workflowId = ref('')
const groupId = ref('')
const name = ref('')
const description = ref('')
const blocks = ref<BlockInstance[]>([])
const wires = ref<Wire[]>([])
const config = ref<WorkflowConfig>({
  max_execution_time: 36000
})
const blockTypes = ref<BlockType[]>([])
const loading = ref(false)
const saving = ref(false)
const loadError = ref<string | null>(null)
const initialized = ref(false)

/**
 * 是否存在尚未保存的画布改动。
 *
 * 画布把编辑经 update:blocks / update:wires / update:config 回传到这里，
 * 因此在这三个处理函数里置脏、在保存成功与重新载入后清零即可覆盖全部编辑路径。
 * beforeunload 只能拦住关闭标签页与刷新，拦不住 Vue Router 的站内跳转；
 * 而画布卸载时会取消 debounce 里待写入的那一次同步，改动会静默丢失，
 * 所以这里补一道路由离开确认。
 */
const hasUnsavedChanges = ref(false)
/** 载入工作流时会连带触发一轮回显，这段窗口内的回传不算用户编辑。 */
let suppressDirtyTracking = false

const markDirty = () => {
  if (suppressDirtyTracking) return
  hasUnsavedChanges.value = true
}

/**
 * 在载入或保存前后暂停置脏。
 *
 * 载入会经 props 回流到画布并触发一次 update:blocks（例如为缺失坐标的节点
 * 补上自动布局结果），保存成功后父组件也会把提交值写回；这些都不是用户编辑。
 * 用 nextTick 等回显走完再恢复追踪。
 */
const withoutDirtyTracking = async (action: () => Promise<void> | void) => {
  suppressDirtyTracking = true
  try {
    await action()
  } finally {
    await nextTick()
    hasUnsavedChanges.value = false
    suppressDirtyTracking = false
  }
}

const handleSave = async (workflowName: string, workflowDesc: string, newWorkflowId: string) => {
  const [group, workflow] = newWorkflowId.split(':')
  const data = {
    group_id: group,
    workflow_id: workflow,
    name: workflowName,
    description: workflowDesc,
    blocks: blocks.value,
    wires: wires.value,
    config: config.value
  }

  saving.value = true
  try {
    if (route.params.id) {
      await updateWorkflow(groupId.value, workflowId.value, data)
      // 保存成功即视为已落盘。必须在下面的 router.push 之前清零，
      // 否则改名跳转会被自己的路由守卫当成“带未保存改动离开”而弹确认。
      suppressDirtyTracking = true
      hasUnsavedChanges.value = false
      if (groupId.value !== group || workflowId.value !== workflow) {
        groupId.value = group
        workflowId.value = workflow
        router.push(`/workflow/editor/${data.group_id}:${data.workflow_id}`)
      }
      name.value = data.name
      description.value = data.description
      blocks.value = data.blocks
      wires.value = data.wires
      config.value = data.config
      // 更新页面标题
      document.title = `工作流 - ${data.name}`
      message.success('保存成功')
    } else {
      await createWorkflow(data.group_id, data.workflow_id, data)
      suppressDirtyTracking = true
      hasUnsavedChanges.value = false
      groupId.value = data.group_id
      workflowId.value = data.workflow_id
      description.value = data.description
      name.value = data.name
      document.title = `工作流 - ${data.name}`
      message.success('创建成功')
      router.push(`/workflow/editor/${data.group_id}:${data.workflow_id}`)
    }
  } catch (caught: unknown) {
    const errorMessage = caught instanceof Error ? caught.message : '保存失败'
    message.error(`保存失败：${errorMessage}`)
  } finally {
    saving.value = false
    // 等 props 回流带来的那一轮回显走完，再恢复置脏追踪，
    // 避免把保存自身写回的值当成新的用户编辑。
    await nextTick()
    if (suppressDirtyTracking) {
      hasUnsavedChanges.value = false
      suppressDirtyTracking = false
    }
  }
}

const fetchWorkflow = async () => {
  if (!route.params.id) {
    // 同一路由组件会在“编辑现有工作流”与“新建工作流”之间复用；进入新建
    // 地址时必须清空上一份定义，不能把旧节点、连线或运行配置带进新工作流。
    groupId.value = ''
    workflowId.value = ''
    name.value = ''
    description.value = ''
    blocks.value = []
    wires.value = []
    config.value = { max_execution_time: 36000 }
    return
  }

  const [group, workflow] = (route.params.id as string).split(':')
  groupId.value = group
  workflowId.value = workflow

  loading.value = true
  loadError.value = null
  try {
    const { workflow: data } = await getWorkflow(group, workflow)
    name.value = data.name
    description.value = data.description
    blocks.value = data.blocks
    wires.value = data.wires
    config.value = mergeWorkflowConfig(data.config, config.value)
    // 更新页面标题
    document.title = `工作流 - ${data.name}`
  } catch (caught: unknown) {
    const errorMessage = caught instanceof Error ? caught.message : '获取工作流失败'
    loadError.value = errorMessage
    message.error(`获取工作流失败：${errorMessage}`)
  } finally {
    loading.value = false
  }
}

const fetchBlockTypes = async () => {
  try {
    const { types } = await listBlockTypes()
    blockTypes.value = types
  } catch (error) {
    message.error('获取区块类型失败')
  }
}

const handleBlocksChange = (newBlocks: any[]) => {
  blocks.value = newBlocks
  markDirty()
}

const handleWiresChange = (newWires: any[]) => {
  wires.value = newWires
  markDirty()
}

const handleConfigChange = (newConfig: WorkflowConfig) => {
  config.value = newConfig
  markDirty()
}

const initializeEditor = async () => {
  await withoutDirtyTracking(async () => {
    initialized.value = false
    loadError.value = null
    await Promise.all([fetchWorkflow(), fetchBlockTypes()])
    if (!loadError.value) {
      initialized.value = true
    }
  })
}

onMounted(() => {
  void initializeEditor()
})

// Vue Router 会复用同一个编辑器组件来处理不同的 :id。监听参数变化，避免
// 浏览器前进/后退或保存后改名时继续显示上一份工作流的数据。
watch(
  () => route.params.id,
  () => {
    void initializeEditor()
  }
)

/**
 * 站内跳转前确认未保存的改动。
 *
 * beforeunload 只覆盖关闭标签页与刷新；点击侧边栏等站内跳转不会触发它，
 * 而画布卸载时会取消 debounce 中待写入的同步，改动会静默丢失。
 * 这里只在确实有未保存改动时拦一次，其余情况不打扰用户。
 */
onBeforeRouteLeave(() => {
  if (!hasUnsavedChanges.value) return true

  return new Promise<boolean>((resolve) => {
    dialog.warning({
      title: '离开工作流编辑器',
      content: '当前工作流有未保存的改动，离开后这些改动会丢失。是否仍要离开？',
      positiveText: '放弃改动并离开',
      negativeText: '留在本页',
      closable: false,
      maskClosable: false,
      onPositiveClick: () => {
        // 用户已明确放弃，清零以免返回本页后又被拦一次
        hasUnsavedChanges.value = false
        resolve(true)
      },
      onNegativeClick: () => resolve(false),
      onClose: () => resolve(false)
    })
  })
})
</script>

<template>
  <div class="workflow-editor">
    <WorkflowCanvas
      v-if="initialized && !loadError"
      :blocks="blocks"
      :wires="wires"
      :block-types="blockTypes"
      :initial-name="name"
      :initial-description="description"
      :initial-workflow-id="groupId + ':' + workflowId"
      :initial-config="config"
      :loading="saving"
      @update:blocks="handleBlocksChange"
      @update:wires="handleWiresChange"
      @update:config="handleConfigChange"
      @save="handleSave"
    />
    <div v-else-if="loadError" class="error-result">
      <NResult status="error" title="无法加载工作流" :description="loadError">
        <template #footer>
          <NButton type="primary" @click="initializeEditor">重试</NButton>
        </template>
      </NResult>
    </div>
    <div v-else class="loading-spinner">
      <NSpin size="large" description="正在加载工作流..." />
    </div>
  </div>
</template>

<style scoped>
.workflow-editor {
  width: 100%;
  height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  background: var(--canvas-bg-color, var(--background-color));
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
}

.loading-spinner {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.error-result {
  margin: auto;
  padding: 2rem;
  background: var(--card-bg-color, white);
  border-radius: var(--radius-md);
  box-shadow: var(--box-shadow-hover, 0 8px 24px rgba(0, 0, 0, 0.12));
  animation: slide-up 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 工作流画布的动画效果 */
:deep(.workflow-canvas) {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

:deep(.workflow-canvas.saving) {
  filter: blur(1px);
  pointer-events: none;
}

:deep(.node) {
  transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1),
    box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

:deep(.node:hover) {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

:deep(.connection) {
  transition: stroke-width 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

:deep(.connection:hover) {
  stroke-width: 3px;
}
</style>
