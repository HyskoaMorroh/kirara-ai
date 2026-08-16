/**
 * 主题色板定义。
 *
 * 每个色板提供 light / dark 两套语义化色值（ThemeSeed），运行时由 theme store
 * 注入到 document.documentElement 的 CSS 变量上，并派生出 naive-ui 的
 * themeOverrides。组件样式只消费 CSS 变量，因此新增色板无需改动任何组件。
 *
 * ⚠ 同步约定（THEME_BOOT_TABLE）：index.html 里的首屏防闪脚本手抄了每个色板的
 * bg / textTertiary 两个值（它不能 import 本文件，否则会把入口 chunk 拉进首屏，
 * 抵消防闪优化）。修改任何色板的 light.bg / light.textTertiary / dark.bg /
 * dark.textTertiary，或新增、删除色板时，必须同步更新 index.html 中
 * THEME_BOOT_TABLE 标记注释之后的对象字面量。单测会校验两处是否一致。
 */

export type ThemeMode = 'system' | 'light' | 'dark'
export type ThemeScheme = 'light' | 'dark'

/**
 * 色板的“版式性格”。
 *
 * 此前 App.vue 用一个冻结的 themeShape 常量给所有色板套同一套圆角与密度，
 * 切换色板只是换色，缺少辨识度。这里把版式也纳入色板定义：默认值与旧常量
 * 逐项一致，因此不填 shape 的色板行为完全不变。
 */
export interface ThemeShape {
  /** 通用圆角，对应 naive-ui common.borderRadius */
  borderRadius: string
  /** 小圆角（输入框、按钮等），对应 --border-radius-small */
  borderRadiusSmall: string
  /** 大圆角（卡片、弹窗等），对应 --border-radius-large */
  borderRadiusLarge: string
  /** 基础字号，对应 naive-ui common.fontSize */
  fontSize: string
  /** 中号控件高度，决定整体密度 */
  controlHeight: string
  /** 正文字重档位（naive-ui Button.fontWeight 等） */
  fontWeight: string
  /**
   * 圆角阶梯的整体缩放系数（1 = 基准梯 4/8/12/16/24px）。
   * 色板的“形状性格”由这一个数字决定：< 1 更方正（工程感、高对比），
   * > 1 更圆润（阅读向）。三个历史圆角字段全部由它派生，不再手写。
   */
  radiusScale: number
}

/**
 * 圆角阶梯的基准梯（px），与 assets/main.css 的 --radius-* 一一对应。
 * 取值刻意落在项目中出现频次最高的 12px 与 8px 上，使既有界面换成令牌后
 * 视觉基本不变。语义分工详见 main.css 中 --radius-* 的注释块。
 */
export const RADIUS_BASE = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24
} as const

/** 完全圆化档不参与缩放：胶囊、状态徽标、头像在任何色板下都必须是整圆 */
export const RADIUS_PILL = '999px'

/** 缩放后仍需保留可见弧度，故设 2px 下限（1px 在多数屏幕上与直角无异） */
const MIN_RADIUS = 2

/** 一套完整的圆角阶梯取值，六档与 --radius-* 同名 */
export interface ThemeRadii {
  xs: string
  sm: string
  md: string
  lg: string
  xl: string
  pill: string
}

/** 按缩放系数生成整套阶梯；CSS 变量与 naive-ui themeOverrides 共用它，二者不会再各说各话 */
export const getRadii = (scale: number): ThemeRadii => {
  const step = (base: number) => `${Math.max(MIN_RADIUS, Math.round(base * scale))}px`
  return {
    xs: step(RADIUS_BASE.xs),
    sm: step(RADIUS_BASE.sm),
    md: step(RADIUS_BASE.md),
    lg: step(RADIUS_BASE.lg),
    xl: step(RADIUS_BASE.xl),
    pill: RADIUS_PILL
  }
}

