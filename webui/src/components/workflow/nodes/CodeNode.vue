<script setup lang="ts">
import { computed, inject, type ComputedRef } from 'vue'
import { Handle, Position, type Connection } from '@vue-flow/core'
import { getTypeColor } from '@/utils/node-colors'
import { CodeOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { CODE_NODE_MAX_WIDTH, CODE_NODE_MIN_WIDTH } from '../useLayout'

const props = defineProps(['id', 'data', 'isValidConnection'])

/** 画布下发的问题汇总；未在画布内使用（如节点列表预览）时为空 */
type NodeIssue = { count: number; severity: 'error' | 'warning'; text: string }
const nodeIssues = inject<ComputedRef<Map<string, NodeIssue>> | null>('workflowNodeIssues', null)

/**
 * 本节点的问题角标。
 *
 * 与 CustomNode 同一套：脚本节点的 `code_node_without_ports` 警告此前只出现在
 * 工具栏的问题列表里，节点本身没有任何提示，于是一个零端口的脚本节点看起来
 * 就是「一个连不上线的坏框」。角标解决「哪个节点有问题」。
 */
const nodeIssue = computed<NodeIssue | null>(() => {
  if (!nodeIssues?.value || !props.id) return null
  return nodeIssues.value.get(props.id) || null
})

/**
 * 两侧端口都为空。
 *
 * 脚本节点的端口是按实例动态声明的（后端 `internal:code` 的类级 inputs/outputs
 * 为空，实例在 `__init__` 里按配置构建），所以刚拖进来的节点确实一个端口都没有。
 * 这是有意的边界而不是故障，但必须说出来，并给出下一步动作。
 */
const hasNoPorts = computed(
  () =>
    (props.data?.inputs?.length ?? 0) === 0 &&
    (props.data?.outputs?.length ?? 0) === 0
)

/**
 * 节点宽度上下限由 useLayout.ts 统一定义并在这里内联绑定。
 *
 * 与 CustomNode 同一处理：CSS 里写死 200/300px 而布局估算另有一套常量时，
 * 两边一旦不同步，dagre 就会按错误的宽度留空隙。CustomNode 已经收敛到常量，
 * 代码节点此前没跟上，是同一个缺陷的残留。
 */
const nodeWidthStyle = computed(() => ({
  minWidth: `${CODE_NODE_MIN_WIDTH}px`,
  maxWidth: `${CODE_NODE_MAX_WIDTH}px`
}))

// 获取短ID
const shortId = computed(() => {
  if (!props.id) return ''
  if (props.id.length > 8) {
    return props.id.slice(-6)
  }
  return props.id
})

// 获取输入端口颜色
const getInputColor = (type: string, required: boolean) => {
  return getTypeColor(type, required).color_on
}

// 获取输出端口颜色
const getOutputColor = (type: string) => {
  return getTypeColor(type).color_on
}

/** 端口标签被截断时靠原生 tooltip 补全信息：标签 + 类型 + 描述 */
const portTitle = (port: { label?: string; name: string; type?: string; description?: string }) => {
  const parts = [port.label || port.name]
  if (port.type) {
    parts.push(`(${port.type})`)
  }
  if (port.description && port.description !== port.label) {
    parts.push(`\n${port.description}`)
  }
  return parts.join(' ')
}

// 连接验证
//
// 「一个输入只允许一条边」这条规则**不在这里**判定：它原先在这里
// `return false` 了事，而 vue-flow 在 Handle 判定 invalid 时不会触发
// `onConnect`，于是这条拒绝永远静默——现象与类型不兼容一模一样（线弹回来），
// 用户无从判断该删掉已有的线还是该换端口。现在交给画布统一判定并给出文案。
const isValidConnection = (connection: Connection) => {
  if (props.isValidConnection) {
    return props.isValidConnection(connection)
  }
  return true
}

// 获取代码预览
const codePreview = computed(() => {
  const code = props.data.config?.code || ''
  if (!code) return '# 请在配置面板编写代码'
  const lines = code.split('\n')
  return lines.length > 5 ? lines.slice(0, 5).join('\n') + '\n# ...' : code
})
</script>

<template>
  <div class="code-node" :style="nodeWidthStyle">
    <!-- 问题角标：错误红、警告黄，鼠标悬停显示全部问题文字（与 CustomNode 同一套） -->
    <div
      v-if="nodeIssue"
      class="node-issue-badge"
      :class="nodeIssue.severity === 'error' ? 'node-issue-error' : 'node-issue-warning'"
      :title="nodeIssue.text"
    >
      {{ nodeIssue.count }}
    </div>
    <div class="code-node-header">
      <div class="header-content">
        <span class="node-label">{{ data.label }}</span>
        <span class="node-id" :title="id">#{{ shortId }}</span>
      </div>
    </div>

    <!--
      零端口空态。
      脚本节点的端口按实例动态声明，刚拖进来时两侧确实都是空的——这是有意的
      边界，不是故障。直接说出来并指向配置面板，比留一个连不上线的空框好得多。
    -->
    <div v-if="hasNoPorts" class="ports-empty-state">
      尚未定义端口，暂时无法连线；请在右侧配置面板添加输入或输出端口。
    </div>

    <!-- 端口双列布局 -->
    <div v-else class="ports-container">
      <!-- 左侧输入端口 -->
      <div class="port-column input-ports">
        <div v-for="input in data.inputs" :key="input.name" class="port-container">
          <Handle
            :id="input.name"
            type="target"
            :position="Position.Left"
            :style="{
              height: '16px',
              width: '6px',
              backgroundColor: getInputColor(input.type, input.required)
            }"
            :isValidConnection="(connection: Connection) => isValidConnection(connection)"
          />
          <div class="port-label" :title="portTitle(input)">{{ input.label || input.name }}</div>
        </div>
      </div>

      <!-- 右侧输出端口 -->
      <div class="port-column output-ports">
        <div v-for="output in data.outputs" :key="output.name" class="port-container output-port">
          <div class="port-label" :title="portTitle(output)">{{ output.label || output.name }}</div>
          <Handle
            :id="output.name"
            type="source"
            :position="Position.Right"
            :style="{ height: '16px', width: '6px', backgroundColor: getOutputColor(output.type) }"
            :is-valid-connection="(connection: Connection) => isValidConnection(connection)"
          />
        </div>
      </div>
    </div>

    <!-- 代码预览区 -->
    <div class="code-node-body">
      <div class="code-preview-header">
        <NIcon size="16" class="code-icon">
          <CodeOutline />
        </NIcon>
        <span class="code-label">代码</span>
      </div>
      <div class="code-preview">
        <pre class="code-preview-content">{{ codePreview }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 代码节点固定使用深色，与代码编辑器的视觉语言一致，两种主题下都保持该外观 */
/* 上述约定现改为由 token 驱动：底色走 --node-bg-*，代码区走 --code-*，
   深色主题下仍是深色终端观感，浅色/松林等色板下不再突兀地黑一块 */
.code-node {
  background: linear-gradient(
    to bottom,
    var(--node-bg-start, #f8f9fa),
    var(--node-bg-end, #ffffff)
  );
  border-radius: var(--radius-sm);
  box-shadow: var(--box-shadow-sm, var(--box-shadow, 0 3px 10px rgba(0, 0, 0, 0.15)));
  /* min-width / max-width 由 useLayout.ts 的常量内联注入，见 nodeWidthStyle；
     这里只保留回退值，防止内联样式因故缺失时节点塌成一条。
     两处数值必须与 CODE_NODE_MIN_WIDTH / CODE_NODE_MAX_WIDTH 一致，
     由 webui/tests/workflow-node-width-source.test.ts 校验。 */
  min-width: 200px;
  max-width: 300px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  border: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.06));
  color: var(--text-color, #333);
  /* 问题角标绝对定位在右上角，需要节点自身作为定位上下文 */
  position: relative;
}

/* 问题角标：不占据版式空间，所以不会影响 useLayout 的尺寸估算 */
.node-issue-badge {
  position: absolute;
  top: -8px;
  right: -8px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  font-size: var(--font-size-xs, 11px);
  line-height: 18px;
  text-align: center;
  font-weight: 600;
  color: #ffffff;
  box-shadow: var(--box-shadow-sm, 0 2px 6px rgba(0, 0, 0, 0.2));
  z-index: 5;
  cursor: help;
}

.node-issue-error {
  background-color: var(--error-color, #d03050);
}

.node-issue-warning {
  background-color: var(--warning-color, #f0a020);
}

/*
  零端口空态：占位高度与端口列相近，避免添加第一个端口时节点高度突然跳变，
  从而触发一次不必要的重叠检测。
*/
.ports-empty-state {
  padding: var(--space-2, 8px) var(--space-3, 12px);
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.01));
  border-bottom: 1px dashed var(--node-border-color, rgba(0, 0, 0, 0.08));
  font-size: var(--font-size-sm, 12px);
  line-height: 1.5;
  color: var(--text-color-tertiary, #666);
}

.code-node-header {
  padding: 10px 14px;
  font-weight: 500;
  color: var(--text-color, #333);
  background-color: var(--node-header-bg, #f0f0f0);
  border-left: 4px solid var(--node-accent-code, var(--info-color, #5c6ac4));
  border-bottom: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.04));
  font-size: var(--font-size-base, 14px);
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-width: 0;
}

.node-label {
  flex-grow: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.node-id {
  font-size: var(--font-size-xs, 11px);
  color: var(--text-color-tertiary, #666);
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.08));
  padding: 2px 5px;
  border-radius: var(--radius-xs);
  margin-left: 6px;
  font-family: monospace;
  cursor: default;
  flex-shrink: 0;
}

.code-node-body {
  padding: var(--space-3, 12px);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.code-preview-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: var(--space-2, 8px);
}

.code-icon {
  color: var(--primary-color, #4080ff);
}

.code-label {
  font-size: var(--font-size-sm, 12px);
  color: var(--text-color-secondary, #555);
  font-weight: 500;
}

.code-preview {
  background: var(--code-bg-color, #f3f4f6);
  /* 嵌在节点（sm 档）内部的代码块，按嵌套原则降到 xs */
  border-radius: var(--radius-xs);
  padding: var(--space-2, 8px);
  overflow: hidden;
}

.code-preview-content {
  margin: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: var(--font-size-sm, 12px);
  white-space: pre-wrap;
  color: var(--code-text-color, #1f2937);
  line-height: 1.4;
}

.ports-container {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--space-2, 8px);
  padding: 6px 0;
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.01));
}

.port-column {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
  flex: 1 1 0;
}

.output-ports {
  align-items: flex-end;
}

.port-container {
  display: flex;
  align-items: center;
  position: relative;
  height: 28px;
  max-width: 100%;
}

.output-port {
  justify-content: flex-end;
}

.port-label {
  font-size: var(--font-size-sm, 12px);
  color: var(--text-color-secondary, #555);
  margin: 0 10px;
  /* 与 CustomNode 一致：固定行高下必须单行截断，避免长标签压到相邻端口 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  line-height: 1.3;
}
</style>
