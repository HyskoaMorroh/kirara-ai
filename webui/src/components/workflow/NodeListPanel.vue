<script setup lang="ts">
import { ref, computed } from 'vue'
import {
  NCard,
  NInput,
  NIcon,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NScrollbar,
  NTag,
  NTooltip,
  NText,
  NDivider
} from 'naive-ui'
import { SearchOutline, AppsOutline, FilterOutline } from '@vicons/ionicons5'
import { useVueFlow } from '@vue-flow/core'
import type { BlockType } from '@/api/block'
import { getTypeColor } from '@/utils/node-colors'
import { createUniqueNodeName } from './workflow-node-utils'

const props = defineProps<{
  blockTypes: BlockType[]
}>()

const { addNodes, getNodes, project } = useVueFlow()

// 搜索功能
const searchQuery = ref('')
const isSearching = computed(() => searchQuery.value.trim().length > 0)

// 按类别分组的区块类型
const groupedBlockTypes = computed(() => {
  const groups: Record<string, BlockType[]> = {}

  // 过滤搜索结果
  const filteredTypes = props.blockTypes.filter((blockType) => {
    if (!isSearching.value) return true

    const query = searchQuery.value.toLowerCase()
    return (
      blockType.type_name.toLowerCase().includes(query) ||
      blockType.label.toLowerCase().includes(query) ||
      blockType.description?.toLowerCase().includes(query)
    )
  })

  // 按 groupId 分组
  filteredTypes.forEach((blockType) => {
    // 从 type_name 中提取 groupId (通常是第一部分)
    const groupId = blockType.type_name.split(':')[0] || '其他'
    if (!groups[groupId]) {
      groups[groupId] = []
    }
    groups[groupId].push(blockType)
  })

  return groups
})

/**
 * 二级分组：把 internal 组按 label 前缀（基础/IM/记忆/LLM/画图）再拆一层。
 *
 * internal 组下有 23 个节点，全部平铺在一个折叠面板里，用户需要肉眼扫过
 * 整列才能找到目标。按功能域细分后，每个小节只有 2~7 项，配合上面已有的
 * 搜索框可以快速定位。
 */
const INTERNAL_SUBGROUPS: { key: string; label: string; match: (label: string) => boolean }[] = [
  { key: 'basic', label: '基础处理', match: (label) => label.startsWith('基础') },
  { key: 'im', label: 'IM 消息', match: (label) => label.startsWith('IM') },
  { key: 'memory', label: '记忆读写', match: (label) => label.startsWith('记忆') },
  { key: 'llm', label: 'LLM 调用', match: (label) => label.startsWith('LLM') },
  { key: 'image', label: '图片生成', match: (label) => label.startsWith('画图') }
]

const getSubgroups = (groupId: string, blockTypes: BlockType[]) => {
  // 只有节点数量较多的 internal 组需要细分，其余分组保持原样
  if (groupId !== 'internal' || blockTypes.length <= 8) {
    return [{ key: '', label: '', items: blockTypes }]
  }

  const assigned = new Set<string>()
  const result: { key: string; label: string; items: BlockType[] }[] = []

  for (const subgroup of INTERNAL_SUBGROUPS) {
    const items = blockTypes.filter((blockType) => subgroup.match(blockType.label || ''))
    items.forEach((item) => assigned.add(item.type_name))
    if (items.length > 0) {
      result.push({ key: subgroup.key, label: subgroup.label, items })
    }
  }

  // 前缀未覆盖到的节点归入「其他」，保证不会漏掉任何节点
  const rest = blockTypes.filter((blockType) => !assigned.has(blockType.type_name))
  if (rest.length > 0) {
    result.push({ key: 'other', label: '其他', items: rest })
  }

  return result
}

// 获取分组的排序
const sortedGroupKeys = computed(() => {
  const keys = Object.keys(groupedBlockTypes.value)

  // 自定义排序逻辑
  const groupOrder = ['internal', 'system', 'mcp', 'game']

  return keys.sort((a, b) => {
    const indexA = groupOrder.indexOf(a)
    const indexB = groupOrder.indexOf(b)

    if (indexA !== -1 && indexB !== -1) return indexA - indexB
    if (indexA !== -1) return -1
    if (indexB !== -1) return 1
    return a.localeCompare(b)
  })
})

/**
 * 预先算好每个分组的二级分组。
 *
 * getSubgroups() 原先直接写在模板的 v-for 里，于是每次重渲染（哪怕只是
 * 搜索框输入或者折叠面板展开）都要为每个分组重跑一遍 5 次 filter。
 * 改为 computed 后只在 blockTypes / 搜索词变化时才重算一次。
 * 保留 getSubgroups 本身不变，方便单独复用与测试。
 */
