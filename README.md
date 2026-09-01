# 提瓦特地脉 · Teyvat Leyline

> A Genshin Impact–styled, multi-threaded downloader for the desktop,
> built with **Electron + Python** hybrid architecture (Vue3 frontend + FastAPI backend),
> and managed by **uv** (Python) + **npm** (frontend).

一款原神（Genshin Impact）二次元风格的 **多线程下载器**。桌面壳采用 **Electron**，
前端用 **Vue 3** 复刻“地脉”光影的美术质感，后端为 **FastAPI** 本地 HTTP 服务，
Python 下载引擎参考 **aria2** 的 byte-range 分片并发思想，支持多线程、断点续传、
暂停、恢复与取消。

Python 依赖由 [uv](https://docs.astral.sh/uv/) 管理；前端依赖由 npm 管理。

---

## ✨ 功能特性

- **多线程分片下载**：探测服务器后按 `Range` 把大文件切成多段并发下载；
- **断点续传**：使用 `.part` 临时文件 + `.part.json` 段状态清单，可随时暂停/恢复；
- **退化为单流**：对不支持 `Range` 或大小未知的资源自动改用顺序单流下载；
- **进度实时推送**：后端经 WebSocket 推送速度、剩余时间与百分比到前端；
- **原神风 GUI**：深蓝地脉底色 + 金色“星落”光效、毛玻璃卡片、发光任务卡；
- **多任务队列**：同时管理多个下载，各自独立进度，支持每任务限速与调整线程数；
- **限速 / 并发 / 重试 / 代理 / SHA256 校验**：全局与每任务均可配置。

## 🏗️ 技术架构

```
┌─────────────────────────── Electron 桌面壳 ───────────────────────────┐
│  app/src/main/index.js     主进程：spawn 后端、读端口、目录选择 IPC       │
│        │  contextBridge (preload)                                      │
│  app/src/renderer/         Vue3 + Vite 前端                            │
│        │  fetch + WebSocket                                            │
└────────┼───────────────────────────────────────────────────────────────┘
         ▼  127.0.0.1:<随机端口>
┌───────────────────────── Python 后端 ──────────────────────────────────┐
│  src/teyvat_leyline/server.py   FastAPI HTTP + WebSocket（随机端口）     │
│        │                                                               │
│  core/engine.py                分片多线程下载引擎                       │
└────────────────────────────────────────────────────────────────────────┘
```

- **随机端口**：后端每次启动向 stdout 打印 `PORT=<n>`，Electron 读取后传给前端，
  避免端口冲突；
- **进程生命周期**：Electron 主进程拉起后端子进程，窗口关闭时一并回收。

## 🚀 快速开始

要求：Python 3.11+、Node.js 18+（打包还需 npm）。

### 1. Python 后端 / CLI

```bash
# 同步依赖并创建虚拟环境
uv sync

# 无界面模式直接下载单个文件（便于脚本/服务器）
uv run teyvat-leyline --url "https://example.com/file.zip" --output ./downloads
```

### 2. 桌面开发（Electron + Vue3）

```bash
cd app
npm install
npm run dev        # 启动 Electron 开发窗口，自动拉起 Python 后端
```

开发时后端每次启动分配随机端口，前端自动从 Electron 主进程获取并连接。

## 📦 桌面打包（Windows）

```powershell
# 1) 安装前端依赖
cd app
npm install

# 2) 构建 Python 后端为单文件 EXE（产出 backend-dist/teyvat-server.exe）
cd ..
.\build-backend.ps1

# 3) 打包桌面应用
cd app
npm run dist        # 生成 NSIS 安装包，产物在 app/release2/
npm run dist:dir    # 仅生成免安装解包目录（便于测试）
```

> **国内网络提示**：electron-builder 下载 Electron / NSIS 二进制可能较慢或失败，
> 可先设置国内镜像再打包：
>
> ```powershell
> $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
> $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
> ```

打包产物会把后端 EXE 放到安装目录的 `resources/backend/` 下，由 Electron 主进程启动。

## 🗂️ 项目结构

```text
teyvat-leyline/
├─ app/                        # Electron + Vue3 桌面前端
│  ├─ src/
│  │  ├─ main/index.js         # Electron 主进程（拉起后端、读端口、目录选择）
│  │  ├─ preload/index.js      # contextBridge 桥接
│  │  └─ renderer/             # Vue3 渲染层
│  │     ├─ src/
│  │     │  ├─ App.vue         # 主界面
│  │     │  ├─ api.js          # fetch + WebSocket 封装
│  │     │  ├─ utils.js        # 格式化工具
│  │     │  ├─ assets/style.css
│  │     │  └─ components/     # TaskCard / SettingsModal / Toaster
│  │     └─ index.html
│  ├─ electron.vite.config.js  # electron-vite 构建配置
│  ├─ electron-builder.yml     # 桌面打包配置
│  └─ package.json
├─ src/teyvat_leyline/
│  ├─ server.py                # FastAPI HTTP + WebSocket 后端入口
│  ├─ __main__.py              # CLI：无界面下载 + 启动指引
│  └─ core/
│     ├─ engine.py             # 任务调度 + 分片多线程 + 断点
│     ├─ http_client.py        # 探测 / Range / 文件名工具
│     ├─ models.py             # 数据模型
│     └─ ratelimiter.py        # 限速 / 并发控制
├─ backend_entry.py            # PyInstaller 后端包内入口
├─ build-backend.ps1           # 打包后端为单文件 EXE
├─ tools/make_icon.py          # 应用图标生成脚本（Pillow）
├─ assets/app.ico              # 应用图标（含多分辨率）
├─ backend-dist/               # 后端 EXE 产物（打包生成，不入库）
├─ tests/                      # 引擎与工具测试
├─ pyproject.toml              # uv / hatchling 构建与依赖清单、入口
└─ uv.lock                     # uv 生成的锁定文件
```

## 🧭 设计说明

下载流程大致为三态：

1. **探测**：`HEAD`（必要时退回 `Range: bytes=0-0` 的 GET）读取 `Content-Length`、
   `Accept-Ranges`、`Content-Disposition`，得到文件大小、是否支持分片、推荐文件名。
2. **分片**：支持 Range 且体积足够时，把文件切成 `N` 段（每段约等于 1 线程），
   每段一个线程用 `Range` 头从各自偏移并发写入临时文件；每段在磁盘上是去重的
   byte-range，无共享 offset 冲突。
3. **收尾**：全部段完成后校验文件大小，`os.replace` 改名到目标路径，删除清单；
   若中途暂停/出错则保存 `.part.json`，下次继续从对应偏移续传。

### 参考的开源项目

本项目在实现时参考并借鉴了以下优秀开源项目的思路：

- [aria2](https://github.com/aria2/aria2) —— 经典的多协议、多线程（多连接）
  CLI 下载器，本项目的 `Range` 分片并发与断点续传思路受其启发；
- [Electron](https://github.com/electron/electron) —— 桌面应用壳与打包方案；
- [httpx](https://github.com/encode/httpx) —— HTTP 客户端库，用于探测与流式下载。

## 📄 许可证

本项目基于 **GNU General Public License v3.0 (GPLv3)** 开源。详见 [LICENSE](./LICENSE)。