<template>
  <div class="admin-shell">
    <!-- 侧边栏：导航 + 角色切换 -->
    <aside class="side">
      <div class="brand">
        <span class="logo">◈</span>
        <div>
          <p class="title">运维 Agent</p>
          <p class="subtitle">控制台</p>
        </div>
      </div>

      <nav class="nav">
        <router-link
          v-for="item in navItems"
          :key="item.name"
          :to="{ name: item.name }"
          class="nav-item"
          active-class="active"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-text">{{ item.title }}</span>
          <el-badge
            v-if="item.name === 'actions' && pendingCount > 0"
            :value="pendingCount"
            class="nav-badge"
          />
        </router-link>
      </nav>

      <div class="side-foot">
        <p class="foot-label">当前角色</p>
        <el-select v-model="role" size="small" style="width: 100%">
          <el-option
            v-for="(meta, key) in auth.ROLES"
            :key="key"
            :label="meta.label"
            :value="key"
          />
        </el-select>
        <p class="foot-hint">{{ auth.ROLES[role]?.desc }}</p>
      </div>
    </aside>

    <!-- 主区域 -->
    <div class="main">
      <header class="topbar">
        <div class="crumb">
          <span class="crumb-root">控制台</span>
          <span class="crumb-sep">/</span>
          <span class="crumb-cur">{{ route.meta?.title }}</span>
        </div>
        <div class="top-right">
          <span class="health" :class="health.ok ? 'ok' : 'bad'">
            <i class="dot"></i>{{ health.text }}
          </span>
        </div>
      </header>

      <section class="content">
        <router-view />
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import { fetchHealth, fetchActions } from '../api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const auth = useAuthStore()
const { role } = storeToRefs(auth)

const navItems = [
  { name: 'chat', title: '对话', icon: '💬' },
  { name: 'audit', title: '审计日志', icon: '📋' },
  { name: 'actions', title: '变更审批', icon: '✅' },
  { name: 'graph', title: '流程编排', icon: '🕸' },
]

const health = ref({ ok: false, text: '连接中…' })
const pendingCount = ref(0)

onMounted(async () => {
  try {
    const h = await fetchHealth()
    health.value = {
      ok: true,
      text: `${h.llm_model} · 存储 ${h.memory_backend} · ${h.allow_execute ? '可执行' : '只读'}`,
    }
  } catch {
    health.value = { ok: false, text: '后端未连接' }
  }

  // 待审批数量放在布局里取，切页面时不会闪烁
  try {
    const res = await fetchActions('pending')
    pendingCount.value = res.total || 0
  } catch {
    pendingCount.value = 0
  }
})
</script>

<style scoped>
.admin-shell {
  display: flex;
  height: 100vh;
  background: var(--bg);
}

.side {
  width: 208px;
  flex: 0 0 208px;
  background: #14355a;
  color: #fff;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}

.logo {
  font-size: 20px;
  opacity: 0.9;
}

.title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.4px;
}

.subtitle {
  margin: 2px 0 0;
  font-size: 11px;
  opacity: 0.6;
}

.nav {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border-radius: 6px;
  color: rgba(255, 255, 255, 0.82);
  text-decoration: none;
  font-size: 13px;
  transition: background 0.15s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.nav-item.active {
  background: #2563eb;
  color: #fff;
}

.nav-icon {
  font-size: 13px;
  width: 16px;
  text-align: center;
}

.nav-text {
  flex: 1;
}

.nav-badge {
  margin-left: auto;
}

.side-foot {
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  padding-top: 12px;
}

.foot-label {
  margin: 0 0 6px;
  font-size: 11px;
  opacity: 0.55;
}

.foot-hint {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.5;
  opacity: 0.55;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 48px;
  flex: 0 0 48px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.crumb {
  font-size: 13px;
  color: var(--muted);
}

.crumb-sep {
  margin: 0 6px;
  opacity: 0.5;
}

.crumb-cur {
  color: var(--text);
  font-weight: 500;
}

.health {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
  display: inline-block;
}

.health.ok .dot {
  background: #16a34a;
}

.health.bad .dot {
  background: #dc2626;
}

.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>