const subgroupsByGroup = computed(() => {
  const result: Record<string, { key: string; label: string; items: BlockType[] }[]> = {}
  for (const [groupId, blockTypes] of Object.entries(groupedBlockTypes.value)) {
    result[groupId] = getSubgroups(groupId, blockTypes)
  }
  return result
})

// 获取分组的显示名称
const getGroupDisplayName = (groupId: string) => {
  const groupNameMap: Record<string, string> = {
    internal: '内部组件',
    system: '系统组件',
    mcp: 'MCP组件',
    game: '娱乐组件'
  }

  return groupNameMap[groupId] || groupId
}

// 拖拽开始处理
const onDragStart = (event: DragEvent, blockType: BlockType) => {
  if (event.dataTransfer) {
    event.dataTransfer.setData('application/vueflow', JSON.stringify(blockType))
    event.dataTransfer.effectAllowed = 'move'
  }
}

/**
 * 从一个统一入口创建节点，供拖放和键盘操作复用。
 *
 * 画布侧的 drop handler 也应复用 workflow-node-utils 中的命名规则；这里不再
 * 保留另一套 `split(':')` 的重复实现，避免两种添加方式生成不同的节点名称。
 */
const addBlockNode = (blockType: BlockType, clientPosition: { x: number; y: number }) => {
  const position = project(clientPosition)
  const name = createUniqueNodeName(
    blockType.type_name,
    getNodes.value.map((node) => node.id)
  )

  addNodes([
    {
      id: name,
      type: blockType.type_name === 'internal:code' ? 'code' : 'custom',
      position,
      data: {
        label: blockType.label,
        blockType,
        config: {},
        inputs: blockType.inputs,
        outputs: blockType.outputs
      }
    }
  ])
}

/**
 * 键盘用户可在当前视口中心添加节点；鼠标拖放仍保持原有交互。
 */
const handleNodeKeydown = (event: KeyboardEvent, blockType: BlockType) => {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  addBlockNode(blockType, { x: window.innerWidth / 2, y: window.innerHeight / 2 })
}

// 根据类型获取颜色样式
const getBlockTypeColor = (blockType: BlockType) => {
  // 优先使用后端下发的分组配色，与画布上节点的左侧色条保持一致
  if (blockType.color) {
    return blockType.color
  }
  // 如果有输出，使用第一个输出的类型颜色
  if (blockType.outputs && blockType.outputs.length > 0) {
    return getTypeColor(blockType.outputs[0].type).color_on
  }
  // 如果有输入，使用第一个输入的类型颜色
  if (blockType.inputs && blockType.inputs.length > 0) {
    return getTypeColor(blockType.inputs[0].type).color_on
  }
  // 默认颜色
  return '#909399'
}

// 获取节点短ID
const getShortId = (typeName: string) => {
  const parts = typeName.split(':')
  return parts[parts.length - 1] || typeName
}
</script>