/**
 * 把缩放系数展开成 ThemeShape 里的圆角字段。
 * 历史字段名（borderRadius / borderRadiusSmall / borderRadiusLarge）全部保留，
 * 只是不再逐个手写字面量——它们分别等于阶梯的 md / sm / lg 档，
 * 因此“色板声明的圆角”与“CSS 阶梯”在源头上就是同一份数据。
 */
const radiusShape = (
  scale: number
): Pick<
  ThemeShape,
  'radiusScale' | 'borderRadius' | 'borderRadiusSmall' | 'borderRadiusLarge'
> => {
  const radii = getRadii(scale)
  return {
    radiusScale: scale,
    borderRadius: radii.md,
    borderRadiusSmall: radii.sm,
    borderRadiusLarge: radii.lg
  }
}

/** 旧 App.vue themeShape 的逐项等价值，任何未声明 shape 的色板都退回到它 */
/* 圆角三档改由基准梯（radiusScale = 1）给出：small 仍是 8px，
   通用档 10px → 12px、大档 12px → 16px，与全站主力字面量 12px/16px 对齐 */
export const DEFAULT_THEME_SHAPE: ThemeShape = {
  ...radiusShape(1),
  fontSize: '14px',
  controlHeight: '38px',
  fontWeight: '500'
}

export interface ThemeSeed {
  /** 主色与其交互态 */
  primary: string
  primaryHover: string
  primaryPressed: string
  /** 状态色 */
  success: string
  warning: string
  error: string
  info: string
  /** 背景层级：页面 / 卡片 / 侧栏 / 半透明浮层 / 悬浮元素 */
  bg: string
  card: string
  sidebar: string
  panel: string
  elevated: string
  /** 文本层级 */
  text: string
  textSecondary: string
  textTertiary: string
  /** 描边与分割线 */
  border: string
  divider: string
  /** 工作流画布底色与网格点 */
  canvas: string
  canvasDot: string
  /** 工作流节点：渐变起止、标题栏、描边、弱化底 */
  nodeStart: string
  nodeEnd: string
  nodeHeader: string
  nodeBorder: string
  nodeMuted: string
  /** 代码块 */
  code: string
  codeText: string
  /** 输入控件底色 */
  input: string
  /** 阴影 */
  shadow: string
  shadowHover: string
  /**
   * 文本专用的无障碍变体（可选，缺省时回落到同名的填充色）。
   *
   * 用法约定：
   * - primary / success / warning / error / info：用于**填充、描边、图标底**等
   *   非文本场景，色相好看但作为正文色常常不足 4.5:1。
   * - *Text 系列：用于**文字**（提示语、状态标签文案、链接文本），已按各色板
   *   自身的页面底色调到 WCAG AA（≥4.5:1）。
   * 换句话说：background/border 用原键，color 用 *Text 键。
   */
  primaryText?: string
  successText?: string
  warningText?: string
  errorText?: string
  infoText?: string
  /** 三级文本的 AA 变体；原 textTertiary 仅适合做占位符、禁用态等装饰性文字 */
  textTertiaryText?: string
  /** 该色板的版式性格，缺省为 DEFAULT_THEME_SHAPE */
  shape?: Partial<ThemeShape>
  /** 对应的 Monaco 编辑器内置主题 ID */
  monaco: string
}

export interface Palette {
  key: string
  label: string
  description: string
  light: ThemeSeed
  dark: ThemeSeed
}

/**
 * 各色板的“形状性格”只需声明一个 radiusScale，整套阶梯（xs→xl）随之缩放，
 * 因此不会出现“某档忘了改”导致的梯度断裂。系数保持了原有色板的相对关系：
 * 石墨最方正、经典居中、松林最圆润；新增的高对比走到最方正一端。
 */

