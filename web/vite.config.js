import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // 前端 dev server 把 /api 转发到 FastAPI，避免跨域
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        /**
         * 手动分包：Element Plus 全量引入后单包超过 1MB，首屏要等它下载完。
         * 把三个"不常变"的大依赖拆出去，浏览器能长期缓存，
         * 业务代码改动时只需要重新下载几 KB 的 index chunk。
         */
        manualChunks: {
          'element-plus': ['element-plus'],
          'vue-core': ['vue', 'vue-router', 'pinia'],
          'vue-flow': ['@vue-flow/core', '@vue-flow/background', '@vue-flow/controls'],
        },
      },
    },
  },
})