<template>
  <div class="node-list-panel">
    <div class="panel-header">
      <NIcon size="18" class="header-icon">
        <AppsOutline />
      </NIcon>
      <span class="header-title">节点列表</span>
    </div>

    <div class="search-container">
      <NInput v-model:value="searchQuery" placeholder="搜索节点..." clearable class="search-input">
        <template #prefix>
          <NIcon>
            <SearchOutline />
          </NIcon>
        </template>
      </NInput>
    </div>

    <NScrollbar>
      <div v-if="Object.keys(groupedBlockTypes).length === 0" class="empty-state">
        <NEmpty description="没有找到匹配的节点" />
      </div>

      <NCollapse
        arrow-placement="right"
        :default-expanded-names="sortedGroupKeys"
        class="node-list-collapse"
      >
        <NCollapseItem
          v-for="groupId in sortedGroupKeys"
          :key="groupId"
          :title="`${getGroupDisplayName(groupId)} (${groupedBlockTypes[groupId].length})`"
          :name="groupId"
        >
          <div
            v-for="subgroup in subgroupsByGroup[groupId] || []"
            :key="subgroup.key || groupId"
            class="node-subgroup"
          >
            <div v-if="subgroup.label" class="subgroup-title">
              {{ subgroup.label }}
              <span class="subgroup-count">{{ subgroup.items.length }}</span>
            </div>
            <div class="node-list">
              <div
                v-for="blockType in subgroup.items"
                :key="blockType.type_name"
                class="node-item"
                draggable="true"
                role="button"
                tabindex="0"
                :aria-label="`添加节点：${blockType.label}。按 Enter 或空格添加到画布中心。`"
                aria-keyshortcuts="Enter Space"
                @dragstart="onDragStart($event, blockType)"
                @keydown="handleNodeKeydown($event, blockType)"
              >
                <div
                  class="custom-node"
                  :style="{ borderLeft: `3px solid ${getBlockTypeColor(blockType)}` }"
                >
                  <div class="custom-node-header">
                    <div class="header-content">
                      <span class="node-label">{{ blockType.label }}</span>
                      <span class="node-id" :title="blockType.type_name"
                        >#{{ getShortId(blockType.type_name) }}</span
                      >
                    </div>
                  </div>

                  <div class="custom-node-body">
                    <div v-if="blockType.description" class="node-description">
                      {{ blockType.description }}
                    </div>

                    <div class="node-meta">
                      <span v-if="blockType.inputs.length > 0" class="node-port-count">
                        入 {{ blockType.inputs.length }}
                      </span>
                      <span v-if="blockType.outputs.length > 0" class="node-port-count">
                        出 {{ blockType.outputs.length }}
                      </span>
                      <span v-if="blockType.configs.length > 0" class="node-port-count">
                        配置 {{ blockType.configs.length }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </NCollapseItem>
      </NCollapse>
    </NScrollbar>

    <div class="drag-hint">
      <NIcon size="16" class="hint-icon">
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
          <path d="M14 8a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
          <path d="M18 14a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
          <path d="M8 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
          <path d="M10 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
          <path d="M18 8a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
          <path d="M8 8a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"></path>
        </svg>
      </NIcon>
      <span>拖拽节点到画布中</span>
    </div>
  </div>
</template>

<style scoped>
.node-list-panel {
  width: 340px;
  background-color: var(--panel-bg-color, rgba(255, 255, 255, 0.8));
  backdrop-filter: blur(10px);
  /* 例外：该面板贴着画布左缘满高铺满，任何圆角都会露出画布底色，故保持直角 */
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
  padding: 20px 14px;
}

.header-icon {
  color: var(--primary-color, #1890ff);
}

.header-title {
  font-weight: 600;
  font-size: 16px;
  color: var(--text-color, #1f2937);
}

.search-container {
  margin: 12px;
}

.search-input {
  border-radius: var(--radius-sm);
}

.node-list-collapse {
  margin: 0 auto;
  width: 300px;
}

.empty-state {
  padding: 20px 0;
  display: flex;
  justify-content: center;
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.node-item {
  cursor: grab;
  transition: all 0.2s ease;
  user-select: none;
}

.node-item:active {
  cursor: grabbing;
}

/* 自定义节点样式 - 参考 CustomNode.vue */
.custom-node {
  background: linear-gradient(to bottom, var(--node-bg-start, #f8f9fa), var(--node-bg-end, #ffffff));
  border-radius: var(--radius-sm);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  border: 1px solid var(--node-border-color, rgba(0, 0, 0, 0.06));
  transition: all 0.2s ease;
}

.node-item:hover .custom-node {
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.15);
  transform: translateY(-2px);
}

.node-item:focus-visible {
  outline: none;
}

.node-item:focus-visible .custom-node {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 2px;
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
}

.node-label {
  flex-grow: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
  display: flex;
  flex-direction: column;
}

.node-description {
  padding: 10px;
  font-size: 12px;
  color: var(--text-color-secondary, #6b7280);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

.node-meta {
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 6px;
  padding: 0 10px 10px;
}

/* 节点的端口/配置数量角标，帮助判断节点复杂度 */
.node-port-count {
  font-size: 11px;
  color: var(--text-color-tertiary, #9ca3af);
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.04));
  padding: 1px 6px;
  border-radius: var(--radius-pill);
}

/* internal 组内的二级分组标题 */
.node-subgroup {
  margin-bottom: 10px;
}

.node-subgroup:last-child {
  margin-bottom: 0;
}

.subgroup-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-color-secondary, #6b7280);
  padding: 4px 2px 6px;
}

.subgroup-count {
  font-size: 11px;
  font-weight: normal;
  color: var(--text-color-tertiary, #9ca3af);
  background-color: var(--node-muted-bg, rgba(0, 0, 0, 0.04));
  padding: 0 6px;
  border-radius: var(--radius-pill);
}

.drag-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px;
  background-color: var(--node-muted-bg, #f9fafb);
  border-radius: var(--radius-sm);
  margin-top: 12px;
  font-size: 12px;
  color: var(--text-color-secondary, #6b7280);
}

.hint-icon {
  color: var(--text-color-tertiary, #9ca3af);
}
</style>