/** 经典：Apple 系统蓝 + 中性灰白，圆润但不夸张，作为默认色板 */
/* 基准梯（radiusScale = 1），即 4/8/12/16/24 本身 */
const classicShape: Partial<ThemeShape> = {
  ...radiusShape(1),
  fontSize: '14px',
  controlHeight: '38px',
  fontWeight: '500'
}

/** 石墨：GitHub 的工程感——更方正的圆角、更紧凑的行高与控件 */
/* 0.5 倍梯：2/4/6/8/12，通用档 6px 与原值完全一致 */
const graphiteShape: Partial<ThemeShape> = {
  ...radiusShape(0.5),
  fontSize: '14px',
  controlHeight: '34px',
  fontWeight: '600'
}

/** 午夜：编辑器气质，圆角适中、字号略小以容纳更多信息 */
/* 0.75 倍梯：3/6/9/12/18，介于石墨与经典之间 */
const midnightShape: Partial<ThemeShape> = {
  ...radiusShape(0.75),
  fontSize: '13px',
  controlHeight: '36px',
  fontWeight: '500'
}

/** 森林：阅读优先，最圆润的边角与最宽松的控件高度 */
/* 1.25 倍梯：5/10/15/20/30，全站最圆的一档 */
const forestShape: Partial<ThemeShape> = {
  ...radiusShape(1.25),
  fontSize: '15px',
  controlHeight: '40px',
  fontWeight: '500'
}

/** 高对比：无装饰、可读性优先，圆角克制、控件放大以便点击 */
/* 0.375 倍梯：2/3/5/6/9。弱视用户依赖清晰的矩形边界定位控件，
   大圆角会削弱这一线索，故这里刻意取阶梯的最方正端 */
const contrastShape: Partial<ThemeShape> = {
  ...radiusShape(0.375),
  fontSize: '15px',
  controlHeight: '42px',
  fontWeight: '600'
}

/** 纯黑：OLED 上以形状而非分割线区分层级，故圆角偏大 */
/* 1.125 倍梯：5/9/14/18/27。纯黑底上 1px 描边几乎不可见，层级只能靠形状
   表达，因此比经典再圆一档，但不到森林那么松 */
const oledShape: Partial<ThemeShape> = {
  ...radiusShape(1.125),
  fontSize: '14px',
  controlHeight: '38px',
  fontWeight: '500'
}

/**
 * 经典：保留 3.3.0a5 原有的蓝白配色，作为默认色板。
 *
 * 主色沿用旧版 naive-ui themeOverrides 的 #007AFF（Apple 系统蓝）：a5 之前的
 * 版本一直是这个值，中途被改成 #4080ff 后所有 naive-ui 控件的色相都偏移了，
 * 这里恢复回来。其余浅色层级（背景、文本、描边）与旧版逐项一致。
 */
