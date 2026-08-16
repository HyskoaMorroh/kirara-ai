import { defineStore } from 'pinia'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { DEFAULT_PALETTE_KEY, getPalette, getRadiiForSeed, getShape, palettes } from '@/theme/palettes'
import type { ThemeMode, ThemeScheme, ThemeSeed } from '@/theme/palettes'
import { getBrowserLocalStorage, readStorageItem, writeStorageItem } from '@/utils/safe-storage'

const MODE_STORAGE_KEY = 'themeMode'
const PALETTE_STORAGE_KEY = 'themePalette'

/** 连当前色板主色都无法解析时的最后兜底（中性灰，不会给界面染上额外色相） */
const FALLBACK_RGB_TRIPLET = '128, 128, 128'

/**
 * 把色板注入为 CSS 变量。
 *
 * 变量名沿用 assets/main.css 里已有的命名（--primary-color、--bg-color 等），
 * 因此所有既有组件样式不需要改动即可跟随主题变化；新增的 --node-* / --canvas-*
 * 等变量用于工作流画布这类原先写死颜色的区域。
 */
const applySeed = (seed: ThemeSeed, scheme: ThemeScheme) => {
  const root = document.documentElement
  const set = (name: string, value: string) => root.style.setProperty(name, value)

  set('--primary-color', seed.primary)
  set('--primary-color-hover', seed.primaryHover)
  set('--primary-color-pressed', seed.primaryPressed)
  set('--primary-color-rgb', hexToRgbTriplet(seed.primary))

  set('--success-color', seed.success)
  set('--warning-color', seed.warning)
  set('--error-color', seed.error)
  set('--info-color', seed.info)
  // 与 --primary-color-rgb 同理：既有样式用 rgba(var(--success-color-rgb), .1)
  // 之类的写法做浅色底，但这些变量此前缺失，渐变底色一直没生效。
  set('--success-color-rgb', hexToRgbTriplet(seed.success, seed.primary))
  set('--warning-color-rgb', hexToRgbTriplet(seed.warning, seed.primary))
  set('--error-color-rgb', hexToRgbTriplet(seed.error, seed.primary))
  set('--info-color-rgb', hexToRgbTriplet(seed.info, seed.primary))

  set('--bg-color', seed.bg)
  set('--background-color', seed.bg)
  set('--card-bg-color', seed.card)
  // 既有样式里存在 rgba(var(--card-bg-color-rgb), 0.8) 的写法，但该变量此前
  // 从未定义，导致这些半透明底色整体失效。这里补齐它。
  set('--card-bg-color-rgb', hexToRgbTriplet(seed.card, seed.primary))
  set('--sidebar-bg-color', seed.sidebar)
  set('--panel-bg-color', seed.panel)
  set('--elevated-bg-color', seed.elevated)

  set('--text-color', seed.text)
  set('--text-primary', seed.text)
  set('--text-color-secondary', seed.textSecondary)
  set('--text-secondary', seed.textSecondary)
  set('--text-color-tertiary', seed.textTertiary)

  set('--border-color', seed.border)
  set('--divider-color', seed.divider)

  set('--canvas-bg-color', seed.canvas)
  set('--canvas-dot-color', seed.canvasDot)

  set('--node-bg-start', seed.nodeStart)
  set('--node-bg-end', seed.nodeEnd)
  set('--node-header-bg', seed.nodeHeader)
  set('--node-border-color', seed.nodeBorder)
  set('--node-muted-bg', seed.nodeMuted)

  set('--code-bg-color', seed.code)
  set('--code-text-color', seed.codeText)
  set('--input-bg-color', seed.input)

  set('--box-shadow', seed.shadow)
  set('--box-shadow-hover', seed.shadowHover)

  // 文本专用的 AA 变体：色板未显式给出时退回同名填充色，行为与改动前一致。
  // 约定：填充/描边用 --*-color，文字用 --*-color-text（详见 palettes.ts 注释）。
  set('--primary-color-text', seed.primaryText ?? seed.primary)
  set('--success-color-text', seed.successText ?? seed.success)
  set('--warning-color-text', seed.warningText ?? seed.warning)
  set('--error-color-text', seed.errorText ?? seed.error)
  set('--info-color-text', seed.infoText ?? seed.info)
  set('--text-color-tertiary-text', seed.textTertiaryText ?? seed.textTertiary)

  // 每个色板自己的版式性格：整套圆角阶梯由 shape.radiusScale 缩放得出，
  // CSS（--radius-*）与 naive-ui 读的是同一个 getRadiiForSeed 结果。
  // --border-radius* 是历史别名，在 main.css 里已指向 --radius-*，但那里是
  // var() 引用，运行时被这里的具体值覆盖后仍需同步，故一并写入。
  const shape = getShape(seed)
  const radii = getRadiiForSeed(seed)
  set('--radius-xs', radii.xs)
  set('--radius-sm', radii.sm)
  set('--radius-md', radii.md)
  set('--radius-lg', radii.lg)
  set('--radius-xl', radii.xl)
  set('--radius-pill', radii.pill)
  set('--border-radius', radii.md)
  set('--border-radius-small', radii.sm)
  set('--border-radius-large', radii.lg)
  set('--border-radius-pill', radii.pill)
  set('--font-size-base', shape.fontSize)

  /*
   * naive-ui 内部变量命名空间（--n-*）本应只由 NConfigProvider 在其子树上注入，
   * 这里从外部写到 :root 属于兼容层：项目里已有 20 余处组件样式直接读 --n-*，
   * 其中多数变量从未被定义，深色下会退化成写死的浅色。
   *
   * ⚠ 版本脆弱性：这些名字是 naive-ui 的实现细节，升级大版本时需复查含义是否
   * 变化。之所以不能全部改走 App.vue 的 themeOverrides：themeOverrides 只作用于
   * provider 子树内的 naive-ui 组件，无法为 scoped CSS 里的 var(--n-*) 提供值。
   * 新代码请使用 --border-color / --text-color-* 等项目自有变量。
   */
  set('--n-border-color', seed.border)
  set('--n-body-color', seed.bg)
  set('--n-card-color', seed.card)
  set('--n-color', seed.card)
  set('--n-color-modal', seed.card)
  set('--n-primary-color', seed.primary)
  set('--n-text-color', seed.text)
  set('--n-text-color-2', seed.textSecondary)
  set('--n-text-color-3', seed.textTertiary)
  set('--n-text-color-rgb', hexToRgbTriplet(seed.text, seed.primary))
  // 消费端存在末尾多一个连字符的拼写错误变量，一并赋值以免它回退到无值
  set('--n-text-color-', seed.text)

  // 供 .dark 选择器与第三方样式（vue-flow / monaco）判断明暗
  root.classList.toggle('dark', scheme === 'dark')
  root.dataset.theme = scheme
  root.style.colorScheme = scheme
}

