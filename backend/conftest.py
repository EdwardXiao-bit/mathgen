"""让 `app` 包在从仓库根目录运行测试时可被导入。

无论用 pytest 还是 `python -m unittest`，都把 backend/ 放进 sys.path。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
