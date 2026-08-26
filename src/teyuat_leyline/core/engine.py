"""多线程分片下载引擎。

下载策略参照 aria2 的分片（byte-range）思想：

* 先 ``HEAD`` / ``Range: bytes=0-0`` 探测文件大小与是否支持 Range；
* 支持 Range 的大文件切分为若干分片，用线程池并发下载；
* 不支持 Range 或大小未知的文件退化为单流顺序下载；
* 断点续传：使用 ``.part.json`` 段状态清单 + ``.part`` 临时文件。

``DownloadEngine`` 是任务调度中心，它负责创建任务、暂停/恢复/取消，
并由一个后台监控线程节流地向 UI 推送进度事件。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from .http_client import (
    CHUNK_SIZE,
    DEFAULT_USER_AGENT,
    _safe_client,
    probe,
    sanitize_filename,
    unique_dest,
)
from .models import DownloadTask, Segment, SegmentStatus, TaskStatus

MIN_SEGMENT_SIZE = 512 * 1024
MAX_SEGMENTS = 16
MONITOR_INTERVAL = 0.2

Listener = Callable[[str, dict], None]


def _now() -> float:
    return time.monotonic()


class _SegmentWorker(threading.Thread):
    """下载单个 byte-range 分片。"""

    def __init__(self, host: "_TaskWorker", segment: Segment) -> None:
        super().__init__(daemon=True)
        self._host = host
        self._segment = segment

    def run(self) -> None:  # noqa: PLR0912  (分支较多但清晰)
        host = self._host
        task = host.task
        segment = self._segment
        segment.status = SegmentStatus.DOWNLOADING

        start = segment.start + segment.downloaded
        end = segment.end
        headers = {**host.headers, "Range": f"bytes={start}-{end}"}

        try:
            with host.client.stream("GET", host.url, headers=headers) as resp:
                # 服务器忽略 Range 却返回 200 表示不支持分片续传
                if resp.status_code not in (200, 206):
                    raise RuntimeError(f"服务端返回 HTTP {resp.status_code}")
                if resp.status_code == 200 and segment.start > 0:
                    raise RuntimeError("服务端忽略了 Range 请求，无法分片续传")

                offset = segment.start + segment.downloaded
                resp.raise_for_status()
                with open(task.tmp_path, "r+b") as fh:
                    fh.seek(offset)
                    for chunk in resp.iter_bytes(64 * 1024):
                        if host.pause.is_set() or host.cancel.is_set():
                            break
                        fh.write(chunk)
                        segment.downloaded += len(chunk)
                        if segment.downloaded >= segment.length:
                            break
                    fh.flush()
        except Exception as exc:  # noqa: BLE001  (记录后转交上层判断)
            segment.status = SegmentStatus.ERROR
            host.record_error(segment, str(exc))
            return

        segment.status = (
            SegmentStatus.PAUSED
            if host.pause.is_set() or host.cancel.is_set()
            else (SegmentStatus.DONE if segment.downloaded >= segment.length else SegmentStatus.PAUSED)
        )


class _TaskWorker(threading.Thread):
    """单个任务的执行线程：探测 -> 分片 -> 汇聚 -> 收尾。"""

    def __init__(self, engine: "DownloadEngine", task: DownloadTask, *, resume: bool = False) -> None:
        super().__init__(daemon=True)
        self.engine = engine
        self.task = task
        self.resume = resume
        self.pause = threading.Event()
        self.cancel = threading.Event()
        self.url = task.url
        self.headers: dict[str, str] = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            **engine.extra_headers,
        }
        self.client = _safe_client(verify=engine.verify)
        self._segment_threads: list[_SegmentWorker] = []
        self._errors: list[str] = []

    # ---- 任务状态上报 -------------------------------------------------
    def notify(self, event: str, task: DownloadTask | None = None, speed: float = 0.0) -> None:
        self.engine._notify(event, task or self.task, speed)

    def record_error(self, segment: Segment, message: str) -> None:
        if message not in self._errors:
            self._errors.append(message)

    # ---- 主流程 -------------------------------------------------------
    def run(self) -> None:  # noqa: PLR0912
        task = self.task
        try:
            task.status = TaskStatus.PROBING
            self.notify("status")

            info = probe(self.url, verify=self.engine.verify, extra_headers=self.engine.extra_headers)

            if self.pause.is_set() or self.cancel.is_set():
                self._abort_on_early_stop()
                return

            self._apply_probe_info(info)

            if self.task.total_size is None or not self.task.supports_range:
                self._download_stream()
            else:
                self._download_segmented()
        except Exception as exc:  # noqa: BLE001
            task.status = TaskStatus.ERROR
            task.error = str(exc)
            self.notify("status")
        finally:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self.engine._workers.pop(task.id, None)

    def _apply_probe_info(self, info) -> None:
        """将探测结果写回任务，并使用真实文件名修正落盘路径。"""
        task = self.task
        task.total_size = info.content_length
        task.supports_range = info.supports_range
        task.etag = info.etag
        task.last_modified = info.last_modified

        real_name = sanitize_filename(info.filename)
        if real_name != task.filename:
            directory = str(Path(task.save_path).parent)
            task.save_path = unique_dest(directory, real_name)
            task.filename = real_name
            task.tmp_path = task.save_path + ".part"
            task.resume_path = task.save_path + ".part.json"

    # ---- 单流下载（无 Range / 大小未知） --------------------------------
    def _download_stream(self) -> None:
        task = self.task
        task.threads = 1
        task.status = TaskStatus.DOWNLOADING
        self.notify("status")

        with open(task.tmp_path, "wb") as fh:
            with self.client.stream("GET", self.url, headers=self.headers) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(64 * 1024):
                    if self.pause.is_set() or self.cancel.is_set():
                        break
                    fh.write(chunk)
                    task.downloaded += len(chunk)
                    task.total_size = task.downloaded

        if self.cancel.is_set():
            self._cleanup(remove_part=True)
            task.status = TaskStatus.CANCELLED
            self.notify("status")
        elif self.pause.is_set():
            task.status = TaskStatus.PAUSED
            self.notify("status")
        else:
            os.replace(task.tmp_path, task.save_path)
            task.status = TaskStatus.COMPLETED
            task.downloaded = task.total_size or 0
            self.notify("status")

    # ---- 分片下载 -----------------------------------------------------
    def _download_segmented(self) -> None:
        task = self.task
        total = int(task.total_size or 0)
        threads = self._segment_count(total)
        task.threads = threads
        task.segments = self._build_segments(total, threads)

        if self.resume:
            self._load_manifest(task)
        else:
            self._ensure_file(total)

        task.downloaded = sum(s.downloaded for s in task.segments)
        remaining = [s for s in task.segments if s.downloaded < s.length]

        if not remaining:
            self._finish_segmented()
            return

        task.status = TaskStatus.DOWNLOADING
        self.notify("status")

        for segment in remaining:
            worker = _SegmentWorker(self, segment)
            self._segment_threads.append(worker)
            worker.start()

        self._wait_segments(remaining)
        self._finish_segmented()

    def _segment_count(self, total: int) -> int:
        if total <= MIN_SEGMENT_SIZE:
            return 1
        ideal = min(self.engine.num_threads, total // MIN_SEGMENT_SIZE or 1, MAX_SEGMENTS)
        return max(1, ideal)

    @staticmethod
    def _build_segments(total: int, threads: int) -> list[Segment]:
        chunk, rem = divmod(total, threads)
        segments: list[Segment] = []
        start = 0
        for i in range(threads):
            length = chunk + (1 if i < rem else 0)
            end = start + length - 1
            segments.append(Segment(index=i, start=start, end=end))
            start = end + 1
        return segments

    def _ensure_file(self, total: int) -> None:
        with open(self.task.tmp_path, "wb"):
            pass
        if total > 0:
            with open(self.task.tmp_path, "r+b") as fh:
                fh.truncate(total)

    def _load_manifest(self, task: DownloadTask) -> None:
        manifest = Path(task.resume_path)
        if not manifest.exists() or not Path(task.tmp_path).exists():
            self._ensure_file(task.total_size or 0)
            return
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._ensure_file(task.total_size or 0)
            return

        if data.get("total_size") != task.total_size:
            self._ensure_file(task.total_size or 0)
            return
        if task.etag and data.get("etag") and data["etag"] != task.etag:
            self._ensure_file(task.total_size or 0)
            return

        segs = data.get("segments", [])
        if len(segs) != len(task.segments):
            self._ensure_file(task.total_size or 0)
            return
        for segment, sdata in zip(task.segments, segs):
            if (segment.start, segment.end) != (sdata.get("start"), sdata.get("end")):
                self._ensure_file(task.total_size or 0)
                return
            segment.downloaded = int(sdata.get("downloaded", 0))
            if segment.downloaded >= segment.length:
                segment.status = SegmentStatus.DONE
        if not Path(task.tmp_path).exists():
            self._ensure_file(task.total_size or 0)

    def _save_manifest(self, task: DownloadTask) -> None:
        payload = {
            "url": task.url,
            "etag": task.etag,
            "last_modified": task.last_modified,
            "total_size": task.total_size,
            "threads": task.threads,
            "segments": [
                {"index": s.index, "start": s.start, "end": s.end, "downloaded": s.downloaded}
                for s in task.segments
            ],
        }
        try:
            Path(task.resume_path).write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _wait_segments(self, remaining: Iterable[Segment]) -> None:
        while True:
            if self.pause.is_set() or self.cancel.is_set():
                break
            if all(s.status in (SegmentStatus.DONE, SegmentStatus.ERROR) for s in remaining):
                break
            time.sleep(0.05)

    def _finish_segmented(self) -> None:
        task = self.task
        # 确保分片线程已停止写盘，避免 Windows 文件锁导致重命名/删除失败
        for worker in self._segment_threads:
            worker.join(timeout=5)
        task.downloaded = sum(s.downloaded for s in task.segments)
        self.notify("status")

        if self.cancel.is_set():
            self._cleanup(remove_part=True)
            task.status = TaskStatus.CANCELLED
            self.notify("status")
            return

        ok = all(s.status == SegmentStatus.DONE for s in task.segments)
        if not ok:
            self._save_manifest(task)
            errored = any(s.status == SegmentStatus.ERROR for s in task.segments)
            task.status = TaskStatus.ERROR if errored else TaskStatus.PAUSED
            if errored and self._errors:
                task.error = self._errors[0]
            self.notify("status")
            return

        if task.total_size is not None and os.path.getsize(task.tmp_path) != task.total_size:
            task.status = TaskStatus.ERROR
            task.error = "文件大小与预期不符"
            self._save_manifest(task)
            self.notify("status")
            return

        os.replace(task.tmp_path, task.save_path)
        self._cleanup(remove_part=False)
        task.status = TaskStatus.COMPLETED
        task.downloaded = task.total_size or 0
        self.notify("status")

    def _abort_on_early_stop(self) -> None:
        task = self.task
        if self.cancel.is_set():
            task.status = TaskStatus.CANCELLED
        else:
            task.status = TaskStatus.PAUSED
        self.notify("status")

    def _cleanup(self, *, remove_part: bool) -> None:
        if remove_part:
            try:
                Path(self.task.tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
        try:
            Path(self.task.resume_path).unlink(missing_ok=True)
        except OSError:
            pass


class DownloadEngine:
    """任务调度中心与事件源。"""

    def __init__(
        self,
        *,
        listener: Listener | None = None,
        save_dir: str = ".",
        num_threads: int = 8,
        verify: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.listener = listener
        self.save_dir = save_dir
        self.num_threads = max(1, num_threads)
        self.verify = verify
        self.extra_headers = extra_headers or {}

        self._tasks: dict[str, DownloadTask] = {}
        self._workers: dict[str, _TaskWorker] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._id_counter = 0
        self._last_sample: dict[str, tuple[float, float]] = {}
        self._last_emitted: dict[str, int] = {}
        self._speed: dict[str, float] = {}
        self._eta: dict[str, float | None] = {}

        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()

    # ---- 对外接口 -----------------------------------------------------
    def add(self, url: str, save_dir: str | None = None) -> str:
        directory = save_dir or self.save_dir
        provisional = sanitize_filename(self._guess_name(url))
        dest = unique_dest(directory, provisional)

        with self._lock:
            self._id_counter += 1
            task_id = f"task-{self._id_counter:04d}"
            task = DownloadTask(
                id=task_id,
                url=url,
                save_path=dest,
                filename=provisional,
                tmp_path=dest + ".part",
                resume_path=dest + ".part.json",
                created_at=time.time(),
            )
            self._tasks[task_id] = task
            self._last_sample[task_id] = (0.0, _now())
            self._last_emitted[task_id] = 0
            worker = _TaskWorker(self, task)
            self._workers[task_id] = worker
            worker.start()

        self._notify("new", task)
        return task_id

    def pause(self, task_id: str) -> None:
        worker = self._workers.get(task_id)
        if worker and worker.is_alive():
            worker.pause.set()

    def resume(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        worker = self._workers.get(task_id)
        if not task or task.status not in (TaskStatus.PAUSED, TaskStatus.ERROR):
            return False
        if worker and worker.is_alive():
            worker.join(timeout=3)
        with self._lock:
            new_worker = _TaskWorker(self, task, resume=True)
            self._workers[task_id] = new_worker
            new_worker.start()
        return True

    def cancel(self, task_id: str) -> None:
        worker = self._workers.get(task_id)
        if worker and worker.is_alive():
            worker.cancel.set()

    def remove(self, task_id: str) -> None:
        self.cancel(task_id)
        with self._lock:
            task = self._tasks.pop(task_id, None)
            worker = self._workers.pop(task_id, None)
            for key in ("_last_sample", "_last_emitted", "_speed", "_eta"):
                getattr(self, key).pop(task_id, None)
            if task:
                self._notify("forget", task)
            if worker and worker.is_alive():
                worker.join(timeout=3)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [
                t.snapshot(self._speed.get(t.id, 0.0), self._eta.get(t.id))
                for t in self._tasks.values()
            ]

    def set_threads(self, n: int) -> None:
        self.num_threads = max(1, n)

    def get_config(self) -> dict:
        return {
            "numThreads": self.num_threads,
            "saveDir": self.save_dir,
            "verify": self.verify,
        }

    def shutdown(self) -> None:
        self._stop.set()
        workers = list(self._workers.values())
        for w in workers:
            w.cancel.set()
        for w in workers:
            w.join(timeout=5)
        try:
            self._monitor.join(timeout=2)
        except RuntimeError:
            pass

    # ---- 内部辅助 -----------------------------------------------------
    @staticmethod
    def _guess_name(url: str) -> str:
        from urllib.parse import unquote, urlparse

        name = unquote(urlparse(url).path.rsplit("/", 1)[-1]) if urlparse(url).path else ""
        return name or "download.bin"

    def _notify(self, event: str, task: DownloadTask, speed: float = 0.0) -> None:
        if not self.listener:
            return
        try:
            self.listener(event, task.snapshot(speed, self._eta.get(task.id)))
        except Exception:  # noqa: BLE001  (UI 回调异常不应影响下载)
            pass

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(MONITOR_INTERVAL)
            now = _now()
            for task in list(self._tasks.values()):
                if task.status not in (TaskStatus.PROBING, TaskStatus.DOWNLOADING):
                    continue
                if task.segments:
                    task.downloaded = sum(s.downloaded for s in task.segments)

                last_d, last_t = self._last_sample.get(task.id, (0.0, now))
                speed = 0.0
                if now > last_t:
                    instant = (task.downloaded - last_d) / (now - last_t)
                    prev = self._speed.get(task.id, 0.0)
                    speed = prev if prev <= 0 else prev * 0.6 + instant * 0.4
                self._last_sample[task.id] = (task.downloaded, now)
                self._speed[task.id] = speed

                eta = None
                if (task.total_size or 0) > task.downloaded and speed > 0:
                    eta = (task.total_size - task.downloaded) / speed
                self._eta[task.id] = eta

                if task.downloaded != self._last_emitted.get(task.id):
                    self._last_emitted[task.id] = task.downloaded
                    self._notify("progress", task, speed)
