"""PyInstaller 打包入口。

以顶层脚本方式运行以构建独立可执行文件；``python -m teyuat_leyline``、
``uv run teyuat-leyline`` 仍走各自的官方入口。
"""

from __future__ import annotations

import pathlib
import sys

# 让脚本既能直接 ``python launcher.py``（源码模式）也能被 PyInstaller 收集。
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from teyuat_leyline.__main__ import main  # noqa: E402  (仅在脚本模式下插入路径后导入)

if __name__ == "__main__":
    main()
