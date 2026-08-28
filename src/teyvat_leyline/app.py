"""pywebview 桌面应用：窗口创建、JS-Python 桥接、进度事件推送。"""

from __future__ import annotations

import json
import queue
import sys
import threading
from importlib import resources
from pathlib import Path

import webview

from . import __app_name__, __version__
from .core.engine import DownloadEngine


def _default_save_dir() -> str:
    # 全部数据保存在当前运行目录下
    return str(Path.cwd())


class Bridge:
    """暴露给前端 JS 的 API（经 ``window.pywebview.api`` 调用）。"""

    def __init__(self, app: "TeyvatApp") -> None:
        self.app = app

    @property
    def engine(self) -> DownloadEngine:
        return self.app.engine

    # ---- 任务操作 -----------------------------------------------------
    def add_task(self, url: str) -> dict:
        if self.engine.is_known(url):
            return {
                "ok": False,
                "known": True,
                "error": "该链接已在下载历史中，为避免重复下载已跳过。",
            }
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
        self.app._save_config()
        return {"ok": True, "numThreads": self.engine.num_threads}

    # ---- 全局设置（限速/并发/重试/代理/校验） -------------------------
    def update_settings(self, settings: dict) -> dict:
        """一次性批量更新全局设置，并写入本地配置文件。"""
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
        self.app._save_config()
        return {"ok": True, "config": eng.get_config()}

    # ---- 每任务独立设置 ----------------------------------------------
    def set_task_speed(self, task_id: str, kbps: int) -> dict:
        self.engine.set_task_speed(task_id, int(kbps))
        return {"ok": True}

    def set_task_threads(self, task_id: str, n: int) -> dict:
        self.engine.set_task_threads(task_id, int(n))
        return {"ok": True}

    # ---- 历史 -------------------------------------------------------
    def get_history(self) -> dict:
        return {"ok": True, "items": self.engine.get_history()}

    def is_known_url(self, url: str) -> dict:
        return {"ok": True, "known": self.engine.is_known(url)}

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


class TeyvatApp:
    """持有窗口与引擎，负责将引擎事件转发到前端 JS。"""

    def __init__(self) -> None:
        self.save_dir = _default_save_dir()
        self._config_file = Path(self.save_dir) / "teyvat-config.json"
        self.window: webview.Window | None = None
        self.engine = DownloadEngine(
            listener=self._on_event,
            save_dir=self.save_dir,
            num_threads=8,
        )
        self._apply_saved_config()
        # 事件回推统一交给单一推送线程串行执行 evaluate_js，
        # 避免多个下载线程并发触碰 webview（Windows 下不安全）。
        self._push_queue: queue.Queue = queue.Queue()
        self._stop_push = threading.Event()
        self._pusher = threading.Thread(
            target=self._push_loop, name="ui-pusher", daemon=True
        )
        self._pusher.start()

    def _apply_saved_config(self) -> None:
        """启动时从本地配置文件恢复全局设置。"""
        try:
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        eng = self.engine
        if isinstance(data.get("numThreads"), int):
            eng.set_threads(data["numThreads"])
        if isinstance(data.get("globalSpeedKbps"), int):
            eng.set_global_speed(data["globalSpeedKbps"])
        if isinstance(data.get("maxConcurrent"), int):
            eng.set_max_concurrent(data["maxConcurrent"])
        if isinstance(data.get("maxRetries"), int):
            eng.set_retry(data["maxRetries"], eng.retry_delay)
        if isinstance(data.get("retryDelay"), (int, float)):
            eng.retry_delay = max(0.0, float(data["retryDelay"]))
        if isinstance(data.get("proxy"), str):
            eng.set_proxy(data["proxy"])
        if isinstance(data.get("hashCheck"), bool):
            eng.set_hash_check(data["hashCheck"])

    def _save_config(self) -> None:
        """将当前全局设置写回本地配置文件，供下次启动恢复。"""
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
        try:
            self._config_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _on_event(self, event: str, task: dict) -> None:
        """引擎回调（可能来自后台线程）→ 入队，交由推送线程转 JS 事件。"""
        self._push_queue.put((event, task))

    def _push_loop(self) -> None:
        while not self._stop_push.is_set():
            try:
                event, task = self._push_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            window = self.window
            if window is None:
                continue
            payload = json.dumps({"event": event, "task": task}, ensure_ascii=True)
            try:
                window.evaluate_js(f"window.__teyvatUI && window.__teyvatUI({payload})")
            except Exception:  # noqa: BLE001  (窗口关闭期间的回调可安全忽略)
                pass

    def close(self) -> None:
        self._stop_push.set()
        try:
            self._pusher.join(timeout=0.5)
        except RuntimeError:
            pass
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
        return Path(base) / "teyvat_leyline" / "web"
    return Path(resources.files("teyvat_leyline").joinpath("web"))


def run(*, debug: bool = False) -> None:
    """创建窗口并进入 pywebview 事件循环。"""
    app = TeyvatApp()
    bridge = Bridge(app)
    index = str(_assets() / "index.html")

    window = webview.create_window(
        f"提瓦特地脉 · Teyvat Leyline",
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
