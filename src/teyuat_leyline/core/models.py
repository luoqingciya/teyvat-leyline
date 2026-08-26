"""核心数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """单个下载任务所处的状态。"""

    QUEUED = "queued"
    PROBING = "probing"         # 正在探测服务器（长度 / 是否支持 Range）
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

    @property
    def is_active(self) -> bool:
        return self in (TaskStatus.PROBING, TaskStatus.DOWNLOADING)

    @property
    def is_stopped(self) -> bool:
        return self in (TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED)


class SegmentStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"


@dataclass
class Segment:
    """分片（byte-range）在磁盘上的位置与下载进度。"""

    index: int
    start: int
    end: int
    downloaded: int = 0
    status: SegmentStatus = SegmentStatus.PENDING

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class DownloadTask:
    """一个下载任务的运行时状态。"""

    id: str
    url: str
    save_path: str
    filename: str
    total_size: int | None = None
    downloaded: int = 0
    status: TaskStatus = TaskStatus.QUEUED
    segments: list[Segment] = field(default_factory=list)
    supports_range: bool = True
    threads: int = 1
    error: str = ""
    created_at: float = 0.0
    etag: str = ""
    last_modified: str = ""
    tmp_path: str = ""
    resume_path: str = ""

    def snapshot(self, speed: float = 0.0, eta: float | None = None) -> dict[str, Any]:
        """返回可供 UI 直接使用的纯 JSON 快照。"""
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "savePath": self.save_path,
            "total": self.total_size,
            "downloaded": self.downloaded,
            "status": self.status.value,
            "speed": speed,
            "eta": eta,
            "error": self.error,
            "threads": self.threads,
            "supportsRange": self.supports_range,
        }
