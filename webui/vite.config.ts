import { fileURLToPath, URL } from 'node:url'
import importMetaUrlPlugin from '@codingame/esbuild-import-meta-url-plugin'

import { defineConfig, normalizePath } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import packageJson from './package.json'

const NPM_PRERELEASE = /^(\d+\.\d+\.\d+)-(a|b|rc)(\d+)$/

function getReleaseVersion(): string {
  const packageVersion = packageJson.version.trim()
  const pep440Version = packageVersion.replace(NPM_PRERELEASE, '$1$2$3')
  const expectedVersion = `v${pep440Version}`
  const configuredVersion = process.env.VITE_APP_VERSION?.trim()
  if (configuredVersion && configuredVersion !== expectedVersion) {
    throw new Error(
      `VITE_APP_VERSION ${configuredVersion} does not match package.json ${expectedVersion}`
    )
  }
  return expectedVersion
}

const appVersion = getReleaseVersion()

function versionMetadataPlugin() {
  return {
    name: 'kirara-version-metadata',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'version.json',
        source: JSON.stringify(
          { version: appVersion, packageVersion: packageJson.version },
          null,
          2
        ) + '\n'
      })
    }
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue(), versionMetadataPlugin()],
  // Added proxy configuration
  server: { 
    proxy: {
      '/backend-api': 'http://127.0.0.1:8080',
      '/backend-api/api/tracing/ws': {
        target: 'ws://127.0.0.1:8080',
        ws: true,
        changeOrigin: true,
      },
      '/backend-api/api/system/logs': {
        target: 'ws://127.0.0.1:8080',
        ws: true,
        changeOrigin: true,
      },
      '/backend-api/api/block/code/lsp': {
        target: 'ws://127.0.0.1:8080',
        ws: true,
        changeOrigin: true,
      }
    }
  },
  base: '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@comfyorg/litegraph/dist/css/litegraph.css': path.resolve(__dirname, 'node_modules/@comfyorg/litegraph/dist/css/litegraph.css'),
    },
    dedupe: ['vscode']
  },
  build: {
    rollupOptions: {
      output: {
        compact: true,
        entryFileNames: `assets/[name]-[hash].js`,
        chunkFileNames: `assets/[name]-[hash].js`,
        assetFileNames: `assets/[name]-[hash][extname]`,
        manualChunks: {
          'cryptojs': ['crypto-js'],
          'naiveui': ['naive-ui'],
          // echarts 仅被快速开始页与 LLM 统计使用，独立成块后不再随页面 chunk
          // 重复下载，并可跨版本命中浏览器缓存。
          'echarts': ['echarts', 'vue-echarts'],
          // vue-flow 是工作流画布的核心依赖，单独拆出可与画布逻辑并行下载。
          'vueflow': [
            '@vue-flow/core',
            '@vue-flow/background',
            '@vue-flow/controls',
            '@vue-flow/minimap',
            '@dagrejs/dagre'
          ]
        }
      }

    }
  },
  define: {
    'import.meta.env.VITE_APP_VERSION': JSON.stringify(appVersion)
  },
  optimizeDeps: {
    esbuildOptions: {
      plugins: [importMetaUrlPlugin],
    },
    include: [
        'vscode/localExtensionHost',
        'vscode-textmate',
        'vscode-oniguruma'
    ]
  },
  worker: {
    format: "es"
  }
})
