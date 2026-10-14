"""Word（.docx）导出通道：把 `SheetContext` 交给 render-export 的 `export_docx`。

**只做通道**：不写 Word 排版、不 import python-docx（那是 `app.export.docx` 的实现细节），
本模块负责「传 `SheetContext` → 拿字节 → 清理」。

冻结签名（`app/contracts/sheet.py`）：

    def export_docx(ctx: SheetContext, out_path: str) -> None: ...

`app.export.docx` 由 render-export 并行实现，可能尚未就绪：
延迟导入 + `ImportError` → `RenderNotReady`（映射 503 `RENDER_NOT_READY`），
与本项目 `/sheet`、PDF 导出的处理口径一致。

**进程内调用即可**：Word 导出是纯 Python 写 zip，没有 Playwright 那样的线程亲和问题
（对比见 `pdf_export` 的模块 docstring）。
"""
from __future__ import annotations

from app.contracts.sheet import SheetContext
from app.services.export_common import (
    best_effort_unlink,
    export_tmp_dir,
    new_token,
    purge_stale,
)
from app.services.sheet_render import RenderNotReady

__all__ = [
    "DOCX_MEDIA_TYPE",
    "DOCX_MAGIC",
    "DOCX_MODULE",
    "DocxExportUnavailable",
    "export_docx_bytes",
]

#: 契约 §5.1：Word 的官方 MIME 类型
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

#: `.docx` 是 zip 容器（首 4 字节）
DOCX_MAGIC = b"PK\x03\x04"

#: 渲染导出方的 Word 模块（冻结入口 `export_docx`）
DOCX_MODULE = "app.export.docx"


class DocxExportUnavailable(RuntimeError):
    """Word 导出通道不可用（生成失败等）——映射为 503 `EXPORT_NOT_READY`。"""


def export_docx_bytes(ctx: SheetContext) -> bytes:
    """`SheetContext` → `.docx` 字节流。"""
    try:
        from app.export.docx import export_docx
    except ImportError as exc:
        # 模块未就绪（render-export 并行开发中）：与"渲染未就绪"同一语义
        raise RenderNotReady(f"{DOCX_MODULE}.export_docx") from exc

    out_dir = export_tmp_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - 环境分支
        raise DocxExportUnavailable(f"导出目录不可写：{out_dir}（{exc}）") from exc

    purge_stale(out_dir)
    path = out_dir / f"docx-{new_token()}.docx"
    try:
        try:
            export_docx(ctx, str(path))
        except RenderNotReady:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成 503，不把栈丢给前端
            raise DocxExportUnavailable(
                f"Word 导出失败：{type(exc).__name__}: {exc}"
            ) from exc

        if not path.exists():
            raise DocxExportUnavailable("Word 导出未产出文件")
        data = path.read_bytes()
    finally:
        best_effort_unlink(path)

    if not data.startswith(DOCX_MAGIC):
        raise DocxExportUnavailable(
            f"导出结果不是 .docx（前 4 字节为 {data[:4]!r}），拒绝返回给前端"
        )
    return data
