import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vitest/config'

// Keep pure workflow-data tests independent from the Monaco-specific Vite build plugins.
export default defineConfig({
  // 与 vite.config.ts 的 alias 保持一致：被测模块里的 `@/` 导入若不是纯类型
  // （类型会被 esbuild 擦除，因此过去恰好能跑），解析就会失败。
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts']
  }
})
