import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: AppLayout,
      children: [
        {
          path: '',
          redirect: '/guide'
        },
        {
          path: '/console',
          name: 'console',
          component: () => import('@/views/console/Console.vue')
        },
        {
          path: '/im',
          name: 'im',
          component: () => import('@/views/im/IMView.vue')
        },
        {
          path: '/im/adapters/:adapterType',
          name: 'im-adapter-detail',
          component: () => import('@/views/im/IMAdapterDetail.vue')
        },
        {
          path: '/im/platforms',
          name: 'im-platforms',
          // 平台适配器的启停与配置都在 IMView，二级 activeKey 也默认指向 platforms。
          redirect: { name: 'im' },
          meta: { title: '平台管理', requiresAuth: true }
        },
        {
          path: '/llm',
          name: 'llm',
          component: () => import('@/views/llm/LLMView.vue')
        },
        {
          path: '/llm/backends',
          name: 'llm-backends',
          // 上游后端的增删改就在模型配置页，无需第二个页面。
          redirect: { name: 'llm' },
          meta: { title: '上游后端', requiresAuth: true }
        },
        {
          path: '/llm/models',
          name: 'llm-models',
          // 模型清单随后端配置一起维护，落到模型配置页。
          redirect: { name: 'llm' },
          meta: { title: '模型清单', requiresAuth: true }
        },
        {
          path: '/llm/agents',
          name: 'llm-agents',
          component: () => import('@/views/llm/AgentView.vue'),
          meta: { title: 'Agent 管理', requiresAuth: true }
        },
        {
          path: '/llm/chat',
          name: 'llm-chat',
          component: () => import('@/views/llm/ChatView.vue'),
          meta: { title: 'Agent 对话', requiresAuth: true }
        },
        {
          path: '/llm/pricing',
          name: 'llm-pricing',
          component: () => import('@/views/llm/PricingView.vue'),
          meta: { title: '成本定价', requiresAuth: true }
        },
        {
          path: '/llm/resilience',
          name: 'llm-resilience',
          component: () => import('@/views/llm/ResilienceView.vue'),
          meta: { title: '容错状态', requiresAuth: true }
        },
        {
          path: '/llm/auto-detect',
          name: 'llm-auto-detect',
          component: () => import('@/views/llm/AutoDetectScheduleView.vue'),
          meta: { title: '自动检测计划', requiresAuth: true }
        },
        {
          path: '/workflow',
          name: 'workflow',
          component: () => import('@/views/workflow/WorkflowList.vue')
        },
        {
          path: '/workflow/templates',
          name: 'workflow-templates',
          component: () => import('@/views/workflow/WorkflowTemplates.vue')
        },
        {
          path: '/workflow/dispatch-rules',
          name: 'workflow-dispatch-rules',
          component: () => import('@/views/workflow/DispatchRules.vue')
        },
        {
          path: '/workflow/editor/:id?',
          name: 'workflow-editor',
          component: () => import('@/views/workflow/WorkflowEditor.vue')
        },
        {
          path: '/plugins',
          name: 'plugins',
          component: () => import('@/views/plugins/PluginMarket.vue')
        },
        {
          path: '/plugins/market',
          name: 'plugin-market',
          component: () => import('@/views/plugins/PluginMarket.vue')
        },
        {
          path: '/resources',
          name: 'resources',
          component: () => import('@/views/resources/ResourceView.vue'),
          meta: { title: '资源管理', requiresAuth: true }
        },
        {
          path: '/resources/dependencies',
          name: 'resource-dependencies',
          redirect: { name: 'resources', query: { panel: 'dependencies' } },
          meta: { title: '系统依赖', requiresAuth: true }
        },
        {
          path: '/resources/dependency-tasks',
          name: 'resource-dependency-tasks',
          redirect: { name: 'resources', query: { panel: 'dependencies' } },
          meta: { title: '依赖任务', requiresAuth: true }
        },
        {
          path: '/memory',
          name: 'memory',
          // 记忆本身是资源类型之一（GET /api/resources?type=memory），
          // 能力一直都在，此前入口却指向占位页，读起来像功能没做。
          redirect: { name: 'resources', query: { type: 'memory' } },
          meta: { title: '记忆管理', requiresAuth: true }
        },
        {
          path: '/memory/search',
          name: 'memory-search',
          // 检索不再需要单独页面：资源管理已有关键词搜索框，落点带上类型即可。
          redirect: { name: 'resources', query: { type: 'memory' } },
          meta: { title: '记忆检索', requiresAuth: true }
        },
        {
          path: '/guide',
          name: 'guide',
          component: () => import('@/views/guide/GuideView.vue')
        },
        {
          path: '/settings',
          name: 'settings',
          component: () => import('@/views/settings/BasicSettings.vue')
        },
        {
          path: '/media',
          name: 'media',
          component: () => import('@/views/media/MediaList.vue')
        },
        {
          path: '/mcp',
          name: 'mcp',
          component: () => import('@/views/mcp/MCPList.vue')
        },
        {
          path: '/mcp/detail/:id',
          name: 'mcp-detail',
          component: () => import('@/views/mcp/MCPDetail.vue'),
          meta: {
            title: 'MCP服务器详情'
          }
        },
        {
          path: '/tracing',
          name: 'tracing',
          meta: {
            title: '系统追踪',
            requiresAuth: true
          },
          children: [
            {
              path: '',
              redirect: '/tracing/statistics',
              name: 'tracing-index'
            },
            {
              path: 'statistics',
              name: 'usage-statistics',
              component: () => import('@/views/tracing/UsageStatisticsView.vue'),
              meta: {
                title: '使用统计',
                requiresAuth: true
              }
            },
            {
              path: 'llm',
              name: 'llm-tracing',
              component: () => import('@/views/tracing/llm/LLMTraceList.vue'),
              meta: {
                title: 'LLM请求追踪',
                requiresAuth: true
              }
            },
            {
              path: 'llm/detail/:traceId',
              name: 'llm-trace-detail',
              component: () => import('@/views/tracing/llm/LLMTraceDetail.vue'),
              meta: {
                title: 'LLM请求详情',
                requiresAuth: true
              }
            },
            {
              path: 'delivery',
              name: 'delivery-timeline',
              component: () => import('@/views/tracing/DeliveryTimelineView.vue'),
              meta: {
                title: '投递时间线',
                requiresAuth: true
              }
            }
          ]
        }
      ]
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/login/LoginView.vue')
    },
    {
      path: '/editor',
      name: 'editor',
      component: () => import('@/editor/Editor.vue')
    }
  ]
})

// 路由守卫
router.beforeEach((to, from, next) => {
  let token: string | null = null
  try {
    token = localStorage.getItem('token')
  } catch {
    // 隐私策略禁用存储时按未登录处理，避免路由守卫抛出异常。
  }
  if (to.name !== 'login' && !token) {
    next({ name: 'login' })
  } else {
    next()
  }
})

export default router
