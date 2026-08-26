import { defineConfig } from 'vite'
import baseConfig from './vite.config'

const backend = 'http://127.0.0.1:18084'
const websocketBackend = 'ws://127.0.0.1:18084'

export default defineConfig({
  ...baseConfig,
  server: {
    ...baseConfig.server,
    host: '127.0.0.1',
    port: 15177,
    strictPort: true,
    proxy: {
      '/backend-api': backend,
      '/backend-api/api/tracing/ws': {
        target: websocketBackend,
        ws: true,
        changeOrigin: true
      },
      '/backend-api/api/system/logs': {
        target: websocketBackend,
        ws: true,
        changeOrigin: true
      },
      '/backend-api/api/block/code/lsp': {
        target: websocketBackend,
        ws: true,
        changeOrigin: true
      }
    }
  }
})
