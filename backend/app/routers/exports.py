"""导出路由（契约 §5.1 `GET /api/papers/{id}/export`，FR-T7 / D57）。

链路**只有一条**（C3 同源）：
`build_sheet_context` → `sheet_render.render_html` → `pdf_export.export_pdf_bytes`
即：与 `GET /sheet` 完全同一份 HTML，PDF 只是把它交给浏览器打印（不另写排版）。

`format=docx` **不假装支持**：Phython 侧的 Word 导出属 M3（设计 §5.3 D33），
这里显式返回 501 `NOT_IMPLEMENTED`，避免前端拿到一个「其实是 PDF」的 .docx。

错误优先级（固定，可测）：卷不存在 → 404；`format` 非法 → 422；
`mode` 非法 → 422；`format=docx` → 501；渲染未就绪 → 503；浏览器不可用 → 503。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.routers._common import render_not_ready
from app.services import paper_service as svc
from app.services import pdf_export
from app.services.errors import ApiError
from app.services.sheet_ctx import build_sheet_context, parse_mode
from app.services.sheet_render import RenderNotReady, render_html

__all__ = ["router", "SUPPORTED_FORMATS", "MEDIA_TYPE_PDF"]

router = APIRouter(tags=["export"])

#: 支持的导出格式（`docx` 现返回 501，见模块 docstring）
SUPPORTED_FORMATS = ("pdf", "docx")

MEDIA_TYPE_PDF = "application/pdf"


def parse_format(raw: str | None) -> str:
    """`format=pdf|docx`（缺省 pdf）；**显式给了非法值**（含空串）→ 422。"""
    if raw is None:
        return "pdf"
    value = raw.strip().lower()
    if value not in SUPPORTED_FORMATS:
        raise ApiError(
            "VALIDATION_ERROR",
            f"format 只能是 {' 或 '.join(SUPPORTED_FORMATS)}，实际为 {raw!r}",
        )
    return value


@router.get(
    ROUTES["export"],
    response_class=Response,
    responses={200: {"content": {MEDIA_TYPE_PDF: {}}}},
)
def export_paper(
    paper_id: int,
    file_format: str = Query(default="pdf", alias="format"),
    mode: str = Query(default="question"),
    header_note: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
) -> Response:
    """把卷面导出为文件流。`format=pdf` 返回 PDF；`format=docx` 返回 501。"""
    svc.get_paper_or_404(db, paper_id)                 # 404 优先于参数校验
    fmt = parse_format(file_format)
    sheet_mode = parse_mode(mode)

    if fmt == "docx":
        raise ApiError(
            "NOT_IMPLEMENTED",
            "Word（.docx）导出属 M3 范围，M2 仅提供 PDF；如需打印请用 format=pdf",
        )

    ctx = build_sheet_context(db, paper_id, sheet_mode, header_note)
    try:
        html = render_html(ctx)
    except RenderNotReady as exc:
        return render_not_ready(exc.expected)

    try:
        pdf = pdf_export.export_pdf_bytes(html)
    except pdf_export.ExportUnavailable as exc:
        raise ApiError(
            "EXPORT_NOT_READY",
            f"PDF 导出通道当前不可用（浏览器/打印环境问题）：{exc}",
        ) from exc

    filename = f"paper-{paper_id}-{sheet_mode.value}.pdf"
    return Response(
        content=pdf,
        media_type=MEDIA_TYPE_PDF,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
