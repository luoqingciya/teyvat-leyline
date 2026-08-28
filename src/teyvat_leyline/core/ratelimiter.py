"""限速与并发门控工具。

* :class:`RateLimiter` — 令牌-时间窗限速，多写线程共享时串行扣减，保证平均速率。
* :class:`ConcurrencyGate` — 限制同时进行的任务数，超出者轮询等待并响应取消。
"""

from __future__ import annotations

import threading
import time

KB = 1024


class RateLimiter:
    """按字节吞吐做速率限制。

    ``rate`` 以字节/秒计，``0`` 表示不限制。每个 ``acquire(n)`` 调用只针对
    本调用传入的速率生效，因此可被多个写线程共享同一个实例而保持总速率正确。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._budget = 0.0  # 已预支的字节余额
        self._stamp = time.monotonic()

    def acquire(self, n: int, rate: float) -> None:
        if rate <= 0 or n <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._budget += (now - self._stamp) * rate
            self._stamp = now
            # 只允许最长 1 秒的突发，避免积压后猛然追平
            if self._budget > rate:
                self._budget = rate
            if self._budget >= n:
                self._budget -= n
                return
            need = n - self._budget
            self._budget = 0.0
            delay = need / rate
        if delay > 0:
            time.sleep(delay)


class ConcurrencyGate:
    """并发任务槽位控制：未拿到槽位的线程在此轮询等待。"""

    def __init__(self, limit: int = 1) -> None:
        self._lock = threading.Condition()
        self._count = 0
        self._limit = max(1, limit)

    def set_limit(self, limit: int) -> None:
        with self._lock:
            self._limit = max(1, limit)
            self._lock.notify_all()

    def wait_slot(self, cancel: threading.Event, timeout: float = 0.2) -> bool:
        """尝试占用一个槽位，返回 True 表示已占用。

        若 ``cancel`` 在等待期间被置位则返回 False，调用方应中止任务。
        """
        while True:
            with self._lock:
                if self._count < self._limit:
                    self._count += 1
                    return True
            if cancel.wait(timeout):
                return False

    def release(self) -> None:
        with self._lock:
            if self._count > 0:
                self._count -= 1
            self._lock.notify_all()