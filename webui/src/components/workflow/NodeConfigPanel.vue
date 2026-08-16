<script setup lang="ts">
import { ref, computed, watch, defineAsyncComponent, h, onBeforeUnmount } from 'vue'
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NSpace,
  NIcon,
  NSelect,
  NSwitch,
  NInputNumber,
  NEmpty,
  NTag,
  NTooltip,
  NCollapse,
  NCollapseItem,
  NScrollbar,
  NSpin,
  NText
} from 'naive-ui'
import {
  CloseOutline,
  AddOutline,
  ArrowForwardOutline,
  RemoveOutline,
  SettingsOutline,
  ExpandOutline,
  CodeOutline
} from '@vicons/ionicons5'
import { useVueFlow } from '@vue-flow/core'
import type { Node, Edge } from '@vue-flow/core'
import type { BlockType } from '@/api/block'
import { getTypeColor } from '@/utils/node-colors'
import { useThemeStore } from '@/stores/theme'
import { getVisibleModelSlotValue } from './workflow-model-options'
import type { MonacoLanguageClient } from 'monaco-languageclient'

// Monaco 编辑器连同 vscode 服务层约 9MB，静态导入会被打进工作流编辑器的首屏
// chunk，导致打开画布时长时间白屏。改为异步组件后，只有选中代码节点、真正需要
// 编辑代码时才下载这部分资源；下载期间用一个占位骨架保持布局稳定。
// 占位组件是独立组件，拿不到本文件的 scoped 属性，因此样式写成内联。
const Editor = defineAsyncComponent({
  loader: () => import('@/editor/Editor.vue'),
  loadingComponent: {
    name: 'EditorLoading',
    render: () =>
      h(
        'div',
        {
          style: {
            flex: '1',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '10px',
            minHeight: '200px',
            color: 'var(--text-color-secondary)'
          }
        },
        [h(NSpin, { size: 'small' }), h('span', { style: { fontSize: '13px' } }, '正在加载代码编辑器...')]
      )
  },
  delay: 0
})

const props = defineProps<{
  selectedNode: Node | null
  blockTypes: BlockType[]
  typeCompatibility: Record<string, Record<string, boolean>>
}>()

const emit = defineEmits<{
  close: []
  /**
   * Vue Flow applies updateNode synchronously. Tell the canvas before that
   * happens so it can capture the pre-edit state for undo/redo.
   */
  'before-node-mutation': []
}>()

const {
  updateNode,
  findNode,
  getConnectedEdges,
  addSelectedNodes,
  nodesSelectionActive,
  removeSelectedNodes,
  getSelectedNodes,
  setCenter,
  getViewport
} = useVueFlow()

const isCodeNode = computed(() => {
  if (!props.selectedNode) return false
  return props.selectedNode.type === 'code'
})

const themeStore = useThemeStore()

const formValue = ref<any>({})
const lspClientRef = ref<MonacoLanguageClient | null>(null)

