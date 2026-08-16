<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NSpace,
  NButton,
  NTag,
  NIcon,
  NInput,
  NEmpty,
  NSpin,
  NText,
  NModal,
  NForm,
  NFormItem,
  useMessage
} from 'naive-ui'
import {
  SearchOutline,
  SparklesOutline,
  ChatbubblesOutline,
  GameControllerOutline,
  SettingsOutline,
  ServerOutline,
  CopyOutline,
  CreateOutline
} from '@vicons/ionicons5'
import {
  listWorkflows,
  getWorkflow,
  createWorkflow,
  type WorkflowInfo,
  type Workflow
} from '@/api/workflow'

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const cloning = ref(false)
const workflows = ref<WorkflowInfo[]>([])
const searchQuery = ref('')

// 从模板创建的表单
const showCloneModal = ref(false)
const cloneSource = ref<WorkflowInfo | null>(null)
const cloneForm = ref({ groupId: 'user', workflowId: '', name: '' })
const showCloneResultModal = ref(false)
const createdWorkflow = ref<{ groupId: string; workflowId: string; name: string } | null>(null)

/**
 * 分组元信息。
 *
 * 这些 group 与后端 `register_preset_workflow` / `data/workflows` 的目录名一一对应，
 * 在这里补上中文名、说明与图标，让用户不必去读 YAML 就知道每类模板的用途。
 */
const GROUP_META: Record<
  string,
  { label: string; description: string; icon: any; tone: 'info' | 'success' | 'warning' | 'default' }
> = {
  chat: {
    label: '对话模板',
    description: '完整的聊天回复流程，包含记忆读写与模型调用，是最常用的起点。',
    icon: ChatbubblesOutline,
    tone: 'info'
  },
  system: {
    label: '系统功能',
    description: '帮助信息、清空记忆等运维类指令，通常配合固定前缀触发。',
    icon: SettingsOutline,
    tone: 'default'
  },
  game: {
    label: '娱乐玩法',
    description: '掷骰、抽卡等互动小功能，适合作为编写自定义工作流的示例。',
    icon: GameControllerOutline,
    tone: 'warning'
  },
  user: {
    label: '我的工作流',
    description: '你自己创建或从模板复制出来的工作流。',
    icon: SparklesOutline,
    tone: 'success'
  }
}

const getGroupMeta = (groupId: string) =>
  GROUP_META[groupId] || {
    label: groupId,
    description: '自定义分组。',
    icon: ServerOutline,
    tone: 'default' as const
  }

const filtered = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return workflows.value
  return workflows.value.filter(
    (item) =>
      item.name.toLowerCase().includes(query) ||
      item.description?.toLowerCase().includes(query) ||
      `${item.group_id}:${item.workflow_id}`.toLowerCase().includes(query)
  )
})

/** 按 group 归类，并让 chat / system / game 这三个内置分组排在前面 */
const grouped = computed(() => {
  const groups: Record<string, WorkflowInfo[]> = {}
  for (const item of filtered.value) {
    if (!groups[item.group_id]) groups[item.group_id] = []
    groups[item.group_id].push(item)
  }

  const order = ['chat', 'system', 'game']
  return Object.keys(groups)
    .sort((a, b) => {
      const ia = order.indexOf(a)
      const ib = order.indexOf(b)
      if (ia !== -1 && ib !== -1) return ia - ib
      if (ia !== -1) return -1
      if (ib !== -1) return 1
      return a.localeCompare(b)
    })
    .map((groupId) => ({ groupId, items: groups[groupId] }))
})

const loadWorkflows = async () => {
  loading.value = true
  try {
    const { workflows: list } = await listWorkflows()
    workflows.value = list
  } catch (error) {
    message.error('加载工作流模板失败')
  } finally {
    loading.value = false
  }
}

const handleOpen = (item: WorkflowInfo) => {
  router.push(`/workflow/editor/${item.group_id}:${item.workflow_id}`)
}

