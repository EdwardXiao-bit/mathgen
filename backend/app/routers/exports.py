"""导出路由。

两条链路：

* **卷面导出**（契约 §5.1 `GET /api/papers/{id}/export`，FR-T7/T8 / D57 + M3）：
  链路**只有一条**（C3 同源）：
  `build_sheet_context` → `render_html` → 打印/写文件。
  * `format=pdf`：把 HTML 交给 `pdf_export`（子进程里的 Chromium 打印同一份 HTML）
  * `format=docx`（M3 起真实现）：把**同一个 `SheetContext`**（含 `answer_layout`）
    交给 `app.export.docx.export_docx`（render-export 的写域，可能尚未就绪 →
    延迟导入 + `ImportError` → 503 `RENDER_NOT_READY`）
* **成绩导出**（契约 §5.5 `GET /api/attempts/export`，FR-T13 + D80）：
  `attempt_service.list_attempts_all` → `app.export.attempt_export.export_attempts`。
  **不要浏览器**，因此这条链路不受 Playwright 环境（AGENTS.md「坑 2」）影响。

错误优先级（固定，可测）：卷不存在 → 404；`format` 非法 → 422；`mode` 非法 → 422；
渲染/Word 模块未就绪 → 503 `RENDER_NOT_READY`；打印或生成失败 → 503 `EXPORT_NOT_READY`。

**`/api/attempts/export` 的注册顺序是契约的一部分**（`docs/04` §5.5）：它必须排在
`/api/attempts/{attempt_id}` 之前，否则 `attempt_id: int` 解析不了字面量 `"export"`，
FastAPI 直接 422 而**不会继续匹配**。这里能满足，是因为 `exports.router` 在
`app/main.py` 里**先于** `attempts.router` 注册；契约包另有权威判据，
本模块的判据则直接查**真实 app 的路由表顺序**（见 `tests/render/test_export_attempts.py`）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.export import attempt_export
from app.routers._common import render_not_ready
from app.services import attempt_service
from app.services import docx_export
from app.services import paper_service as svc
from app.services import pdf_export
from app.services.errors import ApiError
from app.services.sheet_ctx import build_sheet_context, parse_mode
from app.services.sheet_render import RenderNotReady, render_html

__all__ = [
    "router",
    "SUPPORTED_FORMATS",
    "SUPPORTED_ATTEMPT_FORMATS",
    "MEDIA_TYPE_PDF",
    "MEDIA_TYPE_DOCX",
    "parse_format",
    "parse_attempts_format",
]

router = APIRouter(tags=["export"])

#: 支持的导出格式
SUPPORTED_FORMATS = ("pdf", "docx")

#: 成绩导出的格式（契约 §5.5）。与卷面导出**不是**同一组，所以各解析各的。
SUPPORTED_ATTEMPT_FORMATS = attempt_export.SUPPORTED_FORMATS

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


def parse_attempts_format(raw: str | None) -> str:
    """`format=csv|xlsx`（缺省 csv）；**显式给了非法值**（含空串）→ 422。

    与 `parse_format` 分开而不是加参数：两组取值不同（`pdf/docx` vs `csv/xlsx`），
    合成一个函数就会让「`/export?format=pdf` 与 `/attempts/export?format=pdf`」
    的合法集合互相耦合，改一边会误伤另一边。
    """
    if raw is None:
        return "csv"
    value = raw.strip().lower()
    if value not in SUPPORTED_ATTEMPT_FORMATS:
        raise ApiError(
            "VALIDATION_ERROR",
            f"format 只能是 {' 或 '.join(SUPPORTED_ATTEMPT_FORMATS)}，实际为 {raw!r}",
        )
    return value


def _normalise_q(q: str | None) -> str | None:
    """空串 / 纯空白 → `None`（= 不过滤），与列表接口的约定一致。"""
    if q is None:
        return None
    value = q.strip()
    return value or None


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
    #: Word 链路不经过 Chromium，因此**没有** NFR-5 服务端守卫（判据口径不同）；
    #: 只有 PDF 分支可能往这里塞「守卫被跳过」的证据头。
    skip_headers: dict[str, str] = {}

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
        except pdf_export.ExportLayoutRejected as exc:
            # NFR-5：这一卷在这个排版下**必然**有题被跨页切开 → 409 + 可照做的诊断。
            # 与下面的 503 分开：这不是系统故障，重试同一个请求不会有不同结果，
            # 老师要做的是改排版参数（诊断里写了往哪个方向改）。
            raise ApiError("LAYOUT_DOES_NOT_FIT", str(exc)) from exc
        except pdf_export.ExportUnavailable as exc:
            raise ApiError(
                "EXPORT_NOT_READY",
                f"PDF 导出通道当前不可用（浏览器/打印环境问题）：{exc}",
            ) from exc
        media_type, filename = MEDIA_TYPE_PDF, f"paper-{paper_id}-{sheet_mode.value}.pdf"
        skip_headers = _layout_guard_skip_headers(paper_id, data)

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', **skip_headers},
    )


def _layout_guard_skip_headers(paper_id: int, data: object) -> dict[str, str]:
    """守卫被跳过时，把这件事**放进响应头**并在日志里留一条 WARNING（D81 硬约束 2/3）。

    为什么不能只写日志：日志在一个「没人读的地方」，等于状态不存在（task-50 的教训）。
    判据要能在**不读日志**的情况下区分「守卫生效」与「守卫被跳过」两个世界——
    所以跳过必须在响应里看得见。

    WARNING 里必须带**卷号**与**实际溢出行数**（不是「有溢出」这种没有信息量的说法）：
    值班主任拿到这条日志要能立刻回答「哪张卷子、跳过了几次、当时到底有几行放不下」。

    普通 `bytes`（例如测试里的假导出函数）取不到 `guard_skipped`，按「没跳过」处理：
    假函数世界里根本没有真实测量，声称跳过了反而是假信息。
    """
    if not getattr(data, "guard_skipped", False):
        return {}
    rows = tuple(getattr(data, "overflow_rows", ()) or ())
    logging.getLogger("app.routers.exports").warning(
        "NFR-5 排版守卫被跳过（%s=1）：卷 %s 仍有 %d 行放不下%s；"
        "成品可能把题目跨页拆开 —— 这是应急出口，请尽快查明判据为何误报",
        pdf_export.LAYOUT_GUARD_ENV,
        paper_id,
        len(rows),
        f"（行号 {list(rows)}）" if rows else "（未量到行号）",
    )
    return {
        pdf_export.LAYOUT_GUARD_SKIPPED_HEADER: pdf_export.LAYOUT_GUARD_SKIPPED_VALUE,
    }


@router.get(
    ROUTES["export_attempts"],
    response_class=Response,
    responses={200: {"content": {attempt_export.MEDIA_TYPE_CSV: {}, attempt_export.MEDIA_TYPE_XLSX: {}}}},
)
def export_attempts(
    file_format: str = Query(default="csv", alias="format"),
    paper_id: int | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
) -> Response:
    """导出成绩表（FR-T13）：`format=csv`（默认）→ CSV；`format=xlsx` → Excel。

    **故意没有 `limit` / `offset`**：那两个是列表（FR-T11）的分页参数，导出要的是
    「当前筛选下的**全部**记录」（契约 §5.5）。分页参数缺省时 FastAPI 会**忽略**传入的
    `limit`/`offset`（未知查询参数不参与绑定），所以「导出偷偷只给一页」在这里
    在**结构上**不可能发生——判据专门造了「总数 > 一页」的数据来钉死这点。
    """
    fmt = parse_attempts_format(file_format)
    rows = attempt_service.list_attempts_all(db, paper_id=paper_id, q=_normalise_q(q))
    data, media_type, filename = attempt_export.export_attempts(rows, fmt)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": attempt_export.content_disposition(filename)},
    )
