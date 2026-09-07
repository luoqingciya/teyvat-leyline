<script setup>
import { ref, watch } from 'vue'
import { api } from '../api'
import { fmtBytes } from '../utils'

const props = defineProps({
  show: { type: Boolean, default: false }
})
const emit = defineEmits(['close'])

const items = ref([])
const loading = ref(false)

watch(
  () => props.show,
  async (v) => {
    if (v) await load()
  }
)

async function load() {
  loading.value = true
  try {
    const res = await api.getHistory()
    items.value = (res && res.items) || []
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

function fmtTime(ts) {
  if (!ts) return '—'
  try {
    return new Date(ts * 1000).toLocaleString()
  } catch {
    return '—'
  }
}
</script>

<template>
  <div v-if="show" class="overlay" @click.self="emit('close')">
    <div class="settings history glass">
      <div class="settings-head">
        <span class="settings-title">下载历史</span>
        <button class="btn btn-ghost" style="padding: 6px 12px" @click="emit('close')">✕</button>
      </div>

      <div class="settings-body history-body">
        <p v-if="loading" class="history-empty">加载中…</p>
        <p v-else-if="!items.length" class="history-empty">还没有下载记录。</p>
        <div v-for="(it, i) in items" :key="i" class="history-item">
          <div class="history-main">
            <span class="history-name" :title="it.url">{{ it.filename }}</span>
            <span class="history-meta">
              {{ fmtBytes(it.total) }} · {{ fmtTime(it.finishedAt) }}
            </span>
          </div>
          <span class="badge" :class="it.success ? 'completed' : 'error'">
            {{ it.success ? (it.verified ? '校验通过' : '成功') : '失败' }}
          </span>
        </div>
      </div>

      <div class="settings-foot">
        <button class="btn btn-primary" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
