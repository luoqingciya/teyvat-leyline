<#
.SYNOPSIS
    Teyvat Leyline 一键打包脚本（Windows）。

.DESCRIPTION
    使用 uv 同步依赖后，通过 PyInstaller 把 pywebview 应用打包为独立可执行程序，
    默认生成单目录版（build/dist 下的文件夹），保证最快启动与稳定性。

.EXAMPLE
    .\build.ps1                     # 单目录版
    .\build.ps1 -OneFile            # 单文件 EXE
    .\build.ps1 -Zip                # 打包后自动压缩为 zip
    .\build.ps1 -Clean              # 先清理 build/dist
#>

param(
    [switch]$Clean,
    [switch]$OneFile,
    [switch]$Zip,
    [string]$Name = "TeyvatLeyline",
    [string]$Icon = "assets/app.ico"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ---- 0. 环境检查 -----------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "未找到 uv。请先安装 uv：https://docs.astral.sh/uv/"
}

# WebView2 运行时检测（pywebview 在 Windows 上依赖它）
$wvPath = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$wv = Get-ItemProperty $wvPath -ErrorAction SilentlyContinue
if ($wv) {
    Write-Host "WebView2 Runtime: $($wv.name) (pv=$($wv.pv))" -ForegroundColor Green
} else {
    Write-Warning "未检测到 Microsoft Edge WebView2 Runtime，打包后可能无法运行。"
}

# ---- 1. 清理旧产物 ---------------------------------------------------------
if ($Clean) {
    Write-Step "清理旧构建产物 (build/, dist/)"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

# ---- 2. 同步构建依赖 -------------------------------------------------------
Write-Step "同步构建依赖 (uv sync --extra build)"
uv sync --extra build
if ($LASTEXITCODE -ne 0) { throw "uv sync 失败。" }

# ---- 3. 准备 PyInstaller 参数 ----------------------------------------------
$mode  = if ($OneFile) { "--onefile" } else { "--onedir" }
$webSrc = (Resolve-Path "src/teyvat_leyline/web").Path
$pyiArgs = @(
    "--noconfirm", "--clean", $mode, "--windowed",
    "--name", $Name,
    "--specpath", "build",
    "--workpath", "build/pyinstaller",
    "--distpath", "dist",
    # 打包前端资源（Windows 用 ; 分隔 src;dest）
    "--add-data", "$webSrc;teyvat_leyline/web",
    # 收集第三方/自有包与子模块，避免 webview 运行时后端缺失
    "--collect-all", "teyvat_leyline",
    "--collect-all", "webview",
    "--collect-all", "clr_loader",
    "--collect-all", "pythonnet",
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "bottle",
    "--hidden-import", "proxy_tools",
    "launcher.py"
)

if ($Icon -and (Test-Path $Icon)) {
    $pyiArgs += @("--icon", (Resolve-Path $Icon).Path)
    Write-Host "使用图标：$Icon" -ForegroundColor Green
}

# ---- 4. 运行 PyInstaller ---------------------------------------------------
Write-Step "运行 PyInstaller ($mode)"
# 用 `python -m PyInstaller` 而非 `pyinstaller` 控制台入口：
# uv 0.12.x 的 trampoline 在解析脚本路径时会报 “failed to canonicalize script path”。
$native = @("run", "python", "-m", "PyInstaller") + $pyiArgs
& uv @native
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败（退出码 $LASTEXITCODE）。" }

$exe = if ($OneFile) { "dist/$Name.exe" } else { "dist/$Name/$Name.exe" }
if (-not (Test-Path $exe)) { throw "未生成期望的产物：$exe" }

Write-Host "`n打包完成：$exe" -ForegroundColor Green

# ---- 5. （可选）压缩 -------------------------------------------------------
if ($Zip) {
    Write-Step "压缩产物为 zip"
    $zipName = "dist/${Name}-win64.zip"
    if (Test-Path $zipName) { Remove-Item -Force $zipName }
    Compress-Archive -Path "dist/$Name" -DestinationPath $zipName
    Write-Host "已生成：$zipName" -ForegroundColor Green
}

Write-Host "`n★ 双击 $exe 即可启动；将整个 dist/$Name 目录（或 zip）分发给他人即可运行，无需安装 Python。" -ForegroundColor Cyan
