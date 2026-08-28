"""下载核心：HTTP 探测、分片多线程下载、任务调度。"""

from __future__ import annotations

from .engine import DownloadEngine

__all__ = ["DownloadEngine"]
