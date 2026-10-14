"""verify 侧浏览器引导（**只做一件事**：把仓库内已装的 Chromium 指给 Playwright）。

启动 / 复用 / `mkdtemp` 回退 / 版本探测**全部交给产品自己的 `app.export.browser`** ——
本模块不再自写一套（M2 时我在 `check_d59_layout.py` 里直接 `pw.chromium.launch()`，
没设可写 TMP、也不认版本号，导致「裸跑全量」出现 3 条环境假红）。

这样 `unittest` 门禁**裸跑即可通过**，不要求调用者先导出环境变量。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))            # <repo>/backend/tests/e2e_m2
BACKEND = os.path.dirname(os.path.dirname(HERE))             # <repo>/backend
REPO = os.path.dirname(BACKEND)                              # <repo>

# 仓库内的浏览器目录（`playwright install chromium` 时用 PLAYWRIGHT_BROWSERS_PATH 装到这里）。
# 只在调用者没显式指定时才补，避免覆盖别人的环境选择。
_LOCAL_BROWSERS = os.path.join(REPO, ".playwright")
if os.path.isdir(_LOCAL_BROWSERS):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _LOCAL_BROWSERS)

if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.export.browser import (  # noqa: E402
    BrowserError,
    browser_session,
    chromium_executable,
    is_available,
    session,
)

__all__ = ["BrowserError", "browser_session", "chromium_executable", "is_available",
           "session", "BACKEND", "REPO", "HERE"]
