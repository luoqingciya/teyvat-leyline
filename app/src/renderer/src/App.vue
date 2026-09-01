<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, subscribeProgress } from './api'
import { fmtSpeed } from './utils'
import TaskCard from './components/TaskCard.vue'
import SettingsModal from './components/SettingsModal.vue'
import Toaster from './components/Toaster.vue'

const toaster = ref(null)
const tasksRecord = reactive({})
const threads = ref(8)
const saveDir = ref('')
const config = ref({})
const showSettings = ref(false)
const urlInput = ref('')
const connected = ref(false)

const tasks = computed(() => Object.values(tasksRecord))
const activeCount = computed(
  () => tasks.value.filter((t) => t.status === 'downloading' || t.status === 'probing').length
)
const totalSpeed = computed(() =>
  tasks.value.reduce((s, t) => s + (t.speed || 0), 0)
)

// 静态星星背景
function renderStars() {
  const wrap = document.getElementById('stars')
  if (!wrap) return
  wrap.innerHTML = ''
  for (let i = 0; i < 24; i++) {
    const s = document.createElement('span')
    s.className = 'star'
    s.style.left = Math.random() * 100 + '%'
    s.style.top = Math.random() * 100 + '%'
    wrap.appendChild(s)
  }
}

// 与后端全量同步
async function reconcile(list) {
  const seen = new Set()
  for (const t of list || []) {
    if (t && t.id) {
      seen.add(t.id)
      upsert(t)
    }
  }
  for (const id of Object.keys(tasksRecord)) {
    if (!seen.has(id)) delete tasksRecord[id]
  }
}

function upsert(task) {
  const prev = tasksRecord[task.id]
  tasksRecord[task.id] = { ...(prev || {}), ...task }
  notifyTransition(prev, task)
}

let lastNotified = new Map()
function notifyTransition(prev, task) {
  if (!prev) return
  const key = task.id + ':' + task.status
  if (lastNotified.get(key)) return
  lastNotified.set(key, true)
  if (task.status === 'completed' && prev.status !== 'completed') {
    const ok = task.verified === true
    toaster.value?.push(`「${task.filename}」下载完成${ok ? '，哈希校验通过' : ''}`, ok ? 'success' : 'info')
  } else if (task.status === 'error' && prev.status !== 'error') {
    toaster.value?.push(`「${task.filename}」下载异常中断`, 'error')
  }
}

// ---- 交互 ----
function stepThreads(delta) {
  const next = Math.max(1, Math.min(16, threads.value + delta))
  threads.value = next
  api.saveConfig({ numThreads: next }).catch(() => {})
}

async function addTaskFromInput() {
  const url = urlInput.value.trim()
  if (!url) return
  try {
    const res = await api.addTask(url)
    urlInput.value = ''
    if (res && !res.ok && res.known) toaster.value?.push('该链接已在下载历史中，已跳过避免重复下载', 'info')
    else if (res && !res.ok && res.error) toaster.value?.push(res.error, 'error')
    // 立即把新任务纳入列表，不必等下一轮 WS 推送或轮询
    await refresh()
  } catch (e) {
    toaster.value?.push('导入失败: ' + e.message, 'error')
  }
}

async function chooseDir() {
  try {
    const path = await window.electronAPI.pickDirectory()
    if (!path) return
    const res = await api.setDirectory(path)
    if (res?.ok) saveDir.value = res.path
  } catch {
    /* ignore */
  }
}

function applyConfig(cfg) {
  config.value = cfg || {}
  threads.value = cfg?.numThreads ?? 8
  saveDir.value = cfg?.saveDir || ''
}

function onSaved(cfg) {
  config.value = cfg || config.value
  threads.value = cfg?.numThreads ?? threads.value
}

// ---- 初始化 ----
let wsOff = null
let pollTimer = null

async function boot() {
  try {
    const cfg = await api.getConfig()
    applyConfig(cfg)
  } catch (e) {
    toaster.value?.push('后端连接失败: ' + e.message, 'error')
  }
  await refresh()

  wsOff = subscribeProgress(
    ({ task }) => {
      if (task) upsert(task)
    },
    (state) => {
      // connected 反映 WebSocket 是否真正连通，而非是否收到过任务事件
      connected.value = state === 'open'
    }
  )

  // 兜底轮询，确保与后端一致
  pollTimer = setInterval(async () => {
    await refresh()
  }, 5000)
}

