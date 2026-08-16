<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, h, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  NDataTable,
  NButton,
  NSpace,
  NCard,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSwitch,
  useMessage,
  NModal,
  NDivider,
  NScrollbar,
  NAlert,
  NTag,
  NCheckbox,
  type FormInst,
  NIcon,
  NTooltip
} from 'naive-ui'
import {
  dispatchApi,
  getRuleTypeLabel,
  type DispatchRule,
  type DispatchPreviewInput,
  type DispatchPreviewResponse,
  type DispatchPreviewRuleResult,
  type DispatchRuleReachability,
  type RuleGroup,
  type SimpleRule
} from '@/api/dispatch'
import { listWorkflows, type WorkflowInfo } from '@/api/workflow'
import DynamicConfigForm from '@/components/form/DynamicConfigForm.vue'
import { Add, Remove, PencilOutline, HelpCircleOutline } from '@vicons/ionicons5'
import { v4 as uuidv4 } from 'uuid'
import { cloneDispatchRule } from './dispatch-rule-utils'
import {
  getDispatchPreviewDecisionLabel,
  getDispatchPreviewDecisionType
} from './dispatch-preview-utils'

const message = useMessage()
const route = useRoute()
const rules = ref<DispatchRule[]>([])
/**
 * 规则的匹配次序与遮蔽状态，全部来自后端。
 *
 * 「无条件规则会让后续规则永远不被判断」这套语义只在
 * `kirara_ai/workflow/core/dispatch/reachability.py` 中定义一次，前端不再复刻，
 * 否则界面与调度器迟早会对「这条规则到底会不会触发」给出相反的结论。
 */
const ruleReachability = ref<DispatchRuleReachability[]>([])
const ruleTypes = ref<string[]>([])
const showEditModal = ref(false)
const currentRule = ref<DispatchRule>({
  rule_id: '',
  name: '',
  description: '',
  workflow_id: '',
  priority: 5,
  enabled: true,
  rule_groups: [
    {
      operator: 'or',
      rules: []
    }
  ],
  metadata: {}
})
const configSchema = ref<any>(null)
const isCreate = ref(false)
const workflows = ref<WorkflowInfo[]>([])
const selectedRuleType = ref<string>('')
const selectedRuleGroupIndex = ref<number>(-1)
const selectedRuleIndex = ref<number>(-1)
const showRuleConfigModal = ref(false)
const showPreviewModal = ref(false)
const previewLoading = ref(false)
const previewResult = ref<DispatchPreviewResponse | null>(null)
const previewDraft = ref<DispatchRule | undefined>()
const previewInput = ref<DispatchPreviewInput>({
  content: '',
  chat_type: '私聊',
  sender_id: 'preview-user',
  group_id: '',
  mentioned: false
})

// 表格列定义
const columns = [
  {
    title: '#',
    key: '_order',
    width: 56,
    render: (row: any) =>
      h(
        NTooltip,
        { trigger: 'hover', placement: 'right' },
        {
          trigger: () =>
            h(
              NTag,
              {
                size: 'small',
                round: true,
                bordered: false,
                type: row._shadowed ? 'warning' : 'default'
              },
              // 已禁用的规则不参与匹配，因此没有次序，用「—」占位而不是给个假序号
              { default: () => (row._order === null ? '—' : String(row._order)) }
            ),
          default: () =>
            row._order === null
              ? '此规则已禁用，不参与匹配，因此没有匹配顺序。'
              : row._shadowed
                ? '此规则排在一条无条件规则之后，永远不会被判断到。请提高它的优先级。'
                : `匹配顺序第 ${row._order} 位，命中后不再判断后续规则。`        }
      )
  },
  { title: '名称', key: 'name' },
  { title: '描述', key: 'description' },
  {
    title: '工作流',
    key: 'workflow_id',
    render: (row: DispatchRule) => {
      const workflow = workflows.value.find(
        (workflow) => `${workflow.group_id}:${workflow.workflow_id}` === row.workflow_id
      )
      return workflow ? `${workflow.name} (${row.workflow_id})  ` : '未指定'
    }
  },
  {
    title: () =>
      h(
        NTooltip,
        {
          trigger: 'hover',
          placement: 'top'
        },
        {
          trigger: () =>
            h(
              'div',
              { style: { display: 'flex', alignItems: 'center' } },
              {
                default: () => [
                  '优先级',
                  h(
                    NIcon,
                    {},
                    {
                      default: () => h(HelpCircleOutline)
                    }
                  )
                ]
              }
            ),
          default: () =>
            '优先级定义了规则判定的顺序，数值越大优先级越高，会优先被判断。建议根据业务需求合理设置优先级。'
        }
      ),
    key: 'priority'
  },
  {
    title: '状态',
    key: 'enabled',
    render: (row: DispatchRule) => {
      return h(NSwitch, {
        value: row.enabled,
        onUpdateValue: async (value) => {
          try {
            if (value) {
              await dispatchApi.enableRule(row.rule_id)
            } else {
              await dispatchApi.disableRule(row.rule_id)
            }
            await loadRules()
            message.success('操作成功')
          } catch (error) {
            message.error('操作失败')
          }
        }
      })
    }
  },
  {
    title: '操作',
    key: 'actions',
    render: (row: DispatchRule) => {
      return h(NSpace, null, {
        default: () => [
          h(
            NButton,
            {
              size: 'small',
              onClick: () => editRule(row)
            },
            { default: () => '编辑' }
          ),
          h(
            NButton,
            {
              size: 'small',
              type: 'error',
              onClick: () => deleteRule(row.rule_id)
            },
            { default: () => '删除' }
          )
        ]
      })
    }
  }
]