const classic: Palette = {
  key: 'classic',
  label: '经典蓝',
  description: '项目原生蓝白配色，主色与旧版 #007AFF 一致',
  light: {
    primary: '#007aff',
    primaryHover: '#3395ff',
    primaryPressed: '#0062cc',
    success: '#18a058',
    warning: '#f0a020',
    error: '#d03050',
    info: '#5c6ac4',
    bg: '#f5f7fa',
    card: '#ffffff',
    sidebar: '#ffffff',
    panel: 'rgba(255, 255, 255, 0.8)',
    elevated: '#ffffff',
    text: '#333639',
    textSecondary: '#606266',
    textTertiary: '#909399',
    border: '#e5e7eb',
    divider: '#f3f4f6',
    canvas: '#f5f7fa',
    canvasDot: '#aaaaaa',
    nodeStart: '#f8f9fa',
    nodeEnd: '#ffffff',
    nodeHeader: '#f0f0f0',
    nodeBorder: 'rgba(0, 0, 0, 0.06)',
    nodeMuted: '#f9fafb',
    code: '#f3f4f6',
    codeText: '#1f2937',
    input: '#ffffff',
    shadow: '0 4px 12px rgba(0, 0, 0, 0.08)',
    shadowHover: '0 6px 16px rgba(0, 0, 0, 0.12)',
    primaryText: '#006ce2',
    successText: '#138046',
    warningText: '#9b640a',
    errorText: '#d03050',
    infoText: '#5b69c3',
    textTertiaryText: '#6d7077',
    shape: classicShape,
    monaco: 'vs'
  },
  dark: {
    primary: '#5b8dff',
    primaryHover: '#7ba4ff',
    primaryPressed: '#4275e0',
    success: '#63e2b7',
    warning: '#f3a769',
    error: '#e88080',
    info: '#8b95e0',
    bg: '#161719',
    card: '#1e2023',
    sidebar: '#1a1c1f',
    panel: 'rgba(30, 32, 35, 0.86)',
    elevated: '#26282c',
    text: '#dfe2e6',
    textSecondary: '#a8adb5',
    textTertiary: '#7c828b',
    border: '#31343a',
    divider: '#282b30',
    canvas: '#131416',
    canvasDot: '#3a3d43',
    nodeStart: '#24262a',
    nodeEnd: '#1d1f22',
    nodeHeader: '#2b2e33',
    nodeBorder: 'rgba(255, 255, 255, 0.08)',
    nodeMuted: '#222427',
    code: '#1a1c1f',
    codeText: '#d4d7dc',
    input: '#22252a',
    shadow: '0 4px 12px rgba(0, 0, 0, 0.45)',
    shadowHover: '0 6px 18px rgba(0, 0, 0, 0.6)',
    primaryText: '#5b8dff',
    successText: '#63e2b7',
    warningText: '#f3a769',
    errorText: '#e88080',
    infoText: '#8b95e0',
    textTertiaryText: '#8c9199',
    shape: classicShape,
    monaco: 'vs-dark'
  }
}

/** 石墨：GitHub Dark 风格的中性灰蓝，长时间阅读时对比度稳定 */
const graphite: Palette = {
  key: 'graphite',
  label: '石墨灰',
  description: 'GitHub 风格中性灰蓝，弱饱和、久看不累',
  light: {
    primary: '#0969da',
    primaryHover: '#218bff',
    primaryPressed: '#0757ba',
    success: '#1a7f37',
    warning: '#9a6700',
    error: '#cf222e',
    info: '#6639ba',
    bg: '#f6f8fa',
    card: '#ffffff',
    sidebar: '#ffffff',
    panel: 'rgba(255, 255, 255, 0.86)',
    elevated: '#ffffff',
    text: '#1f2328',
    textSecondary: '#59636e',
    textTertiary: '#818b98',
    border: '#d1d9e0',
    divider: '#eaeef2',
    canvas: '#f6f8fa',
    canvasDot: '#afb8c1',
    nodeStart: '#ffffff',
    nodeEnd: '#f6f8fa',
    nodeHeader: '#eaeef2',
    nodeBorder: '#d1d9e0',
    nodeMuted: '#f6f8fa',
    code: '#eff2f5',
    codeText: '#1f2328',
    input: '#ffffff',
    shadow: '0 1px 3px rgba(31, 35, 40, 0.12)',
    shadowHover: '0 3px 8px rgba(31, 35, 40, 0.18)',
    primaryText: '#0969da',
    successText: '#1a7f37',
    warningText: '#986600',
    errorText: '#cf222e',
    infoText: '#6639ba',
    textTertiaryText: '#67717f',
    shape: graphiteShape,
    monaco: 'vs'
  },
  dark: {
    primary: '#4493f8',
    primaryHover: '#6cb0ff',
    primaryPressed: '#2f7ce0',
    success: '#3fb950',
    warning: '#d29922',
    error: '#f85149',
    info: '#a371f7',
    bg: '#0d1117',
    card: '#151b23',
    sidebar: '#010409',
    panel: 'rgba(21, 27, 35, 0.88)',
    elevated: '#1c232b',
    text: '#e6edf3',
    textSecondary: '#9198a1',
    textTertiary: '#6e7681',
    border: '#3d444d',
    divider: '#21262d',
    canvas: '#0d1117',
    canvasDot: '#30363d',
    nodeStart: '#1c232b',
    nodeEnd: '#151b23',
    nodeHeader: '#21262d',
    nodeBorder: '#3d444d',
    nodeMuted: '#161b22',
    code: '#0d1117',
    codeText: '#e6edf3',
    input: '#0d1117',
    shadow: '0 4px 12px rgba(1, 4, 9, 0.55)',
    shadowHover: '0 6px 18px rgba(1, 4, 9, 0.7)',
    primaryText: '#4493f8',
    successText: '#3fb950',
    warningText: '#d29922',
    errorText: '#f85149',
    infoText: '#a371f7',
    textTertiaryText: '#848b96',
    shape: graphiteShape,
    monaco: 'vs-dark'
  }
}

