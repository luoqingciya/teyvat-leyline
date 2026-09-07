<script setup>
import { computed } from 'vue'
import { STATUS_LABEL, fmtBytes, fmtSpeed, fmtEta } from '../utils'
import { api } from '../api'

const props = defineProps({
  task: { type: Object, required: true }
})

const st = computed(() => props.task.status)
const total = computed(() => props.task.total || 0)
const pct = computed(() =>
  total.value > 0 ? Math.min(100, (props.task.downloaded / total.value) * 100).toFixed(2) : 0
)
const active = computed(() => st.value === 'downloading' || st.value === 'probing')
const showRate = computed(() => active.value || st.value === 'paused' || st.value === 'queued')
const showThreads = computed(
  () => active.value || st.value === 'paused' || st.value === 'error' || st.value === 'queued'
)

// 每任务限速（KB/s，0=不限），失焦/回车生效
function setRate(e) {
  const v = Math.max(0, Math.min(1000000, Math.floor(Number(e.target.value) || 0)))
  e.target.value = v
  props.task.speedKbps = v
  api.setTaskSpeed(props.task.id, v).catch(() => {})
}

// 每任务线程数：对运行中的任务在下一次分片重建（重试/继续）时生效
function setThreads(e) {
  const n = Math.max(1, Math.min(16, Number(e.target.value) || 1))
  e.target.value = n
  props.task.threads = n
  api.setTaskThreads(props.task.id, n).catch(() => {})
}
</script>

<template>
  <div class="task-card">
    <div class="task-top">
      <div class="task-name" :title="task.url">{{ task.filename }}</div>
      <div class="badge" :class="st">{{ STATUS_LABEL[st] || st }}</div>
    </div>

    <div class="progress">
      <div class="progress-fill" :style="{ width: pct + '%' }"></div>
    </div>

    <div class="task-meta">
      <span class="meta-dl">{{ fmtBytes(task.downloaded) }} / {{ fmtBytes(total) }}</span>
      <span class="meta-threads">{{ task.threads || 1 }} 线程</span>
      <span v-if="task.retries > 0" class="meta-retries">已重试 {{ task.retries }} 次</span>
      <span class="spacer"></span>
      <span v-if="active && task.speed" class="meta-speed">{{ fmtSpeed(task.speed) }}</span>
      <span v-if="active && total > 0" class="meta-eta">剩余 {{ fmtEta(task.eta) }}</span>
    </div>

    <div v-if="showRate" class="task-rate">
      <span class="row-label">限速</span>
      <input
        class="rate-input"
        type="number"
        min="0"
        :value="task.speedKbps || 0"
        @change="setRate"
      />
      <span class="row-unit">KB/s · 0=不限</span>
    </div>

    <div v-if="showThreads" class="task-rate">
      <span class="row-label">线程</span>
      <select class="rate-select" :value="task.threads || 1" @change="setThreads">
        <option v-for="n in 16" :key="n" :value="n">{{ n }}</option>
      </select>
      <span class="row-unit">· 重试/继续后生效</span>
    </div>

    <div v-if="st === 'completed' && task.verified === true" class="verify-line ok">
      ✓ 完整性校验通过（SHA256）
    </div>
    <div v-if="task.error" class="task-error">{{ task.error }}</div>

    <div class="task-actions">
      <!-- 下载中/探测/排队：暂停 + 取消 -->
      <template v-if="active || st === 'queued'">
        <button class="mini-btn" @click="api.pauseTask(task.id)">暂停</button>
        <button class="mini-btn danger" @click="api.cancelTask(task.id)">取消</button>
      </template>
      <!-- 暂停/错误：继续/重试 + 取消/移除 -->
      <template v-else-if="st === 'paused' || st === 'error'">
        <button class="mini-btn" @click="api.resumeTask(task.id)">
          {{ st === 'error' ? '重试' : '继续' }}
        </button>
        <button class="mini-btn danger" @click="api.cancelTask(task.id)">取消</button>
        <button class="mini-btn danger" @click="api.removeTask(task.id)">移除</button>
      </template>
      <!-- 完成/取消 -->
      <template v-else-if="st === 'completed' || st === 'cancelled'">
        <button class="mini-btn danger" @click="api.removeTask(task.id)">移除</button>
      </template>
    </div>
  </div>
</template>