const formRef = ref<FormInst | null>(null)
const validationRules = ref<any>({
  name: [
    { required: true, message: '请输入规则名称' },
    { min: 1, max: 100, message: '规则名称长度应在1-100个字符之间' }
  ],
  workflow_id: [{ required: true, message: '请选择工作流' }],
  priority: [
    { required: true, message: '请输入优先级' },
    { type: 'number', min: 0, max: 100, message: '优先级应在0-100之间' }
  ],
  enabled: [{ required: true, message: '请选择启用状态' }]
})

// 加载规则列表
const loadRules = async () => {
  try {
    const { rules: ruleList, reachability } = await dispatchApi.getRules()
    rules.value = ruleList
    // 后端在同一次响应里给出可达性，列表与遮蔽提示天然一致，不会出现中间态。
    ruleReachability.value = reachability ?? []
  } catch (error) {
    message.error('加载规则列表失败')
  }
}

const loadWorkflows = async () => {
  try {
    const { workflows: workflowList } = await listWorkflows()
    workflows.value = workflowList
  } catch (error) {
    message.error('加载工作流列表失败')
  }
}

// 加载规则类型
const loadRuleTypes = async () => {
  try {
    const { types } = await dispatchApi.getRuleTypes()
    ruleTypes.value = types
  } catch (error) {
    message.error('加载规则类型失败')
  }
}

// 加载规则配置模式
const loadConfigSchema = async (type: string) => {
  try {
    const { configSchema: schema } = await dispatchApi.getRuleConfigSchema(type)
    configSchema.value = schema
  } catch (error) {
    message.error('加载配置模式失败')
  }
}

// 创建规则
const createRule = (workflowId = '') => {
  isCreate.value = true
  currentRule.value = {
    rule_id: uuidv4(),
    priority: 5,
    enabled: true,
    name: '',
    description: '',
    workflow_id: workflowId,
    rule_groups: [
      {
        operator: 'or',
        rules: []
      }
    ],
    metadata: {}
  }
  showEditModal.value = true
}

// 编辑规则
const editRule = (rule: DispatchRule) => {
  isCreate.value = false
  currentRule.value = cloneDispatchRule(rule)
  showEditModal.value = true
}

// 删除规则
const deleteRule = async (ruleId: string) => {
  try {
    await dispatchApi.deleteRule(ruleId)
    await loadRules()
    message.success('删除成功')
  } catch (error) {
    message.error('删除失败')
  }
}

// 保存规则
const saveRule = async (isCreate: boolean) => {
  try {
    const errors = await formRef.value?.validate()
    if (errors?.warnings?.length) {
      message.error('请检查输入内容')
      return
    }
    if (isCreate) {
      await dispatchApi.createRule(currentRule.value)
    } else {
      await dispatchApi.updateRule(currentRule.value.rule_id!, currentRule.value)
    }
    await loadRules()
    showEditModal.value = false
    message.success('保存成功')
  } catch (error) {
    message.error('保存失败:' + (error as Error).message)
  }
}

