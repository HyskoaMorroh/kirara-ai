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

<script>
export default {
  name: 'TopBar'
}
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
