"""pywebview 桌面应用：窗口创建、JS-Python 桥接（进度由前端轮询获取）。"""

from __future__ import annotations

import json
import sys
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
        """引擎进度回调。进度改为前端轮询 get_tasks() 获取，无需再主动推送。"""

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
    # 改用内存临时 profile：WebView2 持久化目录 %APPDATA%\pywebview 在本机反复启动
    # 后损坏/被锁，导致一打开就“未响应”。应用不依赖浏览器存储，安全改用 InPrivate。
    webview.start(debug=debug, private_mode=True)
