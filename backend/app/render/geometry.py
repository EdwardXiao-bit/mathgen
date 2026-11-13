"""每题答案框几何（D92 / T-M18-02）。

MOde C 要逐题裁答案区，所以需要「第 n 题第 p 页的答案框在哪」的归一化坐标。
这份几何**不能被近似公式猜**：题块高度取决于字宽、折行、行距，而折行由
Chromium 的字体度量决定——`app.layout.pagination` 里那张分页标定表已经用
实测证明「名义公式会得出相反结论」。因此这里和 PDF 导出走同一条路子：

1. `render_with_layout()` 渲染同一份 HTML（和打印的是同一份，C3 同源）；
2. 起 Chromium、**按打印媒体**加载、量 `.q-row` / `.q-blank` 的矩形；
3. 用后端下发的 `data-usable-first-px` / `data-usable-later-px` 把行切成页
   （分页口径与前端预览 `SheetFrame.tsx` 完全一致，避免两套切法漂移）；
4. 归一化成「页内坐标」（0–1、原点左上、4 位小数）。

**为什么坐标要回加页边距**：打印媒体下 `.sheet` 没有 padding，文档流左边就是
版心左边；而 OCR 裁图的对象是**整张纸**（210mm × 297mm），所以页内坐标 = 版心内
坐标 + 页边距，再除以纸面尺寸。

坐标是**纯派生数据**：`sheet_checksum()` 相同的卷面，结果必然相同（判据钉住
确定性），所以按 checksum 做进程内缓存；缓存条目有上限，避免长跑进程堆内存。
"""
from __future__ import annotations

from typing import Any, Mapping

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetMode
from app.export.browser import BrowserError, session as browser_session
from app.layout.pagination import content_width_px
from app.render.marking import LAYOUT_VERSION, sheet_checksum
from app.render.sheet import render_with_layout

__all__ = [
    "PAGE_WIDTH_PX",
    "PAGE_HEIGHT_PX",
    "BOX_DIGITS",
    "answer_boxes",
    "cached_geometry_count",
    "clear_geometry_cache",
    "geometry_of",
    "inject_answer_boxes",
    "render_sheet_with_boxes",
]

#: A4 纸在 96dpi 下的 CSS 像素（210mm / 297mm）。归一化分母用纸面尺寸而不是
#: 版心尺寸：OCR 校正后的目标就是整张纸，角点定位块也在纸边。
PAGE_WIDTH_PX = 210 * 96 / 25.4
PAGE_HEIGHT_PX = 297 * 96 / 25.4

#: 归一化坐标保留 4 位小数（1 px ≈ 0.00126，4 位足够且 JSON 稳定）。
BOX_DIGITS = 4

#: 进程内缓存上限。一份卷面 20–60 题，条目本身很小；上限只为防滥用。
_CACHE_LIMIT = 16

_GEOMETRY_JS = r"""
() => {
  const sheet = document.querySelector('.sheet');
  if (!sheet) return null;
  const sr = sheet.getBoundingClientRect();
  const rows = Array.from(document.querySelectorAll('.q-row'));
  const items = Array.from(document.querySelectorAll('.q-item'));
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { top: r.top, left: r.left, width: r.width, height: r.height };
  };
  const rowOf = new Map();
  rows.forEach((row, index) => {
    row.querySelectorAll('.q-item').forEach((item) => rowOf.set(item, index));
  });
  return {
    sheet: { top: sr.top, left: sr.left, width: sr.width, height: sr.height },
    usableFirst: Number(sheet.dataset.usableFirstPx || ''),
    usableLater: Number(sheet.dataset.usableLaterPx || ''),
    marginPx: Number(sheet.dataset.pageMarginPx || ''),
    rows: rows.map(rect),
    items: items.map((el) => {
      const blank = el.querySelector('.q-blank');
      const cs = blank ? getComputedStyle(blank) : null;
      return {
        seq: Number(el.dataset.seq || 0),
        rowIndex: rowOf.get(el) ?? -1,
        rect: rect(el),
        /* 横线被排版参数关掉时（`--answer-line-display: none`）没有可见框，
           退回题块自身矩形，保证「每题都有框」这条不破。 */
        blank: blank && cs && cs.display !== 'none' ? rect(blank) : null,
      };
    }),
  };
}
"""


