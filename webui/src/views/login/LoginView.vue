<template>
  <div class="login-view">
    <!-- 背景图层 -->
    <div class="login-bg-layer"></div>

    <div class="login-container">
      <!-- 左侧图片区域 -->
      <div class="login-image-section">
        <div class="login-image-content">
          <h1 class="brand-title">Kirara AI</h1>
          <p class="brand-slogan">探索人工智能的无限可能</p>
        </div>
      </div>

      <!-- 右侧登录表单区域 -->
      <div class="login-form-section">
        <div class="login-form-wrapper">
          <div class="login-header">
            <div class="login-logo">
              <div class="i-carbon-bot text-36px animate-float" />
            </div>
            <h2 class="login-title">欢迎使用 Kirara AI</h2>
            <p class="login-subtitle">
              {{ isFirstTime ? '首次使用，请设置管理员密码' : '请输入管理员密码继续' }}
            </p>
          </div>

          <n-form
            ref="formRef"
            :model="formModel"
            :rules="rules"
            label-placement="left"
            label-width="0"
            require-mark-placement="right-hanging"
            size="large"
            class="login-form"
          >
            <n-form-item path="password" class="form-item">
              <n-input
                v-model:value="formModel.password"
                type="password"
                placeholder="请输入密码"
                show-password-on="click"
                :status="passwordFeedback"
                class="password-input"
              >
                <template #prefix>
                  <div class="i-carbon-password" />
                </template>
              </n-input>
            </n-form-item>

            <n-form-item v-if="isFirstTime" path="confirmPassword" class="form-item">
              <n-input
                v-model:value="formModel.confirmPassword"
                type="password"
                placeholder="请确认密码"
                :maxlength="32"
                show-password-on="click"
                class="password-input"
              >
                <template #prefix>
                  <div class="i-carbon-password-confirmation" />
                </template>
              </n-input>
            </n-form-item>

            <div class="form-actions">
              <n-button
                type="primary"
                size="large"
                block
                :loading="loading"
                @click="handleLogin"
                class="login-button"
              >
                {{ isFirstTime ? '设置密码' : '登录' }}
                <div class="button-effect"></div>
              </n-button>

              <div class="forgot-password" v-if="!isFirstTime">
                <n-tooltip trigger="hover" placement="bottom">
                  <template #trigger>
                    <span class="forgot-password-text"> 忘记密码？ </span>
                  </template>
                  <span>删除项目下的 data\web\password.hash 文件即可重置密码</span>
                </n-tooltip>
              </div>
            </div>
          </n-form>
        </div>
      </div>
    </div>

    <div class="login-footer">
      <a href="https://github.com/lss233/kirara-ai" target="_blank" class="footer-link">
        <span>Powered by Kirara AI</span>
      </a>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NForm, NFormItem, NInput, NButton, NTooltip, useMessage } from 'naive-ui'
import type { FormValidationStatus } from 'naive-ui/es/form/src/interface'
import { useLoginViewModel } from './login.vm'

const { isFirstTime, loading, formModel, rules, handleSubmit, checkFirstTime } = useLoginViewModel()

const message = useMessage()
// n-input 的 `status` 只接受 naive-ui 的 FormValidationStatus，宽 string 报 TS2322。
// 该类型没有从包根导出，只能从 es/form/src/interface 取（与 naive-ui 自身
// 各组件 .d.ts 的引用方式一致）。
// 这里只用到 'error' 一档（密码校验失败时置上、成功时清空）。
const passwordFeedback = ref<FormValidationStatus | undefined>(undefined)

const handleLogin = async () => {
  try {
    passwordFeedback.value = undefined
    await handleSubmit()
  } catch (error: any) {
    passwordFeedback.value = 'error'
    message.error('登录失败，请重试')
  }
}

onMounted(() => {
  checkFirstTime()
})
</script>

<style scoped>
.login-view {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background-color: var(--bg-color);
}

/* 背景图层 */
.login-bg-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-size: cover;
  background-position: center;
  filter: brightness(0.7);
  z-index: -2;
}

.login-bg-layer::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: linear-gradient(
    135deg,
    rgba(var(--primary-color-rgb), 0.3) 0%,
    rgba(var(--primary-color-rgb), 0.18) 100%
  );
  z-index: -1;
}

.login-container {
  display: flex;
  width: 90%;
  max-width: 1200px;
  min-height: 600px;
  /* 整屏级的主视觉容器，用阶梯最大档 */
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow: var(--box-shadow-overlay, 0 15px 35px rgba(0, 0, 0, 0.2));
  /* 玻璃拟态底色改用卡片色，浅色下仍是 rgba(255,255,255,.1)，深色下不再发白 */
  background-color: rgba(var(--card-bg-color-rgb, 255, 255, 255), 0.1);
  backdrop-filter: blur(15px);
  border: 1px solid rgba(var(--card-bg-color-rgb, 255, 255, 255), 0.2);
  animation: container-appear 0.8s ease forwards;
}