const openCloneModal = (item: WorkflowInfo) => {
  cloneSource.value = item
  // 默认生成一个不易冲突的 id，用户可以改
  const suffix = Array.from({ length: 4 }, () => Math.floor(Math.random() * 36).toString(36)).join(
    ''
  )
  cloneForm.value = {
    groupId: 'user',
    workflowId: `${item.workflow_id}_${suffix}`,
    name: `${item.name} 副本`
  }
  showCloneModal.value = true
}

/**
 * 以模板为基础创建新工作流。
 *
 * 复用既有的 getWorkflow + createWorkflow 接口：先取出模板的完整定义
 * （blocks / wires / config 都带坐标），再以新的 group:id 保存一份。
 * 这样用户改自己的副本不会影响内置模板，升级时模板也不会被覆盖。
 */
const handleClone = async () => {
  if (!cloneSource.value) return
  const { groupId, workflowId, name } = cloneForm.value
  if (!groupId.trim() || !workflowId.trim() || !name.trim()) {
    message.error('分组、ID 和名称都不能为空')
    return
  }
  if (!/^[a-zA-Z0-9_-]+$/.test(groupId) || !/^[a-zA-Z0-9_-]+$/.test(workflowId)) {
    message.error('分组和 ID 只能包含字母、数字、下划线和短横线')
    return
  }

  cloning.value = true
  try {
    const { workflow } = await getWorkflow(
      cloneSource.value.group_id,
      cloneSource.value.workflow_id
    )
    const payload: Workflow = {
      ...workflow,
      group_id: groupId,
      workflow_id: workflowId,
      name
    }
    await createWorkflow(groupId, workflowId, payload)
    message.success('已从模板创建工作流')
    showCloneModal.value = false
    createdWorkflow.value = { groupId, workflowId, name }
    showCloneResultModal.value = true
  } catch (error: any) {
    message.error(error?.message || '从模板创建失败')
  } finally {
    cloning.value = false
  }
}

const continueEditingCreatedWorkflow = () => {
  if (!createdWorkflow.value) return
  const { groupId, workflowId } = createdWorkflow.value
  router.push(`/workflow/editor/${groupId}:${workflowId}`)
}

/**
 * 只创建规则编辑草稿，不在模板克隆后擅自新增或覆盖任何触发规则。
 */
const createRuleForCreatedWorkflow = () => {
  if (!createdWorkflow.value) return
  const { groupId, workflowId } = createdWorkflow.value
  router.push({
    name: 'workflow-dispatch-rules',
    query: { workflow_id: `${groupId}:${workflowId}` }
  })
}

onMounted(() => {
  loadWorkflows()
})
</script>

