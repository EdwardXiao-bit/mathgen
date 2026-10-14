"""卷面 HTML 路由（契约 §5.1 `GET /api/papers/{id}/sheet`，FR-T5/T6/T9）。

**并行解耦点**：渲染器 `app.render.sheet.render_sheet` 由 task-6 实现，
渲染调用收拢在 `app.services.sheet_render.render_html`（函数内部延迟导入）；
不可用时 → HTTP 503 + `RENDER_NOT_READY`，而不是 500。

**同源约束（C3）**：本路由与 `/export` 走**同一个** `render_html`，
所以「预览看到的 HTML」与「打印成 PDF 的 HTML」逐字节相同。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.routers._common import render_not_ready
from app.services.sheet_ctx import build_sheet_context, parse_mode
from app.services.sheet_render import RenderNotReady, render_html

__all__ = ["router"]

router = APIRouter(tags=["sheet"])


@router.get(ROUTES["sheet"], response_class=HTMLResponse)
def get_sheet(
    paper_id: int,
    mode: str = Query(default="question"),
    header_note: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
):
    """渲染卷面 HTML：`mode=question`（题目版）/ `answer`（答案版）。

    错误优先级：卷不存在 → 404；`mode` 非法 → 422；渲染未就绪 → 503。
    """
    sheet_mode = parse_mode(mode)
    ctx = build_sheet_context(db, paper_id, sheet_mode, header_note)
    try:
        html = render_html(ctx)
    except RenderNotReady as exc:
        return render_not_ready(exc.expected)
    return HTMLResponse(content=html)
