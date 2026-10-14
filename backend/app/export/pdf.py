"""PDF 导出 —— **冻结入口** `export_pdf(html, out_path) -> None`。

实现方式（契约 §4 的 C3 硬约束）：**Playwright(Chromium) 打印 `render_sheet` 产出的那份 HTML**。
本模块不含任何排版代码——不改 CSS、不重排 DOM、不注入样式，
只做「把字符串给浏览器 → 打印成 A4 → 落盘」。

顺带提供两个同源能力（都复用同一个浏览器会话，不另起排版）：

* `export_pdf_report()`：导出同时返回**页数**与**浏览器侧实测排布**，
  供 NFR-5「断言页数与题块不跨页」使用（`docs/02-技术设计.md` §9.2）。
* `export_png()`：把 HTML 截成图片，供人工目视检查（`03-协作规范.md` V5）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.layout.styles import measure_js
from app.export.browser import BrowserError, session as browser_session

__all__ = [
    "export_pdf",
    "export_pdf_report",
    "export_png",
    "measure_page_box",
    "PdfReport",
    "PageBox",
    "count_pdf_pages",
]

#: Chromium 的 PDF 头部会写创建时间，导致**同一份输入**两次产出的字节不同。
#: 本项目把 PDF 当交付物归档（FR-T7），可复现比「保留真实创建时间」更有价值，
#: 因此落盘前把两个时间字段归一为固定值。已实测：同一 HTML 两次导出的
#: 归一化后字节完全一致。
_FIXED_DATE = "D:20000101000000Z"


@dataclass(frozen=True)
class PdfReport:
    """一次 PDF 导出的实测结果。"""

    path: str
    pages: int
    byte_size: int
    measurement: Mapping[str, Any] = field(default_factory=dict)

    @property
    def rows(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.measurement.get("rows") or ())


def export_pdf(html: str, out_path: str) -> None:
    """把完整 HTML 打印成 A4 PDF 并写入 `out_path`（**冻结签名**）。"""
    export_pdf_report(html, out_path)


def export_pdf_report(html: str, out_path: str, measure: bool = True) -> PdfReport:
    """导出 PDF，并返回页数与浏览器侧实测排布。"""
    if not isinstance(html, str):
        raise TypeError("html 必须是 str")
    if not html.strip():
        raise ValueError("html 为空，拒绝导出空 PDF")

    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)

    measurement: Mapping[str, Any] = {}
    try:
        # 刻意**不**在每次导出后关闭浏览器：`02-技术设计.md` §11 明确要求常驻实例复用启动开销
        # （实测 launch 约 1.0s，而一次 pdf() 只要 0.2s）。会话由调用方或进程退出时释放。
        session = browser_session()
        browser = session.browser()
        page = browser.new_page()
        try:
            # 用 set_content 而不是 file:// —— 不落临时文件，也就没有「路径不可写」这条失败面
            page.set_content(html, wait_until="load")
            # 打印媒体查询与打印一致（NFR-5）。**必须在测量之前**：
            # 否则量到的是 screen 媒体下的排布，与真正打印出来的不是同一份。
            page.emulate_media(media="print")
            page.wait_for_timeout(0)
            if measure:
                measurement = page.evaluate(measure_js())
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
            )
        finally:
            page.close()
    except BrowserError:
        raise
    except Exception as exc:  # pragma: no cover - 环境分支
        raise BrowserError(f"PDF 导出失败：{type(exc).__name__}: {exc}") from exc

    pdf_bytes = _normalise_dates(pdf_bytes)
    with open(out_path, "wb") as handle:
        handle.write(pdf_bytes)

    return PdfReport(
        path=os.path.abspath(out_path),
        pages=count_pdf_pages(pdf_bytes),
        byte_size=len(pdf_bytes),
        measurement=measurement,
    )


def export_png(html: str, out_path: str, full_page: bool = True, scale: float = 1.0) -> str:
    """把 HTML 截成 PNG（供目视检查版式；不是打印通道）。"""
    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)

    with browser_session() as session:
        browser = session.browser()
        page = browser.new_page(viewport={"width": 794, "height": 1123})  # A4 @96dpi
        try:
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            page.screenshot(path=out_path, full_page=full_page, scale="css" if scale == 1.0 else "device")
        finally:
            page.close()
    return os.path.abspath(out_path)


def _normalise_dates(pdf_bytes: bytes) -> bytes:
    """把 PDF 里的创建/修改时间归一为固定值（可复现归档）。

    只改这两个字段，不动版式相关的任何字节。
    """
    normalised = re.sub(
        rb"/CreationDate\s*\([^)]*\)",
        f"/CreationDate ({_FIXED_DATE})".encode(),
        pdf_bytes,
    )
    return re.sub(
        rb"/ModDate\s*\([^)]*\)",
        f"/ModDate ({_FIXED_DATE})".encode(),
        normalised,
    )


_PAGE_OBJ_RE = re.compile(rb"/Type\s*/Page[^s]")
_COUNT_RE = re.compile(rb"/Count\s+(\d+)")


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """统计 PDF 页数（不依赖第三方库）。

    先数 `/Type /Page` 对象；对象被压进对象流时退化到目录里的 `/Count`。
    两种路径都用「强制分页」的样本自检过（见 tests/render）。
    """
    pages = len(_PAGE_OBJ_RE.findall(pdf_bytes))
    if pages:
        return pages
    counts = [int(m) for m in _COUNT_RE.findall(pdf_bytes)]
    return max(counts, default=0)


# ---------------------------------------------------------------------------
# 页面盒实测：NFR-5 的分页判定必须基于「Chromium 真实的页面盒」，不是 A4 名义值
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageBox:
    """**实测**的页面盒（CSS 像素，96dpi）。

    * `usable_first_px` —— 第 1 页能装多少内容（文档流坐标）
    * `usable_px` —— 第 2 页起每页能装多少内容

    带 `row_top_px` / `row_height_px` / `row_step_px`，因为标定是**按真实卷面的
    行排布**反解的，保留它们便于审计与回归。

    **不要用 A4 名义版心代替它**：本机实测例（页边距 2cm、4 列、默认字号）
    名义版心 1047px，而第 1 页实际只装得下约 1082px 的**内容**——
    名义值既不是上界也不是下界，只能实测。
    """

    usable_first_px: float
    usable_px: float
    margin: float
    row_top_px: float = 0.0
    row_height_px: float = 0.0
    row_step_px: float = 0.0
    rows_on_first_page: int = 0
    probes: int = 0

    @property
    def relief_mm(self) -> float:
        """实测首页可用高度相对名义版心的偏差（mm，正数为「更小」）。"""
        from app.layout.pagination import MM_PER_PX
        from app.layout.styles import A4_HEIGHT_MM

        nominal = (A4_HEIGHT_MM - self.margin) / MM_PER_PX
        return (nominal - self.usable_first_px) * MM_PER_PX


#: 标定结果缓存：页面盒由「纸张 + 页边距 + 卷面行高」决定。
#: 进程内同 key 只测一次（每次标定约 25 次 pdf()，实测约 6s）。
_PAGE_BOX_CACHE: dict[tuple, PageBox] = {}


def measure_page_box(
    render_rows: "Callable[[int], str]",
    margin: float,
    max_rows: int = 40,
    cache_key: object | None = None,
    cache: bool = True,
) -> PageBox:
    """**实测**第 1 页能装多少内容：逐行加题，看 PDF 页数何时从 1 跳到 2。

    ## 为什么必须实测、且必须用 PDF（而不是 DOM）

    试过两条纯 DOM 的路，都失败（记录在此，避免后人重走）：

    * `getClientRects()` 在 `emulate_media('print')` 下**永远只返回 1 个矩形**，
      即使该行在真实 PDF 里被切开 —— Playwright 的打印媒体仿真**不启用分页布局引擎**；
    * 行内塞 1×1 标记读位置，得到的是**连续**文档流坐标，与页无关。

    也试过「在某行前插 `break-before: page` 再比较页数」二分，**失败**：
    该判据对 k **不单调**（实测 100 题卷面，k=11 delta=1 而 k=12 delta=0）。

    最终采用最朴素也最可靠的办法：**逐行加题，直接读 PDF 页数**。

    ## 结果的含义

    找到第一个让页数变成 2 的行数 `k`，则

        usable_first ∈ (bottom(k-1), bottom(k))

    这里取**区间上界** `bottom(k)`（保守：宁多报跨界，不漏报截断），
    并把整个区间一并记在 `row_top_px` / `row_step_px` 里便于审计。

    约 `k+2` 次 `pdf()`（典型 14 次，实测 3~6s），进程内按 `cache_key` 缓存。
    """
    key = (cache_key if cache_key is not None else margin, margin, max_rows)
    if cache and key in _PAGE_BOX_CACHE:
        return _PAGE_BOX_CACHE[key]

    probes = 0
    session = browser_session()
    page = session.browser().new_page()

    def pages_of(html: str) -> int:
        nonlocal probes
        probes += 1
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        return count_pdf_pages(page.pdf(format="A4", print_background=True, prefer_css_page_size=True))

    def row_bounds(rows: int) -> list[dict]:
        page.set_content(render_rows(rows), wait_until="load")
        page.emulate_media(media="print")
        return list(page.evaluate(measure_js())["rows"])

    try:
        geometry = row_bounds(2)
        if len(geometry) < 2:
            raise BrowserError("标定失败：卷面没有可测量的行容器（.q-row）")
        row_top = float(geometry[0]["top"])
        row_height = float(geometry[0]["height"])
        row_step = float(geometry[1]["top"]) - row_top

        previous = pages_of(render_rows(1))
        first_break_rows = None
        rows = 1
        while rows < max_rows:
            rows += 1
            current = pages_of(render_rows(rows))
            if current > previous:
                first_break_rows = rows
                break
            previous = current

        if first_break_rows is None:
            rows_on_first = max_rows
            bounds = row_bounds(max_rows)
        else:
            rows_on_first = first_break_rows - 1
            bounds = row_bounds(first_break_rows)

        # 第 1 页装得下的最后一行（0-based index = rows_on_first - 1）的底边
        fitting = bounds[rows_on_first - 1] if rows_on_first >= 1 else {"top": 0.0, "height": 0.0}
        usable_first = float(fitting["top"]) + float(fitting["height"])
    finally:
        page.close()

    box = PageBox(
        usable_first_px=float(usable_first),
        usable_px=float(usable_first) - float(row_step),
        margin=float(margin),
        row_top_px=float(row_top),
        row_height_px=float(row_height),
        row_step_px=float(row_step),
        rows_on_first_page=int(rows_on_first),
        probes=probes,
    )
    if cache:
        _PAGE_BOX_CACHE[key] = box
    return box


def clear_page_box_cache() -> None:
    """清空标定缓存（测试用）。"""
    _PAGE_BOX_CACHE.clear()