/* 左侧图片区域 */
.login-image-section {
  /* 主视觉的前景/蒙版局部变量，明暗两套都显式定义 */
  --hero-fg: #ffffff;
  --hero-fg-accent: #e0e0ff;
  --hero-scrim-start: rgba(0, 0, 0, 0.55);
  --hero-scrim-end: rgba(0, 0, 0, 0.62);

  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 3rem;
  /* 原先只有一层 0.7→0.45 的主色半透明，白字对比度仅 2.5:1（松林色板 1.8:1）。
     现在改为「主色实底渐变 + 固定深色蒙版」两层：主色仍决定色相，
     蒙版把底色压暗，四套色板的明暗两态下白字对比度均 ≥ 8:1，稳过 AA。 */
  background-image: linear-gradient(135deg, var(--hero-scrim-start) 0%, var(--hero-scrim-end) 100%),
    linear-gradient(
      135deg,
      var(--primary-color-pressed, #2f6be0) 0%,
      var(--primary-color, #4080ff) 100%
    );
  color: var(--hero-fg);
  overflow: hidden;
}

/* 深色主题下整体再压暗一档，与页面底色的明度差保持一致 */
.dark .login-image-section {
  --hero-fg-accent: #c9cdff;
  --hero-scrim-start: rgba(0, 0, 0, 0.56);
  --hero-scrim-end: rgba(0, 0, 0, 0.72);
}

.login-image-section::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-size: contain;
  background-position: bottom right;
  background-repeat: no-repeat;
  opacity: 0.2;
  z-index: 0;
  animation: image-float 6s ease-in-out infinite;
}

.login-image-content {
  position: relative;
  z-index: 1;
  width: 100%;
}

.brand-title {
  /* 主视觉标题属于展示级字号，超出 --font-size-* 阶梯（最大 3xl）的范围，
     若强行套用会明显变小，故保留展示字号本身 */
  font-size: 3.5rem;
  font-weight: 800;
  margin-bottom: 1rem;
  /* 渐变文字取主视觉前景色，深色主题下换成偏冷的浅紫，不再写死纯白 */
  background: linear-gradient(to right, var(--hero-fg, #ffffff), var(--hero-fg-accent, #e0e0ff));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: slide-in-left 0.8s ease forwards;
}

.brand-slogan {
  font-size: var(--font-size-2xl, 1.5rem);
  margin-bottom: 3rem;
  opacity: 0;
  animation: fade-in 0.8s ease forwards 0.3s;
}

.brand-features {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 1rem;
  font-size: var(--font-size-xl, 1.2rem);
  opacity: 0;
  transform: translateX(-20px);
}

.feature-item:nth-child(1) {
  animation: slide-in-left 0.6s ease forwards 0.5s;
}

.feature-item:nth-child(2) {
  animation: slide-in-left 0.6s ease forwards 0.7s;
}

.feature-item:nth-child(3) {
  animation: slide-in-left 0.6s ease forwards 0.9s;
}

.feature-item div {
  /* 图标尺寸，非正文字号 */
  font-size: 1.8rem;
}

/* 右侧表单区域 */
.login-form-section {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background-color: var(--panel-bg-color, rgba(255, 255, 255, 0.9));
  position: relative;
  overflow: hidden;
}

.login-form-section::before {
  content: '';
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  /* 旋转光斑原先固定用白色，深色主题下会泛起灰雾，改为跟随卡片色 */
  background: radial-gradient(
    circle,
    rgba(var(--card-bg-color-rgb, 255, 255, 255), 0.1) 0%,
    rgba(var(--card-bg-color-rgb, 255, 255, 255), 0) 70%
  );
  animation: rotate 20s linear infinite;
}

.login-form-wrapper {
  width: 100%;
  max-width: 400px;
  position: relative;
  z-index: 1;
}

.login-header {
  text-align: center;
  margin-bottom: 2.5rem;
}

.login-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 1.5rem;
}

.login-logo div {
  /* 图标尺寸而非正文字号，超出字阶范围，保留原值 */
  font-size: 3rem;
  color: var(--primary-color);
}

.login-title {
  margin: 0;
  font-size: var(--font-size-3xl, 1.8rem);
  font-weight: 600;
  color: var(--text-color, #333);
  margin-bottom: 0.5rem;
  animation: fade-in 0.8s ease forwards;
}

.login-subtitle {
  color: var(--text-color-secondary, #666);
  font-size: var(--font-size-base, 1rem);
  animation: fade-in 0.8s ease forwards 0.2s;
  opacity: 0;
}

.login-form {
  animation: slide-up 0.8s ease forwards 0.3s;
  opacity: 0;
}

.form-item {
  margin-bottom: 1.5rem;
}

.form-actions {
  margin-top: 2rem;
}

.login-button {
  height: 50px;
  font-size: var(--font-size-xl, 1.1rem);
  font-weight: 600;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-color-hover) 100%);
  border: none;
  position: relative;
  overflow: hidden;
}

.button-effect {
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  /* 扫光高光跟随卡片色，深色主题下不再是刺眼的白条 */
  background: linear-gradient(
    90deg,
    transparent,
    rgba(var(--card-bg-color-rgb, 255, 255, 255), 0.2),
    transparent
  );
  transition: all 0.6s ease;
}

.login-button:hover .button-effect {
  left: 100%;
}

.forgot-password {
  text-align: center;
  margin-top: 1.5rem;
  font-size: var(--font-size-sm, 0.9rem);
}

.forgot-password-text {
  color: var(--primary-color-text, var(--primary-color));
  cursor: pointer;
  position: relative;
  transition: all 0.3s ease;
}

/* 「忘记密码」是自定义可点击文本，需要可见的键盘聚焦环 */
.forgot-password-text:focus-visible {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 3px;
  /* 行内文字的聚焦环属于内联小件，用 xs 档 */
  border-radius: var(--radius-xs);
}

.forgot-password-text::after {
  content: '';
  position: absolute;
  bottom: -2px;
  left: 0;
  width: 0;
  height: 1px;
  background-color: var(--primary-color);
  transition: width 0.3s ease;
}

.forgot-password-text:hover {
  color: var(--primary-color-hover);
}

.forgot-password-text:hover::after {
  width: 100%;
}

.login-footer {
  text-align: center;
  margin-top: 2rem;
  position: absolute;
  bottom: 1rem;
  width: 100%;
  animation: fade-in 0.8s ease forwards 1s;
  opacity: 0;
}

.footer-link {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  /* 原先读未定义的 --n-text-color-rgb，整段颜色其实一直没生效 */
  color: var(--text-color-tertiary, #909399);
  text-decoration: none;
  transition: all 0.3s ease;
}

.footer-link:hover {
  color: var(--text-primary, #333639);
  /* 光晕取主色而非纯白，深浅主题下都能看出是「点亮」 */
  text-shadow: 0 0 10px rgba(var(--primary-color-rgb, 64, 128, 255), 0.5);
}

/* 页脚链接需要可见的键盘聚焦环 */
.footer-link:focus-visible {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 3px;
  /* 行内链接的聚焦环属于内联小件，用 xs 档 */
  border-radius: var(--radius-xs);
}

.footer-icon {
  font-size: var(--font-size-xl, 1.2rem);
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes image-float {
  0% {
    transform: translateY(0) scale(1);
  }
  50% {
    transform: translateY(-10px) scale(1.02);
  }
  100% {
    transform: translateY(0) scale(1);
  }
}

/* 响应式布局 */
@media (max-width: 992px) {
  .login-container {
    flex-direction: column;
    width: 95%;
    max-width: 500px;
  }

  .login-image-section {
    padding: 2rem;
    min-height: 250px;
  }

  .brand-title {
    /* 展示级字号，见上方说明 */
    font-size: 2.5rem;
  }

  .brand-slogan {
    font-size: var(--font-size-xl, 1.2rem);
    margin-bottom: 1.5rem;
  }

  .brand-features {
    flex-direction: row;
    justify-content: space-around;
    gap: 1rem;
  }

  .feature-item {
    flex-direction: column;
    text-align: center;
    font-size: var(--font-size-base, 1rem);
  }
}

@media (max-width: 768px) {
  .login-container {
    width: 100%;
    height: 100vh;
    /* 例外：窄屏下容器铺满整屏，圆角会在屏幕四角露出底色，故保持直角 */
    border-radius: 0;
    margin: 0;
  }

  .login-image-section {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    z-index: -1;
    /* 窄屏下该区块退为整页背景（表单浮在其上），蒙版略放松但仍保证白字过 AA */
    --hero-scrim-start: rgba(0, 0, 0, 0.45);
    --hero-scrim-end: rgba(0, 0, 0, 0.6);
  }

  .login-form-section {
    background-color: var(--panel-bg-color, rgba(255, 255, 255, 0.85));
    backdrop-filter: blur(15px);
    min-height: 100vh;
    padding: 2rem 1.5rem;
  }

  .brand-features {
    display: none;
  }

  .login-form-wrapper {
    padding: 2rem;
    /* 窄屏下表单浮在整页主视觉上，属于大型表面档 */
    border-radius: var(--radius-lg);
    background-color: var(--card-bg-color, rgba(255, 255, 255, 0.9));
    box-shadow: var(--box-shadow-lg, var(--box-shadow-hover, 0 10px 30px rgba(0, 0, 0, 0.1)));
  }
}

@media (max-width: 480px) {
  .login-form-wrapper {
    padding: 1.5rem;
  }

  .login-title {
    font-size: var(--font-size-2xl, 1.5rem);
  }

  .login-subtitle {
    font-size: var(--font-size-sm, 0.9rem);
  }

  .login-logo div {
    /* 同上：图标尺寸 */
    font-size: 2.5rem;
  }
}

/* 尊重系统的「减少动态效果」偏好：登录页动画较多，统一收敛为直接呈现终态 */
@media (prefers-reduced-motion: reduce) {
  .login-container,
  .login-image-section::before,
  .login-form-section::before,
  .brand-title,
  .brand-slogan,
  .feature-item,
  .login-title,
  .login-subtitle,
  .login-form,
  .login-footer {
    animation: none !important;
    opacity: 1;
    transform: none;
  }
}
</style>
