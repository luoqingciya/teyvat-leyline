"""PyInstaller 单文件后端的包内入口。

直接以 ``server.py`` 作为脚本执行时，``from . import ...`` 相对导入
因为没有父包而失败。此文件从包内导入 ``main``，让 PyInstaller 正确把
``teyvat_leyline.server`` 当模块分析并打包。
"""

from teyvat_leyline.server import main

if __name__ == "__main__":
    main()