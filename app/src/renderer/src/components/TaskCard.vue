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

// 每任务限速步进
function stepRate(delta) {
  const next = Math.max(0, (props.task.speedKbps || 0) + delta)
  api.setTaskSpeed(props.task.id, next)
  props.task.speedKbps = next
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
      <span class="spacer"></span>
      <span v-if="active && task.speed" class="meta-speed">{{ fmtSpeed(task.speed) }}</span>
      <span v-if="active && total > 0" class="meta-eta">剩余 {{ fmtEta(task.eta) }}</span>
    </div>

    <div v-if="showRate" class="task-rate">
      <span class="row-label">限速</span>
      <button class="step-btn" aria-label="减少限速" @click="stepRate(-1)">−</button>
      <span class="rate-val">{{ task.speedKbps || 0 }}</span>
      <button class="step-btn" aria-label="增加限速" @click="stepRate(1)">＋</button>
      <span class="row-unit">KB/s · 0=不限</span>
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
      </template>
      <!-- 完成/取消 -->
      <template v-else-if="st === 'completed' || st === 'cancelled'">
        <button class="mini-btn danger" @click="api.removeTask(task.id)">移除</button>
      </template>
    </div>
  </div>
</template>