/** 打开只读试运行；编辑中的草稿会作为临时规则参与排序，绝不写入服务端。 */
const openPreview = (draftRule?: DispatchRule) => {
  previewDraft.value = draftRule ? cloneDispatchRule(draftRule) : undefined
  previewResult.value = null
  showPreviewModal.value = true
}

const runPreview = async () => {
  previewLoading.value = true
  try {
    previewResult.value = await dispatchApi.previewRules({
      ...previewInput.value,
      group_id: previewInput.value.group_id || null,
      draft_rule: previewDraft.value
    })
  } catch (error) {
    message.error('试运行失败：' + (error as Error).message)
  } finally {
    previewLoading.value = false
  }
}

const previewReason = (result: DispatchPreviewRuleResult) => {
  if (result.explanation.reason) return result.explanation.reason as string
  const conditions = (result.explanation.groups || []).flatMap((group: any) => group.rules || [])
  const failed = conditions.filter((condition: any) => condition.matched === false)
  if (failed.length) return `未满足：${failed.map((condition: any) => getRuleTypeLabel(condition.type)).join('、')}`
  return result.matched ? '全部条件满足' : ''
}

// 添加规则组
const addRuleGroup = () => {
  if (!currentRule.value.rule_groups) {
    currentRule.value.rule_groups = []
  }
  currentRule.value.rule_groups.push({
    operator: 'or',
    rules: []
  })
}

// 删除规则组
const removeRuleGroup = (index: number) => {
  currentRule.value.rule_groups?.splice(index, 1)
}

// 添加规则
const addRule = (groupIndex: number) => {
  selectedRuleGroupIndex.value = groupIndex
  selectedRuleIndex.value = currentRule.value.rule_groups[groupIndex].rules.length

  // 创建新规则，但不设置类型
  const newRule: SimpleRule = {
    type: '',
    config: {}
  }
  currentRule.value.rule_groups[groupIndex].rules.push(newRule)
}

// 删除规则
const removeRule = (groupIndex: number, ruleIndex: number) => {
  currentRule.value.rule_groups[groupIndex].rules.splice(ruleIndex, 1)
}

// 规则类型选项
const ruleTypeOptions = computed(() =>
  ruleTypes.value.map((type) => ({
    label: getRuleTypeLabel(type),
    value: type
  }))
)

/**
 * 按实际匹配顺序排列的规则视图。
 *
 * 后端 `get_active_rules()` 按「优先级降序 + rule_id 升序」排序后**首个命中即执行**，
 * 但表格原先按接口返回顺序展示，用户无法看出谁会先被判断。这里复刻同一套
 * 排序，并标注序号与遮蔽风险，让「为什么这条规则没生效」变得可见。
 *
 * 排序与遮蔽判定都来自后端 `/dispatch/rules` 的 reachability 字段（唯一实现），
 * 本计算属性只负责把它挂到表格行上，不再自己判断什么是无条件规则。
 */
const orderedRules = computed(() => {
  const reachabilityByRuleId = new Map(
    ruleReachability.value.map((item) => [item.rule_id, item])
  )
  const ruleByRuleId = new Map(rules.value.map((rule) => [rule.rule_id, rule]))

  // 以后端给出的次序为准；接口已按调度顺序返回规则，两者天然对齐。
  const orderedIds = ruleReachability.value.length
    ? ruleReachability.value.map((item) => item.rule_id)
    : rules.value.map((rule) => rule.rule_id)

  return orderedIds
    .map((ruleId, index) => {
      const rule = ruleByRuleId.get(ruleId)
      if (!rule) return null
      const reachability = reachabilityByRuleId.get(ruleId)
      return {
        ...rule,
        // 后端已给出结论时以它为准：order 为 null 表示该规则已禁用、不参与匹配，
        // 此时不能用下标补一个假序号，否则又会回到「序号虚高」的老问题。
        _order: reachability ? reachability.order : index + 1,
        _catchAll: reachability?.catch_all ?? false,
        // 被前面的无条件规则完全遮蔽
        _shadowed: reachability?.unreachable ?? false,
        _shadowedBy: reachability?.shadowed_by_rule_id ?? null
      }
    })
    .filter((row): row is NonNullable<typeof row> => row !== null)
})

/** 存在被遮蔽的规则时在页面顶部给出提示 */
const shadowedRules = computed(() => orderedRules.value.filter((rule) => rule._shadowed))

