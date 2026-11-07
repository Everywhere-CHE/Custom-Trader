import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // 将体积较大的第三方依赖拆分为可独立缓存的分包
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/echarts')) return 'echarts'
          if (id.includes('node_modules/lightweight-charts')) return 'charts'
          if (
            id.includes('node_modules/antd') ||
            id.includes('node_modules/@ant-design')
          ) {
            return 'antd'
          }
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 可用 VITE_API_TARGET 覆盖（如本地起 8001 测试新后端）
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: process.env.VITE_API_TARGET?.replace('http', 'ws') || 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
