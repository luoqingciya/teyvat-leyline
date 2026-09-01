"""FastAPI 后端：把 ``DownloadEngine`` 暴露成 127.0.0.1 上的本地 HTTP + WebSocket 服务。

设计要点（Electron + Python 混合架构下由 Electron 主进程作为子进程拉起）：

* 监听 ``127.0.0.1`` 且端口为 0（随机），由操作系统分配空闲端口，避免占用冲突；
* 启动完成后向 stdout 打印 ``PORT=<实际端口>``（flush），Electron 读取后据此连接；
* REST 负责请求/响应，WebSocket ``/api/ws`` 负责把引擎的进度/状态事件下行推给前端；
* 引擎的工作线程完全不动，只把旧 ``app.py`` 里窗口/桥接层替换为 HTTP 接口。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __app_name__, __version__
from .core.engine import DownloadEngine

PORT_MARKER = "PORT="


class Server:
    """持有引擎与配置，方法签名与原 ``Bridge``/``TeyvatApp`` 一一对应。"""

    def __init__(self) -> None:
        self.save_dir = str(Path.cwd())
        self._config_file = Path(self.save_dir) / "teyvat-config.json"
        # url -> 该任务的去除重去重判断需要在 add 前查 is_known
        self.engine = DownloadEngine(
            listener=self._on_event,
            save_dir=self.save_dir,
            num_threads=8,
        )
        self._apply_saved_config()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()

    # ---- 引擎事件 -> WebSocket 广播（线程安全） -----------------------
    def _on_event(self, event: str, task: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        payload = {"event": event, "task": task}
        loop.call_soon_threadsafe(self._broadcast, payload)

    def _broadcast(self, payload: dict) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass

    # ---- 配置持久化 ---------------------------------------------------
    def _apply_saved_config(self) -> None:
        try:
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        eng = self.engine
        for key, setter in (
            ("numThreads", lambda v: eng.set_threads(int(v))),
            ("globalSpeedKbps", lambda v: eng.set_global_speed(int(v))),
            ("maxConcurrent", lambda v: eng.set_max_concurrent(int(v))),
            ("maxRetries", lambda v: eng.set_retry(int(v), eng.retry_delay)),
            ("retryDelay", lambda v: setattr(eng, "retry_delay", max(0.0, float(v)))),
            ("proxy", lambda v: eng.set_proxy(str(v))),
            ("hashCheck", lambda v: eng.set_hash_check(bool(v))),
        ):
            if key in data:
                setter(data[key])

    def _save_config(self) -> None:
        cfg = self.engine.get_config()
        payload = {
            "numThreads": cfg["numThreads"],
            "globalSpeedKbps": cfg["globalSpeedKbps"],
            "maxConcurrent": cfg["maxConcurrent"],
            "maxRetries": cfg["maxRetries"],
            "retryDelay": cfg["retryDelay"],
            "proxy": cfg["proxy"],
            "hashCheck": cfg["hashCheck"],
        }
        payload["saveDir"] = self.save_dir
        try:
            self._config_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # ---- 业务方法（对应原 js_api Bridge） -----------------------------
    def get_config(self) -> dict:
        return {**self.engine.get_config(), "version": __version__, "appName": __app_name__}

    def get_tasks(self) -> list[dict]:
        return self.engine.list_tasks()

    def add_task(self, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return {"ok": False, "known": False, "error": "链接不能为空。"}
        if self.engine.is_known(url):
            return {
                "ok": False,
                "known": True,
                "error": "该链接已在下载历史中，为避免重复下载已跳过。",
            }
        task_id = self.engine.add(url, self.save_dir)
        task = next((t for t in self.engine.list_tasks() if t["id"] == task_id), None)
        return {"ok": True, "task": task}

    def pause_task(self, task_id: str) -> dict:
        self.engine.pause(task_id)
        return {"ok": True}

    def resume_task(self, task_id: str) -> dict:
        return {"ok": self.engine.resume(task_id)}

    def cancel_task(self, task_id: str) -> dict:
        self.engine.cancel(task_id)
        return {"ok": True}

    def remove_task(self, task_id: str) -> dict:
        self.engine.remove(task_id)
        return {"ok": True}

    def is_known_url(self, url: str) -> dict:
        return {"ok": True, "known": self.engine.is_known(url or "")}

    def update_settings(self, settings: dict) -> dict:
        eng = self.engine
        if "numThreads" in settings:
            eng.set_threads(int(settings["numThreads"]))
        if "globalSpeedKbps" in settings:
            eng.set_global_speed(int(settings["globalSpeedKbps"]))
        if "maxConcurrent" in settings:
            eng.set_max_concurrent(int(settings["maxConcurrent"]))
        if "maxRetries" in settings:
            eng.set_retry(int(settings["maxRetries"]), eng.retry_delay)
        if "retryDelay" in settings:
            eng.retry_delay = max(0.0, float(settings["retryDelay"]))
        if "proxy" in settings:
            eng.set_proxy(str(settings["proxy"] or ""))
        if "hashCheck" in settings:
            eng.set_hash_check(bool(settings["hashCheck"]))
        self._save_config()
        return {"ok": True, "config": eng.get_config()}

    def set_task_speed(self, task_id: str, kbps: int) -> dict:
        self.engine.set_task_speed(task_id, int(kbps))
        return {"ok": True}

    def set_task_threads(self, task_id: str, n: int) -> dict:
        self.engine.set_task_threads(task_id, int(n))
        return {"ok": True}

    def get_history(self) -> dict:
        return {"ok": True, "items": self.engine.get_history()}

    def set_directory(self, path: str) -> dict:
        path = (path or "").strip()
        if path and Path(path).is_dir():
            self.save_dir = path
            self.engine.save_dir = path
            self._save_config()
            return {"ok": True, "path": path}
        return {"ok": False, "path": "", "error": "无效的保存目录。"}

    def shutdown(self) -> dict:
        self.engine.shutdown()
        return {"ok": True}


def create_app(server: Server) -> FastAPI:
    app = FastAPI(title="teyvat-leyline", version=__version__)
    # 仅本机服务，允许跨源是安全的（Electron 渲染层/file 与 Vite dev 都来自不同源）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    class SettingsBody(BaseModel):
        settings: dict[str, Any]

    class TaskBody(BaseModel):
        url: str

    class IntBody(BaseModel):
        value: int = 0  # 由具体接口复用

    class SpeedBody(BaseModel):
        kbps: int

    class ThreadsBody(BaseModel):
        n: int

    class DirBody(BaseModel):
        path: str

    @app.get("/api/config")
    def get_config() -> dict:
        return server.get_config()

    @app.put("/api/config")
    def put_config(body: SettingsBody) -> dict:
        return server.update_settings(body.settings)

    @app.get("/api/tasks")
    def get_tasks() -> list[dict]:
        return server.get_tasks()

    @app.post("/api/tasks")
    def add_task(body: TaskBody) -> dict:
        return server.add_task(body.url)

    @app.post("/api/tasks/{task_id}/pause")
    def pause(task_id: str) -> dict:
        return server.pause_task(task_id)

    @app.post("/api/tasks/{task_id}/resume")
    def resume(task_id: str) -> dict:
        return server.resume_task(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str) -> dict:
        return server.cancel_task(task_id)

    @app.delete("/api/tasks/{task_id}")
    def remove(task_id: str) -> dict:
        return server.remove_task(task_id)

    @app.post("/api/tasks/{task_id}/speed")
    def speed(task_id: str, body: SpeedBody) -> dict:
        return server.set_task_speed(task_id, body.kbps)

    @app.post("/api/tasks/{task_id}/threads")
    def threads(task_id: str, body: ThreadsBody) -> dict:
        return server.set_task_threads(task_id, body.n)

    @app.get("/api/history")
    def history() -> dict:
        return server.get_history()

    @app.get("/api/known")
    def known(url: str = "") -> dict:
        return server.is_known_url(url)

    @app.put("/api/directory")
    def directory(body: DirBody) -> dict:
        return server.set_directory(body.path)

    @app.post("/api/shutdown")
    def shutdown() -> dict:
        return server.shutdown()

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        if server._loop is None:
            server._loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        server._queues.add(q)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    await websocket.send_text('{"ping":true}')
                    continue
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            server._queues.discard(q)

    return app


class _PortAwareServer(uvicorn.Server):
    """绑定成功后立刻把实际端口写到 stdout（避免端口 0 的竞态与手动解析日志）。"""

    async def startup(self, sockets: list | None = None) -> None:
        await super().startup(sockets=sockets)
        port = int(self.servers[0].sockets[0].getsockname()[1])
        print(f"{PORT_MARKER}{port}", flush=True)


def main() -> None:
    server = Server()
    app = create_app(server)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        access_log=False,
    )
    _PortAwareServer(config).run()


if __name__ == "__main__":
    main()