/**
 * 编辑草稿的可达性预判。
 *
 * 语义仍然只有后端那一份：这里把草稿 POST 给 `/dispatch/reachability` 做静态分析，
 * 只是把请求 debounce 起来以保持「边改边看」的即时感，绝不在本地重算遮蔽关系。
 * 因此界面不可能与后端对「这条规则会不会被触发」产生分歧。
 */
const draftReachability = ref<DispatchRuleReachability | null>(null)
const draftShadowedRuleNames = ref<string[]>([])
let draftReachabilityTimer: number | null = null
/** 只采纳最后一次请求的结果，避免慢响应覆盖新草稿的判断 */
let draftReachabilityRequestId = 0

const refreshDraftReachability = async () => {
  const requestId = ++draftReachabilityRequestId
  const draft = cloneDispatchRule(currentRule.value)
  // 还没配置任何条件的草稿在后端看来等同于无条件规则，但它本来就无法保存
  // （创建/更新接口会拒绝空条件规则），此时提示“会遮蔽后续规则”只是噪音。
  const draftHasConditions = (draft.rule_groups || []).some((group) => group.rules.length > 0)
  try {
    const { reachability } = await dispatchApi.analyzeReachability(draft)
    if (requestId !== draftReachabilityRequestId) return
    const draftEntry = reachability.find((item) => item.rule_id === draft.rule_id) ?? null
    draftReachability.value = draftEntry
    // 草稿自身是无条件规则时，它会遮蔽排在后面的其他规则，同样值得提示。
    draftShadowedRuleNames.value = draftHasConditions
      ? reachability
          .filter((item) => item.unreachable && item.shadowed_by_rule_id === draft.rule_id)
          .map((item) => item.name)
      : []
  } catch (error) {
    if (requestId !== draftReachabilityRequestId) return
    // 预判失败不该打断编辑：清空提示即可，保存时后端仍是权威判断。
    draftReachability.value = null
    draftShadowedRuleNames.value = []
  }
}

const scheduleDraftReachability = () => {
  if (draftReachabilityTimer !== null) {
    window.clearTimeout(draftReachabilityTimer)
  }
  draftReachabilityTimer = window.setTimeout(() => {
    draftReachabilityTimer = null
    void refreshDraftReachability()
  }, 300)
}

watch(
  [showEditModal, currentRule],
  ([isEditing]) => {
    if (!isEditing) {
      if (draftReachabilityTimer !== null) {
        window.clearTimeout(draftReachabilityTimer)
        draftReachabilityTimer = null
      }
      draftReachabilityRequestId += 1
      draftReachability.value = null
      draftShadowedRuleNames.value = []
      return
    }
    scheduleDraftReachability()
  },
  { deep: true }
)

onBeforeUnmount(() => {
  if (draftReachabilityTimer !== null) {
    window.clearTimeout(draftReachabilityTimer)
    draftReachabilityTimer = null
  }
})

// 规则类型变化时更新配置表单
const handleRuleTypeChange = async (type: string, groupIndex: number, ruleIndex: number) => {
  if (!type) return // 如果用户清空了选择，直接返回

  selectedRuleType.value = type
  selectedRuleGroupIndex.value = groupIndex
  selectedRuleIndex.value = ruleIndex

  // 更新规则类型
  currentRule.value.rule_groups[groupIndex].rules[ruleIndex].type = type
  currentRule.value.rule_groups[groupIndex].rules[ruleIndex].config = {}

  // 加载配置模式并打开配置对话框
  await loadConfigSchema(type)
  showRuleConfigModal.value = true
}

// 确认规则配置
const confirmRuleConfig = (config: any) => {
  if (selectedRuleGroupIndex.value === -1 || !currentRule.value.rule_groups) return

  // 更新规则配置
  currentRule.value.rule_groups[selectedRuleGroupIndex.value].rules[
    selectedRuleIndex.value
  ].config = config
}

// 关闭规则配置对话框
const closeRuleConfigModal = () => {
  showRuleConfigModal.value = false
}

onMounted(async () => {
  await Promise.all([loadRules(), loadRuleTypes(), loadWorkflows()])

  // 模板中心只通过 URL 传入“创建草稿”的意图；不自动保存、更不会覆盖已有规则。
  const workflowId = typeof route.query.workflow_id === 'string' ? route.query.workflow_id : ''
  if (workflowId) {
    if (workflows.value.some((workflow) => `${workflow.group_id}:${workflow.workflow_id}` === workflowId)) {
      createRule(workflowId)
    } else {
      message.warning('目标工作流不存在，未创建触发规则草稿')
    }
  }
})
</script>

