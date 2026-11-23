<template>
  <!-- 只留一个出口：布局、顶栏、侧边栏都下沉到 layouts/AdminLayout.vue -->
  <router-view />
</template>

<script setup>
// App.vue 不再承担布局职责。
// 改造前这里是"顶栏 + 一个 ChatPanel"，页面只有一种形态；
// 现在有四个视图，布局必须抽出去，否则每加一页都要复制一份顶栏。
</script>
