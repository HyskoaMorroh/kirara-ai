<script setup lang="ts">
import { NCard, NSpace, NText, NRadioGroup, NRadioButton, NIcon } from 'naive-ui'
import { SunnyOutline, MoonOutline, DesktopOutline } from '@vicons/ionicons5'
import { useThemeStore } from '@/stores/theme'
import { DEFAULT_THEME_SHAPE, getRadii } from '@/theme/palettes'
import type { ThemeMode } from '@/theme/palettes'

const themeStore = useThemeStore()

const modeOptions: { value: ThemeMode; label: string; icon: any }[] = [
  { value: 'system', label: '跟随系统', icon: DesktopOutline },
  { value: 'light', label: '浅色', icon: SunnyOutline },
  { value: 'dark', label: '深色', icon: MoonOutline }
]

// 预览图使用色板自身的色值绘制，切换模式时同步反映浅色/深色两套取值
const previewSeed = (paletteKey: string) => {
  const palette = themeStore.palettes.find((item) => item.key === paletteKey)
  return palette ? palette[themeStore.scheme] : themeStore.seed
}

// 每个色板还有自己的圆角性格，预览里一并体现，避免“看起来只是换了颜色”
const previewShape = (paletteKey: string) => ({
  ...DEFAULT_THEME_SHAPE,
  ...(previewSeed(paletteKey).shape ?? {})
})

// 预览缩略图只有 84px 高，直接套用色板的完整阶梯会显得过圆；这里按 0.5 倍
// 重新生成一套阶梯，既保留“哪个色板更方正”的差异，又与缩略图尺寸相称。
const previewRadii = (paletteKey: string) =>
  getRadii(previewShape(paletteKey).radiusScale * 0.5)
</script>

<template>
  <n-card title="外观主题" class="settings-card">
    <div style="margin-bottom: 16px">
      <n-text>
        选择界面明暗模式与配色方案。设置保存在本地浏览器中，立即生效且刷新后保留。
      </n-text>
    </div>

    <div class="appearance-section">
      <div class="section-label">明暗模式</div>
      <n-radio-group
        :value="themeStore.mode"
        name="theme-mode"
        @update:value="themeStore.setMode"
      >
        <n-radio-button v-for="option in modeOptions" :key="option.value" :value="option.value">
          <div class="mode-option">
            <n-icon size="16">
              <component :is="option.icon" />
            </n-icon>
            <span>{{ option.label }}</span>
          </div>
        </n-radio-button>
      </n-radio-group>
      <div class="section-hint">
        <n-text depth="3">
          跟随系统时会读取操作系统的深色偏好，并在系统切换时自动跟随。
        </n-text>
      </div>
    </div>

    <div class="appearance-section">
      <div class="section-label">配色方案</div>
      <!--
        选项直接来自 theme store 的 palettes，新增色板（高对比、纯黑等）无需改动
        这里即可出现在选择器中；aria-pressed 让屏幕阅读器能读出当前选中项。
      -->
      <div class="palette-grid">
        <button
          v-for="palette in themeStore.palettes"
          :key="palette.key"
          type="button"
          class="palette-card"
          :class="{ 'palette-card--active': palette.key === themeStore.paletteKey }"
          :aria-pressed="palette.key === themeStore.paletteKey"
          :aria-label="`配色方案：${palette.label}，${palette.description}`"
          @click="themeStore.setPalette(palette.key)"
        >
          <div
            class="palette-preview"
            :style="{
              backgroundColor: previewSeed(palette.key).bg,
              borderColor: previewSeed(palette.key).border,
              borderRadius: previewRadii(palette.key).md
            }"
          >
            <div
              class="preview-sidebar"
              :style="{
                backgroundColor: previewSeed(palette.key).sidebar,
                borderColor: previewSeed(palette.key).border
              }"
            >
              <span
                class="preview-dot"
                :style="{ backgroundColor: previewSeed(palette.key).primary }"
              />
              <span
                class="preview-line"
                :style="{ backgroundColor: previewSeed(palette.key).textTertiary }"
              />
              <span
                class="preview-line"
                :style="{ backgroundColor: previewSeed(palette.key).textTertiary }"
              />
            </div>
            <div class="preview-body">
              <div
                class="preview-node"
                :style="{
                  backgroundColor: previewSeed(palette.key).card,
                  borderColor: previewSeed(palette.key).border,
                  borderRadius: previewRadii(palette.key).sm
                }"
              >
                <span
                  class="preview-node-header"
                  :style="{ backgroundColor: previewSeed(palette.key).nodeHeader }"
                />
                <span
                  class="preview-line preview-line--wide"
                  :style="{ backgroundColor: previewSeed(palette.key).textTertiary }"
                />
              </div>
              <div class="preview-swatches">
                <span :style="{ backgroundColor: previewSeed(palette.key).primary }" />
                <span :style="{ backgroundColor: previewSeed(palette.key).success }" />
                <span :style="{ backgroundColor: previewSeed(palette.key).warning }" />
                <span :style="{ backgroundColor: previewSeed(palette.key).error }" />
              </div>
            </div>
          </div>
          <div class="palette-meta">
            <div class="palette-name">{{ palette.label }}</div>
            <div class="palette-desc">{{ palette.description }}</div>
          </div>
        </button>
      </div>
    </div>
  </n-card>
