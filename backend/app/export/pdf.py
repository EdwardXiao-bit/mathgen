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

from app.layout.fitting import LayoutTooLarge, guard_layout_of_html
from app.layout.pagination import content_width_px
from app.layout.styles import measure_js
from app.export.browser import BrowserError, session as browser_session

from app.contracts.layout import LayoutParams

__all__ = [
    "export_pdf",
    "export_pdf_report",
    "export_png",
    "layout_of",
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


def _candidate_measure(page: Any) -> Callable[[str, LayoutParams], Mapping[str, Any]]:
    """给 `app.layout.fitting` 的重排搜索用的「量一个候选排版」回调。

    只在**已经判定放不下**时才被调用（失败路径），所以这里多花几秒换一句
    「实测把字号调到 18 号以下」是划算的：诊断里没有具体目标值，老师就只能自己猜。

    视口必须跟着候选的版心宽走（`content_width_px`）——沿用主路径那条修正，
    否则量到的行高系统性偏小，搜索会把「其实还是放不下」的组合报成能放下。
    """

    def measure_candidate(html: str, layout: LayoutParams) -> Mapping[str, Any]:
        page.set_viewport_size(
            {"width": int(round(content_width_px(layout))), "height": 1200}
        )
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        return page.evaluate(measure_js())

    return measure_candidate


def export_pdf(html: str, out_path: str) -> None:
    """把完整 HTML 打印成 A4 PDF 并写入 `out_path`（**冻结签名**）。"""
    export_pdf_report(html, out_path)


def export_pdf_report(
    html: str,
    out_path: str,
    measure: bool = True,
    reject_overflow: bool = False,
) -> PdfReport:
    """导出 PDF，并返回页数与浏览器侧实测排布。

    ## `reject_overflow`（NFR-5 的服务端出口，M6/task-44 决策 ii）

    默认 `False`：本函数的**冻结语义**是「打印并回报」，不替调用方做策略决定，
    所以既有的直接调用方（`tests/render` 的实物用例、`tests/e2e_m2`）行为不变。

    传 `True` 时，在**打印之前**用同一份实测判「有没有哪一行连一整页都放不下」
    （`detect_overflow`，阈值取实测标定的分页线）；有就抛 `LayoutTooLarge`，
    **不写入 `out_path`** —— 交付物要么完整可印，要么不产生。

    判定必须在这里做（而不是由调用方拿到 `PdfReport` 之后再判）：行排布是
    `measure_js()` 在**真实打印媒体**下量出来的，调用方手上没有那份读数；
    等 `export_pdf` 返回时 `page` 已经关掉了。

    ## 坏字节校验 + 一次重试（task-14）

    全量门禁曾**偶发**（实测 6 轮中 1 轮）出现产物不以 `%PDF-` 开头、
    也不以 `%%EOF` 结尾的情况 —— 即 `page.pdf()` 在某种瞬时状态下没有给出
    完整 PDF，而旧实现**照样把它写盘**，于是失败以「测试断言 False」的形式
    出现在离现场很远的地方，无法定位。

    现在两件事：

    1. `_ensure_valid_pdf()` —— 落盘**之前**校验头/尾；不合格就丢弃这份字节，
       **换一个新 page 重试一次**（瞬时渲染器故障重试即好）；
    2. 重试仍不合格 → 抛 `BrowserError`，**永不写坏文件**。
       这样失败点就在现场，且带首尾字节证据。

    注意：这不改变任何正常路径的行为（校验通过就原样写），也不改变 V1 契约。
    """
    if not isinstance(html, str):
        raise TypeError("html 必须是 str")
    if not html.strip():
        raise ValueError("html 为空，拒绝导出空 PDF")

    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)

    last_problem = ""
    for attempt in (1, 2):
        measurement: Mapping[str, Any] = {}
        try:
            # 刻意**不**在每次导出后关闭浏览器：`02-技术设计.md` §11 明确要求常驻实例复用启动开销
            # （实测 launch 约 1.0s，而一次 pdf() 只要 0.2s）。会话由调用方或进程退出时释放。
            session = browser_session()
            browser = session.browser()
            # 测量视口必须 = **真实打印版心宽**：否则文本折行行为与纸上不同，
            # `measure_js` 量出的行高会系统性偏小，`detect_overflow()` /
            # `detect_page_crossing()` 就有漏报（NFR-5 判据链的假阴性）。
            # 详见 `app.layout.pagination.content_width_px()`。
            # 注意：`page.pdf()` 走 Chromium 自己的分页布局，**与屏幕视口无关**
            # （实测同一 HTML 在 1280px 与版心宽下导出的字节 sha256 相同），
            # 所以改视口只修正测量，不改变任何交付物。
            width = content_width_px(layout_of(html))
            page = browser.new_page(viewport={"width": width, "height": 1200})
            try:
                # 用 set_content 而不是 file:// —— 不落临时文件，也就没有「路径不可写」这条失败面
                page.set_content(html, wait_until="load")
                # 打印媒体查询与打印一致（NFR-5）。**必须在测量之前**：
                # 否则量到的是 screen 媒体下的排布，与真正打印出来的不是同一份。
                page.emulate_media(media="print")
                page.wait_for_timeout(0)
                if measure:
                    measurement = page.evaluate(measure_js())
                    if reject_overflow:
                        # 判定 + 拒绝都交给 `app.layout.fitting`（单一出口）：
                        # 它带完整诊断（行号 / 行高 / 阈值 / 可照做的建议）。
                        guard_layout_of_html(
                            html,
                            measurement,
                            measure=_candidate_measure(page),
                        )
                pdf_bytes = page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                page.close()
        except (BrowserError, LayoutTooLarge):
            # 两者都**不重试**：`BrowserError` 的语义是「通道坏了」；
            # `LayoutTooLarge` 是**确定性**判定（同一份 HTML 同一份排版再量一次
            # 必然还是超），重试只会白花一次测量。
            raise
        except Exception as exc:  # pragma: no cover - 环境分支
            last_problem = f"{type(exc).__name__}: {exc}"
            if attempt == 2:
                raise BrowserError(f"PDF 导出失败：{last_problem}") from exc
            continue

        pdf_bytes = _normalise_dates(pdf_bytes)
        problem = _pdf_payload_problem(pdf_bytes)
        if problem is None:
            with open(out_path, "wb") as handle:
                handle.write(pdf_bytes)
            return PdfReport(
                path=os.path.abspath(out_path),
                pages=count_pdf_pages(pdf_bytes),
                byte_size=len(pdf_bytes),
                measurement=measurement,
            )

        last_problem = (f"第 {attempt} 次导出的字节不是完整 PDF：{problem}"
                        f"（size={len(pdf_bytes)}, head={pdf_bytes[:12]!r}, "
                        f"tail={pdf_bytes[-12:]!r}）")

    raise BrowserError(f"PDF 导出失败（已重试一次）：{last_problem}")


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


