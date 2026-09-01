import { BrowserWindow, app, dialog, ipcMain, shell } from 'electron'
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import readline from 'node:readline'

const __dirname = dirname(fileURLToPath(import.meta.url))
const isDev = !!process.env['ELECTRON_RENDERER_URL']

// 项目根目录（含 pyproject.toml / uv / src ），打包后指向外层 resources。
// 开发时 __dirname = app/out/main，向上三级即仓库根；打包后由 extraResources 把 python 后端放到 resources/backend。
const PROJECT_ROOT = join(__dirname, '..', '..', '..')

let backend = null
let backendPort = null

function resolveBackendLaunch() {
  if (isDev) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return {
      command: 'uv',
      args: ['run', 'teyvat-server'],
      cwd: PROJECT_ROOT
    }
  }
  // 生产：extraResources 里的 PyInstaller 单文件后端
  const exe = join(process.resourcesPath, 'backend', 'teyvat-server.exe')
  if (!existsSync(exe)) {
    throw new Error('未找到后端可执行文件: ' + exe)
  }
  // 保存目录由后端自己做为主目录；单文件运行 cwd 设为 resources
  return { command: exe, args: [], cwd: process.resourcesPath }
}

function startBackend() {
  backendPort = null
  const { command, args, cwd } = resolveBackendLaunch()
  backend = spawn(command, args, { cwd, windowsHide: false })

  backend.stdout?.setEncoding('utf-8')
  const rl = readline.createInterface({ input: backend.stdout })

  rl.on('line', (line) => {
    const m = /^PORT=(\d+)/.exec(line.trim())
    if (m) {
      backendPort = Number(m[1])
      console.log(`[backend] 端口 ${backendPort}`)
    } else {
      console.log('[backend]', line)
    }
  })

  backend.stderr?.setEncoding('utf-8')
  backend.stderr?.on('data', (chunk) => console.error('[backend]', chunk.toString()))

  backend.on('exit', (code, signal) => {
    console.log('[backend] 退出', code, signal)
    backend = null
  })
  backend.on('error', (err) => console.error('[backend] 启动失败', err))
}

function killProcessTree(pid) {
  if (process.platform === 'win32') {
    // 强制结束包括子进程在内的整棵进程树。
    // PyInstaller 单文件后端会解压出真正的 uvicorn 子进程，仅 kill 引导进程会残留。
    // 用 spawnSync 同步等待 taskkill 完成，避免 Electron 退出时把 taskkill 子进程一并带走导致后端残留。
    try {
      spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
    } catch {
      /* ignore */
    }
  } else {
    try {
      process.kill(pid, 'SIGTERM')
    } catch {
      /* ignore */
    }
  }
}

function stopBackend() {
  if (backend && backend.exitCode === null) {
    killProcessTree(backend.pid)
  }
  backend = null
  backendPort = null
  if (process.platform === 'win32') {
    // 兜底：清理任何残留的同名后端进程（含历史遗留的孤儿进程）。
    try {
      spawnSync('taskkill', ['/IM', 'teyvat-server.exe', '/T', '/F'], { stdio: 'ignore', windowsHide: true })
    } catch {
      /* ignore */
    }
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1080,
    height: 760,
    minWidth: 900,
    minHeight: 620,
    backgroundColor: '#0b0e1c',
    title: '提瓦特地脉 · Teyvat Leyline',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (isDev && process.env['ELECTRON_RENDERER_URL']) {
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return win
}

// ---- IPC ----
ipcMain.handle('backend:getPort', () => backendPort)
ipcMain.handle('dialog:pickDir', async () => {
  const r = await dialog.showOpenDialog({
    properties: ['openDirectory', 'createDirectory'],
    title: '选择保存目录'
  })
  if (r.canceled || !r.filePaths[0]) return null
  return r.filePaths[0]
})

// ---- 生命周期 ----
app.whenReady().then(() => {
  try {
    startBackend()
  } catch (err) {
    console.error('后端启动失败', err)
  }
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopBackend()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopBackend()
})