"""卷面 HTML 路由（契约 §5.1 `GET /api/papers/{id}/sheet`，FR-T5/T6/T9）。

**并行解耦点**：渲染器 `app.render.sheet.render_sheet` 由 task-6 实现，
本路由在**函数内部延迟导入**；`ImportError` → HTTP 503 + `RENDER_NOT_READY`，
这样两个智能体可以真并行（后端先就绪，渲染未就绪也不会 500 崩）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.services.errors import ApiError
from app.services.sheet_ctx import build_sheet_context, parse_mode

__all__ = ["router", "render_not_ready"]

router = APIRouter(tags=["sheet"])


def render_not_ready(expected: str = "app.render.sheet") -> JSONResponse:
    """渲染模块未就绪的标准 503 响应（结构与其他错误一致）。"""
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "RENDER_NOT_READY",
                "message": f"卷面渲染模块尚未就绪（缺少 {expected}），请稍后重试",
            }
        },
    )


@router.get(ROUTES["sheet"], response_class=HTMLResponse)
def get_sheet(
    paper_id: int,
    mode: str = Query(default="question"),
    header_note: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
):
    """渲染卷面 HTML：`mode=question`（题目版）/ `answer`（答案版）。"""
    sheet_mode = parse_mode(mode)
    ctx = build_sheet_context(db, paper_id, sheet_mode, header_note)

    try:
        from app.render.sheet import render_sheet
    except ImportError:
        return render_not_ready()

    html = render_sheet(ctx)
    if not isinstance(html, str):
        raise ApiError("VALIDATION_ERROR", "渲染器必须返回 HTML 字符串")
    return HTMLResponse(content=html)
