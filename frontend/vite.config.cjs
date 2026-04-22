const path = require('node:path')
const { defineConfig } = require('vite')
const vue = require('@vitejs/plugin-vue').default

const apiTarget = process.env.LISTENING_TRAINER_API_TARGET || 'http://127.0.0.1:8000'

module.exports = defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (requestPath) => requestPath.replace(/^\/api/, ''),
      },
      '/audio': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/dictionary': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