<template>
  <div class="dispatch-rules">
    <n-space vertical>
      <n-card title="规则列表" class="dispatch-rules-card">
        <template #header-extra>
          <n-space>
            <n-button @click="openPreview()"> 试运行消息 </n-button>
            <n-button type="primary" @click="createRule"> 创建规则 </n-button>
          </n-space>
        </template>
        <div class="dispatch-rules-description">
          触发规则决定了 Kirara AI 何时会执行工作流，更多介绍请阅读<a
            href="https://kirara-docs.app.lss233.com/guide/configuration/dispatch.html"
            target="_blank"
            >官方文档</a
          >。
        </div>
        <n-alert
          v-if="shadowedRules.length > 0"
          type="warning"
          :bordered="false"
          class="dispatch-rules-alert"
        >
          有 {{ shadowedRules.length }} 条规则排在无条件规则（如默认兜底规则）之后，永远不会被触发：
          {{ shadowedRules.map((rule) => rule.name).join('、') }}。
          请提高这些规则的优先级，或降低兜底规则的优先级。
        </n-alert>
        <div class="dispatch-rules-hint">
          下表按实际匹配顺序排列：从上到下依次判断，<strong>命中第一条后即执行并停止</strong>。
        </div>
        <n-data-table
          :columns="columns"
          :data="orderedRules"
          :bordered="false"
          :single-line="false"
          :row-class-name="(row: any) => (row._shadowed ? 'rule-row-shadowed' : '')"
        />
      </n-card>
      <!-- 编辑规则对话框 -->
      <n-modal
        v-model:show="showEditModal"
        preset="dialog"
        :title="isCreate ? '创建规则' : '编辑规则'"
        style="width: 1200px"
      >
        <n-alert
          v-if="draftReachability?.unreachable"
          type="warning"
          :bordered="false"
          class="draft-reachability-alert"
        >
          这条规则排在无条件规则
          {{ draftReachability.shadowed_by_rule_id }} 之后（<template
            v-if="draftReachability.order !== null"
            >匹配顺序第 {{ draftReachability.order }} 位</template
          ><template v-else>已禁用，不参与匹配</template>），保存后永远不会被触发。请提高它的优先级。
        </n-alert>
        <n-alert
          v-else-if="draftShadowedRuleNames.length > 0"
          type="warning"
          :bordered="false"
          class="draft-reachability-alert"
        >
          这条规则是无条件规则，保存后会让排在它之后的
          {{ draftShadowedRuleNames.length }} 条规则永远不被触发：
          {{ draftShadowedRuleNames.join('、') }}。
        </n-alert>
        <div class="rule-edit-container">
          <!-- 基本信息 -->
          <div class="rule-basic-form">
            <n-form label-placement="left" label-width="80" :rules="validationRules" ref="formRef">
              <n-form-item label="名称" required feedback="用于区分不同的规则，必须保持唯一">
                <n-input v-model:value="currentRule.name" placeholder="请输入名称" />
              </n-form-item>
              <n-form-item label="描述" feedback="用于描述规则的用途，方便你理解">
                <n-input
                  v-model:value="currentRule.description"
                  type="textarea"
                  placeholder="请输入描述"
                />
              </n-form-item>
              <n-form-item label="工作流" required feedback="指定触发规则的工作流">
                <n-select
                  v-model:value="currentRule.workflow_id"
                  :options="
                    workflows.map((workflow) => ({
                      label:
                        workflow.name + ' (' + workflow.group_id + ':' + workflow.workflow_id + ')',
                      value: `${workflow.group_id}:${workflow.workflow_id}`
                    }))
                  "
                  placeholder="请选择工作流"
                />
              </n-form-item>
              <n-form-item label="优先级" required feedback="数值越大优先级越高，会优先被判断">
                <n-input-number v-model:value="currentRule.priority" :min="0" :max="100" />
              </n-form-item>
              <n-form-item label="启用状态">
                <n-switch v-model:value="currentRule.enabled" />
              </n-form-item>
            </n-form>
          </div>

          <!-- 触发条件 -->
          <div class="rule-config-form">
            <n-divider vertical />
            <div class="config-form-container">
              <h3 class="config-title">触发条件</h3>
              <n-scrollbar style="max-height: 400px">
                <div class="rule-groups">
                  <div class="rule-group-header">
                    <span class="rule-group-label">当</span>
                  </div>

                  <div
                    v-for="(group, groupIndex) in currentRule.rule_groups"
                    :key="groupIndex"
                    class="rule-group"
                  >
                    <div class="rule-list">
                      <template v-for="(rule, ruleIndex) in group.rules" :key="ruleIndex">
                        <div class="rule-item">
                          <n-select
                            v-model:value="rule.type"
                            :options="ruleTypeOptions"
                            @update:value="
                              (type) => handleRuleTypeChange(type, groupIndex, ruleIndex)
                            "
                            class="rule-type-select"
                            placeholder="请选择规则类型"
                          />
                          <n-button
                            circle
                            tertiary
                            type="info"
                            @click="
                              () => {
                                selectedRuleGroupIndex = groupIndex
                                selectedRuleIndex = ruleIndex
                                selectedRuleType = rule.type
                                loadConfigSchema(rule.type)
                                showRuleConfigModal = true
                              }
                            "
                            :disabled="!rule.type"
                          >
                            <template #icon>
                              <n-icon>
                                <PencilOutline />
                              </n-icon>
                            </template>
                          </n-button>
                          <n-button
                            circle
                            tertiary
                            type="error"
                            @click="removeRule(groupIndex, ruleIndex)"
                          >
                            <template #icon>
                              <n-icon>
                                <Remove />
                              </n-icon>
                            </template>
                          </n-button>
                        </div>
                        <span class="operator">或</span>
                      </template>
                      <n-button dashed class="add-rule-btn" @click="addRule(groupIndex)">
                        <template #icon>
                          <n-icon>
                            <Add />
                          </n-icon>
                        </template>
                        添加条件
                      </n-button>
                    </div>

                    <div class="group-operator">且</div>
                  </div>

                  <n-button
                    dashed
                    block
                    class="add-group-btn"
                    @click="addRuleGroup"
                    :disabled="
                      currentRule.rule_groups[currentRule.rule_groups.length - 1].rules.length === 0
                    "
                  >
                    <template #icon>
                      <n-icon>
                        <Add />
                      </n-icon>
                    </template>
                    添加条件组
                  </n-button>
                </div>
              </n-scrollbar>
            </div>
          </div>
        </div>

        <!-- 规则配置对话框 -->
        <n-modal
          v-model:show="showRuleConfigModal"
          preset="dialog"
          :title="'配置' + getRuleTypeLabel(selectedRuleType) + '规则'"
          style="width: 600px"
        >
          <dynamic-config-form
            v-if="configSchema && selectedRuleGroupIndex >= 0"
            :model-value="
              currentRule.rule_groups[selectedRuleGroupIndex].rules[selectedRuleIndex]?.config || {}
            "
            :schema="configSchema"
            @update:model-value="confirmRuleConfig"
          />
          <template #action>
            <n-button type="primary" @click="closeRuleConfigModal"> 确定 </n-button>
          </template>
        </n-modal>

        <template #action>
          <n-button secondary @click="openPreview(currentRule)">试运行当前草稿</n-button>
          <n-button type="primary" @click="saveRule(isCreate)"> 确定 </n-button>
        </template>
      </n-modal>

      <n-modal
        v-model:show="showPreviewModal"
        preset="dialog"
        title="试运行触发规则"
        style="width: min(760px, calc(100vw - 32px))"
      >
        <p class="preview-description">
          仅在内存中按真实优先级判断；不会执行工作流、发送消息、保存规则或连接 IM。
        </p>
        <n-form label-placement="left" label-width="88">
          <n-form-item label="示例消息">
            <n-input v-model:value="previewInput.content" type="textarea" :rows="3" placeholder="输入要模拟的消息" />
          </n-form-item>
          <div class="preview-form-row">
            <n-form-item label="聊天类型">
              <n-select
                v-model:value="previewInput.chat_type"
                :options="[
                  { label: '私聊', value: '私聊' },
                  { label: '群聊', value: '群聊' }
                ]"
              />
            </n-form-item>
            <n-form-item label="发送者 ID">
              <n-input v-model:value="previewInput.sender_id" />
            </n-form-item>
          </div>
          <div class="preview-form-row">
            <n-form-item v-if="previewInput.chat_type === '群聊'" label="群号">
              <n-input v-model:value="previewInput.group_id" placeholder="未填时使用试运行群" />
            </n-form-item>
            <n-form-item label="机器人被 @">
              <n-checkbox v-model:checked="previewInput.mentioned">模拟被机器人提及</n-checkbox>
            </n-form-item>
          </div>
        </n-form>
        <n-alert v-if="previewResult" :type="previewResult.selected_rule_id ? 'success' : 'info'" :bordered="false">
          <template v-if="previewResult.selected_rule_id">
            将执行 {{ previewResult.selected_rule_id }} → {{ previewResult.selected_workflow_id }}。
          </template>
          <template v-else>没有可确定的命中规则；请检查“无法确定”项或补充消息条件。</template>
        </n-alert>
        <div v-if="previewResult" class="preview-results" aria-live="polite">
          <div v-for="result in previewResult.rules" :key="result.rule_id" class="preview-rule-row">
            <n-tag size="small" :type="getDispatchPreviewDecisionType(result.decision)">
              {{ getDispatchPreviewDecisionLabel(result.decision) }}
            </n-tag>
            <div class="preview-rule-main">
              <strong>{{ result.name }}</strong>
              <!-- 已禁用规则没有匹配次序，写成「未参与匹配」而不是「第 null 位」 -->
              <span v-if="result.order === null">未参与匹配 · {{ result.rule_id }} · {{ result.workflow_id }}</span>
              <span v-else>第 {{ result.order }} 位 · {{ result.rule_id }} · {{ result.workflow_id }}</span>
              <small v-if="result.unreachable">
                排在无条件规则 {{ result.shadowed_by_rule_id }} 之后，对任何消息都不会被判断到。
              </small>
              <small v-if="previewReason(result)">{{ previewReason(result) }}</small>
            </div>
          </div>
        </div>
        <template #action>
          <n-button :loading="previewLoading" type="primary" @click="runPreview">开始试运行</n-button>
        </template>
      </n-modal>
    </n-space>
  </div>
