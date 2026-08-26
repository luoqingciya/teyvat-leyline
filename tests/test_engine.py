"""下载引擎与 HTTP 工具测试（使用本地 Range 服务器）。"""

from __future__ import annotations

import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from teyuat_leyline.core.engine import DownloadEngine
from teyuat_leyline.core.http_client import probe, sanitize_filename


def _content(size: int) -> bytes:
    """可重复的确定性内容，便于逐字节校验。"""
    pattern = bytes(range(256))
    return (pattern * ((size // 256) + 1))[:size]


class _RangeHandler(BaseHTTPRequestHandler):
    data: bytes = b""
    name: str = "sample.bin"
    support_range: bool = True
    CHUNK = 64 * 1024

    def do_HEAD(self) -> None:  # noqa: N802 (http.server 约定)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.data)))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self.send_header("Content-Disposition", f'attachment; filename="{self.name}"')
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (http.server 约定)
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
