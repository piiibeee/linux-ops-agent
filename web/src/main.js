import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

// VueFlow 的样式必须显式引入，否则画布渲染出来是错位的
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'

import App from './App.vue'
import router from './router'
import './style.css'

// 顺序：pinia 要在 router 之前，路由守卫里可能要读 store
createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