/** 午夜：One Dark 风格的偏紫深蓝，主色偏青，节点区分度高 */
const midnight: Palette = {
  key: 'midnight',
  label: '午夜蓝',
  description: 'One Dark 风格深蓝紫，青色主调，节点辨识度高',
  light: {
    primary: '#2f7d95',
    primaryHover: '#3d97b2',
    primaryPressed: '#25667a',
    success: '#3f8f5f',
    warning: '#c1811f',
    error: '#c0483f',
    info: '#6b62c4',
    bg: '#eef1f6',
    card: '#ffffff',
    sidebar: '#f7f9fc',
    panel: 'rgba(255, 255, 255, 0.85)',
    elevated: '#ffffff',
    text: '#2c313a',
    textSecondary: '#5a6270',
    textTertiary: '#8a92a0',
    border: '#d7dce5',
    divider: '#e8ecf2',
    canvas: '#eef1f6',
    canvasDot: '#b3bac7',
    nodeStart: '#ffffff',
    nodeEnd: '#f4f6fa',
    nodeHeader: '#e6ebf2',
    nodeBorder: '#d7dce5',
    nodeMuted: '#f4f6fa',
    code: '#eaeef4',
    codeText: '#2c313a',
    input: '#ffffff',
    shadow: '0 4px 12px rgba(44, 49, 58, 0.1)',
    shadowHover: '0 6px 16px rgba(44, 49, 58, 0.16)',
    primaryText: '#2c758c',
    successText: '#357850',
    warningText: '#936218',
    errorText: '#ba463d',
    infoText: '#685fc3',
    textTertiaryText: '#656d7c',
    shape: midnightShape,
    monaco: 'vs'
  },
  dark: {
    primary: '#56b6c2',
    primaryHover: '#74c9d4',
    primaryPressed: '#3f98a3',
    success: '#98c379',
    warning: '#e5c07b',
    error: '#e06c75',
    info: '#c678dd',
    bg: '#1e2127',
    card: '#282c34',
    sidebar: '#21252b',
    panel: 'rgba(40, 44, 52, 0.88)',
    elevated: '#31363f',
    text: '#dcdfe4',
    textSecondary: '#9da5b4',
    textTertiary: '#7f8796',
    border: '#3e4451',
    divider: '#2c313a',
    canvas: '#1b1e24',
    canvasDot: '#3e4451',
    nodeStart: '#31363f',
    nodeEnd: '#282c34',
    nodeHeader: '#3a4048',
    nodeBorder: '#3e4451',
    nodeMuted: '#2c313a',
    code: '#21252b',
    codeText: '#dcdfe4',
    input: '#2c313a',
    shadow: '0 4px 12px rgba(16, 18, 22, 0.5)',
    shadowHover: '0 6px 18px rgba(16, 18, 22, 0.66)',
    primaryText: '#56b6c2',
    successText: '#98c379',
    warningText: '#e5c07b',
    errorText: '#e5848b',
    infoText: '#cc85e0',
    textTertiaryText: '#9aa0ac',
    shape: midnightShape,
    monaco: 'vs-dark'
  }
}