</template>

<style scoped>
.settings-card {
  max-width: 800px;
  margin: 0 auto;
}

/* 尺寸改用设计令牌（取值与原字面量一致，仅换成令牌以便全局统一调节） */
.appearance-section {
  margin-bottom: var(--space-6);
}

.appearance-section:last-child {
  margin-bottom: 0;
}

.section-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
  margin-bottom: 10px;
}

.section-hint {
  margin-top: var(--space-2);
  font-size: var(--font-size-sm);
}

.mode-option {
  display: flex;
  align-items: center;
  gap: 6px;
}

.palette-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-3);
}

.palette-card {
  padding: 10px;
  border: 2px solid var(--border-color);
  border-radius: var(--radius-md);
  background-color: var(--card-bg-color);
  cursor: pointer;
  text-align: left;
  transition: all var(--transition-duration) var(--transition-timing-function);
  font: inherit;
  color: inherit;
}

.palette-card:hover {
  border-color: var(--primary-color-hover);
  box-shadow: var(--box-shadow);
}

.palette-card--active {
  border-color: var(--primary-color);
  box-shadow: var(--box-shadow);
}

.palette-card:focus-visible {
  outline: 2px solid var(--primary-color);
  outline-offset: 2px;
}

.palette-preview {
  display: flex;
  gap: 6px;
  height: 84px;
  padding: 6px;
  border: 1px solid;
  /* 实际取值由模板里的 previewRadii 内联覆盖（每个色板不同），这里是兜底 */
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.preview-sidebar {
  width: 30%;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 6px 5px;
  border-right: 1px solid;
  /* 例外：缩略图内的迷你侧栏仅 25px 宽，属发丝级装饰，套用阶梯会糊成一团 */
  border-radius: 3px;
}

.preview-dot {
  width: 10px;
  height: 10px;
  /* 例外：正圆，语义上等价于 pill，但 50% 对 10px 方块更精确 */
  border-radius: 50%;
}

.preview-line {
  height: 4px;
  width: 80%;
  /* 例外：4px 高的示意线条，发丝级装饰 */
  border-radius: 2px;
  opacity: 0.55;
}

.preview-line--wide {
  width: 90%;
}

.preview-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 4px 2px;
}

.preview-node {
  border: 1px solid;
  /* 实际取值由模板里的 previewRadii 内联覆盖（每个色板不同），这里是兜底 */
  border-radius: var(--radius-xs);
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.preview-node-header {
  display: block;
  height: 8px;
  /* 例外：8px 高的示意色条，发丝级装饰 */
  border-radius: 2px;
}

.preview-swatches {
  display: flex;
  gap: 4px;
}

.preview-swatches span {
  width: 12px;
  height: 6px;
  /* 例外：6px 高的迷你色样，发丝级装饰 */
  border-radius: 2px;
}

.palette-meta {
  margin-top: var(--space-2);
}

.palette-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-color);
}

.palette-desc {
  font-size: var(--font-size-sm);
  color: var(--text-color-secondary);
  line-height: 1.4;
  margin-top: 2px;
}
</style>
