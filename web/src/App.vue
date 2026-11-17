<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">
        <span class="logo">◈</span>
        <div>
          <p class="title">Linux 运维 Agent</p>
          <p class="subtitle">LangGraph · RAG · 人在回路</p>
        </div>
      </div>
    </header>
    <main class="body">
      <ChatPanel />
    </main>
  </div>
</template>

<script setup>
import ChatPanel from './components/ChatPanel.vue'
</script>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

.topbar {
  flex: 0 0 auto;
  background: #14355a;
  color: #fff;
  padding: 14px 24px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  font-size: 20px;
  opacity: 0.9;
}

.title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.subtitle {
  margin: 2px 0 0;
  font-size: 11px;
  opacity: 0.65;
}

.body {
  flex: 1;
  min-height: 0;
}
</style>