def _pdf_payload_problem(pdf_bytes: bytes) -> str | None:
    """校验打印通道给的字节是不是**完整 PDF**；不合格则返回可读原因。

    两条判据：

    * **头**：Chromium 的 PDF 必以 `%PDF-` 开头。不是 → `page.pdf()` 根本没返回 PDF
      （瞬时渲染器/通道故障），不是「写出截断」。
    * **尾**：完整 PDF 以 `%%EOF` 收尾（允许尾部空白）。缺尾 → 字节流不完整。

    ## 为什么加这道校验（task-14 的教训）

    M3 门禁排查期间出现过「产物不以 `%PDF-` 开头」的失败。事后定位到根因是
    **跑门禁时有人在改实现文件**（见 `docs/03-协作规范.md` 新增的「运行窗口内不落盘」），
    不是导出通道的缺陷 —— 但那次排查暴露了一个**真实的健壮性缺口**：

    旧实现对 `page.pdf()` 的返回值**不做任何校验就直接写盘**。一旦拿到空字节或残缺字节，
    坏文件照样落盘，失败会在离现场很远的断言里冒出来，极难定位。
    现在改成「落盘前校验 → 不合格就换新 page 重试一次 → 仍不合格则抛错且**不写文件**」，
    失败点因此回到现场，并带首尾字节证据。

    代价为零：正常路径只是三次内存比较。
    """
    if not pdf_bytes:
        return "字节为空"
    if not pdf_bytes.startswith(b"%PDF-"):
        return "缺少 %PDF- 头"
    if not pdf_bytes.rstrip().endswith(b"%%EOF"):
        return "缺少 %%EOF 尾（字节流不完整）"
    if len(pdf_bytes) < 200:
        return f"体积异常（{len(pdf_bytes)} 字节）"
    return None


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
_PAGE_MARGIN_RE = re.compile(r"--page-margin\s*:\s*([0-9.]+)cm")

