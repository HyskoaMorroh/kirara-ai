<script setup lang="ts">
import { computed, inject, type ComputedRef } from 'vue'
import { Handle, Position, type Connection } from '@vue-flow/core'
import { getTypeColor } from '@/utils/node-colors'
import { NODE_MAX_WIDTH, NODE_MIN_WIDTH } from '../useLayout'

const props = defineProps(['id', 'data', 'isValidConnection'])

/** 画布下发的问题汇总；未在画布内使用（如节点列表预览）时为空 */
type NodeIssue = { count: number; severity: 'error' | 'warning'; text: string }
const nodeIssues = inject<ComputedRef<Map<string, NodeIssue>> | null>('workflowNodeIssues', null)

/**
 * 本节点的问题角标。
 *
 * 复用画布已有的校验汇总（含必需输入未连接、孤立节点、以及新增的框重叠），
 * 把问题直接标在出问题的节点上，用户不必先点「检查」才知道哪里不对。
 */
const nodeIssue = computed<NodeIssue | null>(() => {
  if (!nodeIssues?.value || !props.id) return null
  return nodeIssues.value.get(props.id) || null
})

/**
 * 节点宽度上下限由 useLayout.ts 统一定义并在这里内联绑定。
 *
 * 原先 CSS 里写死 200/300px，而布局估算另有一套常量，两边一旦不同步，
 * dagre 就会按错误的宽度留空隙。现在只有一处真值，估算与真实盒子不可能再打架。
 */
const nodeWidthStyle = computed(() => ({
  minWidth: `${NODE_MIN_WIDTH}px`,
  maxWidth: `${NODE_MAX_WIDTH}px`
}))

const shortId = computed(() => {
  if (!props.id) return ''
  if (props.id.length > 8) {
    return props.id.slice(-6)
  }
  return props.id
})

const getInputColor = (type: string, required: boolean) => {
  return getTypeColor(type, required).color_on
}

/**
 * 标题栏底色。
 *
 * 后端下发的 color 是一个饱和度较高的分组色，直接铺满标题栏会压过节点内容，
 * 所以取它的低透明度版本作为底色，再由左侧色条呈现完整色相——既能区分类别，
 * 也不影响文字对比度。未配置颜色时回退到主题的节点标题色。
 */
const headerBackground = computed(() => {
  const color = props.data?.blockType?.color
  if (!color) {
    return 'var(--node-header-bg, #f0f0f0)'
  }
  const hex = color.replace('#', '')
  if (hex.length !== 6) {
    return 'var(--node-header-bg, #f0f0f0)'
  }
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  if ([r, g, b].some((channel) => Number.isNaN(channel))) {
    return 'var(--node-header-bg, #f0f0f0)'
  }
  return `rgba(${r}, ${g}, ${b}, 0.14)`
})

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

/**
 * 配置项预览的兜底文本。
 *
 * bool / str / List[...] / int / float 之外的类型（Any、dict、以及
 * options_provider 提供的自定义对象）原先没有分支，值格会渲染成空白，
 * 让用户误以为配置丢失。这里统一转成可读文本。
 */
const fallbackConfigText = (config: { name: string; default?: any }) => {
  const value = props.data.config?.[config.name] ?? config.default
  if (value === undefined || value === null || value === '') {
    return '未设置'
  }
  if (Array.isArray(value)) {
    return value.join(',')
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return '[对象]'
    }
  }
  return String(value)
}

/** 配置名截断后的完整提示：名称 + 说明 */
const configTitle = (config: { label?: string; name: string; description?: string }) => {
  const label = config.label || config.name
  return config.description && config.description !== label
    ? `${label}\n${config.description}`
    : label
}

// 「一个输入只允许一条边」这条规则**不在这里**判定，理由同 CodeNode：
// Handle 判定 invalid 时 vue-flow 不触发 `onConnect`，在这里 `return false`
// 会让这条拒绝永远静默，现象与类型不兼容无法区分。现在交给画布统一判定。
const isValidConnection = (connection: Connection) => {
  if (props.isValidConnection) {
    return props.isValidConnection(connection)
  }
  return true
}
</script>