/** 森林：Solarized 风格暖调低蓝光，适合夜间与长时间盯屏 */
const forest: Palette = {
  key: 'forest',
  label: '松林绿',
  description: 'Solarized 风格暖调低蓝光，夜间护眼',
  light: {
    primary: '#268bd2',
    primaryHover: '#3d9ee0',
    primaryPressed: '#1f72ac',
    success: '#859900',
    warning: '#b58900',
    error: '#dc322f',
    info: '#6c71c4',
    bg: '#fdf6e3',
    card: '#fffdf6',
    sidebar: '#f7f0dd',
    panel: 'rgba(255, 253, 246, 0.88)',
    elevated: '#fffdf6',
    // 旧值 text:#3f4b52 / textSecondary:#657b83 在暖底（#fdf6e3）上分别只有
    // 8.32 与 4.13 对比度，二级文本不足 AA；这里整体压深一档。
    text: '#33444d',
    textSecondary: '#4d6168',
    textTertiary: '#93a1a1',
    border: '#e3dbc4',
    divider: '#eee6d2',
    canvas: '#fdf6e3',
    canvasDot: '#c8c0a8',
    nodeStart: '#fffdf6',
    nodeEnd: '#f9f2df',
    nodeHeader: '#f0e8d3',
    nodeBorder: '#e3dbc4',
    nodeMuted: '#f7f0dd',
    code: '#f0e8d3',
    codeText: '#3f4b52',
    input: '#fffdf6',
    shadow: '0 4px 12px rgba(101, 123, 131, 0.14)',
    shadowHover: '0 6px 16px rgba(101, 123, 131, 0.2)',
    primaryText: '#2074af',
    successText: '#677600',
    warningText: '#8c6a00',
    errorText: '#d72724',
    infoText: '#6267c0',
    textTertiaryText: '#637171',
    shape: forestShape,
    monaco: 'vs'
  },
  dark: {
    primary: '#4aa3c9',
    primaryHover: '#66b6d8',
    primaryPressed: '#3a86a8',
    success: '#8fa629',
    warning: '#c39a2b',
    error: '#dc5b52',
    info: '#7c81c9',
    bg: '#002b36',
    card: '#073642',
    sidebar: '#00232c',
    panel: 'rgba(7, 54, 66, 0.9)',
    elevated: '#0c414e',
    text: '#c5cfd1',
    textSecondary: '#93a1a1',
    textTertiary: '#7a8a8a',
    border: '#0f4c5c',
    divider: '#083f4c',
    canvas: '#00232c',
    canvasDot: '#1a5464',
    nodeStart: '#0c414e',
    nodeEnd: '#073642',
    nodeHeader: '#0f4c5c',
    nodeBorder: '#155c6e',
    nodeMuted: '#063240',
    code: '#00232c',
    codeText: '#c5cfd1',
    input: '#083f4c',
    shadow: '0 4px 12px rgba(0, 15, 20, 0.5)',
    shadowHover: '0 6px 18px rgba(0, 15, 20, 0.66)',
    primaryText: '#63b0d1',
    successText: '#99b12c',
    warningText: '#cba12d',
    errorText: '#e7908a',
    infoText: '#a0a4d8',
    textTertiaryText: '#9da9a9',
    shape: forestShape,
    monaco: 'vs-dark'
  }
}

/**
 * 高对比：以可读性为唯一目标的无障碍色板。
 *
 * 浅色所有语义色对页面底色 ≥6:1、深色 ≥8:1，均满足 WCAG AA（正文 4.5:1）
 * 并对大号文字达到 AAA；描边刻意用 #767676 以上，保证非文本对比度 ≥3:1。
 */