#: M6：渲染产出的 HTML 里六个排版参数都是 `--<name>: <value>` 形式（契约第 2 条）。
#: 数值项带单位后缀，布尔项是 `display` 关键字（`block`/`none`、`grid`/`none`）。
_LAYOUT_VAR_RES = {
    "cols_per_row": re.compile(r"--cols-per-row\s*:\s*(\d+)"),
    "row_gap": re.compile(r"--row-gap\s*:\s*([0-9.]+)"),
    "font_size": re.compile(r"--font-size\s*:\s*([0-9.]+)"),
    "page_margin": re.compile(r"--page-margin\s*:\s*([0-9.]+)"),
}
_ANSWER_LINE_RE = re.compile(r"--answer-line-display\s*:\s*([A-Za-z-]+)")
_HEADER_BAR_RE = re.compile(r"--header-bar-display\s*:\s*([A-Za-z-]+)")


def layout_of(html: str, page_margin: float | None = None) -> LayoutParams:
    """从渲染好的 HTML 里取回**完整**排版参数（六个 CSS 变量）。

    为什么不改 `export_pdf_report(html, out_path)` 的签名去多传一个 layout：
    该函数是**冻结入口** `export_pdf` 的实现，调用方遍布导出/测试；
    而 `render_sheet()` 产出的 HTML 里本来就带着 `:root { --page-margin: 2.0cm }`
    （契约第 2 条要求六个参数都注入为 CSS 变量），读它既无歧义也不用改签名。
    显式传入 `page_margin` 时优先用它。

    **M6/task-44 起读全部六项**（此前只读 `page_margin`）：`LAYOUT_DOES_NOT_FIT`
    的诊断要报出「实际参数值」，只说页边距就拼不出 `每行 6 题 / 字号 20 / 页边距 3cm`。
    缺失或形态不认识的项**回落到默认值**——真实渲染永远写全六项，回落只服务于
    手工构造的 HTML（测试里的 `<html></html>` 之类），不能让它们在这里抛异常。
    """
    values: dict[str, float | bool] = {}
    for name, pattern in _LAYOUT_VAR_RES.items():
        found = pattern.search(html)
        if found is None:
            continue
        raw = found.group(1)
        values[name] = float(raw) if name == "row_gap" else (
            int(raw) if name == "cols_per_row" else float(raw)
        )
    if page_margin is not None:
        values["page_margin"] = float(page_margin)
    for name, pattern in (("answer_line", _ANSWER_LINE_RE), ("header_bar", _HEADER_BAR_RE)):
        found = pattern.search(html)
        if found is not None:
            values[name] = found.group(1) not in ("none",)
    return LayoutParams(**values)  # type: ignore[arg-type]


def _layout_of(html: str, page_margin: float | None = None) -> LayoutParams:
    """兼容旧名：等价于公开的 `layout_of`。

    保留是因为 `measure_page_box` 与若干注释引用了这个名字（`_layout_of` 一直是
    模块私有，没有外部调用方）；新代码请用 `layout_of`。
    """
    return layout_of(html, page_margin)


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

    ## 测量视口 = 真实版心宽（M6）

    页面盒是用 `measure_js()` 量到的**行几何**反解的，所以测量视口若与真实打印版心
    不等宽，折行行为不同、`row_step_px` 就会偏小。故这里同样把视口设成
    `content_width_px(LayoutParams(page_margin=margin))`；
    而**判定分页线与页数**的那几次 `page.pdf()` 与视口无关（实测字节 sha256 相同）。
    """
    key = (cache_key if cache_key is not None else margin, margin, max_rows)
    if cache and key in _PAGE_BOX_CACHE:
        return _PAGE_BOX_CACHE[key]

    probes = 0
    session = browser_session()
    page = session.browser().new_page(
        viewport={"width": content_width_px(LayoutParams(page_margin=margin)), "height": 1200}
    )

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