<template>
  <div class="templates-page">
    <n-card class="templates-header" :bordered="false">
      <div class="header-main">
        <div class="header-title">
          <n-icon size="26" class="title-icon">
            <SparklesOutline />
          </n-icon>
          <span>工作流模板</span>
        </div>
        <div class="header-description">
          这里列出了全部可用的工作流。内置模板已经配好节点与连线，
          点「以此为模板」复制一份即可在副本上自由修改，原模板不受影响。
        </div>
      </div>
      <div class="header-actions">
        <n-input
          v-model:value="searchQuery"
          placeholder="搜索模板名称、说明或 ID"
          clearable
          class="search-input"
        >
          <template #prefix>
            <n-icon><SearchOutline /></n-icon>
          </template>
        </n-input>
        <n-button @click="loadWorkflows" :loading="loading">刷新</n-button>
      </div>
    </n-card>

    <n-spin :show="loading">
      <n-empty
        v-if="!loading && grouped.length === 0"
        description="没有匹配的工作流模板"
        class="empty-state"
      />

      <div v-for="group in grouped" :key="group.groupId" class="template-group">
        <div class="group-header">
          <n-icon size="18" class="group-icon">
            <component :is="getGroupMeta(group.groupId).icon" />
          </n-icon>
          <span class="group-label">{{ getGroupMeta(group.groupId).label }}</span>
          <n-tag size="small" round :bordered="false" :type="getGroupMeta(group.groupId).tone">
            {{ group.items.length }}
          </n-tag>
          <span class="group-description">{{ getGroupMeta(group.groupId).description }}</span>
        </div>

        <div class="template-grid">
          <n-card
            v-for="item in group.items"
            :key="`${item.group_id}:${item.workflow_id}`"
            class="template-card"
            :bordered="false"
            embedded
          >
            <div class="card-title">
              <span class="card-name">{{ item.name }}</span>
              <n-tag size="small" :bordered="false" class="card-id">
                {{ item.group_id }}:{{ item.workflow_id }}
              </n-tag>
            </div>
            <div class="card-description">
              <n-text depth="3">{{ item.description || '暂无说明' }}</n-text>
            </div>
            <div class="card-meta">
              <n-tag size="small" round :bordered="false">{{ item.block_count }} 个节点</n-tag>
            </div>
            <div class="card-actions">
              <n-button size="small" secondary @click="openCloneModal(item)">
                <template #icon>
                  <n-icon><CopyOutline /></n-icon>
                </template>
                以此为模板
              </n-button>
              <n-button size="small" quaternary @click="handleOpen(item)">
                <template #icon>
                  <n-icon><CreateOutline /></n-icon>
                </template>
                直接编辑
              </n-button>
            </div>
          </n-card>
        </div>
      </div>
    </n-spin>

    <!-- 从模板创建 -->
    <n-modal
      v-model:show="showCloneModal"
      preset="card"
      title="从模板创建工作流"
      :style="{ width: '520px' }"
    >
      <n-form label-placement="left" label-width="90">
        <n-form-item label="来源模板">
          <n-text depth="2">
            {{ cloneSource?.name }}（{{ cloneSource?.group_id }}:{{ cloneSource?.workflow_id }}）
          </n-text>
        </n-form-item>
        <n-form-item label="分组">
          <n-input v-model:value="cloneForm.groupId" placeholder="例如 user" />
        </n-form-item>
        <n-form-item label="工作流 ID">
          <n-input v-model:value="cloneForm.workflowId" placeholder="仅字母、数字、下划线、短横线" />
        </n-form-item>
        <n-form-item label="名称">
          <n-input v-model:value="cloneForm.name" placeholder="请输入新工作流的名称" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCloneModal = false">取消</n-button>
          <n-button type="primary" :loading="cloning" @click="handleClone">创建并编辑</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal
      v-model:show="showCloneResultModal"
      preset="card"
      title="工作流已创建"
      :style="{ width: '520px' }"
    >
      <n-text v-if="createdWorkflow" depth="2">
        已创建「{{ createdWorkflow.name }}」。你可以先编辑工作流，或选择为它准备一条触发规则草稿。
      </n-text>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCloneResultModal = false">留在模板中心</n-button>
          <n-button secondary @click="createRuleForCreatedWorkflow">创建触发规则草稿</n-button>
          <n-button type="primary" @click="continueEditingCreatedWorkflow">继续编辑</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.templates-page {
  padding: 20px;
}

.templates-header {
  margin-bottom: 20px;
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.header-main {
  margin-bottom: 16px;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 8px;
}

.title-icon {
  color: var(--primary-color);
}

.header-description {
  font-size: 13px;
  color: var(--text-color-secondary);
  line-height: 1.6;
  max-width: 720px;
}

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.search-input {
  max-width: 360px;
}

.template-group {
  margin-bottom: 28px;
}

.group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.group-icon {
  color: var(--primary-color);
}

.group-label {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-color);
}

.group-description {
  font-size: 12px;
  color: var(--text-color-tertiary);
}

.template-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.template-card {
  transition: all var(--transition-duration) var(--transition-timing-function);
}

.template-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--box-shadow-hover);
}

.card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.card-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-id {
  font-family: 'SFMono-Regular', Consolas, Menlo, monospace;
  font-size: 11px;
  flex-shrink: 0;
}

.card-description {
  font-size: 12px;
  line-height: 1.5;
  min-height: 36px;
  margin-bottom: 10px;
}

.card-meta {
  margin-bottom: 12px;
}

.card-actions {
  display: flex;
  gap: 8px;
}

.empty-state {
  padding: 60px 0;
}

@media (max-width: 768px) {
  .templates-page {
    padding: 12px;
  }

  .header-actions {
    flex-direction: column;
    align-items: stretch;
  }

  .search-input {
    max-width: none;
  }
}
</style>
