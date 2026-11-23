<template>
  <div class="chat-view">
    <ChatPanel />
  </div>
</template>

<script setup>
import ChatPanel from '../components/ChatPanel.vue'
</script>

<style scoped>
/* 对话页保持原来的全高布局，只是外面多了一层容器（由布局的 content 撑开） */
.chat-view {
  height: 100%;
}
</style>
