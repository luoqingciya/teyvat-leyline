"""命令行入口。

``uv run teyuat-leyline`` 启动图形界面；
``uv run python -m teyuat_leyline --url <链接>`` 以无界面模式下载单个文件。
"""

from __future__ import annotations

import argparse
import sys


def _safe_stdout(text: str) -> None:
    """写入控制台；在 ``--windowed`` 打包后 sys.stdout 可能为 None。"""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:  # noqa: BLE001  (无控制台时静默)
        pass


def _cli_download(url: str, output: str, threads: int) -> int:
    from .core.engine import DownloadEngine

    engine = DownloadEngine(save_dir=output, num_threads=threads)
    engine.add(url)
    # 简单轮询直到任务结束
    task_id = next(iter(engine._tasks), "")
    try:
        from .core.models import TaskStatus

        while True:
            task = engine._tasks.get(task_id)
            if task is None:
                break
            status = task.status
            if status not in (TaskStatus.PROBING, TaskStatus.DOWNLOADING, TaskStatus.QUEUED):
                break
            _safe_stdout(f"\r{task.filename}: {task.downloaded}/{task.total_size or '?'} ({status.value})")
            import time

            time.sleep(0.2)
        _safe_stdout(
            "\n完成。" if task and task.status == TaskStatus.COMPLETED
            else f"\n状态：{task.status.value if task else '未知'}"
        )
    except KeyboardInterrupt:
        engine.shutdown()
        return 130
    finally:
        engine.shutdown()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="teyuat-leyline", description="提瓦特地脉 · Teyuat Leyline 多线程下载器")
    parser.add_argument("--url", help="直接下载指定链接（无界面模式）")
    parser.add_argument("--output", default=".", help="无界面模式下的保存目录")
    parser.add_argument("--threads", type=int, default=8, help="分片线程数")
    args = parser.parse_args()

    if args.url:
        sys.exit(_cli_download(args.url, args.output, args.threads))

    from .app import run

    run()


if __name__ == "__main__":
    main()
