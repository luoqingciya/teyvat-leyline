// 封装与 Python 后端的 HTTP + WebSocket 通信。
// 端口来自 Electron 主进程从后端 stdout 读到的随机端口。

let base = ''
let wsBase = ''
let readyPromise = null

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// 后端由主进程拉起，随机端口在 stdout 就绪需要一点时间（PyInstaller 单文件需先解压）。
// 这里循环轮询端口，避免拿到 null 而连到错误的地址。
async function waitForPort(timeoutMs = 20000) {
  if (!window.electronAPI) {
    // 纯 Web 调试环境：无主进程，使用 VITE 配置的端口
    return import.meta.env.VITE_BACKEND_PORT || null
  }
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const port = await window.electronAPI.getBackendPort()
    if (port) return port
    await sleep(300)
  }
  return null
}

async function init() {
  if (readyPromise) return readyPromise
  readyPromise = (async () => {
    const port = await waitForPort()
    if (!port) throw new Error('无法获取后端端口')
    base = `http://127.0.0.1:${port}`
    wsBase = `ws://127.0.0.1:${port}/api/ws`
  })()
  try {
    return await readyPromise
  } catch (e) {
    // 关键：init 失败时清空缓存，让后续调用重新探测端口。
    // 否则首次失败会被永久缓存，导致“连接中…”和导入任务全部失效。
    readyPromise = null
    throw e
  }
}

async function request(path, options = {}) {
  await init()
  const res = await fetch(base + path, options)
  const text = await res.text()
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text}`)
  return text ? JSON.parse(text) : null
}

function json(method, path, body) {
  return request(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
}

export const api = {
  init,
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
  isKnown: (url) => request(`/api/known?url=${encodeURIComponent(url)}`)
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
      await init()
    } catch {
      // init 失败（端口还没读到），稍后重试，避免永久卡在“连接中”
      onStatus('close')
      retry += 1
      schedule(Math.min(5000, 500 * retry))
      return
    }
    try {
      ws = new WebSocket(wsBase)
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