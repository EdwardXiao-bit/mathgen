"""导出包（**冻结入口**：`export_pdf`）。

* `browser`：Chromium 定位与进程级复用（唯一启动浏览器的地方）
* `pdf`：Playwright 打印同一份 HTML（C3 / NFR-5），并回报页数与实测排布

本包只依赖 `app.render` / `app.layout` 与 Playwright，**不查数据库、不 import FastAPI**
（契约 §8 D-3）。
"""
from __future__ import annotations

import os
import sys

if __package__:
    _BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _BACKEND not in sys.path:  # pragma: no cover
        sys.path.insert(0, _BACKEND)

from app.export.browser import (  # noqa: E402
    BrowserError,
    browser_session,
    chromium_executable,
    is_available,
    playwright_tmp,
    session,
)
from app.export.pdf import (  # noqa: E402
    PdfReport,
    count_pdf_pages,
    export_pdf,
    export_pdf_report,
    export_png,
)

__all__ = [
    "BrowserError",
    "PdfReport",
    "browser_session",
    "chromium_executable",
    "count_pdf_pages",
    "export_pdf",
    "export_pdf_report",
    "export_png",
    "is_available",
    "playwright_tmp",
    "session",
]
