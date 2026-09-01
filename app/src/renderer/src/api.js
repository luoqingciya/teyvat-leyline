// 封装与 Python 后端的 HTTP + WebSocket 通信。
// 端口来自 Electron 主进程从后端 stdout 读到的随机端口。

let base = ''
let wsBase = ''
let readyPromise = null

async function init() {
  if (readyPromise) return readyPromise
  readyPromise = (async () => {
    const port = window.electronAPI
      ? await window.electronAPI.getBackendPort()
      : null
    const p = port || import.meta.env.VITE_BACKEND_PORT || 61670
    base = `http://127.0.0.1:${p}`
    wsBase = `ws://127.0.0.1:${p}/api/ws`
  })()
  return readyPromise
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

// WebSocket 进度订阅：传入回调，返回取消函数。
export function subscribeProgress(handler) {
  let ws = null
  let closed = false
  let retry = 0

  const connect = async () => {
    if (closed) return
    await init()
    ws = new WebSocket(wsBase)
    ws.onopen = () => {
      retry = 0
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
      if (!closed && retry < 10) {
        retry += 1
        setTimeout(connect, 800 * retry)
      }
    }
    ws.onerror = () => ws && ws.close()
  }

  connect()
  return () => {
    closed = true
    if (ws) ws.close()
  }
}