watch(
  () => props.selectedNode,
  (node) => {
    if (node) {
      formValue.value = {
        id: node.id,
        ...node.data.config
      }
      if (isCodeNode.value) {
        if (node.data.config?.code) {
          formValue.value.code = node.data.config.code
        } else {
          formValue.value.code = `
# 在这里编写代码
# 程序将以 execute(...) 函数作为入口
from typing import Dict, Any

def execute(${
            props.selectedNode?.data.inputs
              ?.map((input: { name: string; type: string }) => `${input.name}: ${input.type}`)
              .join(', ') || ''
          }) -> Dict[str, Any]:
    # ... 处理数据 ...
    # 设置输出
    result = {
        ${
          props.selectedNode?.data.outputs
            ?.map(
              (output: { name: string; type: string }) =>
                `"${output.name}": None # 请修改为实际结果`
            )
            .join(',\n        ') || ''
        }
    }

    return result`.trim()
        }
      }
    } else {
      formValue.value = {}
    }
  },
  { immediate: true, deep: true }
)

/** 只有这几个字段会被面板改动；blockType 等只读字段不必参与比较 */
const MUTABLE_NODE_DATA_KEYS = ['config', 'inputs', 'outputs'] as const

/**
 * 变更检测用的轻量指纹。
 *
 * 原先直接 `JSON.stringify(node.data)` 两次：node.data 里嵌着完整的
 * blockType（含全部端口与配置项定义），代码节点还带着整个代码缓冲区，
 * 每敲一个字符就要序列化两遍。这里只序列化真正可变的三个字段。
 */
const getNodeDataFingerprint = (data: any) => {
  if (!data) return ''
  const picked: Record<string, unknown> = {}
  for (const key of MUTABLE_NODE_DATA_KEYS) {
    picked[key] = data[key]
  }
  return JSON.stringify(picked)
}

/**
 * Keep undo history meaningful: a form close or an editor event that leaves
 * the serializable node data unchanged must not create a no-op history entry.
 */
const updateSelectedNodeData = (updatedData: any) => {
  const node = props.selectedNode
  if (!node || getNodeDataFingerprint(node.data) === getNodeDataFingerprint(updatedData)) return

  emit('before-node-mutation')
  updateNode(node.id, { data: updatedData })
}

const applyNodeConfig = () => {
  if (!props.selectedNode) return

  const updatedData = {
    ...props.selectedNode.data,
    config: { ...props.selectedNode.data.config }
  }

  if (props.selectedNode.data.blockType.configs && !isCodeNode.value) {
    props.selectedNode.data.blockType.configs.forEach((config: { name: string | number }) => {
      updatedData.config[config.name] = formValue.value[config.name]
    })
  }

  if (isCodeNode.value) {
    updatedData.config.code = formValue.value.code
  }

  updateSelectedNodeData(updatedData)
}

const closeNodeConfig = () => {
  // 关闭前先把 Monaco 的防抖尾巴落盘，否则最后几个字符会丢
  flushPendingCodeConfig()
  applyNodeConfig()
  emit('close')
}

/**
 * Monaco 输入的防抖写回。
 *
 * `@update:modelValue` 在代码编辑器里是逐字符触发的，直接调 applyNodeConfig
 * 意味着每敲一个键就要遍历配置项、拷贝一份 data、再做一次指纹比较，
 * 并同步推动画布的历史与 emit 链路。代码是长文本，这条路径最贵。
 * 这里合并 300ms 内的连续输入。
 *
 * 待写回的内容在排定定时器时就连节点 id 一起记下，而不是等触发时再读
 * props.selectedNode——否则用户在 300ms 内切换到别的节点，这次编辑会被
 * 写到错误的节点上或者直接丢失。
 */
const CODE_APPLY_DEBOUNCE = 300
let codeApplyTimer: number | null = null
let pendingCodeEdit: { nodeId: string; code: string } | null = null

const commitPendingCodeEdit = () => {
  const pending = pendingCodeEdit
  pendingCodeEdit = null
  if (!pending) return

  const node = findNode(pending.nodeId)
  if (!node) return

  const updatedData = {
    ...node.data,
    config: { ...node.data.config, code: pending.code }
  }
  if (getNodeDataFingerprint(node.data) === getNodeDataFingerprint(updatedData)) return
  emit('before-node-mutation')
  updateNode(node.id, { data: updatedData })
}

const flushPendingCodeConfig = () => {
  if (codeApplyTimer !== null) {
    clearTimeout(codeApplyTimer)
    codeApplyTimer = null
  }
  commitPendingCodeEdit()
}

const applyCodeConfigDebounced = () => {
  if (!props.selectedNode) return
  pendingCodeEdit = { nodeId: props.selectedNode.id, code: formValue.value.code }
  if (codeApplyTimer !== null) {
    clearTimeout(codeApplyTimer)
  }
  codeApplyTimer = window.setTimeout(() => {
    codeApplyTimer = null
    commitPendingCodeEdit()
  }, CODE_APPLY_DEBOUNCE)
}

onBeforeUnmount(() => {
  flushPendingCodeConfig()
})

// 切换到别的节点前，先把上一个节点尚未落盘的代码写回，避免编辑内容丢失。
// commitPendingCodeEdit 用记下的 nodeId 定位节点，所以此时 props 已经换掉也无妨。
watch(
  () => props.selectedNode?.id,
  () => {
    flushPendingCodeConfig()
  }
)

const inputConnections = computed(() => {
  if (!props.selectedNode) return []
  const edges = getConnectedEdges([props.selectedNode])
  return edges
    .filter((edge) => edge.target === props.selectedNode?.id)
    .map((edge) => {
      const sourceNode = findNode(edge.source)
      const sourceHandle =
        sourceNode?.data.blockType.outputs.find(
          (output: { name: string }) => output.name === edge.sourceHandle
        ) ||
        sourceNode?.data.outputs?.find(
          (output: { name: string }) => output.name === edge.sourceHandle
        )
      return {
        ...edge,
        handleLabel: sourceHandle?.label || edge.sourceHandle,
        label: sourceNode?.data?.label || edge.source
      }
    })
})

const outputConnections = computed(() => {
  if (!props.selectedNode) return []
  const edges = getConnectedEdges([props.selectedNode])
  return edges
    .filter((edge) => edge.source === props.selectedNode?.id)
    .map((edge) => {
      const targetNode = findNode(edge.target)
      const targetHandle =
        targetNode?.data.blockType.inputs.find(
          (input: { name: string }) => input.name === edge.targetHandle
        ) ||
        targetNode?.data.inputs?.find((input: { name: string }) => input.name === edge.targetHandle)
      return {
        ...edge,
        handleLabel: targetHandle?.label || edge.targetHandle,
        label: targetNode?.data?.label || edge.target
      }
    })
})

function getInputConnectionsByHandle(handleName: string) {
  if (!props.selectedNode || !inputConnections.value) return []
  return inputConnections.value.filter((conn) => conn.targetHandle === handleName)
}

function getOutputConnectionsByHandle(handleName: string) {
  if (!props.selectedNode || !outputConnections.value) return []
  return outputConnections.value.filter((conn) => conn.sourceHandle === handleName)
}

const navigateToNode = (nodeId: string) => {
  const node = findNode(nodeId)
  removeSelectedNodes(getSelectedNodes.value)
  if (node) {
    addSelectedNodes([node])
    nodesSelectionActive.value = true
    const viewport = getViewport()
    const centerX = node.position.x
    const centerY = node.position.y
    setCenter(centerX, centerY, {
      zoom: viewport.zoom
    })
  }
}

const inputEditMode = ref(false)
const outputEditMode = ref(false)
const newInputData = ref({ name: '', label: '', type: 'str', required: false })
const newOutputData = ref({ name: '', label: '', type: 'str' })

const addInputPort = () => {
  if (props.selectedNode && isCodeNode.value) {
    const updatedData = {
      ...props.selectedNode.data,
      inputs: [
        ...(props.selectedNode.data.inputs || []),
        {
          name: newInputData.value.name,
          label: newInputData.value.label || newInputData.value.name,
          type: newInputData.value.type,
          required: newInputData.value.required
        }
      ],
      config: {
        ...(props.selectedNode.data.config || {})
      }
    }
    updatedData.config.inputs = updatedData.inputs
    updateSelectedNodeData(updatedData)
    inputEditMode.value = false
    newInputData.value = { name: '', label: '', type: 'str', required: false }
    updateLspMandatoryFunction()
  }
}

const removeInputPort = (name: string) => {
  if (props.selectedNode && isCodeNode.value) {
    const updatedData = {
      ...props.selectedNode.data,
      inputs: props.selectedNode.data.inputs?.filter((input: any) => input.name !== name)
    }
    updatedData.config.inputs = updatedData.inputs
    updateSelectedNodeData(updatedData)
    updateLspMandatoryFunction()
  }
}

const addOutputPort = () => {
  if (props.selectedNode && isCodeNode.value) {
    const updatedData = {
      ...props.selectedNode.data,
      outputs: [
        ...(props.selectedNode.data.outputs || []),
        {
          name: newOutputData.value.name,
          label: newOutputData.value.label || newOutputData.value.name,
          type: newOutputData.value.type
        }
      ]
    }
    updatedData.config.outputs = updatedData.outputs
    updateSelectedNodeData(updatedData)
    outputEditMode.value = false
    newOutputData.value = { name: '', label: '', type: 'str' }
  }
}

const removeOutputPort = (name: string) => {
  if (props.selectedNode && isCodeNode.value) {
    const updatedData = {
      ...props.selectedNode.data,
      outputs: props.selectedNode.data.outputs?.filter((output: any) => output.name !== name)
    }
    updatedData.config.outputs = updatedData.outputs
    updateSelectedNodeData(updatedData)
  }
}

const codeEditorRef = ref<any>(null)
const isEditorFullscreen = ref(false)

const handleEditorMount = ({
  editor,
  lspClient
}: {
  editor: any
  lspClient: MonacoLanguageClient
}) => {
  codeEditorRef.value = { editor }
  lspClientRef.value = lspClient
  updateLspMandatoryFunction()
}

const formatCode = () => {
  if (codeEditorRef.value && codeEditorRef.value.editor) {
    codeEditorRef.value.editor.getAction('editor.action.formatDocument')?.run()
  }
}

const toggleFullscreenEditor = () => {
  isEditorFullscreen.value = !isEditorFullscreen.value

  if (isEditorFullscreen.value && codeEditorRef.value && codeEditorRef.value.editor) {
    setTimeout(() => {
      codeEditorRef.value.editor.layout()
    }, 1)
  }
}

const updateLspMandatoryFunction = () => {
  if (!lspClientRef.value || !props.selectedNode || !isCodeNode.value) return

  const currentCode = formValue.value.code || ''
  const executeRegex = /def\s+execute\s*\([^)]*\)\s*->\s*Dict\[str,\s*Any\]:/
  const newSignature = `def execute(${
    props.selectedNode.data.inputs
      ?.map((input: { name: string; type: string }) => `${input.name}: ${input.type}`)
      .join(', ') || ''
  }) -> Dict[str, Any]:`

  if (executeRegex.test(currentCode)) {
    formValue.value.code = currentCode.replace(executeRegex, newSignature)
  } else if (!currentCode.includes('def execute')) {
    formValue.value.code = `
# 在这里编写代码
# 程序将以 execute(...) 函数作为入口
from typing import Dict, Any

${newSignature}
    # ... 处理数据 ...
    # 设置输出
    result = {
        ${
          props.selectedNode?.data.outputs
            ?.map(
              (output: { name: string; type: string }) =>
                `"${output.name}": None # 请修改为实际结果`
            )
            .join(',\n        ') || ''
        }
    }

    return result`.trim()
  }

  lspClientRef.value.sendNotification('workspace/didChangeConfiguration', {
    settings: {
      mandatoryFunction: {
        name: 'execute',
        params:
          props.selectedNode.data.inputs?.map((input: { name: string; type: string }) => ({
            name: input.name,
            type_hint: input.type
          })) || [],
        return_type: 'Dict[str, Any]'
      }
    }
  })
}

const isListType = (type: string) => {
  return type.startsWith('List[') && type.endsWith(']')
}

const getSelectOptions = (config: any) => {
  const options = (config.options || []).map((opt: any) => {
    if (typeof opt === 'object' && opt.label && opt.value !== undefined) {
      return { label: opt.label, value: opt.value, description: opt.description }
    }
    return { label: String(opt), value: opt }
  })

  if (!isListType(config.type) && !config.required) {
    options.unshift({ label: '不指定', value: null })
  }
  return options
}

const updateSelectConfigValue = (configName: string, value: unknown) => {
  formValue.value[configName] = value
  applyNodeConfig()
}

function getTypeColorStyle(type: string) {
  const typeColor = getTypeColor(type)
  return {
    borderLeftColor: typeColor.color_on,
    borderLeftWidth: '3px',
    borderLeftStyle: 'solid' as const
  }
}

const getTypeOptions = () => {
  const typeCompatibility = props.typeCompatibility || {}
  const typeOptions: { label: string; value: string }[] = []
  const predefinedTypes = [
    { label: '字符串(str)', value: 'str' },
    { label: '整数(int)', value: 'int' },
    { label: '浮点数(float)', value: 'float' },
    { label: '布尔值(bool)', value: 'bool' },
    { label: '任意类型(Any)', value: 'Any' }
  ]
  predefinedTypes.forEach((type) => typeOptions.push(type))

  const predefinedValues = new Set(predefinedTypes.map((t) => t.value))
  Object.keys(typeCompatibility)
    .filter((key) => !predefinedValues.has(key))
    .forEach((key) => {
      typeOptions.push({ label: key, value: key })
    })
  return typeOptions
}
</script>

<template>
  <NCard
    title="节点配置"
    size="small"
    class="node-config-panel"
    :bordered="false"
    content-style="height: 100%;"
  >
    <template #header>
      <div class="panel-header">
        <NIcon size="18" class="header-icon">
          <SettingsOutline />
        </NIcon>
        <span class="header-title">节点配置</span>
      </div>
    </template>

    <template #header-extra>
      <div class="header-actions">
        <NButton
          quaternary
          circle
          size="small"
          title="关闭节点配置"
          aria-label="关闭节点配置"
          @click="closeNodeConfig"
          class="close-button"
        >
          <template #icon>
            <NIcon>
              <CloseOutline />
            </NIcon>
          </template>
        </NButton>
      </div>
    </template>
    <NScrollbar style="height: 100%">
      <div v-if="selectedNode" class="config-content">
        <!-- 基本信息 -->
        <div class="node-description">
          <NText class="node-description-value">{{
            selectedNode.data.blockType.description
          }}</NText>
        </div>

        <div class="node-id-section">
          <div class="node-id-label">节点 ID:</div>
          <NText code class="node-id-value">{{ formValue.id }}</NText>
        </div>

        <NCollapse arrow-placement="right" :default-expanded-names="[1, 2, 4]">
          <!-- 输入连接折叠面板 -->
          <NCollapseItem title="输入连接" name="1">
            <!-- 统一的输入端口列表 -->
            <div v-if="selectedNode">
              <div
                v-for="input in selectedNode.type === 'code'
                  ? selectedNode.data.inputs
                  : selectedNode.data.blockType.inputs"
                :key="input.name"
                class="connection-group"
              >
                <div class="connection-point-header" :style="getTypeColorStyle(input.type)">
                  <span class="connection-point-name">{{ input.label }}</span>
                  <span class="connection-point-name" v-if="isCodeNode || !input.label">{{
                    input.name
                  }}</span>
                  <span class="connection-type-badge">{{ input.type }}</span>
                  <span v-if="input.required" class="required-badge">必需</span>
                  <span v-else class="optional-badge">可选</span>

                  <!-- 代码节点显示删除按钮 -->
                  <NButton
                    v-if="isCodeNode"
                    quaternary
                    circle
                    size="small"
                    title="删除输入端口"
                    aria-label="删除输入端口"
                    @click="removeInputPort(input.name)"
                    class="remove-port-button"
                  >
                    <template #icon>
                      <NIcon>
                        <RemoveOutline />
                      </NIcon>
                    </template>
                  </NButton>
                </div>

                <div
                  v-if="getInputConnectionsByHandle(input.name).length > 0"
                  class="connection-list"
                >
                  <div
                    v-for="conn in getInputConnectionsByHandle(input.name)"
                    :key="conn.id"
                    class="connection-item"
                  >
                    <div class="connection-info">
                      <NIcon class="connection-icon" size="16">
                        <AddOutline />
                      </NIcon>
                      <div class="connection-details">
                        <div class="connection-source">
                          <span class="connection-label">{{ conn.label }}</span>
                          <span class="connection-handle">{{ conn.handleLabel || '-' }}</span>
                        </div>
                      </div>
                    </div>
                    <NButton
                      quaternary
                      circle
                      size="small"
                      @click="navigateToNode(conn.source)"
                      class="navigate-button"
                    >
                      <template #icon>
                        <NIcon>
                          <ArrowForwardOutline />
                        </NIcon>
                      </template>
                    </NButton>
                  </div>
                </div>
                <div v-else class="no-connections">
                  <NIcon class="empty-icon" size="16">
                    <RemoveOutline />
                  </NIcon>
                  <span>未连接</span>
                </div>
              </div>
              <div class="port-management">
                <div v-if="isCodeNode && !inputEditMode">
                  <NButton
                    type="default"
                    size="small"
                    @click="inputEditMode = !inputEditMode"
                    class="add-port-button"
                  >
                    <template #icon>
                      <NIcon>
                        <AddOutline />
                      </NIcon>
                    </template>
                    {{ '添加输入' }}
                  </NButton>
                </div>

                <!-- 内联输入端口添加表单 -->
                <div v-if="isCodeNode && inputEditMode" class="inline-form">
                  <div class="inline-form-row">
                    <NInput
                      v-model:value="newInputData.name"
                      placeholder="输入参数名（英文）"
                      size="small"
                      class="inline-form-input"
                    />
                    <NInput
                      v-model:value="newInputData.label"
                      placeholder="显示名称（可选）"
                      size="small"
                      class="inline-form-input"
                    />
                  </div>
                  <div class="inline-form-row">
                    <NSelect
                      v-model:value="newInputData.type"
                      size="small"
                      class="inline-form-input"
                      :options="getTypeOptions()"
                    />
                    <div class="required-switch-container">
                      <span class="switch-label">必需</span>
                      <NSwitch v-model:value="newInputData.required" size="small" />
                    </div>
                  </div>
                  <NSpace>
                    <NButton
                      type="primary"
                      size="small"
                      @click="addInputPort"
                      :disabled="!newInputData.name"
                      class="inline-form-button"
                    >
                      添加
                    </NButton>
                    <NButton size="small" @click="inputEditMode = false" class="inline-form-button">
                      取消
                    </NButton>
                  </NSpace>
                </div>
              </div>
              <NEmpty
                v-if="
                  !isCodeNode &&
                  (!selectedNode.data.blockType.inputs ||
                    selectedNode.data.blockType.inputs.length === 0)
                "
                description="无输入端口"
                class="empty-ports"
              >
                <template #icon>
                  <NIcon size="48" class="empty-icon">
                    <RemoveOutline />
                  </NIcon>
                </template>
              </NEmpty>
            </div>
          </NCollapseItem>

          <!-- 输出连接折叠面板 -->
          <NCollapseItem title="输出连接" name="2">
            <div v-if="selectedNode">
              <div
                v-for="output in selectedNode.type === 'code'
                  ? selectedNode.data.outputs
                  : selectedNode.data.blockType.outputs"
                :key="output.name"
                class="connection-group"
              >
                <div class="connection-point-header" :style="getTypeColorStyle(output.type)">
                  <span class="connection-point-name">{{ output.label }}</span>
                  <span class="connection-point-name" v-if="isCodeNode || !output.label">{{
                    output.name
                  }}</span>
                  <span class="connection-type-badge">{{ output.type }}</span>

                  <!-- 代码节点显示删除按钮 -->
                  <NButton
                    v-if="isCodeNode"
                    quaternary
                    circle
                    size="small"
                    title="删除输出端口"
                    aria-label="删除输出端口"
                    @click="removeOutputPort(output.name)"
                    class="remove-port-button"
                  >
                    <template #icon>
                      <NIcon>
                        <RemoveOutline />
                      </NIcon>
                    </template>
                  </NButton>
                </div>

                <div
                  v-if="getOutputConnectionsByHandle(output.name).length > 0"
                  class="connection-list"
                >
                  <div
                    v-for="conn in getOutputConnectionsByHandle(output.name)"
                    :key="conn.id"
                    class="connection-item"
                  >
                    <div class="connection-info">
                      <NIcon class="connection-icon" size="16">
                        <AddOutline />
                      </NIcon>
                      <div class="connection-details">
                        <div class="connection-target">
                          <span class="connection-handle">{{ conn.handleLabel || '-' }}</span>
                          <span class="connection-label">{{ conn.label }}</span>
                        </div>
                      </div>
                    </div>
                    <NButton
                      quaternary
                      circle
                      size="small"
                      @click="navigateToNode(conn.target)"
                      class="navigate-button"
                    >
                      <template #icon>
                        <NIcon>
                          <ArrowForwardOutline />
                        </NIcon>
                      </template>
                    </NButton>
                  </div>
                </div>
                <div v-else class="no-connections">
                  <NIcon class="empty-icon" size="16">
                    <RemoveOutline />
                  </NIcon>
                  <span>未连接</span>
                </div>
              </div>

              <div class="port-management">
                <div v-if="isCodeNode && !outputEditMode">
                  <NButton
                    type="default"
                    size="small"
                    @click="outputEditMode = !outputEditMode"
                    class="add-port-button"
                  >
                    <template #icon>
                      <NIcon>
                        <AddOutline />
                      </NIcon>
                    </template>
                    {{ '添加输出' }}
                  </NButton>
                </div>

                <!-- 内联输出端口添加表单 -->
                <div v-if="isCodeNode && outputEditMode" class="inline-form">
                  <div class="inline-form-row">
                    <NInput
                      v-model:value="newOutputData.name"
                      placeholder="输出参数名（英文）"
                      size="small"
                      class="inline-form-input"
                    />
                    <NInput
                      v-model:value="newOutputData.label"
                      placeholder="显示名称（可选）"
                      size="small"
                      class="inline-form-input"
                    />
                  </div>
                  <div class="inline-form-row">
                    <NSelect
                      v-model:value="newOutputData.type"
                      size="small"
                      class="inline-form-input"
                      :options="getTypeOptions()"
                    />
                  </div>
                  <NSpace>
                    <NButton
                      type="primary"
                      size="small"
                      @click="addOutputPort"
                      :disabled="!newOutputData.name"
                      class="inline-form-button"
                    >
                      添加
                    </NButton>
                    <NButton
                      size="small"
                      @click="outputEditMode = false"
                      class="inline-form-button"
                    >
                      取消
                    </NButton>
                  </NSpace>
                </div>
              </div>

              <NEmpty
                v-if="
                  !isCodeNode &&
                  (!selectedNode.data.blockType.outputs ||
                    selectedNode.data.blockType.outputs.length === 0)
                "
                description="无输出端口"
                class="empty-ports"
              >
                <template #icon>
                  <NIcon size="48" class="empty-icon">
                    <RemoveOutline />
                  </NIcon>
                </template>
              </NEmpty>
            </div>
          </NCollapseItem>

          <!-- 普通节点的参数配置 -->
          <NCollapseItem
            v-if="
              selectedNode?.data?.blockType?.configs &&
              selectedNode.data.blockType.configs.length > 0 &&
              !isCodeNode
            "
            title="参数配置"
            name="3"
          >
            <div
              v-for="config in selectedNode.data.blockType.configs"
              :key="config.name"
              class="config-form-item"
            >
              <div class="config-label">{{ config.label || config.name }}</div>
              <div v-if="config.description" class="config-description">
                {{ config.description }}
              </div>

              <!-- 字符串类型配置 -->
              <NInput
                v-if="config.type === 'str' && !config.has_options"
                v-model:value="formValue[config.name]"
                :placeholder="`请输入${config.label || config.name}`"
                type="textarea"
                class="custom-input"
                @update:value="applyNodeConfig"
              />

              <!-- 单选类型配置 -->
              <NSelect
                v-else-if="config.has_options && !isListType(config.type)"
                :value="getVisibleModelSlotValue(
                  config.name,
                  formValue[config.name],
                  getSelectOptions(config)
                )"
                :options="getSelectOptions(config)"
                :placeholder="`请选择${config.label || config.name}`"
                class="custom-select"
                @update:value="(value) => updateSelectConfigValue(config.name, value)"
              />

              <!-- 多选类型配置 List[actual_type]-->
              <NSelect
                v-else-if="config.has_options && isListType(config.type)"
                v-model:value="formValue[config.name]"
                :options="getSelectOptions(config)"
                :placeholder="`请选择${config.label || config.name}`"
                multiple
                class="custom-select"
                @update:value="applyNodeConfig"
              />

              <!-- 布尔类型配置 -->
              <NSwitch
                v-else-if="config.type === 'bool'"
                v-model:value="formValue[config.name]"
                class="custom-switch"
                @update:value="applyNodeConfig"
              />

              <!-- 数字类型配置 -->
              <NInputNumber
                v-else-if="config.type === 'int' || config.type === 'float'"
                v-model:value="formValue[config.name]"
                :precision="config.type === 'float' ? 2 : 0"
                :placeholder="`请输入${config.label || config.name}`"
                class="custom-input-number"
                @update:value="applyNodeConfig"
              />
            </div>
          </NCollapseItem>

          <!-- 正常模式下的代码编辑器 -->
          <NCollapseItem v-if="isCodeNode && !isEditorFullscreen" title="代码编辑器" name="4">
            <div class="code-editor-section">
              <div class="code-editor-container">
                <div class="code-editor-header">
                  <NSpace>
                    <NButton
                      size="small"
                      @click="toggleFullscreenEditor"
                      class="editor-action-button"
                    >
                      <template #icon>
                        <NIcon>
                          <ExpandOutline />
                        </NIcon>
                      </template>
                      全屏编辑
                    </NButton>
                  </NSpace>
                </div>
                <Editor
                  v-model="formValue.code"
                  language="python"
                  :theme="themeStore.monacoTheme"
                  :options="{
                    minimap: { enabled: false },
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true
                  }"
                  ref="codeEditorRef"
                  @editorDidMount="handleEditorMount"
                  @update:modelValue="applyCodeConfigDebounced"
                />
              </div>
            </div>
          </NCollapseItem>
          <!-- 全屏编辑器模式 -->
          <Teleport to="body" v-if="isEditorFullscreen">
            <div class="fullscreen-editor-container">
              <div class="fullscreen-editor-header">
                <div class="fullscreen-editor-title">
                  <NIcon size="18">
                    <CodeOutline />
                  </NIcon>
                  <span>代码编辑器</span>
                </div>
                <NSpace>
                  <NButton
                    size="small"
                    @click="toggleFullscreenEditor"
                    class="editor-action-button"
                  >
                    <template #icon>
                      <NIcon>
                        <CloseOutline />
                      </NIcon>
                    </template>
                    退出全屏
                  </NButton>
                </NSpace>
              </div>
              <div class="fullscreen-editor-content">
                <Editor
                  v-model="formValue.code"
                  language="python"
                  :options="{
                    minimap: { enabled: true },
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    automaticLayout: true
                  }"
                  ref="codeEditorRef"
                  @editorDidMount="handleEditorMount"
                  @update:modelValue="applyCodeConfigDebounced"
                />
              </div>
            </div>
          </Teleport>
        </NCollapse>
      </div>

      <NEmpty v-else description="请选择一个节点进行配置" class="empty-config">
        <template #icon>
          <NIcon size="48" class="empty-icon">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="8" cy="18" r="4"></circle>
              <path d="M12 18h8"></path>
              <path d="M12 12h8"></path>
              <path d="M12 6h8"></path>
              <circle cx="4" cy="12" r="1"></circle>
              <circle cx="4" cy="6" r="1"></circle>
            </svg>
          </NIcon>
        </template>
      </NEmpty>
    </NScrollbar>
  </NCard>
</template>

<style scoped>
.node-config-panel {
  width: min(500px, 100vw);
  max-width: 100%;
  background-color: var(--panel-bg-color);
  backdrop-filter: blur(10px);
  /* 例外：该面板贴着画布右缘满高铺满，任何圆角都会露出画布底色，故保持直角 */
  border-radius: 0;
  transition: all 0.3s ease;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-icon {
  color: var(--primary-color);
}

.header-title {
  font-weight: 600;
  font-size: 16px;
  color: var(--text-color);
}

@media (max-width: 640px) {
  .node-config-panel {
    width: 100vw;
  }

  .header-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.close-button:hover {
  background-color: rgba(var(--primary-color-rgb), 0.08);
  color: var(--error-color);
}

.config-content {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding-right: 10px;
}

.node-id-section {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background-color: var(--node-muted-bg);
  border-radius: var(--radius-sm);
  margin-bottom: 8px;
}

.node-id-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
}

.node-id-value {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  color: var(--primary-color);
  background-color: rgba(var(--primary-color-rgb), 0.1);
  border-radius: var(--radius-xs);
}

.node-description {
  margin: 8px 8px;
}

.node-description-value {
  font-size: 13px;
  color: var(--text-color-secondary);
  line-height: 1.4;
}

.config-form-item {
  margin-bottom: 16px;
}

.config-form-item:last-child {
  margin-bottom: 0;
}

.config-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
  margin-bottom: 6px;
}

.config-description {
  font-size: 12px;
  color: var(--text-color-secondary);
  margin-bottom: 8px;
  line-height: 1.4;
}

.custom-input,
.custom-select,
.custom-input-number {
  border-radius: var(--radius-sm);
  transition: all 0.2s;
  width: 100%;
  margin-top: 4px;
}

.custom-input:hover,
.custom-select:hover,
.custom-input-number:hover {
  border-color: var(--primary-color);
}

.custom-switch {
  transition: all 0.2s;
  margin-top: 4px;
}

.cancel-button {
  border-radius: var(--radius-sm);
}

.apply-button {
  border-radius: var(--radius-sm);
  background-color: var(--primary-color);
  border-color: var(--primary-color);
}

.apply-button:hover {
  background-color: var(--primary-color-hover);
  border-color: var(--primary-color-hover);
}

.empty-config {
  padding: 20px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--text-color-tertiary);
  height: 100%;
}

.empty-icon {
  color: var(--text-color-tertiary);
}

.connection-group {
  margin-bottom: 12px;
  background-color: var(--card-bg-color);
  border-radius: var(--radius-sm);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  overflow: hidden;
}

.connection-group:last-child {
  margin-bottom: 0;
}

.connection-point-header {
  padding: 8px 12px;
  background-color: var(--node-muted-bg);
  display: flex;
  align-items: center;
  gap: 8px;
}

.connection-point-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
}

.connection-type-badge {
  font-size: 11px;
  color: var(--text-color-secondary);
  background-color: var(--code-bg-color);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
}

.required-badge {
  font-size: 11px;
  color: var(--error-color);
  background-color: rgba(239, 68, 68, 0.1);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  margin-left: auto;
}

.optional-badge {
  font-size: 11px;
  color: var(--text-color-secondary);
  background-color: rgba(107, 114, 128, 0.1);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  margin-left: auto;
}

.connection-list {
  padding: 4px 0;
}

.connection-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--divider-color);
}

.connection-item:last-child {
  border-bottom: none;
}

.connection-item:hover {
  background-color: var(--node-muted-bg);
}

.connection-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.connection-icon {
  color: var(--text-color-tertiary);
  flex-shrink: 0;
}

.connection-details {
  flex: 1;
  min-width: 0;
}

.connection-source,
.connection-target {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.connection-label {
  font-size: 13px;
  color: var(--text-color);
  font-weight: 500;
}

.connection-handle {
  font-size: 12px;
  color: var(--text-color-secondary);
  background-color: var(--code-bg-color);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
}

.navigate-button {
  color: var(--text-color-secondary);
  transition: all 0.2s;
}

.navigate-button:hover {
  color: var(--primary-color);
  background-color: rgba(var(--primary-color-rgb), 0.1);
}

.no-connections {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: var(--text-color-tertiary);
  font-size: 12px;
  font-style: italic;
}

.no-connection-points {
  font-size: 13px;
  color: var(--text-color-secondary);
  padding: 12px;
  text-align: center;
  background-color: var(--node-muted-bg);
  border-radius: var(--radius-sm);
}

/* 添加内联表单相关样式 */
.port-management {
  display: flex;
  justify-content: center;
  align-items: center;
  margin-bottom: 12px;
  padding: 8px 12px;
  background-color: var(--node-muted-bg);
  height: 140px;
}

.inline-form {
  background-color: var(--node-muted-bg);
  padding: 12px;
  border-radius: var(--radius-sm);
  margin-bottom: 16px;
}

.inline-form-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.inline-form-input {
  flex: 1;
}

.required-switch-container {
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-label {
  font-size: 13px;
  color: var(--text-color);
}

.inline-form-button {
  margin-top: 8px;
}

.port-list {
  margin-bottom: 16px;
}

.port-item {
  margin-bottom: 8px;
  background-color: var(--card-bg-color);
  border-radius: var(--radius-sm);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  overflow: hidden;
}

.port-info {
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.port-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
}

.port-details {
  display: flex;
  align-items: center;
  gap: 8px;
}

.port-type-tag {
  font-size: 11px;
  color: var(--text-color-secondary);
  background-color: var(--code-bg-color);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
}

.port-required-tag {
  font-size: 11px;
  color: var(--error-color);
  background-color: rgba(239, 68, 68, 0.1);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
}

.port-optional-tag {
  font-size: 11px;
  color: var(--text-color-secondary);
  background-color: rgba(107, 114, 128, 0.1);
  padding: 1px 6px;
  border-radius: var(--radius-pill);
}

.remove-port-button {
  color: var(--text-color-secondary);
  transition: all 0.2s;
}

.remove-port-button:hover {
  color: var(--error-color);
  background-color: rgba(255, 77, 79, 0.1);
}

.code-editor-container {
  width: 100%;
  height: 500px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  background-color: var(--node-muted-bg);
  display: flex;
  flex-direction: column;
}

.code-editor-header {
  display: flex;
  justify-content: flex-end;
  padding: 8px 12px;
  background-color: var(--code-bg-color);
  border-bottom: 1px solid var(--border-color);
}

.editor-action-button {
  background-color: var(--elevated-bg-color);
  border: 1px solid var(--border-color);
  transition: all 0.2s;
}

.editor-action-button:hover {
  background-color: var(--card-bg-color);
  border-color: var(--primary-color);
}

.section-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
  margin-bottom: 8px;
}

.params-form {
  margin-bottom: 16px;
}

.param-item {
  margin-bottom: 8px;
}

.param-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
  margin-right: 8px;
}

.param-input {
  width: 100%;
}

.empty-params {
  padding: 12px;
  text-align: center;
  color: var(--text-color-tertiary);
  font-size: 12px;
}

.execution-output {
  margin-bottom: 16px;
}

.result-container {
  background-color: var(--card-bg-color);
  padding: 12px;
  border-radius: var(--radius-sm);
  overflow: auto;
}

.result-content {
  white-space: pre-wrap;
}

.fullscreen-editor-container {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  right: 0;
  z-index: 9999;
  background-color: var(--card-bg-color);
  display: flex;
  flex-direction: column;
}

.fullscreen-editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background-color: var(--card-bg-color);
  border-bottom: 1px solid var(--border-color);
}

.fullscreen-editor-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-color);
  font-weight: 500;
}

.fullscreen-editor-content {
  flex: 1;
  height: calc(100vh - 56px);
  background-color: var(--node-muted-bg);
  border-top: 1px solid var(--border-color);
}
</style>
