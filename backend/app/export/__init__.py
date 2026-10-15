"""导出包（**冻结入口**：`export_pdf` / `export_docx`）。

* `browser`：Chromium 定位与进程级复用（唯一启动浏览器的地方）
* `pdf`：Playwright 打印同一份 HTML（C3 / NFR-5），并回报页数与实测排布
* `docx`：Word 导出（M3），结构与 HTML/PDF 对齐、可二次编辑（契约第 6 条）
* `attempt_export`：成绩导出（M5 / FR-T13），把 `AttemptSummary` 变成 CSV / xlsx

本包只依赖 `app.render` / `app.layout` 与 Playwright / python-docx，
**不查数据库、不 import FastAPI**（契约 §8 D-3）。

`attempt_export` 自带这句约束之外的**轻**优势：它只用标准库（xlsx 走已装的
`openpyxl`），不 import Playwright / python-docx。因此**成绩导出不需要浏览器**。
"""
from __future__ import annotations

import os
import sys

if __package__:
    _BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _BACKEND not in sys.path:  # pragma: no cover
        sys.path.insert(0, _BACKEND)

from app.export.attempt_export import (  # noqa: E402
    HEADER_LABELS as ATTEMPT_HEADER_LABELS,
    content_disposition as attempt_content_disposition,
    export_attempts,
    filename_for as attempt_export_filename,
)
from app.export.browser import (  # noqa: E402
    BrowserError,
    browser_session,
    chromium_executable,
    is_available,
    playwright_tmp,
    session,
)
from app.export.docx import (  # noqa: E402
    DocxExportError,
    docx_body_text,
    export_docx,
)
from app.export.pdf import (  # noqa: E402
    PdfReport,
    count_pdf_pages,
    export_pdf,
    export_pdf_report,
    export_png,
)

__all__ = [
    "ATTEMPT_HEADER_LABELS",
    "BrowserError",
    "DocxExportError",
    "PdfReport",
    "attempt_content_disposition",
    "attempt_export_filename",
    "browser_session",
    "chromium_executable",
    "count_pdf_pages",
    "docx_body_text",
    "export_attempts",
    "export_docx",
    "export_pdf",
    "export_pdf_report",
    "export_png",
    "is_available",
    "playwright_tmp",
    "session",
]
