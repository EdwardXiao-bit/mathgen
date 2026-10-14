"""导出路由（契约 §5.1 `GET /api/papers/{id}/export`，FR-T7/T8 / D57 + M3）。

链路**只有一条**（C3 同源）：
`build_sheet_context` → `render_html` → 打印/写文件。

* `format=pdf`：把 HTML 交给 `pdf_export`（子进程里的 Chromium 打印同一份 HTML）
* `format=docx`（M3 起真实现）：把**同一个 `SheetContext`**（含 `answer_layout`）
  交给 `app.export.docx.export_docx`（render-export 的写域，可能尚未就绪 →
  延迟导入 + `ImportError` → 503 `RENDER_NOT_READY`）

错误优先级（固定，可测）：卷不存在 → 404；`format` 非法 → 422；`mode` 非法 → 422；
渲染/Word 模块未就绪 → 503 `RENDER_NOT_READY`；打印或生成失败 → 503 `EXPORT_NOT_READY`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.routers._common import render_not_ready
from app.services import docx_export
from app.services import paper_service as svc
from app.services import pdf_export
from app.services.errors import ApiError
from app.services.sheet_ctx import build_sheet_context, parse_mode
from app.services.sheet_render import RenderNotReady, render_html

__all__ = ["router", "SUPPORTED_FORMATS", "MEDIA_TYPE_PDF", "MEDIA_TYPE_DOCX"]

router = APIRouter(tags=["export"])

#: 支持的导出格式
SUPPORTED_FORMATS = ("pdf", "docx")

MEDIA_TYPE_PDF = "application/pdf"
MEDIA_TYPE_DOCX = docx_export.DOCX_MEDIA_TYPE


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
    responses={200: {"content": {MEDIA_TYPE_PDF: {}, MEDIA_TYPE_DOCX: {}}}},
)
def export_paper(
    paper_id: int,
    file_format: str = Query(default="pdf", alias="format"),
    mode: str = Query(default="question"),
    header_note: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
) -> Response:
    """把卷面导出为文件流：`format=pdf` → PDF；`format=docx` → Word。"""
    svc.get_paper_or_404(db, paper_id)                 # 404 优先于参数校验
    fmt = parse_format(file_format)
    sheet_mode = parse_mode(mode)
    ctx = build_sheet_context(db, paper_id, sheet_mode, header_note)

    if fmt == "docx":
        try:
            data = docx_export.export_docx_bytes(ctx)
        except RenderNotReady as exc:
            return render_not_ready(exc.expected)
        except docx_export.DocxExportUnavailable as exc:
            raise ApiError("EXPORT_NOT_READY", f"Word 导出当前不可用：{exc}") from exc
        media_type, filename = MEDIA_TYPE_DOCX, f"paper-{paper_id}-{sheet_mode.value}.docx"
    else:
        try:
            html = render_html(ctx)
        except RenderNotReady as exc:
            return render_not_ready(exc.expected)
        try:
            data = pdf_export.export_pdf_bytes(html)
        except pdf_export.ExportUnavailable as exc:
            raise ApiError(
                "EXPORT_NOT_READY",
                f"PDF 导出通道当前不可用（浏览器/打印环境问题）：{exc}",
            ) from exc
        media_type, filename = MEDIA_TYPE_PDF, f"paper-{paper_id}-{sheet_mode.value}.pdf"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
