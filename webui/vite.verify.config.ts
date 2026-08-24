import { defineConfig } from 'vite'
import baseConfig from './vite.config'

export default defineConfig({
  ...baseConfig,
  server: {
    ...baseConfig.server,
    proxy: {
      '/backend-api': 'http://127.0.0.1:18082',
      '/backend-api/api/tracing/ws': {
        target: 'ws://127.0.0.1:18082',
        ws: true,
        changeOrigin: true
      },
      '/backend-api/api/system/logs': {
        target: 'ws://127.0.0.1:18082',
        ws: true,
        changeOrigin: true
      },
      '/backend-api/api/block/code/lsp': {
        target: 'ws://127.0.0.1:18082',
        ws: true,
        changeOrigin: true
      }
    }
  }
})
