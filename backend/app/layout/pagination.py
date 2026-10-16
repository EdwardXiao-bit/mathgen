"""分页几何与自动微调 —— NFR-5「题目不被跨页截断」的判定与修补。

**两个概念要分清**（这是 NFR-5 的核心，别混）：

* **vertical 溢出**：一行题块本身比一个版心还高（例如 `cols_per_row=2` + 大字号 +
  含答题空位）。这种行**无论怎么排都会被切开**，只能改排版参数（减小行距/字号、
  增加每行题数、缩小页边距）。
* **跨页截断**：一行横跨分页线。`break-inside: avoid` 会让 Chromium 把整行推到下一页，
  所以只要行高不超版心，就**不会**出现真正的截断——代价是页面底部可能留下空白。

因此本模块的算法是：

1. 量出每个行容器的 `top` / `height`（浏览器侧测量，脚本见 `layout.styles.measure_js`）；
2. 按版心高度算出**内容区**高度（A4 高 - 上下页边距）；
3. 任何一行 `height > content_height` 即判定为 `overflow_rows`；
4. 只要存在溢出，就按 `DIRECTIONS` 轮转着收紧一个参数并重排，直到无溢出或达到上限；
5. 返回**最佳一次**的排版参数与诊断信息（`ReflowResult.diagnostics` 会原样交给调用方，
   老师端可以据此提示「建议减小字号」——设计文档 §9.2 要求的回溯调整）。

本模块是纯计算，不导入 `render` / `export`，避免循环依赖：
需要重排时由调用方传入 `render_with(layout)` 回调。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Mapping

from app.contracts.layout import BOUNDS, LayoutParams

from app.layout.styles import A4_HEIGHT_MM, A4_WIDTH_MM, DIRECTIONS

__all__ = [
    "MM_PER_PX",
    "PaginationGeometry",
    "PageMeasurement",
    "Overflow",
    "PageCrossing",
    "ReflowResult",
    "geometry_for",
    "content_width_px",
    "rows_for",
    "measure_page",
    "detect_overflow",
    "detect_page_crossing",
    "usable_geometry",
    "reflow",
]

#: CSS 像素 -> 毫米（Chromium 打印按 96dpi 布局）
MM_PER_PX = 25.4 / 96.0


@dataclass(frozen=True)
class PaginationGeometry:
    """一页的可用几何（单位：px，按 96dpi）。"""

    page_width_mm: float
    page_height_mm: float
    margin_mm: float

    @property
    def content_height_px(self) -> float:
        return (self.page_height_mm - 2 * self.margin_mm) / MM_PER_PX

    @property
    def content_width_px(self) -> float:
        return (self.page_width_mm - 2 * self.margin_mm) / MM_PER_PX


@dataclass(frozen=True)
class PageMeasurement:
    """浏览器侧量到的卷面排布（`layout.styles.measure_js` 的返回值）。"""

    rows: tuple[Mapping[str, float], ...]
    items: tuple[Mapping[str, float], ...]
    doc_height: float
    body_width: float
    styles: Mapping[str, str]

    @classmethod
    def from_raw(cls, raw: Mapping) -> "PageMeasurement":
        return cls(
            rows=tuple(raw.get("rows") or ()),
            items=tuple(raw.get("items") or ()),
            doc_height=float(raw.get("docHeight") or 0.0),
            body_width=float(raw.get("bodyWidth") or 0.0),
            styles=dict(raw.get("styles") or {}),
        )


@dataclass(frozen=True)
class Overflow:
    """某一行的溢出判定。"""

    row_index: int
    height_px: float
    content_height_px: float

    @property
    def overflow_px(self) -> float:
        return self.height_px - self.content_height_px


@dataclass(frozen=True)
class PageCrossing:
    """某一行横跨了分页线（NFR-5 要防的正是这个）。"""

    row_index: int
    start_page: int
    end_page: int
    top_px: float
    bottom_px: float

    @property
    def crosses(self) -> bool:
        return self.end_page > self.start_page


@dataclass(frozen=True)
class ReflowResult:
    """自动微调的结果。"""

    layout: LayoutParams
    attempts: int
    overflow_rows: tuple[Overflow, ...]
    diagnostics: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.overflow_rows


def geometry_for(layout: LayoutParams) -> PaginationGeometry:
    return PaginationGeometry(A4_WIDTH_MM, A4_HEIGHT_MM, layout.page_margin)


#: `LayoutParams.page_margin` 的单位是**厘米**，换算成毫米要乘 10。
#: （我第一版漏了这一步，`210 - 2 * 2.0` 直接算成 206mm → 视口取 779px，
#: 比真实版心 170mm 宽 20%，等于没修。既有的 `test_layout_params.py:81` 与
#: `test_visual_d70.py:222` 一直是乘 10 的，这里与它们对齐。）
_MM_PER_CM = 10.0


def _margin_mm(layout: LayoutParams) -> float:
    """页边距，毫米。

    `LayoutParams.page_margin` 的单位是 **cm**，而页面几何全部按 **mm** 算。
    混用会差 10 倍，且**两个方向都踩过**：
    `content_width_px()` 把版心算宽了 20%；
    `usable_geometry()` / `detect_page_crossing()` 把「第 2 页起让出两个页边距」
    算少了 10 倍（`page_margin / MM_PER_PX` 把 cm 当 mm），`page_of()` 于是会
    把越过分页线的行算成没越过——正是 NFR-5 要防的漏报。
    """
    return layout.page_margin * _MM_PER_CM


def content_width_px(layout: LayoutParams) -> int:
    """真实打印版心的宽度（CSS 像素）——**测量视口必须用它**。

    ## 为什么测量视口必须等于版心宽（M6 实测，NFR-5 的判据前提）

    本机的 `measure_js` 在 Chromium 的**屏幕视口**里量 `.q-row` 的几何，而真实打印时
    `.sheet { width: 100% }` 填的是 A4 的**版心宽**。两者若不等宽，文本**折行行为**就不同：

    | 场景（cols=4、font=14、margin=2cm、24 题长题面） | 首行高 |
    | --- | --- |
    | 视口 1280px（`new_page()` 的默认值） | 105.48px |
    | 视口 642px（= 210mm − 2×2cm） | 157.77px（折 4 行） |

    1280px 下窄列不折行、642px 下折行，于是**测出来的行高与真实打印差近 1.5 倍**
    （105.48px vs 157.77px）。行高被系统性低估，`detect_overflow()` 与
    `detect_page_crossing()` 就会漏报——这是 NFR-5 判据链上的一个假阴性来源。

    ## 为什么这只是「测量」修正，不动交付物

    同一份 HTML 在视口 1280px 与版心宽下 `page.pdf()` 的字节**完全相同**
    （实测 sha256 相等，3 组参数），因为打印走的是 Chromium 自己的分页布局，
    与屏幕视口无关。所以把测量视口改成版心宽，只让**量出来的几何**与纸上一致。

    按 96dpi 布局换算：`(210mm - 2 × page_margin_cm × 10) / MM_PER_PX`。
    1.0/2.0/3.0cm → 786/643/567px（真实 PDF 的文字 x 范围 10.05/20.11/29.90mm
    与这三者一致：1cm 时左内边距不足 1cm，Chromium 取自己的最小打印边距）。
    """
    content_mm = A4_WIDTH_MM - 2.0 * _margin_mm(layout)
    return round(content_mm / MM_PER_PX)


def rows_for(item_count: int, cols_per_row: int) -> int:
    """题量按每行题数切行后的行数（向上取整，0 题 0 行）。"""
    if cols_per_row <= 0:
        raise ValueError("cols_per_row 必须为正整数")
    return (item_count + cols_per_row - 1) // cols_per_row


def measure_page(measurement: Mapping | PageMeasurement) -> PageMeasurement:
    """把测量字典规整成 `PageMeasurement`。"""
    if isinstance(measurement, PageMeasurement):
        return measurement
    return PageMeasurement.from_raw(measurement)


def detect_overflow(
    measurement: Mapping | PageMeasurement,
    layout: LayoutParams,
    tolerance_px: float = 1.0,
) -> tuple[Overflow, ...]:
    """找出「一行连一整页都放不下」的行。

    这种行无论怎么排都会被切开，只能改排版参数（减小行距 / 字号、增加每行题数、
    缩小页边距）。判定阈值用 `usable_geometry()` 的**实测标定值**，不是 A4 名义版心。
    """
    page = measure_page(measurement)
    usable_first, _usable_later = usable_geometry(layout)
    found: list[Overflow] = []
    for index, row in enumerate(page.rows):
        height = float(row.get("height") or 0.0)
        if height > usable_first + tolerance_px:
            found.append(Overflow(index, height, usable_first))
    return tuple(found)


#: 页面盒的**实测标定**说明（不是推导值）。
#:
#: 名义上 A4 高 297mm、`@page { margin: m }` 时第 1 页可印 `297 - m`（mm）。
#: 但 Chromium 1223 的打印通道实际给不出这么多：纸张上下各有一圈不可印边距，
#: 而且**只有实测才知道确切数字**——用名义值判定「某行是否跨页」会得到与真实 PDF
#: 相反的结论（首轮实现就踩了这个坑：名义版心判定 60 题卷「有跨界」，
#: 实际 PDF 里那一行被完整推到了第 2 页）。
#:
#: 因此本模块的 `detect_page_crossing()` 默认改用调用方**实测**的页面盒
#: （`app.export.measure_page_box()`，用真实卷面内容二分反解），
#: 拿不到实测值时退化为**保守**的名义几何（宁可多报跨界，不可漏报）。
#:
#: 实测数据（`app/export/calibrate.py` 的复跑用例，2026-10-14，Chromium 1223）：
#:
#: | 页边距 | 名称版心 | 实测首页可装 |
#: | --- | --- | --- |
#: | 2cm、4 列、默认字号 | 277mm (1047px) | 12 行约 1082px |
#: | 3cm、4 列、默认字号 | 267mm (1009px) | 8 行约 937px  |
#:
#: 两组数字说明：**同一份卷面在不同页边距下的实际容量差异，比名义值大得多**，
#: 所以只能按卷面实测，不能按公式外推。
USABLE_FALLBACK_RATIO = 0.92

#: **实测的分页线表**（单位：CSS 像素，文档流坐标）。
#:
#: 键 = 页边距（cm），值 = 「第 1 页放得下的最后一行的**底边**」。
#: 也就是「第 1 页分页线落在 (该值, 该值 + 一行高] 区间内」。
#:
#: 来源：`app/export/measure_page_box()` 的实测（`tests/render/test_export_pdf.py`
#: 的 `test_measured_capacity_matches_contract_table` 会复跑并核对，偏差超过
#: 一行步距就红）。卷面口径：4 列、默认字号 14pt、标题 + 卷头栏。
#:
#: ## M6 修正（这张表原先记错了，两处错都要记住）
#:
#: 1. **方向反了**：原表把 1.0cm 记成 649.4px、3.0cm 记成 1008.7px，
#:    即「页边距越大、可用高度越大」。这在物理上不可能：页边距大 ⇒ 版心小 ⇒
#:    可用高度小。原因是原表在**高并发**下扫描，每次 `pdf()` 量到的分页线互相干扰，
#:    把最宽松的一组记到了最紧的页边距上。
#: 2. **值也偏小**：M6 用同样的标定口径串行重测，得到
#:    1.0/1.5/2.0/2.5/3.0cm = 983.12 / 983.12 / 911.27 / 878.67 / 832.95 px，
#:    单调递减（1.0 与 1.5 同档，是 Chromium 内置最小打印边距造成的阶梯）。
#:
#: 新表取**实测值本身**（不再人为取区间下界）：`measure_page_box()` 返回的
#: `usable_first_px` 已经是「放得下的最后一行底边」，用它当 `usable_first`
#: 会让「贴边的行」判成跨界——这是**偏保守**方向（可能把刚好放得下的行算成跨界，
#: 但不会漏报真正的截断），符合 NFR-5 的安全取向。
#: 表里 2.0cm 档的实测 911.27 与旧表 864.9 相差一行步距（71.86px）以内，
#: 也能通过契约测试的容差。
MEASURED_BREAK_CEILING_PX = {
    1.0: 983.1,
    1.1: 983.1,
    1.2: 983.1,
    1.3: 983.1,
    1.4: 983.1,
    1.5: 983.1,
    1.6: 911.3,
    1.7: 911.3,
    1.8: 911.3,
    1.9: 911.3,
    2.0: 911.3,
    2.1: 911.3,
    2.2: 911.3,
    2.3: 911.3,
    2.4: 911.3,
    2.5: 878.7,
    2.6: 878.7,
    2.7: 878.7,
    2.8: 878.7,
    2.9: 878.7,
    3.0: 833.0,
}

def usable_geometry(layout: LayoutParams) -> tuple[float, float]:
    """没有实测页面盒时使用的页面几何（文档流坐标，px）。

    * 页边距命中实测表 → 用实测的第 1 页可用高度；
    * 否则 → 名义版心 × `USABLE_FALLBACK_RATIO`（更保守）。

    第 2 页起每页比第 1 页多让出「上下两个页边距」，按同一收缩比例推。
    """
    nominal_first = (A4_HEIGHT_MM - _margin_mm(layout)) / MM_PER_PX
    nominal_later = (A4_HEIGHT_MM - 2 * _margin_mm(layout)) / MM_PER_PX
    measured = MEASURED_BREAK_CEILING_PX.get(round(layout.page_margin, 1))
    first = measured if measured is not None else nominal_first * USABLE_FALLBACK_RATIO
    return first, nominal_later - (nominal_first - first)


def detect_page_crossing(
    measurement: Mapping | PageMeasurement,
    layout: LayoutParams,
    usable_first_px: float | None = None,
    tolerance_px: float = 2.0,
    safety_factor: float = 0.97,
) -> tuple[PageCrossing, ...]:
    """判断有没有行容器横跨分页线（NFR-5 的核心断言）。

    **坐标系**：`measure_js` 量到的是**文档流失坐标**（正文首行顶部起算，连续）。
    一个行容器若跨越某条分页线，真实 PDF 里就意味着该题被拦腰截断。

    `usable_first_px` 是 `app.export.measure_page_box()` **实测**出来的第 1 页可用高度；
    不给则用 `MEASURED_BREAK_CEILING_PX` 实测表 / 名义版心兜底（更保守）。
    实测值本身有「一行」的分辨率误差，故再乘 `safety_factor`（默认 0.97），
    保证偏保守：可能把贴边的行也判成跨界，但不会漏报真正的截断。

    `.q-row { break-inside: avoid }` 生效时 Chromium 会把整行推到下一页，因此
    「页数符合预期」（`export_pdf_report().pages`）与「无行跨界」（本函数）是
    **两条独立证据**，都要分别断言。
    """
    page = measure_page(measurement)
    nominal_first, _nominal_later = usable_geometry(layout)
    if usable_first_px is None:
        usable_first = nominal_first
    else:
        # 实测值优先。它本身有「一行」的分辨率误差，再乘安全系数，
        # 保证偏保守：可能把贴边的行也判成跨界，但不会漏报真正的截断。
        usable_first = min(usable_first_px * safety_factor, nominal_first)

    # 第 2 页起每页比首页多让出「上下两个页边距」
    usable_later = max(1.0, usable_first - _margin_mm(layout) / MM_PER_PX)

    def page_of(edge: float) -> int:
        """文档流坐标 `edge` 落在第几页。"""
        if edge <= usable_first + tolerance_px:
            return 1
        return 2 + int((edge - usable_first - tolerance_px) // usable_later)

    crossings: list[PageCrossing] = []
    for index, row in enumerate(page.rows):
        top = float(row.get("top") or 0.0)
        bottom = top + float(row.get("height") or 0.0)
        start_page = page_of(top)
        end_page = page_of(bottom)
        if end_page > start_page:
            crossings.append(PageCrossing(index, start_page, end_page, top, bottom))
    return tuple(crossings)


def _shrink(layout: LayoutParams, name: str, delta: float) -> LayoutParams | None:
    """按步长调整一个参数；越界或没有实际变化时返回 None。"""
    if name not in BOUNDS:
        return None
    lo, hi = BOUNDS[name]
    current = getattr(layout, name)
    candidate = current + delta
    if name == "cols_per_row":
        candidate = int(round(candidate))
    else:
        # 避免浮点误差累积出 0.7000000000000001 这类值
        candidate = round(candidate, 2)
    if not (lo <= candidate <= hi) or candidate == current:
        return None
    return replace(layout, **{name: candidate})


def reflow(
    render_with: Callable[[LayoutParams], str],
    measure: Callable[[str], Mapping | PageMeasurement],
    layout: LayoutParams,
    max_attempts: int = 6,
    directions: Iterable[tuple[str, float]] = DIRECTIONS,
) -> ReflowResult:
    """尝试让所有行都放得下一个版心；返回最佳结果。

    `render_with(layout)` 由调用方注入（通常是 `functools.partial(render_sheet, ctx_template)`），
    `measure(html)` 负责在浏览器里量出排布。本模块本身不做 I/O。
    """
    direction_list = tuple(directions)
    diagnostics: list[str] = []

    current = layout
    best = current
    best_overflow = detect_overflow(measure(render_with(current)), current)
    attempts = 1

    if not best_overflow:
        return ReflowResult(current, attempts, (), ("首轮排版即无溢出",))

    diagnostics.append(
        f"首轮有 {len(best_overflow)} 行超出可印区（最大超出 "
        f"{max(o.overflow_px for o in best_overflow) * MM_PER_PX:.1f}mm），尝试收紧排版参数"
    )

    cursor = 0
    while attempts < max_attempts and direction_list:
        name, delta = direction_list[cursor % len(direction_list)]
        cursor += 1
        attempts += 1
        candidate = _shrink(best, name, delta)
        if candidate is None:
            diagnostics.append(f"{name} 已到下界，跳过")
            if cursor > len(direction_list) * 3:
                break
            continue
        overflow = detect_overflow(measure(render_with(candidate)), candidate)
        diagnostics.append(
            f"尝试 {name} {getattr(best, name)} -> {getattr(candidate, name)}："
            f"溢出 {len(overflow)} 行"
        )
        if len(overflow) < len(best_overflow) or (not overflow and best_overflow):
            best, best_overflow = candidate, overflow
        if not best_overflow:
            diagnostics.append("所有行都能放进单个版心")
            return ReflowResult(best, attempts, (), tuple(diagnostics))

    if best_overflow:
        diagnostics.append(
            f"已达调整上限（{attempts} 次）仍有 {len(best_overflow)} 行超高，"
            "建议老师减小字号或减少每行题数"
        )
    return ReflowResult(best, attempts, best_overflow, tuple(diagnostics))
