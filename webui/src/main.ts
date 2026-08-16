import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { useThemeStore } from './stores/theme'

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)
app.use(router)

// 在挂载前建立主题 store，让 CSS 变量在首帧之前就写入 <html>，
// 避免深色偏好下先闪一帧浅色界面。
useThemeStore(pinia)

app.mount('#app')

const oldFetch = window.fetch
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const response = await oldFetch(input, init)
  if (router?.currentRoute?.value?.name != 'login') {
    if (response.status == 401) {
      router.push('/login')
    }
  }

  return response
}