</template>

<style scoped>
.dispatch-rules {
  padding: 16px;
}

.dispatch-rules-card {
  animation: fade-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.dispatch-rules-description {
  margin-bottom: 16px;
}

/* 遮蔽规则的告警条 */
.dispatch-rules-alert {
  margin-bottom: 12px;
}

/* 编辑草稿的可达性预判提示 */
.draft-reachability-alert {
  margin-bottom: 12px;
}

/* 匹配顺序说明 */
.dispatch-rules-hint {
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--text-color-secondary);
}

/* 永远不会被触发的规则整行弱化，与告警条呼应 */
:deep(.rule-row-shadowed) td {
  opacity: 0.55;
  background-color: rgba(var(--warning-color-rgb), 0.06);
}

.rule-edit-container {
  display: flex;
  min-height: 400px;
}

.rule-basic-form {
  flex: 0 0 360px;
  padding-right: 16px;
}

.rule-config-form {
  flex: 1;
  display: flex;
  min-width: 0;
}

.config-form-container {
  flex: 1;
  padding-left: 16px;
}

.config-title {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 500;
  color: var(--n-text-color);
}

.rule-groups {
  margin-bottom: 16px;
}

.rule-group-header {
  margin-bottom: 16px;
}

.rule-group-label {
  font-size: 16px;
  font-weight: 500;
  color: var(--n-text-color);
}

