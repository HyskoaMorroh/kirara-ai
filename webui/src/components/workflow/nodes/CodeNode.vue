<script setup lang="ts">
import { computed, ref } from 'vue'
import { Handle, Position, useVueFlow, type Connection } from '@vue-flow/core'
import { getTypeColor } from '@/utils/node-colors'
import { CodeOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

const props = defineProps(['id', 'data', 'isValidConnection'])
const { getHandleConnections } = useVueFlow()

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
const isValidConnection = (connection: Connection) => {
  // 一个输入只能有一个连接
  const incomers = getHandleConnections({
    id: connection.targetHandle,
    nodeId: connection.target,
    type: 'target'
  })
  if (incomers.length > 0) {
    return false
  }

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
  <div class="code-node">
    <div class="code-node-header">
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
  min-width: 200px;
  max-width: 300px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  border: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.06));
  color: var(--text-color, #333);
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
