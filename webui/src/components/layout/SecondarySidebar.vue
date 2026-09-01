<script setup lang="ts">
import { computed, h, watch } from 'vue'
import { NMenu, NDivider } from 'naive-ui'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import type { MenuOption } from 'naive-ui'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const appStore = useAppStore()
const router = useRouter()
const emit = defineEmits<{
  (e: 'hasContent', hasContent: boolean): void
}>()

// 根据当前路由获取二级菜单配置
const menuOptions = computed<MenuOption[]>(() => {
  const currentPath = route.path.split('/')[1]

  switch (currentPath) {
    case 'im':
      return []
    case 'llm':
      return [
        {
          label: () => '模型配置',
          key: 'llm-config',
          path: '/llm'
        },
        {
          label: () => 'Agent 管理',
          key: 'llm-agents',
          path: '/llm/agents'
        },
        {
          label: () => 'Agent 对话',
          key: 'llm-chat',
          path: '/llm/chat'
        },
        {
          label: () => '成本定价',
          key: 'llm-pricing',
          path: '/llm/pricing'
        },
        {
          label: () => '容错状态',
          key: 'llm-resilience',
          path: '/llm/resilience'
        },
        {
          label: () => '自动检测计划',
          key: 'llm-auto-detect',
          path: '/llm/auto-detect'
        }
      ]
    case 'workflow':
      return [
        {
          label: () => '工作流列表',
          key: 'workflow-list',
          path: '/workflow'
        },
        {
          label: () => '触发规则',
          key: 'workflow-dispatch-rules',
          path: '/workflow/dispatch-rules'
        },
        {
          label: () => '模板管理',
          key: 'workflow-templates',
          path: '/workflow/templates'
        }
      ]
    case 'plugins':
      return []
    case 'resources':
      return [
        {
          label: () => '资源列表',
          key: 'resources-list',
          path: '/resources'
        },
        {
          label: () => '记忆资源',
          // 记忆是资源类型之一，落点带上 type 让页面直接筛好，而不是跳过去再手动选一次。
          key: 'resources-memory',
          path: '/resources?type=memory'
        },
        {
          label: () => '系统依赖',
          key: 'resources-dependencies',
          path: '/resources/dependencies'
        }
      ]
    case 'tracing':
      return [
        {
          label: () => '使用统计',
          key: 'tracing-statistics',
          path: '/tracing/statistics'
        },
        {
          label: () => 'LLM 请求记录',
          key: 'tracing-llm',
          path: '/tracing/llm'
        },
        {
          label: () => '投递时间线',
          key: 'tracing-delivery',
          path: '/tracing/delivery'
        }
      ]
    default:
      return []
  }
})

// 当前选中的菜单项
const activeKey = computed(() => {
  // 先按整条链接精确认一次：有两个条目共用 /resources，只有 query 不同（资源列表 / 记忆资源），
  // 只比 path 的话点进记忆资源，高亮仍停在资源列表，读起来像点击没生效。
  const query = route.query
  const suffix = query.type ? `?type=${String(query.type)}` : ''
  const exact = menuOptions.value.find(
    (option) => option.path === route.fullPath || option.path === `${route.path}${suffix}`
  )
  if (exact) {
    return exact.key
  }

  const pathParts = route.path.split('/')
  if (pathParts.length > 2) {
    return `${pathParts[1]}-${pathParts[2]}`
  }
  return `${pathParts[1]}-${getDefaultSubModule(pathParts[1])}`
})

// 获取模块的默认子模块
const getDefaultSubModule = (module: string) => {
  switch (module) {
    case 'im':
      return 'platforms'
    case 'llm':
      return 'config'
    case 'workflow':
      return 'list'
    case 'plugins':
      return 'installed'
    case 'resources':
      return 'list'
    case 'tracing':
      // 与路由的 `/tracing` 重定向保持一致：默认落在使用统计。
      return 'statistics'
    default:
      return ''
  }
}

const getMenuTitle = (key: string) => {
  switch (key) {
    case 'workflow':
      return '工作流'
    case 'llm':
      return '模型与 Agent'
    case 'plugins':
      return '插件'
    case 'resources':
      return '资源与记忆'
    case 'tracing':
      return '系统记录'
    default:
      return ''
  }
}
// 监听菜单变化，更新当前路由
const handleUpdateValue = (key: string) => {
  const selectedMenu = menuOptions.value.find((option) => option.key === key)
  if (selectedMenu && selectedMenu.path) {
    router.push(selectedMenu.path)
  }
}

watch(
  () => menuOptions.value,
  () => {
    if (menuOptions.value.length === 0) {
      emit('hasContent', false)
    } else {
      emit('hasContent', true)
    }
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="menuOptions.length > 0" class="secondary-menu-container">
    <div class="secondary-menu-header">
      <h3 class="secondary-menu-title">{{ getMenuTitle(appStore.currentModule) }}</h3>
    </div>
    <n-menu
      :value="activeKey"
      :options="menuOptions"
      @update:value="handleUpdateValue"
      class="secondary-menu"
    />
  </div>
</template>

<style scoped>
.secondary-menu-header {
  padding: 0 24px;
  height: var(--sidebar-title-height);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
}

.secondary-menu-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-color);
}

.secondary-menu {
  margin-top: 8px;
}

:deep(.n-menu-item) {
  height: 40px;
  margin: 4px 12px;
  border-radius: var(--radius-sm);
}

:deep(.n-menu-item-content) {
  padding: 0 12px;
}

:deep(.n-menu-item-content-header) {
  font-size: 14px;
  font-weight: 500;
}
</style>
