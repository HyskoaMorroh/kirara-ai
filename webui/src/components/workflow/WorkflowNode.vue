<script setup lang="ts">
import CustomNode from './nodes/CustomNode.vue'

// Keep the plan's stable component path without introducing a second node implementation.
defineOptions({ inheritAttrs: false })
</script>

<template>
  <CustomNode v-bind="$attrs" />
</template>
