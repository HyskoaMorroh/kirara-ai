<script setup lang="ts">
import { RouterView } from 'vue-router'
import {
  NLoadingBarProvider,
  NDialogProvider,
  NMessageProvider,
  NConfigProvider,
  lightTheme,
  darkTheme,
  NModalProvider,
  useLoadingBar
} from 'naive-ui'
import HelloWorld from './components/HelloWorld.vue'
import AppLayout from './layouts/AppLayout.vue'
import { computed } from 'vue'
import { useThemeStore } from '@/stores/theme'
import hljs from 'highlight.js/lib/core'
import json from 'highlight.js/lib/languages/json'

hljs.registerLanguage('json', json)

const themeStore = useThemeStore()

// 圆角、字号、控件尺寸等版式约定原先是一个冻结常量，导致切换色板只是换色。
// 现在由色板自己声明 shape（缺省项回落到 DEFAULT_THEME_SHAPE，与旧常量逐项
// 相同），theme store 同时把这些值写成 --border-radius* CSS 变量，
// 因此 naive-ui 与手写 CSS 消费的是同一套圆角，不会再各说各话。
//
// 圆角部分改为直接消费 store 的 radii（同一个 getRadiiForSeed 结果）：
// 语义映射与 main.css 的注释一致——控件走 sm、卡片/弹窗走 lg、标签走 pill。
const themeShape = computed(() => {
  const shape = themeStore.shape
  const radii = themeStore.radii
  return {
    common: {
      borderRadius: radii.md,
      borderRadiusSmall: radii.sm,
      fontSize: shape.fontSize
    },
    Button: {
      borderRadius: radii.sm,
      heightMedium: shape.controlHeight,
      fontWeight: shape.fontWeight
    },
    Card: {
      borderRadius: radii.lg
    },
    Dialog: {
      borderRadius: radii.lg
    },
    Input: {
      borderRadius: radii.sm,
      heightMedium: shape.controlHeight
    },
    // 标签本就是胶囊形，与 main.css 的 .n-tag 规则保持一致，避免两侧不符
    Tag: {
      borderRadius: radii.pill
    }
  }
})

const theme = computed(() => {
  const seed = themeStore.seed
  const shape = themeShape.value
  return {
    common: {
      ...shape.common,
      primaryColor: seed.primary,
      primaryColorHover: seed.primaryHover,
      primaryColorPressed: seed.primaryPressed,
      primaryColorSuppl: seed.primaryHover,
      successColor: seed.success,
      warningColor: seed.warning,
      errorColor: seed.error,
      infoColor: seed.info,
      bodyColor: seed.bg,
      cardColor: seed.card,
      modalColor: seed.card,
      popoverColor: seed.elevated,
      tableColor: seed.card,
      inputColor: seed.input,
      textColorBase: seed.text,
      textColor1: seed.text,
      textColor2: seed.textSecondary,
      textColor3: seed.textTertiary,
      borderColor: seed.border,
      dividerColor: seed.divider,
      hoverColor: `rgba(${themeStore.isDark ? '255, 255, 255' : '0, 0, 0'}, 0.06)`
    },
    Button: {
      ...shape.Button,
      // 次要按钮的文字用 AA 变体，主色本身在浅色下作为文字常常不足 4.5:1
      textColor: seed.primaryText ?? seed.primary
    },
    Card: { ...shape.Card },
    Dialog: { ...shape.Dialog },
    Input: { ...shape.Input },
    Tag: { ...shape.Tag }
  }
})

// 深色方案下切换 naive-ui 的内建基础主题，未被 themeOverrides 覆盖的
// 组件（滚动条、下拉、日期选择器等）也能得到正确的底色。
const baseTheme = computed(() => (themeStore.isDark ? darkTheme : null))
</script>

<template>
  <n-config-provider :theme="baseTheme" :theme-overrides="theme" abstract :hljs="hljs">
    <n-modal-provider>
      <n-message-provider>
        <n-loading-bar-provider>
          <n-dialog-provider>
            <router-view v-slot="{ Component }">
              <component :is="Component" />
            </router-view>
          </n-dialog-provider>
        </n-loading-bar-provider>
      </n-message-provider>
    </n-modal-provider>
  </n-config-provider>
</template>

<style>
/*
 * 语义色取值以 assets/main.css 为唯一基准（等同 palettes.ts 的 classic 色板）。
 * 此前这里是另一套 iOS 系统色，同一个选择器上两份不同取值，JS 未执行时到底
 * 生效哪一份取决于样式注入顺序，深浅两套都可能与运行时主题不一致。现在改为与
 * main.css 逐值对齐，只保留下面这几个 main.css 未定义的键（背景与文本）。
 * --secondary-color 目前没有任何消费方，仍保留声明，方便后续需要时直接可用。
 */
:root {
  --primary-color: #007aff;
  --secondary-color: #5856d6;
  --success-color: #18a058;
  --warning-color: #f0a020;
  --error-color: #d03050;
  --background-color: #f2f2f7;
  --text-primary: #000000;
  --text-secondary: #8e8e93;
}

/*
 * 深色对应值。上面这组是 iOS 系统色，被 html/body 直接消费；此前没有 .dark
 * 版本，JS 未执行或 store 初始化失败时深色用户会看到浅底深字。
 * --secondary-color 目前没有消费方，仍两套都保留，方便后续需要时直接可用。
 * 语义色同样与 main.css 的 .dark 区块（classic.dark）逐值对齐。
 */
.dark {
  --primary-color: #5b8dff;
  --secondary-color: #5e5ce6;
  --success-color: #63e2b7;
  --warning-color: #f3a769;
  --error-color: #e88080;
  --background-color: #161719;
  --text-primary: #ffffff;
  --text-secondary: #98989d;
}

/* 无 JS 兜底：data-theme 缺失说明明暗尚未被任何一方决定，此时才跟随系统 */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
    --primary-color: #5b8dff;
    --secondary-color: #5e5ce6;
    --success-color: #63e2b7;
    --warning-color: #f3a769;
    --error-color: #e88080;
    --background-color: #161719;
    --text-primary: #ffffff;
    --text-secondary: #98989d;
  }
}

html,
body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif;
  background-color: var(--background-color);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

#app {
  width: 100%;
  height: 100%;
}

/* 全局过渡动画 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateX(20px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}
</style>
