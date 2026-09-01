import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  getBackendPort: () => ipcRenderer.invoke('backend:getPort'),
  pickDirectory: () => ipcRenderer.invoke('dialog:pickDir')
})