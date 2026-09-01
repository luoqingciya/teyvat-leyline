<script setup>
import { ref } from 'vue'

const toasts = ref([])
let seq = 0

function push(message, type = 'info') {
  const id = ++seq
  toasts.value.push({ id, message, type })
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, 3800)
}

defineExpose({ push })
</script>

<template>
  <div class="toasts">
    <div
      v-for="t in toasts"
      :key="t.id"
      class="toast"
      :class="t.type"
    >
      {{ t.message }}
    </div>
  </div>
</template>