/**
 * #rrggbb -> "r, g, b"，供 rgba(var(--primary-color-rgb), a) 这类既有写法使用
 *
 * 解析失败时退回 fallback（调用方传入当前色板的主色）而不是写死的经典蓝：
 * 后者会让松林绿或纯黑用户在某个色值拼错时莫名染上一层蓝色调，且完全无声。
 */
const hexToRgbTriplet = (hex: string, fallback?: string): string => {
  const value = hex.trim()

  // 色板里的部分层级（如 panel）直接给的是 rgb()/rgba() 字符串
  const rgbMatch = value.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i)
  if (rgbMatch) {
    return `${rgbMatch[1]}, ${rgbMatch[2]}, ${rgbMatch[3]}`
  }

  let color = value.replace('#', '')
  if (color.length === 3) {
    color = color[0] + color[0] + color[1] + color[1] + color[2] + color[2]
  }
  const r = parseInt(color.substring(0, 2), 16)
  const g = parseInt(color.substring(2, 4), 16)
  const b = parseInt(color.substring(4, 6), 16)
  if ([r, g, b].some((channel) => Number.isNaN(channel))) {
    console.warn(`[theme] 无法解析颜色 "${hex}"，已退回 ${fallback ?? FALLBACK_RGB_TRIPLET}`)
    // fallback 自身也可能非法，因此这里递归一次但不再传 fallback，避免无限回退
    return fallback ? hexToRgbTriplet(fallback) : FALLBACK_RGB_TRIPLET
  }
  return `${r}, ${g}, ${b}`
}

