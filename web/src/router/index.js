import { createRouter, createWebHistory } from 'vue-router'
import AdminLayout from '../layouts/AdminLayout.vue'

/**
 * 路由设计说明：
 * 四个视图都挂在 AdminLayout 下面（嵌套路由），这样侧边栏只渲染一次，
 * 切换视图时不会整页重建 —— 这是 B 端最基本的体验要求。
 *
 * 用 createWebHistory（干净 URL）而不是 hash：
 * 需要 nginx 配 try_files 兜底，仓库里 deploy/nginx-ops-agent.conf 已经配好了。
 */
const routes = [
  {
    path: '/',
    component: AdminLayout,
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'chat',
        component: () => import('../views/ChatView.vue'),
        meta: { title: '对话', icon: '💬' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('../views/AuditView.vue'),
        meta: { title: '审计日志', icon: '📋' },
      },
      {
        path: 'actions',
        name: 'actions',
        component: () => import('../views/ActionView.vue'),
        meta: { title: '变更审批', icon: '✅' },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('../views/GraphView.vue'),
        meta: { title: '流程编排', icon: '🕸' },
      },
    ],
  },
  // 兜底：访问不存在的路径回对话页，别给白屏
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const title = to.meta?.title
  document.title = title ? `${title} · 运维 Agent 控制台` : '运维 Agent 控制台'
})

export default router
