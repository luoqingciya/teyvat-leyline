# 打包 Python 后端为单文件 teyvat-server.exe，输出到 backend-dist
# 供 electron-builder 通过 extraResources 放进最终安装包。

$ErrorActionPreference = "Stop"

Write-Host "=== 开始打包 Python 后端 teyvat-server ===" -ForegroundColor Cyan

# 按照约定，必须用 uv run python -m PyInstaller 绕过 uv 0.12.3 trampoline 路径问题
uv run python -m PyInstaller --onefile --name teyvat-server `
  --paths src `
  --hidden-import=uvicorn.logging `
  --hidden-import=uvicorn.lifespan `
  --hidden-import=uvicorn.lifespan.on `
  --hidden-import=uvicorn.lifespan.off `
  --hidden-import=uvicorn.protocols.http `
  --hidden-import=uvicorn.protocols.http.h11_impl `
  --hidden-import=uvicorn.protocols.websockets `
  --hidden-import=click `
  backend_entry.py

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller 打包失败"
    exit 1
}

# 移动到 backend-dist 供 electron-builder 读取
$dist = Join-Path $PWD.Path "dist"
$target = Join-Path $PWD.Path "backend-dist"
if (-not (Test-Path $target)) { New-Item -ItemType Directory -Path $target | Out-Null }
Move-Item -Force (Join-Path $dist "teyvat-server.exe") (Join-Path $target "teyvat-server.exe")

# 清理
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue

Write-Host "=== 后端打包完成：backend-dist/teyvat-server.exe ===" -ForegroundColor Green
