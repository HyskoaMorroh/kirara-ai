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
          component: () => import('@/views/ComingSoon.vue')
        },
        {
          path: '/llm',
          name: 'llm',
          component: () => import('@/views/llm/LLMView.vue')
        },
        {
          path: '/llm/backends',
          name: 'llm-backends',
          component: () => import('@/views/ComingSoon.vue')
        },
        {
          path: '/llm/models',
          name: 'llm-models',
          component: () => import('@/views/ComingSoon.vue')
        },
        {
          path: '/llm/chat',
          name: 'llm-chat',
          component: () => import('@/views/ComingSoon.vue')
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
          path: '/memory',
          name: 'memory',
          component: () => import('@/views/ComingSoon.vue')
        },
        {
          path: '/memory/search',
          name: 'memory-search',
          component: () => import('@/views/ComingSoon.vue')
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
              redirect: '/tracing/llm',
              name: 'tracing-index'
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
