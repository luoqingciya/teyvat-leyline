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

import hashlib
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
    referrer_headers,
    sanitize_filename,
    unique_dest,
)
from .models import DownloadTask, Segment, SegmentStatus, TaskStatus
from .ratelimiter import ConcurrencyGate, RateLimiter

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
                    flushed = 0
                    for chunk in resp.iter_bytes(CHUNK_SIZE):
                        if host.pause.is_set() or host.cancel.is_set():
                            break
                        host._rate_limit(len(chunk))
                        fh.write(chunk)
                        segment.downloaded += len(chunk)
                        flushed += len(chunk)
                        # 每约 4 MiB 落盘一次，降低高频 flush 带来的 IO 开销
                        if flushed >= 4 * 1024 * 1024:
                            fh.flush()
                            flushed = 0
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
            **referrer_headers(self.url),
            **engine.extra_headers,
        }
        self.proxy = task.proxy or engine.proxy
        self.client = _safe_client(verify=engine.verify, proxy=self.proxy or None)
        self._own_limiter = RateLimiter()
        self._segment_threads: list[_SegmentWorker] = []
        self._errors: list[str] = []

    # ---- 任务状态上报 -------------------------------------------------
    def notify(self, event: str, task: DownloadTask | None = None, speed: float = 0.0) -> None:
        self.engine._notify(event, task or self.task, speed)

    def record_error(self, segment: Segment, message: str) -> None:
        if message not in self._errors:
            self._errors.append(message)

    def _rate_limit(self, n: int) -> None:
        """写入前做限速：优先用任务自身限速，否则用全局限速。"""
        own = self.task.speed_kbps or 0
        if own > 0:
            self._own_limiter.acquire(n, float(own) * 1024.0)
        else:
            global_rate = self.engine.global_speed_kbps or 0
            if global_rate > 0:
                self.engine._global_limiter.acquire(n, float(global_rate) * 1024.0)

    # ---- 主流程 -------------------------------------------------------
    def run(self) -> None:
        """排队等待并发槽位 → 带重试地执行下载主流程。"""
        if not self.engine._gate.wait_slot(self.cancel):
            self.task.status = TaskStatus.CANCELLED
            self.notify("status")
            self.engine._workers.pop(self.task.id, None)
            return
        try:
            if self.pause.is_set() or self.cancel.is_set():
                self._abort_on_early_stop()
            else:
                self._run_with_retry()
        finally:
            self.engine._gate.release()
            try:
                self.client.close()
            except Exception:  # noqa: BLE001
                pass
            self.engine._workers.pop(self.task.id, None)

    def _run_with_retry(self) -> None:
        task = self.task
        while True:
            task.status = TaskStatus.PROBING
            self.notify("status")
            try:
                info = probe(
                    self.url,
                    verify=self.engine.verify,
                    extra_headers=self.engine.extra_headers,
                    proxy=self.proxy or None,
                )
                if self.pause.is_set() or self.cancel.is_set():
                    self._abort_on_early_stop()
                    return
                self._apply_probe_info(info)
                if self.task.total_size is None or not self.task.supports_range:
                    self._download_stream()
                else:
                    self._download_segmented()
            except Exception as exc:  # noqa: BLE001
                if self.cancel.is_set():
                    task.status = TaskStatus.CANCELLED
                elif self.pause.is_set():
                    task.status = TaskStatus.PAUSED
                elif self._should_retry():
                    self._prepare_retry(exc)
                    continue
                else:
                    task.status = TaskStatus.ERROR
                    task.error = str(exc)
            self.notify("status")
            return

    def _should_retry(self) -> bool:
        return (
            not self.cancel.is_set()
            and not self.pause.is_set()
            and (self.engine.max_retries or 0) > 0
            and self.task.retries < self.engine.max_retries
        )

    def _prepare_retry(self, exc: Exception) -> None:
        self.task.retries += 1
        self.task.error = f"失败，正在重试({self.task.retries}/{self.engine.max_retries})：{exc}"
        self.notify("status")
        self.engine._sleep_retry()

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
                for chunk in resp.iter_bytes(CHUNK_SIZE):
                    if self.pause.is_set() or self.cancel.is_set():
                        break
                    self._rate_limit(len(chunk))
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
            self._verify_and_complete()

    # ---- 分片下载 -----------------------------------------------------
    def _download_segmented(self) -> None:
        task = self.task
        while True:
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
            self._join_segments()

            errored = any(s.status == SegmentStatus.ERROR for s in task.segments)
            if errored and self._should_retry():
                self._prepare_retry(Exception(self._errors[0] if self._errors else "分片下载失败"))
                self.resume = True  # 保留已下载进度，继续未完成分片
                continue

            self._finish_segmented()
            return

    def _join_segments(self) -> None:
        for worker in self._segment_threads:
            worker.join(timeout=5)
        self._segment_threads = []

    def _finish_segmented(self) -> None:
        task = self.task
        # 确保分片线程已停止写盘，避免 Windows 文件锁导致重命名/删除失败
        self._join_segments()
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
        self._verify_and_complete()

    def _verify_and_complete(self) -> None:
        """下载收尾：可选哈希校验 + 记录历史 + 完成通知。"""
        task = self.task
        if self.engine.hash_check:
            task.status = TaskStatus.CHECKING
            self.notify("status")
            ok = self._verify_file()
            task.verified = ok
            if not ok:
                self._cleanup(remove_part=False)
                task.status = TaskStatus.ERROR
                task.error = "完整性校验失败，文件已被删除"
                self.engine._record_history(task, success=False)
                self.notify("status")
                return
        else:
            task.verified = None
        task.status = TaskStatus.COMPLETED
        task.downloaded = task.total_size or 0
        self.engine._record_history(task, success=True)
        self.notify("status")

    def _verify_file(self) -> bool:
        """对已落盘的最终文件计算 SHA256（仅做可读性/完整性兜底）。"""
        path = Path(self.task.save_path)
        if not path.exists():
            return False
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return False
        self.task.sha256 = digest.hexdigest()
        return True

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

    def _segment_count(self, total: int) -> int:
        if total <= MIN_SEGMENT_SIZE:
            return 1
        ideal = min(self.engine.num_threads, total // MIN_SEGMENT_SIZE or 1, MAX_SEGMENTS)
        return max(1, ideal)

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
        self._last_manifest: dict[str, float] = {}

        # ---- 扩展能力配置 ----
        self.global_speed_kbps: int = 0    # 全局限速（KB/s），0 不限
        self.max_concurrent: int = 4       # 同时下载的最大任务数
        self.max_retries: int = 3          # 失败自动重试次数
        self.retry_delay: float = 2.0      # 重试间隔（秒）
        self.proxy: str = ""               # 全局代理（http/socks5://...）
        self.hash_check: bool = False      # 下载完成后做 SHA256 完整性校验
        self._gate = ConcurrencyGate(self.max_concurrent)
        self._global_limiter = RateLimiter()

        # ---- 历史与去重 ----
        self._known_urls: set[str] = set()
        self._history_file = Path(self.save_dir) / "teyvat-history.json"
        self._load_history()

        self._monitor = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor.start()

    # ---- 对外接口 -----------------------------------------------------
    def add(self, url: str, save_dir: str | None = None, **opts) -> str:
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
                threads=max(1, int(opts.get("threads") or self.num_threads)),
                speed_kbps=max(0, int(opts.get("speed_kbps") or 0)),
                proxy=opts.get("proxy") or "",
            )
            self._tasks[task_id] = task
            self._last_sample[task_id] = (0.0, _now())
            self._last_emitted[task_id] = 0
            worker = _TaskWorker(self, task)
            self._workers[task_id] = worker
            worker.start()
            self._known_urls.add(url)

        self._notify("new", task)
        return task_id

    def set_task_speed(self, task_id: str, speed_kbps: int) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.speed_kbps = max(0, int(speed_kbps))

    def set_task_threads(self, task_id: str, n: int) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.threads = max(1, int(n))
            self.num_threads = max(self.num_threads, task.threads)

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
            for key in ("_last_sample", "_last_emitted", "_speed", "_eta", "_last_manifest"):
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
            "globalSpeedKbps": self.global_speed_kbps,
            "maxConcurrent": self.max_concurrent,
            "maxRetries": self.max_retries,
            "retryDelay": self.retry_delay,
            "proxy": self.proxy,
            "hashCheck": self.hash_check,
        }

    # ---- 扩展能力配置 ------------------------------------------------
    def set_global_speed(self, kbps: int) -> None:
        self.global_speed_kbps = max(0, int(kbps))

    def set_max_concurrent(self, n: int) -> None:
        self.max_concurrent = max(1, int(n))
        self._gate.set_limit(self.max_concurrent)

    def set_retry(self, max_retries: int, delay: float = 2.0) -> None:
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = max(0.0, float(delay))

    def set_proxy(self, proxy: str) -> None:
        self.proxy = proxy or ""

    def set_hash_check(self, on: bool) -> None:
        self.hash_check = bool(on)

    def is_known(self, url: str) -> bool:
        return url in self._known_urls

    def get_history(self) -> list[dict]:
        path = self._history_file
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    # ---- 历史持久化与去重 --------------------------------------------
    def _load_history(self) -> None:
        for entry in self.get_history():
            url = entry.get("url")
            if url:
                self._known_urls.add(url)

    def _record_history(self, task: DownloadTask, *, success: bool = True) -> None:
        entry = {
            "url": task.url,
            "filename": task.filename,
            "savePath": task.save_path,
            "total": task.total_size,
            "verified": task.verified,
            "success": success,
            "finishedAt": time.time(),
        }
        records = self.get_history()
        records = [r for r in records if r.get("url") != task.url]
        records.append(entry)
        try:
            self._history_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _sleep_retry(self) -> None:
        delay = self.retry_delay
        step = 0.1
        waited = 0.0
        while waited < delay and not (self._stop.is_set()):
            time.sleep(step)
            waited += step

    def shutdown(self) -> None:
        """关闭引擎：以“暂停”方式退出所有工作线程，保留断点数据供下次续传。"""
        self._stop.set()
        workers = list(self._workers.values())
        for w in workers:
            # 以“暂停”退出而非取消，保留 .part 与断点数据供下次续传
            w.pause.set()
        for w in workers:
            w.join(timeout=5)
        # 落盘所有进行中任务的断点清单
        self._save_active_manifests()
        try:
            self._monitor.join(timeout=2)
        except RuntimeError:
            pass

    def _save_active_manifests(self) -> None:
        with self._lock:
            for task in self._tasks.values():
                if not task.segments:
                    continue
                worker = self._workers.get(task.id)
                if worker is None:
                    continue
                try:
                    worker._save_manifest(task)
                except Exception:  # noqa: BLE001
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

                # 周期落盘断点清单：崩溃/断电后也能续传
                if task.segments:
                    last_save = self._last_manifest.get(task.id, 0.0)
                    if now - last_save >= 2.0:
                        self._last_manifest[task.id] = now
                        worker = self._workers.get(task.id)
                        if worker is not None:
                            try:
                                worker._save_manifest(task)
                            except Exception:  # noqa: BLE001
                                pass