async function refresh() {
  try {
    const list = await api.getTasks()
    reconcile(list || [])
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  renderStars()
  boot()
})

onBeforeUnmount(() => {
  if (wsOff) wsOff()
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div id="stars-wrap">
    <!-- 原神风静态背景 -->
    <div class="bg" aria-hidden="true">
      <div class="aurora aurora-a"></div>
      <div class="aurora aurora-b"></div>
      <div class="aurora aurora-c"></div>
      <div class="stars" id="stars"></div>
    </div>

    <header class="topbar">
      <div class="brand">
        <div class="emblem" aria-hidden="true">
          <svg viewBox="0 0 64 64" class="emblem-svg">
            <defs>
              <linearGradient id="emblemGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#7fe8d4" />
                <stop offset=".55" stop-color="#ffd97a" />
                <stop offset="1" stop-color="#b58af0" />
              </linearGradient>
            </defs>
            <circle cx="32" cy="32" r="28" fill="none" stroke="url(#emblemGrad)" stroke-width="2" opacity=".85" />
            <path d="M32 8 L39 25 L57 25 L43 36 L49 54 L32 43 L15 54 L21 36 L7 25 L25 25 Z"
              fill="url(#emblemGrad)" opacity=".95" stroke="#fff8e1" stroke-width=".8" />
            <circle cx="32" cy="32" r="6" fill="#fff" opacity=".9" />
          </svg>
        </div>
        <div class="brand-text">
          <h1>提瓦特地脉</h1>
          <span class="sub">TEYVAT&nbsp;LEYLINE&nbsp;·&nbsp;多线程下载</span>
        </div>
      </div>

      <div class="top-actions">
        <div class="threads glass">
          <span class="label">地脉细流</span>
          <div class="stepper">
            <button class="step-btn" aria-label="减少线程" @click="stepThreads(-1)">−</button>
            <span id="threadsVal">{{ threads }}</span>
            <button class="step-btn" aria-label="增加线程" @click="stepThreads(1)">＋</button>
          </div>
        </div>
        <button class="btn btn-ghost" @click="showSettings = true">设置</button>
        <button class="btn btn-ghost" @click="chooseDir">选择目录</button>
      </div>
    </header>

    <main class="container">
      <section class="add-bar glass">
        <input
          id="urlInput"
          v-model="urlInput"
          type="text"
          placeholder="将下载链接注入地脉…　https://example.com/file.zip"
          spellcheck="false"
          @keydown.enter="addTaskFromInput"
        />
        <button class="btn btn-primary" @click="addTaskFromInput">导入任务</button>
      </section>

      <section class="stats">
        <div class="stat glass">
          <div class="stat-icon icon-task"></div>
          <div class="stat-meta">
            <span class="stat-num">{{ tasks.length }}</span>
            <span class="stat-label">任务总数</span>
          </div>
        </div>
        <div class="stat glass">
          <div class="stat-icon icon-active"></div>
          <div class="stat-meta">
            <span class="stat-num">{{ activeCount }}</span>
            <span class="stat-label">进行中</span>
          </div>
        </div>
        <div class="stat glass">
          <div class="stat-icon icon-speed"></div>
          <div class="stat-meta">
            <span class="stat-num">{{ fmtSpeed(totalSpeed) }}</span>
            <span class="stat-label">汇聚速度</span>
          </div>
        </div>
        <div class="stat glass">
          <div class="stat-icon icon-dir"></div>
          <div class="stat-meta">
            <span class="stat-dir">{{ saveDir || '—' }}</span>
            <span class="stat-label">保存目录</span>
          </div>
        </div>
      </section>

      <section v-if="tasks.length" id="taskList" class="tasks glass">
        <TaskCard v-for="t in tasks" :key="t.id" :task="t" />
      </section>

      <div v-else class="empty">
        <div class="empty-emblem">✧</div>
        <p>微风拂过提瓦特大陆，等待冒险家投入新的“地脉”任务…</p>
        <p class="empty-hint">在上方粘贴链接，即可开始汇聚地脉之流。</p>
      </div>
    </main>

    <footer class="footer">
      <span>Teyvat Leyline · 提瓦特地脉</span>
      <span class="dot">·</span>
      <span>Electron + Python</span>
      <span class="spacer"></span>
      <span>{{ connected ? '已连接地脉' : '连接中…' }}</span>
    </footer>

    <SettingsModal :show="showSettings" @close="showSettings = false" @saved="onSaved" />
    <Toaster ref="toaster" />
  </div>
</template>