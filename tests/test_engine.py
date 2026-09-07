"""下载引擎与 HTTP 工具测试（使用本地 Range 服务器）。"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from teyvat_leyline.core.engine import DownloadEngine
from teyvat_leyline.core.http_client import probe, sanitize_filename


def _content(size: int) -> bytes:
    """可重复的确定性内容，便于逐字节校验。"""
    pattern = bytes(range(256))
    return (pattern * ((size // 256) + 1))[:size]


class _RangeHandler(BaseHTTPRequestHandler):
    data: bytes = b""
    name: str = "sample.bin"
    support_range: bool = True
    CHUNK = 64 * 1024

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.data)))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self.send_header("Content-Disposition", f'attachment; filename="{self.name}"')
        self.end_headers()

    def do_GET(self) -> None:
        total = len(self.data)
        range_header = self.headers.get("Range")
        start, end, status = 0, total - 1, 200

        if self.support_range and range_header:
            match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else total - 1
                end = min(end, total - 1)
                if start >= total:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                status = 206

        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()

        offset, remaining = start, end - start + 1
        while remaining > 0:
            n = min(self.CHUNK, remaining)
            self.wfile.write(self.data[offset : offset + n])
            offset += n
            remaining -= n
            time.sleep(0.02)

    def log_message(self, *args) -> None:  # 静默日志
        return


@pytest.fixture()
def server():
    data = _content(4 * 1024 * 1024 + 137)  # ~4MB，凑出非整数分片
    _RangeHandler.data = data
    _RangeHandler.name = "sample.bin"
    _RangeHandler.support_range = True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{port}/sample.bin", "data": data, "httpd": httpd}
    httpd.shutdown()
    httpd.server_close()


def _wait(engine: DownloadEngine, task_id: str, statuses: set[str], timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = next((t for t in engine.list_tasks() if t["id"] == task_id), None)
        if task and task["status"] in statuses:
            return task
        time.sleep(0.05)
    raise AssertionError(f"等待任务 {task_id} 状态 {statuses} 超时")


def test_multi_thread_segmented_download(server, tmp_path: Path) -> None:
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        task = _wait(engine, task_id, {"completed"})
        assert task["threads"] == 4
        assert task["downloaded"] == len(server["data"])
        target = tmp_path / "sample.bin"
        assert target.read_bytes() == server["data"]
    finally:
        engine.shutdown()


def test_pause_and_resume(server, tmp_path: Path) -> None:
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        # 等下载出一点数据再暂停，确保命中分片过程
        deadline = time.time() + 30
        while time.time() < deadline:
            t = next((x for x in engine.list_tasks() if x["id"] == task_id), None)
            if t and t["downloaded"] > 0:
                break
            time.sleep(0.03)

        engine.pause(task_id)
        paused = _wait(engine, task_id, {"paused"})
        assert paused["downloaded"] > 0

        assert engine.resume(task_id) is True
        completed = _wait(engine, task_id, {"completed", "error"})
        assert completed["status"] == "completed"
        assert (tmp_path / "sample.bin").read_bytes() == server["data"]
    finally:
        engine.shutdown()


def test_stream_fallback(server, tmp_path: Path) -> None:
    server["httpd"].RequestHandlerClass.support_range = False
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        task = _wait(engine, task_id, {"completed"})
        assert task["threads"] == 1
        assert (tmp_path / "sample.bin").read_bytes() == server["data"]
    finally:
        engine.shutdown()


def test_probe_filename(server) -> None:
    info = probe(server["url"])
    assert info.filename == "sample.bin"
    assert info.content_length == len(server["data"])
    assert info.supports_range is True


def test_sanitize_filename() -> None:
    assert sanitize_filename('a<b>c:d"e|f?g*h') == "a_b_c_d_e_f_g_h"
    assert sanitize_filename("..") == "download.bin"


# ---- 补充用例：保留名 / 探测失败 / 哈希与历史 / 取消 / 恢复 / 单流续传 / Range 降级 ----

def test_sanitize_windows_reserved_names() -> None:
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("com1.txt") == "_com1.txt"
    assert sanitize_filename("NUL.zip") == "_NUL.zip"
    assert sanitize_filename("normal.txt") == "normal.txt"


class _NotFoundHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_HEAD

    def log_message(self, *args) -> None:
        return


class _RangeIgnoredHandler(_RangeHandler):
    """HEAD 宣称支持 Range，GET 却无视 Range 直接 200 全量返回。"""

    def do_GET(self) -> None:
        total = len(self.data)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        offset, remaining = 0, total
        while remaining > 0:
            n = min(self.CHUNK, remaining)
            self.wfile.write(self.data[offset : offset + n])
            offset += n
            remaining -= n
            time.sleep(0.02)


@contextmanager
def _http_server(handler_cls, data: bytes, name: str = "sample.bin"):
    handler_cls.data = data
    handler_cls.name = name
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/sample.bin"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_probe_not_found_raises() -> None:
    with _http_server(_NotFoundHandler, b"x") as url, pytest.raises(RuntimeError, match="404"):
        probe(url)


def test_hash_check_and_history(server, tmp_path: Path) -> None:
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    engine.set_hash_check(True)
    try:
        task_id = engine.add(server["url"])
        task = _wait(engine, task_id, {"completed"})
        assert task["verified"] is True
        history = engine.get_history()
        assert any(h["url"] == server["url"] and h["success"] for h in history)
        assert (tmp_path / "teyvat-history.json").exists()
    finally:
        engine.shutdown()


def test_cancel_paused_task(server, tmp_path: Path) -> None:
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        _wait_downloaded(engine, task_id)
        engine.pause(task_id)
        _wait(engine, task_id, {"paused"})

        engine.cancel(task_id)
        task = _wait(engine, task_id, {"cancelled"})
        assert task["status"] == "cancelled"
        assert not (tmp_path / "sample.bin.part").exists()
        assert not (tmp_path / "sample.bin.part.json").exists()
    finally:
        engine.shutdown()


def test_readd_after_cancel(server, tmp_path: Path) -> None:
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        _wait_downloaded(engine, task_id)
        engine.pause(task_id)
        _wait(engine, task_id, {"paused"})
        engine.cancel(task_id)
        _wait(engine, task_id, {"cancelled"})
        engine.remove(task_id)

        # 取消并移除后，同一链接应能重新添加
        new_id = engine.add(server["url"])
        assert new_id != task_id
        task = _wait(engine, new_id, {"completed"})
        assert task["downloaded"] == len(server["data"])
    finally:
        engine.shutdown()


def test_recover_after_restart(server, tmp_path: Path) -> None:
    engine1 = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    task_id = engine1.add(server["url"])
    _wait_downloaded(engine1, task_id)
    engine1.pause(task_id)
    _wait(engine1, task_id, {"paused"})
    engine1.shutdown()

    # 新引擎启动时扫描断点清单，恢复为可继续的暂停任务
    engine2 = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        tasks = engine2.list_tasks()
        recovered = next(t for t in tasks if t["status"] == "paused" and t["downloaded"] > 0)
        assert recovered["url"] == server["url"]
        assert recovered["total"] == len(server["data"])

        assert engine2.resume(recovered["id"]) is True
        _wait(engine2, recovered["id"], {"completed"})
        assert (tmp_path / "sample.bin").read_bytes() == server["data"]
    finally:
        engine2.shutdown()


def test_stream_resume(server, tmp_path: Path) -> None:
    server["httpd"].RequestHandlerClass.support_range = False
    engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
    try:
        task_id = engine.add(server["url"])
        _wait_downloaded(engine, task_id)
        engine.pause(task_id)
        paused = _wait(engine, task_id, {"paused"})
        partial = paused["downloaded"]

        assert engine.resume(task_id) is True
        task = _wait(engine, task_id, {"completed"})
        # 续传重新计数，进度不应叠加
        assert task["downloaded"] == len(server["data"]) > partial
        assert (tmp_path / "sample.bin").read_bytes() == server["data"]
    finally:
        engine.shutdown()


def test_range_ignored_falls_back_to_stream(tmp_path: Path) -> None:
    data = _content(2 * 1024 * 1024 + 11)
    with _http_server(_RangeIgnoredHandler, data) as url:
        engine = DownloadEngine(save_dir=str(tmp_path), num_threads=4)
        try:
            task_id = engine.add(url)
            task = _wait(engine, task_id, {"completed"})
            # 探测被骗后应自动降级为单流并完整下载
            assert task["threads"] == 1
            assert task["supportsRange"] is False
            assert (tmp_path / "sample.bin").read_bytes() == data
        finally:
            engine.shutdown()


def _wait_downloaded(engine: DownloadEngine, task_id: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = next((t for t in engine.list_tasks() if t["id"] == task_id), None)
        if task and task["downloaded"] > 0:
            return
        time.sleep(0.03)
    raise AssertionError(f"等待任务 {task_id} 产生进度超时")