def _round(value: float) -> float:
    return round(value, BOX_DIGITS)


def _page_breaks(rows: list[Mapping[str, Any]], usable_first: float, usable_later: float) -> list[float]:
    """把行切成页，返回每页「起始行顶部」在文档流里的 y 坐标。

    与 `frontend/src/teacher/SheetFrame.tsx` 的循环逐字同构：一行放不下时
    （底边越过本页可用高度）就把它推到下一页，下一页的起点取**该行自身顶部**。
    注意上限是「上一页起点 + 本页可用高度」而不是「纸面高度」——第一页比后续页
    少让两个页边距，这正是后端下发两个不同数值的原因。
    """
    if not rows:
        return [0.0]
    starts = [0.0]
    page_start = 0.0
    limit = usable_first
    for row in rows:
        top = float(row["top"])
        bottom = top + float(row["height"])
        if bottom > page_start + limit + 0.5 and top > page_start + 0.5:
            page_start = top
            limit = usable_later
            starts.append(page_start)
    return starts


def boxes_from_measurement(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 `_GEOMETRY_JS` 的原始读数换成每题一条的页内归一化框。"""
    if not raw:
        raise ValueError("几何读数为空")
    usable_first = float(raw["usableFirst"])
    usable_later = float(raw["usableLater"])
    margin = float(raw["marginPx"])
    if not (usable_first > 0 and usable_later > 0 and margin > 0):
        raise ValueError("卷面缺少分页几何（data-usable-*-px / data-page-margin-px）")

    rows = list(raw.get("rows") or ())
    starts = _page_breaks(rows, usable_first, usable_later)
    page_of_row = [0] * len(rows)
    for index in range(1, len(starts)):
        for row_index, row in enumerate(rows):
            if float(row["top"]) >= starts[index] - 0.5:
                page_of_row[row_index] = index
    # 用前缀推进，避免上面那层 O(页数 × 行数) 在大卷子上放大——
    # 行按文档流排序，页起点也单调递增，一次扫描足够。
    page_of_row = [0] * len(rows)
    page_index = 0
    for row_index, row in enumerate(rows):
        while page_index + 1 < len(starts) and float(row["top"]) >= starts[page_index + 1] - 0.5:
            page_index += 1
        page_of_row[row_index] = page_index

    boxes: list[dict[str, Any]] = []
    for item in raw.get("items") or ():
        row_index = int(item.get("rowIndex", -1))
        if row_index < 0 or row_index >= len(rows):
            raise ValueError(f"题 {item.get('seq')} 不属于任何行容器")
        page = page_of_row[row_index]
        page_start = starts[page]
        box = item.get("blank") or item["rect"]
        x = (float(box["left"]) + margin) / PAGE_WIDTH_PX
        y = (float(box["top"]) - page_start + margin) / PAGE_HEIGHT_PX
        w = float(box["width"]) / PAGE_WIDTH_PX
        h = float(box["height"]) / PAGE_HEIGHT_PX
        seq = int(item["seq"])
        boxes.append(
            {
                "seq": seq,
                "page": page + 1,
                "x": _round(x),
                "y": _round(y),
                "w": _round(w),
                "h": _round(h),
                "box": "%d,%.4f,%.4f,%.4f,%.4f" % (seq, _round(x), _round(y), _round(w), _round(h)),
            }
        )
    boxes.sort(key=lambda entry: entry["seq"])
    return boxes


def _measure(html: str, layout: LayoutParams) -> Mapping[str, Any]:
    """在打印媒体下量一次几何（与 PDF 导出同一个浏览器会话）。"""
    session = browser_session()
    browser = session.browser()
    page = browser.new_page(viewport={"width": int(round(content_width_px(layout))), "height": 1200})
    try:
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        page.wait_for_timeout(0)
        raw = page.evaluate(_GEOMETRY_JS)
    except Exception as exc:  # pragma: no cover - 环境分支
        raise BrowserError(f"答案框几何量测失败：{type(exc).__name__}: {exc}") from exc
    finally:
        page.close()
    if not raw:
        raise BrowserError("答案框几何量测失败：文档里找不到 .sheet")
    return raw


def _cache() -> dict[str, dict[str, Any]]:
    global _CACHE
    return _CACHE


_CACHE: dict[str, dict[str, Any]] = {}


def clear_geometry_cache() -> None:
    """清空进程内几何缓存（判据用；生产路径不需要）。"""
    _cache().clear()


def cached_geometry_count() -> int:
    """当前缓存条目数（判据用：证明同一 checksum 只量一次）。"""
    return len(_cache())


def geometry_of(ctx: SheetContext, layout: LayoutParams | None = None) -> dict[str, Any]:
    """量出一份卷面的答案框几何，按 checksum 缓存。

    返回 `{"checksum", "layout_version", "pages", "boxes": [...]}`；
    `boxes` 每项含 `seq/page/x/y/w/h` 与拼好的 `box` 字符串。
    """
    if not isinstance(ctx, SheetContext):
        raise TypeError("geometry_of 只接受 SheetContext")
    effective = layout if layout is not None else ctx.layout
    if not isinstance(effective, LayoutParams):
        raise TypeError("layout 必须是 LayoutParams")
    if ctx.mode is not SheetMode.QUESTION:
        raise ValueError("只有题目版有答案框（答案版不裁图）")

    checksum = sheet_checksum(ctx, effective)
    cache = _cache()
    cached = cache.get(checksum)
    if cached is not None:
        return cached

    html = render_with_layout(ctx, effective)
    raw = _measure(html, effective)
    boxes = boxes_from_measurement(raw)
    pages = max((entry["page"] for entry in boxes), default=1)
    geometry = {
        "checksum": checksum,
        "layout_version": LAYOUT_VERSION,
        "pages": pages,
        "boxes": boxes,
    }
    if len(cache) >= _CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    cache[checksum] = geometry
    return geometry


#: 兼容旧名（判据与后续 M18-03 都按 `answer_boxes` 读）。
answer_boxes = geometry_of


def inject_answer_boxes(html: str, geometry: Mapping[str, Any]) -> str:
    """把几何写回 HTML：每个 `.q-item` 补 `data-answer-box` / `data-page`。

    只做字符串替换、**不改 DOM 结构**，所以打印排版与注入前逐像素一致
    （这两个属性不参与任何 CSS 选择器）。题号在页面内唯一，替换点不会撞。
    """
    out = html
    for entry in geometry.get("boxes") or ():
        seq = int(entry["seq"])
        needle = '<div class="q-item" data-seq="%d"' % seq
        if needle not in out:
            raise ValueError(f"HTML 里找不到题 {seq} 的题块")
        replacement = needle + ' data-answer-box="%s" data-page="%d"' % (entry["box"], int(entry["page"]))
        out = out.replace(needle, replacement, 1)
    return out


def render_sheet_with_boxes(ctx: SheetContext, layout: LayoutParams | None = None) -> str:
    """渲染题目版 HTML 并在每个题块上标注答案框几何。

    M18-03 的裁图服务用它（也可以直接读 `geometry_of()`——两者数据同源，
    判据钉住「HTML 上的属性与几何一致」）。答案版没有答案框，原样返回。
    """
    if not isinstance(ctx, SheetContext):
        raise TypeError("render_sheet_with_boxes 只接受 SheetContext")
    effective = layout if layout is not None else ctx.layout
    html = render_with_layout(ctx, effective)
    if ctx.mode is not SheetMode.QUESTION:
        return html
    return inject_answer_boxes(html, geometry_of(ctx, effective))
