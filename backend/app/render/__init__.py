"""渲染包（**冻结入口**：`render_sheet`）。

契约见 `app/contracts/sheet.py` 文件末尾。包根导出 `render_sheet`、`render_with_layout`
与 `chunk_rows`，使调用方（后端 API 路由）只需 `from app.render import render_sheet`。
"""
from __future__ import annotations

import os
import sys

# 让 `python -m unittest discover -s backend/tests/render -t backend` 与
# `python -c "from app.render import render_sheet"`（cwd 为 backend）两种入口都能工作。
# 这一句是**打包兜底**：正常情况下 `app` 已经是顶层包，不会生效。
if __package__:
    _BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _BACKEND not in sys.path:  # pragma: no cover - 仅在直接执行时走到
        sys.path.insert(0, _BACKEND)

from app.render.sheet import (  # noqa: E402  (必须在 sys.path 兜底之后导入)
    SheetRenderError,
    chunk_rows,
    render_sheet,
    render_with_layout,
)

__all__ = ["render_sheet", "render_with_layout", "chunk_rows", "SheetRenderError"]
