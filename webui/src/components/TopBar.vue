<template>
  <div class="topbar">
    <p>Kirara AI</p>
    <!-- <ul>
      <li><a href="#">Home</a></li>
      <li><a href="#">About</a></li>
      <li><a href="#">Contact</a></li>
    </ul> -->
  </div>
</template>

<script setup lang="ts">
// 不写 lang="ts" 时 vue-tsc 会把这段当 JS 虚拟文件（TopBar.vue.js）纳入 program，
// 撞上 tsconfig 的 allowJs: false，每次 typecheck 都固定吐一条 TS6504。
// 与其为一个组件放开 allowJs，不如让它跟仓库其余组件一样走 TS。
defineOptions({ name: 'TopBar' })
</script>

<style scoped>
/* 注意：本组件当前没有任何引用方（全仓库无 import），保留以备后用，
   样式已按主题 token 改写，一旦被引入即可直接跟随主题 */
.topbar {
  background-color: var(--sidebar-bg-color, #333);
  color: var(--text-primary, #fff);
  height: 50px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 var(--space-5, 20px);
}

.logo {
  height: 40px;
}

ul {
  list-style: none;
  display: flex;
}

li {
  margin-right: var(--space-5, 20px);
}

a {
  color: var(--text-primary, #fff);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

/* 导航链接需要可见的键盘聚焦环 */
a:focus-visible {
  outline: 2px solid var(--primary-color, #4080ff);
  outline-offset: 2px;
}
</style>
