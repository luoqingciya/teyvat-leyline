# 提瓦特地脉 · Teyuat Leyline

> A Genshin Impact–styled, multi-threaded downloader for the desktop,
> built with **pywebview** and managed entirely by **uv**.

一款原神（Genshin Impact）二次元风格的 **Python 多线程下载器**。它基于 `pywebview`
提供原生桌面窗口，前端使用纯 HTML/CSS/JS 复刻“地脉”光影的美术质感；
下载引擎参考 **aria2** 的 byte-range 分片并发思想，支持多线程、断点续传、
暂停、恢复与取消。

本项目依赖完全由 [uv](https://docs.astral.sh/uv/) 管理，开箱即用，无需手动安装依赖。

---

## ✨ 功能特性

- **多线程分片下载**：探测服务器后按 `Range` 把大文件切成多段并发下载；
- **断点续传**：使用 `.part` 临时文件 + `.part.json` 段状态清单，可随时暂停/恢复；
- **退化为单流**：对不支持 `Range` 或大小未知的资源自动改用顺序单流下载；
- **进度监控**：后台线程按固定节流推送速度、剩余时间与百分比到前端；
- **原神风 GUI**：深蓝地脉底色 + 金色的“星落”光效、毛玻璃卡片、发光任务卡；
- **uv 全托管**：`uv sync`、`uv lock`、`uv run`，无 pip 侵入；
- **多任务队列**：同时管理多个下载，各自独立进度。

## 🚀 快速开始

要求：Python 3.11+（推荐 3.12）、桌面系统（当前以 Windows 为主，pywebview 需要
Microsoft Edge WebView2 运行时，Win10/11 通常已自带）。

```bash
# 1. 同步依赖并创建虚拟环境（自动下载/使用 3.12）
uv sync

# 2. 启动图形界面
uv run teyuat-leyline

# 3. 无界面模式直接下载单个文件（便于脚本/服务器）
uv run teyuat-leyline --url "https://example.com/file.zip" --output ./downloads
```

也可以直接用模块方式运行：

```bash
uv run python -m teyuat_leyline
```

## 📦 一键打包（Windows）

仓库内置 `build.ps1`，用 **PyInstaller** 把 pywebview 应用打成独立可执行程序，
分发到没有安装 Python 的机器也能直接运行。

```powershell
.\build.ps1                    # 单目录版（推荐，启动最快、最稳）
.\build.ps1 -OneFile           # 打成单个 EXE
.\build.ps1 -Zip               # 打包完成后再压缩为 zip
.\build.ps1 -Clean             # 先清理 build/ 与 dist/
```

脚本会自动：检测 WebView2 运行时、`uv sync --extra build` 安装构建依赖、
收集前端资源与 webview/pythonnet 运行时、生成并校验产物。

默认产物：`dist/TeyuatLeyline/TeyuatLeyline.exe`（单目录版会把 `dist/TeyuatLeyline`
整个目录分发给别人即可运行）。

> 可选：在仓库根目录放置 `assets/app.ico`，脚本会自动用其作为程序图标。

## 🗂️ 项目结构

```text
teyuat-leyline/
├─ pyproject.toml            # uv / hatchling 构建与依赖清单
├─ uv.lock                  # uv 生成的锁定文件
├─ build.ps1                # 一键打包（PyInstaller）脚本
├─ launcher.py              # PyInstaller 打包入口
├─ src/teyuat_leyline/
│  ├─ app.py                 # pywebview 窗口与 JS 桥接
│  ├─ __main__.py            # CLI 入口
│  └─ core/
│     ├─ engine.py           # 任务调度 + 分片多线程 + 断点
│     ├─ http_client.py      # 探测 / Range / 文件名工具
│     └─ models.py           # 数据模型
│  └─ web/
│     ├─ index.html          # 前端页面
│     ├─ style.css           # 原神风样式
│     └─ app.js              # 前端逻辑与进度渲染
└─ tests/                    # 引擎与工具测试
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
- [pywebview](https://github.com/r0x0r/pywebview) —— 本项目所使用的桌面
  WebView 框架（本仓库也作为其使用示例出现）；
- [httpx](https://github.com/encode/httpx) —— HTTP 客户端库，用于探测与流式下载。

## 📄 许可证

本项目基于 **GNU General Public License v3.0 (GPLv3)** 开源。详见 [LICENSE](./LICENSE)。
