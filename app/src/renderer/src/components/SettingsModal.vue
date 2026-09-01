<script setup>
import { reactive, ref, watch } from 'vue'
import { clamp } from '../utils'
import { api } from '../api'

const props = defineProps({
  show: { type: Boolean, default: false }
})
const emit = defineEmits(['close', 'saved'])

const form = reactive({
  numThreads: 8,
  globalSpeedKbps: 0,
  maxConcurrent: 4,
  maxRetries: 3,
  retryDelay: 2,
  proxy: '',
  hashCheck: false
})

watch(
  () => props.show,
  (v) => {
    if (v) loadConfig()
  }
)

async function loadConfig() {
  try {
    const cfg = await api.getConfig()
    if (!cfg) return
    form.numThreads = cfg.numThreads ?? 8
    form.globalSpeedKbps = cfg.globalSpeedKbps ?? 0
    form.maxConcurrent = cfg.maxConcurrent ?? 4
    form.maxRetries = cfg.maxRetries ?? 3
    form.retryDelay = cfg.retryDelay ?? 2
    form.proxy = cfg.proxy ?? ''
    form.hashCheck = !!cfg.hashCheck
  } catch {
    /* ignore */
  }
}

function toggleHash() {
  form.hashCheck = !form.hashCheck
}

async function save() {
  const settings = {
    numThreads: clamp(Number(form.numThreads), 1, 16, 8),
    globalSpeedKbps: clamp(Number(form.globalSpeedKbps), 0, 100000, 0),
    maxConcurrent: clamp(Number(form.maxConcurrent), 1, 64, 4),
    maxRetries: clamp(Number(form.maxRetries), 0, 99, 3),
    retryDelay: clamp(Number(form.retryDelay), 0, 3600, 2),
    proxy: form.proxy.trim(),
    hashCheck: form.hashCheck
  }
  const res = await api.saveConfig(settings)
  emit('saved', res?.config || settings)
  emit('close')
}
</script>

<template>
  <div v-if="show" class="overlay" @click.self="emit('close')">
    <div class="settings glass">
      <div class="settings-head">
        <span class="settings-title">设置</span>
        <button class="btn btn-ghost" style="padding: 6px 12px" @click="emit('close')">✕</button>
      </div>

      <div class="settings-body">
        <label class="field">
          <span class="field-label">默认线程数</span>
          <input v-model="form.numThreads" type="number" min="1" max="16" />
        </label>
        <label class="field">
          <span class="field-label">全局限速（KB/s，0=不限）</span>
          <input v-model="form.globalSpeedKbps" type="number" min="0" />
        </label>
        <label class="field">
          <span class="field-label">最大并发任务数</span>
          <input v-model="form.maxConcurrent" type="number" min="1" max="64" />
        </label>
        <label class="field">
          <span class="field-label">失败重试次数</span>
          <input v-model="form.maxRetries" type="number" min="0" max="99" />
        </label>
        <label class="field">
          <span class="field-label">重试间隔（秒）</span>
          <input v-model="form.retryDelay" type="number" min="0" step="0.1" />
        </label>
        <label class="field">
          <span class="field-label">代理</span>
          <input v-model="form.proxy" placeholder="如 socks5://127.0.0.1:1080" />
        </label>

        <div class="field switch-row">
          <div class="switch-text">
            <span class="field-label">下载后哈希校验（SHA256）</span>
            <span class="field-hint">校验更耗时，但能确保文件完整性</span>
          </div>
          <div
            class="toggle"
            role="switch"
            :aria-checked="form.hashCheck"
            tabindex="0"
            @keydown.space.prevent="toggleHash"
            @click="toggleHash"
          >
            <span class="knob"></span>
          </div>
        </div>
      </div>

      <div class="settings-foot">
        <button class="btn btn-ghost" @click="emit('close')">取消</button>
        <button class="btn btn-primary" @click="save">保存</button>
      </div>
    </div>
  </div>
</template>