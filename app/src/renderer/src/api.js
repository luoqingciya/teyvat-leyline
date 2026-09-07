// 封装与 Python 后端的 HTTP + WebSocket 通信。
// 端口与鉴权令牌都来自 Electron 主进程对后端 stdout 的解析：
// 后端启动时打印 PORT=<n> 与 TOKEN=<hex>，请求需携带 X-Teyvat-Token 头。

let base = ''
let wsBase = ''
let token = ''
let baseReady = false

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function readAuth() {
  if (window.electronAPI?.getBackendAuth) {
    try {
      return await window.electronAPI.getBackendAuth()
    } catch {
      return null
    }
  }
  // 纯 Web 调试环境：无主进程，使用 Vite 注入的端口与令牌
  return {
    port: import.meta.env.VITE_BACKEND_PORT || null,
    token: import.meta.env.VITE_BACKEND_TOKEN || ''
  }
}

// 读取一次端口/令牌，端口变化时更新 base。
// 后端崩溃会被主进程重启且端口随机变化，因此每次连接前都重读（一次 IPC 调用，开销可忽略）。
async function refreshBase() {
  const auth = await readAuth()
  if (!auth || !auth.port) return false
  token = auth.token || ''
  const port = String(auth.port)
  if (!base || !base.endsWith(`:${port}`)) {
    base = `http://127.0.0.1:${port}`
    wsBase = `ws://127.0.0.1:${port}/api/ws`
  }
  return true
}

// 后端由主进程拉起，随机端口在 stdout 就绪需要一点时间（PyInstaller 单文件需先解压）。
async function ensureBase(waitMs = 20000) {
  if (baseReady && (await refreshBase())) return
  const deadline = Date.now() + waitMs
  while (Date.now() < deadline) {
    if (await refreshBase()) {
      baseReady = true
      return
    }
    await sleep(300)
  }
  throw new Error('无法获取后端端口')
}

async function request(path, options = {}) {
  await ensureBase()
  const headers = { ...(options.headers || {}) }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers['X-Teyvat-Token'] = token
  const res = await fetch(base + path, { ...options, headers })
  const text = await res.text()
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text}`)
  return text ? JSON.parse(text) : null
}

function json(method, path, body) {
  return request(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body)
  })
}

export const api = {
  init: () => ensureBase(),
  getConfig: () => request('/api/config'),
  saveConfig: (settings) => json('PUT', '/api/config', { settings }),
  getTasks: () => request('/api/tasks'),
  addTask: (url) => json('POST', '/api/tasks', { url }),
  pauseTask: (id) => json('POST', `/api/tasks/${id}/pause`),
  resumeTask: (id) => json('POST', `/api/tasks/${id}/resume`),
  cancelTask: (id) => json('POST', `/api/tasks/${id}/cancel`),
  removeTask: (id) => json('DELETE', `/api/tasks/${id}`),
  setTaskSpeed: (id, kbps) => json('POST', `/api/tasks/${id}/speed`, { kbps }),
  setTaskThreads: (id, n) => json('POST', `/api/tasks/${id}/threads`, { n }),
  setDirectory: (path) => json('PUT', '/api/directory', { path }),
  getHistory: () => request('/api/history')
}

// WebSocket 进度订阅：传入数据回调与状态回调，返回取消函数。
// onStatus(state)：'open' | 'close' | 'error'
export function subscribeProgress(handler, onStatus = () => {}) {
  let ws = null
  let closed = false
  let retry = 0
  let timer = null

  // 带上限的退避重试：后端未就绪或连接断开时持续重连，而不是重试几次就放弃。
  const schedule = (delay) => {
    if (closed) return
    timer = setTimeout(connect, delay)
  }

  const connect = async () => {
    if (closed) return
    try {
      await ensureBase(2000)
    } catch {
      // 端口还没读到（后端未就绪），稍后重试，避免永久卡在“连接中”
      onStatus('close')
      retry += 1
      schedule(Math.min(5000, 500 * retry))
      return
    }
    const url = token ? `${wsBase}?token=${encodeURIComponent(token)}` : wsBase
    try {
      ws = new WebSocket(url)
    } catch {
      onStatus('close')
      retry += 1
      schedule(Math.min(5000, 500 * retry))
      return
    }
    ws.onopen = () => {
      retry = 0
      onStatus('open')
    }
    ws.onmessage = (e) => {
      let data
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }
      if (data.ping) return
      handler(data)
    }
    ws.onclose = () => {
      onStatus('close')
      if (!closed) {
        retry += 1
        schedule(Math.min(5000, 500 * retry))
      }
    }
    ws.onerror = () => {
      onStatus('error')
      if (ws) ws.close() // 触发 onclose 走统一重连
    }
  }

  connect()
  return () => {
    closed = true
    if (timer) clearTimeout(timer)
    if (ws) ws.close()
  }
}
