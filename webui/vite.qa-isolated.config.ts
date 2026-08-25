import { defineConfig } from 'vite'
import baseConfig from './vite.config'

const backend = 'http://127.0.0.1:18083'
const websocketBackend = 'ws://127.0.0.1:18083'

export default defineConfig({
  ...baseConfig,
  server: {
    ...baseConfig.server,
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