.rule-group {
  background: var(--n-card-color);
  border-radius: var(--radius-sm);
}

.rule-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.rule-item {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--n-color-modal);
  padding: 4px;
  /* 条件行嵌在 .rule-group（sm 档）内部，按嵌套原则降到 xs */
  border-radius: var(--radius-xs);
}

.rule-type-select {
  width: 200px;
}

.operator {
  color: var(--n-text-color-3);
  font-weight: 500;
  padding: 0 8px;
}

.group-operator {
  margin-top: 16px;
  color: var(--n-text-color-3);
  font-weight: 500;
}

.add-group-btn {
  margin-top: 16px;
}

:deep(.n-form-item .n-form-item-label) {
  font-weight: 500;
}

:deep(.n-input-number) {
  width: 100%;
}

.preview-description {
  margin: 0 0 16px;
  color: var(--n-text-color-3);
  line-height: 1.6;
}

.preview-form-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.preview-results {
  max-height: 300px;
  margin-top: 16px;
  overflow: auto;
  border-top: 1px solid var(--n-border-color);
}

.preview-rule-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 4px;
  border-bottom: 1px solid var(--n-border-color);
}

.preview-rule-main {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.preview-rule-main span,
.preview-rule-main small {
  color: var(--n-text-color-3);
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .preview-form-row {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