export const useThemeStore = defineStore('theme', () => {
  // 与其他 store 共用 utils/safe-storage，避免各处重复实现 try/catch 读写
  const storage = getBrowserLocalStorage()
  const readStorage = (key: string) => readStorageItem(storage, key)
  const writeStorage = (key: string, value: string) => writeStorageItem(storage, key, value)

  const readMode = (): ThemeMode => {
    const stored = readStorage(MODE_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'light'
  }

  const mode = ref<ThemeMode>(readMode())
  const paletteKey = ref<string>(
    readStorage(PALETTE_STORAGE_KEY) || DEFAULT_PALETTE_KEY
  )

  // 跟随系统时需要监听系统偏好变化
  const systemQuery = window.matchMedia?.('(prefers-color-scheme: dark)')
  const systemPrefersDark = ref(systemQuery?.matches ?? false)
  const handleSystemChange = (event: MediaQueryListEvent) => {
    systemPrefersDark.value = event.matches
  }
  if (systemQuery) {
    if (typeof systemQuery.addEventListener === 'function') {
      systemQuery.addEventListener('change', handleSystemChange)
    } else if (typeof systemQuery.addListener === 'function') {
      // Safari < 14 兜底
      systemQuery.addListener(handleSystemChange)
    }
  }

  // store 被销毁（测试用例逐个建 Pinia 实例、或 HMR 重载）时解绑，避免监听器堆积
  onScopeDispose(() => {
    if (!systemQuery) return
    if (typeof systemQuery.removeEventListener === 'function') {
      systemQuery.removeEventListener('change', handleSystemChange)
    } else if (typeof systemQuery.removeListener === 'function') {
      // Safari < 14 兜底
      systemQuery.removeListener(handleSystemChange)
    }
  })

  /** 实际生效的明暗方案，system 模式下由系统偏好决定 */
  const scheme = computed<ThemeScheme>(() => {
    if (mode.value === 'system') {
      return systemPrefersDark.value ? 'dark' : 'light'
    }
    return mode.value
  })

  const palette = computed(() => getPalette(paletteKey.value))
  const seed = computed<ThemeSeed>(() => palette.value[scheme.value])
  const isDark = computed(() => scheme.value === 'dark')
  /** 当前色板的版式（圆角、字号、控件密度），供 App.vue 生成 themeOverrides */
  const shape = computed(() => getShape(seed.value))
  /** 当前色板的完整圆角阶梯，供 naive-ui themeOverrides 与预览图消费 */
  const radii = computed(() => getRadiiForSeed(seed.value))
  /** 供 Monaco 编辑器使用的主题 ID */
  const monacoTheme = computed(() => seed.value.monaco)

  const setMode = (next: ThemeMode) => {
    mode.value = next
    writeStorage(MODE_STORAGE_KEY, next)
  }

  const setPalette = (next: string) => {
    paletteKey.value = getPalette(next).key
    writeStorage(PALETTE_STORAGE_KEY, paletteKey.value)
  }

  const toggleScheme = () => {
    setMode(isDark.value ? 'light' : 'dark')
  }

  watch(seed, (value) => applySeed(value, scheme.value), { immediate: true })

  return {
    // 状态
    mode,
    paletteKey,
    // 计算属性
    scheme,
    palette,
    palettes,
    seed,
    isDark,
    shape,
    radii,
    monacoTheme,
    // 动作
    setMode,
    setPalette,
    toggleScheme
  }
})
