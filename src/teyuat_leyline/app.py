"""pywebview 桌面应用：窗口创建、JS-Python 桥接、进度事件推送。"""

from __future__ import annotations

import json
import sys
import threading
from importlib import resources
from pathlib import Path

import webview

from . import __app_name__, __version__
from .core.engine import DownloadEngine


def _default_save_dir() -> str:
    downloads = Path.home() / "Downloads"
    return str(downloads) if downloads.is_dir() else str(Path.home())


class Bridge:
    """暴露给前端 JS 的 API（经 ``window.pywebview.api`` 调用）。"""

    def __init__(self, app: "TeyuatApp") -> None:
        self.app = app

    @property
    def engine(self) -> DownloadEngine:
        return self.app.engine

    # ---- 任务操作 -----------------------------------------------------
    def add_task(self, url: str) -> dict:
        task_id = self.engine.add(url, self.app.save_dir)
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

    def get_tasks(self) -> list[dict]:
        return self.engine.list_tasks()

    # ---- 配置 / 目录 --------------------------------------------------
    def get_config(self) -> dict:
        return {**self.engine.get_config(), "version": __version__, "appName": __app_name__}

    def set_threads(self, num_threads: int) -> dict:
        self.engine.set_threads(int(num_threads))
        return {"ok": True, "numThreads": self.engine.num_threads}

    def choose_directory(self) -> dict:
        window = self.app.window
        if window is None:
            return {"ok": False, "path": ""}
        result = window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and result[0]:
            path = result[0]
            self.app.save_dir = path
            self.engine.save_dir = path
            return {"ok": True, "path": path}
        return {"ok": False, "path": ""}

    def shutdown(self) -> dict:
        self.app.close()
        return {"ok": True}


class TeyuatApp:
    """持有窗口与引擎，负责将引擎事件转发到前端 JS。"""

    def __init__(self) -> None:
        self.save_dir = _default_save_dir()
        self.window: webview.Window | None = None
        self.engine = DownloadEngine(
            listener=self._on_event,
            save_dir=self.save_dir,
            num_threads=8,
        )

    def _on_event(self, event: str, task: dict) -> None:
        """引擎回调（可能来自后台线程）→ 推送 JS 事件。"""
        window = self.window
        if window is None:
            return
        payload = json.dumps({"event": event, "task": task}, ensure_ascii=True)
        try:
            window.evaluate_js(f"window.__teyuatUI && window.__teyuatUI({payload})")
        except Exception:  # noqa: BLE001  (窗口关闭期间的回调可安全忽略)
            pass

    def close(self) -> None:
        if self.window:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.engine.shutdown()


def _assets() -> Path:
    """返回 web 资源目录。

    打包（PyInstaller）后 ``importlib.resources`` 对非代码数据可能解析失败，
    因此在 frozen 模式优先读取 ``sys._MEIPASS`` 下的解包目录。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", str(Path(sys.executable).resolve().parent))
        return Path(base) / "teyuat_leyline" / "web"
    return Path(resources.files("teyuat_leyline").joinpath("web"))


def run(*, debug: bool = False) -> None:
    """创建窗口并进入 pywebview 事件循环。"""
    app = TeyuatApp()
    bridge = Bridge(app)
    index = str(_assets() / "index.html")

    window = webview.create_window(
        f"提瓦特地脉 · Teyuat Leyline",
        index,
        js_api=bridge,
        width=1120,
        height=760,
        min_size=(900, 620),
        background_color="#0d1020",
        text_select=True,
    )
    app.window = window
    webview.start(debug=debug, private_mode=False)
