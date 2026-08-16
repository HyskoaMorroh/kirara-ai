<script setup lang="ts">
import { NResult, NButton, NSpace } from 'naive-ui'
import { ref, onMounted } from 'vue'

const building = ref(false)

onMounted(() => {
  building.value = true
})
</script>

<template>
  <div class="container">
    <NResult
      status="404"
      :title="building ? '努力建设中... 喵~' : '迷路了吗？'"
      description="这个功能还在小猫咪的秘密工坊里，加班加点赶工呢... 🚀"
    >
      <template #icon>
        <div class="text-8xl animated-icon">🚧</div>
      </template>
      <template #footer>
        <NSpace style="width: 100%; justify-content: center">
          <NButton
            tag="a"
            href="https://github.com/DarkSkyTeam/chatgpt-for-bot-webui"
            target="_blank"
            type="primary"
          >
            去 GitHub 看看
          </NButton>

          <NButton
            tag="a"
            href="https://kirara-docs.app.lss233.com/"
            target="_blank"
            type="primary"
          >
            去文档看看
          </NButton>
        </NSpace>
      </template>
    </NResult>
    <div class="badges">
      <a href="https://github.com/DarkSkyTeam/chatgpt-for-bot-webui" target="_blank">
        <img
          src="https://img.shields.io/github/stars/DarkSkyTeam/chatgpt-for-bot-webui?style=flat&color=green"
          alt="GitHub Stars"
        />
      </a>
      <a href="https://github.com/lss233/chatgpt-mirai-qq-bot" target="_blank">
        <img
          src="https://img.shields.io/github/stars/lss233/chatgpt-mirai-qq-bot?style=flat&color=blue"
          alt="GitHub Stars"
        />
      </a>
    </div>
  </div>
</template>

<style scoped>
.container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  width: 100%;
  height: calc(100vh - 64px);
  text-align: center;
}

.text-8xl {
  font-size: 8rem;
}

.badges {
  margin-top: 20px;
}

.badges a {
  margin: 0 10px;
}

.animated-icon {
  animation: rotate 2s linear infinite;
  display: inline-block;
}

@keyframes rotate {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
