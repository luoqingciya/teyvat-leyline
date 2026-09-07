import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  // 返回 { port, token }：token 用于本地 API 鉴权，两者都可能为 null（后端未就绪）
  getBackendAuth: () => ipcRenderer.invoke('backend:getAuth'),
  pickDirectory: () => ipcRenderer.invoke('dialog:pickDir')
})