<template>
  <div class="custom-node" :style="nodeWidthStyle">
    <!-- 问题角标：错误红、警告黄，鼠标悬停显示全部问题文字 -->
    <div
      v-if="nodeIssue"
      class="node-issue-badge"
      :class="nodeIssue.severity === 'error' ? 'node-issue-error' : 'node-issue-warning'"
      :title="nodeIssue.text"
    >
      {{ nodeIssue.count }}
    </div>
    <div
      class="custom-node-header"
      :style="{
        backgroundColor: headerBackground,
        borderLeft: '4px solid var(--node-accent-custom, var(--primary-color, #4080ff))'
      }"
    >
      <div class="header-content">
        <span class="node-label">{{ data.label }}</span>
        <span class="node-id" :title="id">#{{ shortId }}</span>
      </div>
    </div>
    <!-- 端口双列布局 -->
    <div class="ports-container">
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
          <!--
            必填角标必须留在 .port-label 之外：它原先写在被 ellipsis 截断的
            标签里，长标签一旦溢出，第一个被吃掉的恰好是这个「*」——
            最需要它的时候反而看不见。
          -->
          <div class="port-label" :title="portTitle(input)">
            {{ input.label || input.name }}
          </div>
          <span v-if="input.required" class="port-required-mark" title="必需输入">*</span>
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
    <div class="custom-node-body">
      <div
        v-if="data.blockType.configs && data.blockType.configs.length > 0"
        class="config-preview"
      >
        <div
          v-for="config in data.blockType.configs"
          :key="config.name"
          class="config-preview-item"
        >
          <div class="config-name" :title="configTitle(config)">
            {{ config.label || config.name }}
          </div>
          <div class="config-value">
            <!-- 布尔类型配置预览 -->
            <template v-if="config.type === 'bool'">
              <span
                class="config-badge"
                :class="data.config?.[config.name] ? 'config-badge-true' : 'config-badge-false'"
              >
                {{ data.config?.[config.name] ? '是' : '否' }}
              </span>
            </template>

            <!-- 字符串类型配置预览 -->
            <template v-else-if="config.type === 'str'">
              <span class="config-value-text" :title="data.config?.[config.name] || '未设置'">
                {{ data.config?.[config.name] || '未设置' }}
              </span>
            </template>

            <!-- 数组类型配置预览 -->
            <template v-else-if="config.type.startsWith('List[') && config.type.endsWith(']')">
              <span class="config-value-text" :title="data.config?.[config.name] || '未设置'">
                {{ data.config?.[config.name]?.join(',') || '未设置' }}
              </span>
            </template>

            <!-- 数字类型配置预览 -->
            <template v-else-if="config.type === 'int' || config.type === 'float'">
              <span class="config-value-number">
                {{ data.config?.[config.name] ?? config.default ?? 0 }}
              </span>
            </template>

            <!-- 其余类型（Any / dict / 自定义对象等）统一回退，避免出现空白值格 -->
            <template v-else>
              <span class="config-value-text" :title="fallbackConfigText(config)">
                {{ fallbackConfigText(config) }}
              </span>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-node {
  background: linear-gradient(to bottom, var(--node-bg-start, #f8f9fa), var(--node-bg-end, #ffffff));
  border-radius: var(--radius-sm);
  box-shadow: var(--box-shadow-sm, 0 2px 8px rgba(0, 0, 0, 0.08));
  /* min-width / max-width 由 useLayout.ts 的常量内联注入，见 nodeWidthStyle；
     这里只保留回退值，防止内联样式因故缺失时节点塌成一条 */
  min-width: 220px;
  max-width: 360px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  border: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.06));
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

.custom-node-header {
  padding: 10px 14px;
  font-weight: 500;
  color: var(--text-color, #333);
  border-bottom: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.04));
  font-size: 14px;
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
  font-size: 11px;
  color: var(--text-color-tertiary, rgba(0, 0, 0, 0.45));
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.04));
  padding: 2px 5px;
  border-radius: var(--radius-xs);
  margin-left: 6px;
  font-family: monospace;
  cursor: default;
  flex-shrink: 0;
}

.custom-node-body {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.config-preview {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.config-preview-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: var(--radius-xs);
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.02));
  transition: background-color 0.2s;
}

.config-preview-item:hover {
  background-color: var(--node-header-bg, rgba(0, 0, 0, 0.04));
}

.config-name {
  color: var(--text-color-secondary, #555);
  font-weight: 500;
  flex-shrink: 0;
  width: 45%;
  /* 长配置名（如「额外隔离标识符」）原先会折行，把节点撑高且显得凌乱，
     改为单行截断，完整文字由 title 提示补全 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.config-value {
  flex-grow: 1;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.config-badge {
  display: inline-block;
  padding: 2px 6px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 500;
}

.config-badge-true {
  background-color: rgba(var(--primary-color-rgb, 24, 144, 255), 0.14);
  color: var(--primary-color, #1890ff);
  border: none;
}

.config-badge-false {
  background-color: var(--node-header-bg, rgba(0, 0, 0, 0.04));
  color: var(--text-color-tertiary, #999);
  border: none;
}

.config-value-text {
  color: var(--text-color, #1f2937);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
  display: inline-block;
  font-weight: 500;
}

.config-value-number {
  color: var(--primary-color, #0369a1);
  font-family: monospace;
  font-weight: 500;
}

.ports-container {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.01));
}

/* 输入/输出两列各自独立成列，行高一致才能让第 N 个输入与第 N 个输出对齐 */
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

/* 该规则在当前模板中已无对应节点（端口区改为 .port-column 双列结构后遗留）。
   按约定不删除既有样式，仅在此标注为暂未使用，避免后来者误以为它仍生效。 */
.port-section {
  display: flex;
  flex-direction: column;
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
  font-size: 12px;
  color: var(--text-color-secondary, #555);
  margin: 0 10px;
  /* 端口行是固定高度，长标签必须单行截断，否则换行后会压到下一行端口上 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  line-height: 1.3;
}

/* 必填角标移出标签后，标签自身的右外边距会把「*」推远。
   把这 10px 的间隙让给角标，视觉上仍紧贴标签，行内总间距不变。 */
.input-ports .port-label {
  margin-right: 2px;
}

.input-ports .port-required-mark {
  margin-right: 8px;
}

/* 必填输入的角标，避免用户漏连必需端口 */
/* 作为 .port-label 的兄弟节点存在，因此必须禁止收缩：它不参与省略号截断 */
.port-required-mark {
  color: var(--error-color, #d03050);
  margin-left: 2px;
  flex-shrink: 0;
  font-size: var(--font-size-sm, 12px);
  line-height: 1.3;
}
</style>