const contrast: Palette = {
  key: 'contrast',
  label: '高对比',
  description: '为低视力与强光环境准备，全部语义色满足 WCAG AA',
  light: {
    primary: '#0b4fbe',
    primaryHover: '#1160d8',
    primaryPressed: '#083c93',
    success: '#0f6b34',
    warning: '#8a5000',
    error: '#b3001b',
    info: '#4b2ea6',
    bg: '#ffffff',
    card: '#ffffff',
    sidebar: '#f2f2f2',
    panel: 'rgba(255, 255, 255, 0.96)',
    elevated: '#ffffff',
    text: '#000000',
    textSecondary: '#3a3a3a',
    textTertiary: '#595959',
    border: '#767676',
    divider: '#a6a6a6',
    canvas: '#ffffff',
    canvasDot: '#8c8c8c',
    nodeStart: '#ffffff',
    nodeEnd: '#f2f2f2',
    nodeHeader: '#e6e6e6',
    nodeBorder: '#595959',
    nodeMuted: '#f2f2f2',
    code: '#f2f2f2',
    codeText: '#000000',
    input: '#ffffff',
    shadow: '0 0 0 1px #767676',
    shadowHover: '0 0 0 2px #0b4fbe',
    primaryText: '#0b4fbe',
    successText: '#0f6b34',
    warningText: '#8a5000',
    errorText: '#b3001b',
    infoText: '#4b2ea6',
    textTertiaryText: '#595959',
    shape: contrastShape,
    monaco: 'hc-light'
  },
  dark: {
    primary: '#7ab8ff',
    primaryHover: '#a3ceff',
    primaryPressed: '#559fee',
    success: '#5ce6a8',
    warning: '#ffc266',
    error: '#ff8f8f',
    info: '#b9a6ff',
    bg: '#000000',
    card: '#101010',
    sidebar: '#000000',
    panel: 'rgba(16, 16, 16, 0.96)',
    elevated: '#1a1a1a',
    text: '#ffffff',
    textSecondary: '#e0e0e0',
    textTertiary: '#b8b8b8',
    border: '#8f8f8f',
    divider: '#5c5c5c',
    canvas: '#000000',
    canvasDot: '#6b6b6b',
    nodeStart: '#1a1a1a',
    nodeEnd: '#101010',
    nodeHeader: '#262626',
    nodeBorder: '#8f8f8f',
    nodeMuted: '#141414',
    code: '#000000',
    codeText: '#ffffff',
    input: '#101010',
    shadow: '0 0 0 1px #8f8f8f',
    shadowHover: '0 0 0 2px #7ab8ff',
    primaryText: '#7ab8ff',
    successText: '#5ce6a8',
    warningText: '#ffc266',
    errorText: '#ff8f8f',
    infoText: '#b9a6ff',
    textTertiaryText: '#b8b8b8',
    shape: contrastShape,
    monaco: 'hc-black'
  }
}

/**
 * 纯黑：为 OLED / AMOLED 屏准备的真黑（#000000）色板。
 *
 * 深色底完全关闭像素以省电并去掉灰底泛光；层级靠极暗的近黑灰与圆角区分，
 * 而不是靠亮度阶梯。浅色一套给出中性偏冷的对应版本，方便日间切换。
 */
const oled: Palette = {
  key: 'oled',
  label: '纯黑',
  description: 'OLED 真黑省电配色，深色底为 #000000',
  light: {
    primary: '#0977b5',
    primaryHover: '#1a8dcf',
    primaryPressed: '#075f92',
    success: '#128158',
    warning: '#96631a',
    error: '#c62828',
    info: '#6d4fd6',
    bg: '#fafafa',
    card: '#ffffff',
    sidebar: '#ffffff',
    panel: 'rgba(255, 255, 255, 0.9)',
    elevated: '#ffffff',
    text: '#111113',
    textSecondary: '#4a4d52',
    textTertiary: '#6c7076',
    border: '#e2e3e6',
    divider: '#eeeff1',
    canvas: '#fafafa',
    canvasDot: '#b4b7bc',
    nodeStart: '#ffffff',
    nodeEnd: '#f7f7f8',
    nodeHeader: '#eeeff1',
    nodeBorder: '#e2e3e6',
    nodeMuted: '#f5f5f6',
    code: '#f2f3f5',
    codeText: '#111113',
    input: '#ffffff',
    shadow: '0 4px 12px rgba(17, 17, 19, 0.08)',
    shadowHover: '0 6px 16px rgba(17, 17, 19, 0.14)',
    primaryText: '#0977b5',
    successText: '#128158',
    warningText: '#96631a',
    errorText: '#c62828',
    infoText: '#6d4fd6',
    textTertiaryText: '#6c7076',
    shape: oledShape,
    monaco: 'vs'
  },
  dark: {
    primary: '#5ac8fa',
    primaryHover: '#7fd6fb',
    primaryPressed: '#3aa8d8',
    success: '#63e2b7',
    warning: '#f3a769',
    error: '#e88080',
    info: '#a78bfa',
    bg: '#000000',
    card: '#0b0b0d',
    sidebar: '#000000',
    panel: 'rgba(11, 11, 13, 0.92)',
    elevated: '#161619',
    text: '#f0f0f2',
    textSecondary: '#a9adb4',
    textTertiary: '#7b8088',
    border: '#26262b',
    divider: '#191a1d',
    canvas: '#000000',
    canvasDot: '#2e2f34',
    nodeStart: '#161619',
    nodeEnd: '#0b0b0d',
    nodeHeader: '#1f1f24',
    nodeBorder: '#2e2f34',
    nodeMuted: '#101013',
    code: '#000000',
    codeText: '#e6e6e9',
    input: '#0f0f12',
    shadow: '0 4px 12px rgba(0, 0, 0, 0.8)',
    shadowHover: '0 6px 20px rgba(0, 0, 0, 0.9)',
    primaryText: '#5ac8fa',
    successText: '#63e2b7',
    warningText: '#f3a769',
    errorText: '#e88080',
    infoText: '#a78bfa',
    textTertiaryText: '#7c8189',
    shape: oledShape,
    monaco: 'vs-dark'
  }
}

/** 所有可选色板，顺序即设置面板中的展示顺序 */
export const palettes: Palette[] = [classic, graphite, midnight, forest, contrast, oled]

export const DEFAULT_PALETTE_KEY = classic.key

export const getPalette = (key: string): Palette =>
  palettes.find((palette) => palette.key === key) || classic

/** 取色板的最终版式（缺省项回落到 DEFAULT_THEME_SHAPE） */
export const getShape = (seed: ThemeSeed): ThemeShape => ({
  ...DEFAULT_THEME_SHAPE,
  ...(seed.shape ?? {})
})

/**
 * 取色板的完整圆角阶梯。
 * theme store 用它写 --radius-* 变量，App.vue 用它生成 naive-ui 的 borderRadius，
 * 两侧共用同一个函数，避免“CSS 一套、naive-ui 另一套”的历史问题复发。
 */
export const getRadiiForSeed = (seed: ThemeSeed): ThemeRadii =>
  getRadii(getShape(seed).radiusScale)

/**
 * THEME_BOOT_TABLE 的数据源：index.html 首屏脚本手抄的就是这张表。
 * 单测通过对比本函数的输出与 index.html 中的字面量来发现漂移。
 */
export const bootPaletteTable = (): Record<string, { light: [string, string]; dark: [string, string] }> =>
  Object.fromEntries(
    palettes.map((palette) => [
      palette.key,
      {
        light: [palette.light.bg, palette.light.textTertiary] as [string, string],
        dark: [palette.dark.bg, palette.dark.textTertiary] as [string, string]
      }
    ])
  )
