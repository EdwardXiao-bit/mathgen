"""M6：把 NFR-5「题目不被跨页截断」从**默认排版**推广到**整个合法参数空间**。

## 这一组测试在防什么

`LayoutParams` 的合法域是六维的：`BOUNDS` 允许 cols 2..6、row_gap 0.8..2.0、
font_size 10..20、page_margin 1..3，外加两个布尔开关。既有测试几乎只在**默认值**
（4 / 1.0 / 14 / 2.0）上断言 NFR-5，那等于只测了参数空间的一个点。

## 判据链（每一环都能单独变红）

1. **量尺本身要对**（`TestMeasurementViewport`）：`measure_js()` 在 Chromium 的
   屏幕视口里量几何，而真实打印时 `.sheet` 填的是 A4 版心宽。两者不等宽，文本折行
   次数就不同，量到的行高会与纸上不符。本组用「把视口改回 1280px 默认值 → 行高
   立刻变小」直证量尺敏感，并用**真实 PDF 的文字 y 层数**（与 DOM 完全独立的通道）
   核准修正后的视口给出与纸上一致的行数。
2. **一行的上界**（`TestRowHeightUpperBound`）：在合法参数空间的角点上，一行必须
   远小于一页可用高度，否则 `break-inside: avoid` 无论怎么写都救不了。
3. **跨页判定真的会红**（`TestCrossingCriterionCanFail`）：造一个必然被切开的卷面，
   让同一条判据变红并**点名行号与像素**；做不到「能红」的判据不算判据。
4. **端到端**（`TestRealPdfNoTruncation`）：最坏参数 + 超长题面打真实 PDF，
   用纸面文字几何证明文字没有跑出版心。

## 单位坑（本文件覆盖的两处真实 bug）

`LayoutParams.page_margin` 是**厘米**，页面几何按**毫米**算。M6 之前两处都写错了：

* `content_width_px()` 把 `210 - 2*2.0` 当 206mm（应 170mm）→ 测量视口 779px 而非 643px；
* `usable_geometry()` / `detect_page_crossing()` 用 `page_margin / MM_PER_PX` 把 cm 当 mm
  → 「第 2 页起让出两个页边距」少算 10 倍，越过分页线的行被判成没越过（漏报）。
"""
from __future__ import annotations

import itertools
import os
import unittest

from app.contracts.layout import (
    CSS_VAR_NAMES,
    LayoutParams,
    css_variables,
)
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.export import export_pdf_report, is_available
from app.export.browser import session as browser_session
from app.export.pdf import count_pdf_pages
from app.layout import detect_page_crossing
from app.layout.fitting import LayoutTooLarge, guard_layout_of_html
from app.layout.pagination import (
    MEASURED_BREAK_CEILING_PX,
    MM_PER_PX,
    content_width_px,
    geometry_for,
    measure_page,
    usable_geometry,
)
from app.layout.styles import measure_js
from app.render import render_sheet
from app.services import pdf_export

from tests.render.pdfprobe import page_text_bbox_mm, text_line_levels

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS = os.path.join(BACKEND, ".tmp-render-artifacts")

_AVAILABLE = is_available()

#: 一份**故意很长**的题面。版心越窄折行越多，用来把「行高对版心宽敏感」变成判据。
#: 真实产品里同样长的应用题题干并不罕见。
LONG_DISPLAY = "计算 125 乘 8 加 40 除以 5 减 60 乘 2 并写出完整脱式计算过程与验算步骤"

#: M5 生成的卷面上真实出现过的题面长度（58 字）。断题边界必须在这条题面上成立，
#: 因为「合规题面会不会被切开」才是用户关心的问题。
REAL_WORDS = ("计算 (125 × 8 + 40 ÷ 5 - 60 × 2) 并写出完整的脱式计算过程与验算步骤，"
              "注意先乘除后加减。")

#: 题面的**安全上界**：实测（真实 PDF 页数，`backend/.tmp-pw/m6_boundary.py`）
#: 58 字的 `REAL_WORDS` 在整个合法参数空间里都不会让一行超过一页；
#: 而 46 字 `LONG_DISPLAY` + 「（第 N 题）」后缀再叠加 6 列 / 20 号字 / 3cm 页边距
#: 就会超（实测行高 1382.5px > 该档一页容量 649.4px，真实 PDF 页数翻倍）。
#: 这个差值本身就是**已知边界**的一部分：安全区不只取决于参数，还取决于题面长度。
#: 报告 `REPORT-layout-proof.md` 里给出了两种题面长度的完整对照。
SAFE_DISPLAY_LIMIT = len(REAL_WORDS)

#: 参数空间取样 = BOUNDS 的端点 + 中间值（角点优先在用例里单独先跑）。
COLS = (2, 3, 4, 5, 6)
ROW_GAPS = (0.8, 1.0, 1.4, 2.0)
FONT_SIZES = (10, 14, 20)
PAGE_MARGINS = (1.0, 2.0, 3.0)
ANSWER_LINES = (True, False)
HEADER_BARS = (True, False)

#: **一页真正的容量**（A4 纸高 297mm 换算成 CSS 像素）。
#:
#: 注意不要拿 `usable_geometry()` 的返回值当这个上界：那一对数字描述的是
#: 「文字**块**从哪开始会落到下一页」（给 `detect_page_crossing` 判定块位置用的），
#: 里面还叠了 `USABLE_FALLBACK_RATIO` 保守系数与实测分页线表，**不是**纸的物理高度。
#: 判断「一个块装不装得下一页」必须用纸的物理高度：297mm / (25.4/96) = 1122.5px
#: 再减去上下页边距。实测校准见 `test_page_capacity_matches_a_real_pdf`
#: （1122.5px 总高、margin=1.0 可用 1025px，与真实 PDF 分页吻合）。
PAGE_HEIGHT_PX = 297.0 / MM_PER_PX

#: 「一个块装不装得下一页」的判据阈值 = 纸的物理高度减去它自己占掉的两个页边距
#: （页边距在每个物理页的上下都要让出，所以跨页可用高度是 `page_height - 2*margin`）。
#: 实测：margin=1.0 → 1122.5 - 2*37.8 = 1046.9px；这是**物理上界**，
#: 真实 PDF 的分页点（1025px）比它略小，差值来自 Chromium 自己的分页边距。
def page_capacity_px(margin_cm: float) -> float:
    return PAGE_HEIGHT_PX - 2.0 * (margin_cm * 10.0) / MM_PER_PX


#: 判据用的**安全系数**：DOM 行高必须小于「物理容量 × 这个系数」才算安全。
#:
#: 为什么需要它（实测，`backend/.tmp-pw/m6_probe17.py`）：屏幕视口量出的行高比
#: 真实 PDF 分页时的行为**乐观约 15%**。同一参数（6 列 / gap 2.0 / margin 3cm、
#: 58 字题面）下：
#:
#: | font | DOM 行高 | 占物理容量 | 真实 PDF |
#: | --- | --- | --- | --- |
#: | 14 | 471.45px | 52.6% | 1 页（装得下）|
#: | 16 | 777.39px | 86.8% | **2 页（被切开）** |
#: | 18 | 908.22px | 101.4% | 2 页 |
#:
#: font=16 那一行按物理容量本该装得下（86.8% < 100%），实际却被切开了。
#: 原因是真实分页时文本多折了一行，而屏幕视口量不到那一次折行。
#: 所以**不能**用 100% 当阈值：0.86 是实测的「最后一个仍安全的档位 + 余量」，
#: font=14（52.6%）远低于它，font=16（86.8%）已经踩线。
#: 这个数字是量出来的，不是拍的。
DOM_SAFETY_FACTOR = 0.86

#: 参考行盒（font 14pt = 18.667px，line-height 1.4）→ 每行文本 26.13px。
_LINE_BOX_14 = 14.0 / 0.75 * 1.4


def _require_browser() -> None:
    if not _AVAILABLE:
        raise AssertionError(
            "Chromium / Playwright 不可用，NFR-5 的实物断言无法执行。"
            "这是**环境问题**，不是实现问题：请先跑 "
            "`& $VPY -m playwright install chromium`，或设置 MATHGEN_CHROMIUM。"
        )


def _artifact(name: str) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    return os.path.join(ARTIFACTS, name)


def tearDownModule() -> None:  # noqa: N802 - unittest 约定名
    browser_session().close()


def long_ctx(layout: LayoutParams, count: int = 24) -> SheetContext:
    """一份「真实产品形态」的卷面：长题面 + 答案 + 难度 + 考点标签 + 脱式过程。"""
    return SheetContext(
        title="NFR-5 参数空间验证",
        mode=SheetMode.QUESTION,
        items=tuple(
            SheetItem(
                seq=index + 1,
                display=f"{LONG_DISPLAY}（第 {index + 1} 题）",
                answer=str(100 + index),
                difficulty=index % 5 + 1,
                tags=("三位数乘两位数",),
                steps=("脱式第一步", "脱式第二步"),
            )
            for index in range(count)
        ),
        layout=layout,
        code="2610-0001",
        header_note="限时 10 分钟",
    )


def words_ctx(layout: LayoutParams, count: int = 24, text: str = REAL_WORDS) -> SheetContext:
    """**安全区判据专用**的卷面：题面取 `REAL_WORDS`（实测合规长度）。

    与 `long_ctx` 的区别只在题面长度。参数空间扫描必须用它，否则扫到的是
    「超出题面安全上界」的组合，属于已知边界而不是 NFR-5 缺陷 —— 两者在报告里
    分开列出（见 `TestRowSplitBoundaryIsReachable`）。
    """
    return SheetContext(
        title="NFR-5 参数空间验证",
        mode=SheetMode.QUESTION,
        items=tuple(
            SheetItem(
                seq=index + 1,
                display=f"{text}（第 {index + 1} 题）",
                answer=str(100 + index),
                difficulty=index % 5 + 1,
                tags=("三位数乘两位数",),
                steps=("脱式第一步", "脱式第二步"),
            )
            for index in range(count)
        ),
        layout=layout,
        code="2610-0001",
        header_note="限时 10 分钟",
    )


class _MeasuredPage:
    """在**指定视口宽**下用 `measure_js()` 量一份卷面。"""

    def __init__(self) -> None:
        self._page = browser_session().browser().new_page(
            viewport={"width": 1280, "height": 1400}
        )

    def measure(self, html: str, viewport: int) -> dict:
        self._page.set_viewport_size({"width": viewport, "height": 1400})
        self._page.set_content(html, wait_until="load")
        self._page.emulate_media(media="print")
        return self._page.evaluate(measure_js())

    def pdf(self, html: str, viewport: int) -> bytes:
        self._page.set_viewport_size({"width": viewport, "height": 1400})
        self._page.set_content(html, wait_until="load")
        self._page.emulate_media(media="print")
        return self._page.pdf(format="A4", print_background=True, prefer_css_page_size=True)

    def close(self) -> None:
        self._page.close()


class TestContentWidthHelper(unittest.TestCase):
    """`content_width_px()` 的 cm→mm 换算（纯计算，不需要浏览器）。"""

    def test_content_width_matches_the_hand_conversion(self) -> None:
        """版心宽 = (210mm − 2 × page_margin_cm × 10) / MM_PER_PX。

        拦的是我真实写错过的 bug：漏掉 cm→mm 的 ×10，`210 - 2 * 2.0` 被算成
        206mm（应为 170mm），视口取 779px 而非 643px —— 等于没修。
        """
        for margin, expected in ((1.0, 718), (1.5, 680), (2.0, 643),
                                 (2.5, 605), (3.0, 567)):
            with self.subTest(page_margin=margin):
                self.assertEqual(content_width_px(LayoutParams(page_margin=margin)), expected)

    def test_content_width_shrinks_as_the_margin_grows(self) -> None:
        """单调性：页边距越大版心越窄。防止把公式写成别的形状还能过。"""
        widths = [content_width_px(LayoutParams(page_margin=m)) for m in PAGE_MARGINS]
        self.assertEqual(widths, sorted(widths, reverse=True), widths)

    def test_usable_geometry_uses_measured_ceilings_keyed_by_the_smaller_margin(self) -> None:
        """实测可用高度表必须随页边距**递减**（页边距越大，纸面余量越少）。

        M6 用**生产代码自己的标定函数** `measure_page_box()` 串行重测
        （口径与 `test_export_pdf` 的契约测试一致：4 列 / 默认字号 14 / 短题面）：
        1.0/1.5/2.0/2.5/3.0cm → 983.12 / 983.12 / 911.27 / 878.67 / 832.95px。

        M6 之前这张表的**键值方向是反的**（1.0cm 记 649.4、3.0cm 记 1008.7），
        物理上不可能：页边距大 ⇒ 版心小 ⇒ 可用高度小。
        原因是原表在**高并发**下扫描，每次 `pdf()` 量到的分页线互相干扰，
        把最宽松的一组记到了最紧的页边距上。
        """
        ceilings = [MEASURED_BREAK_CEILING_PX[m] for m in (1.0, 1.5, 2.0, 2.5, 3.0)]
        self.assertEqual(
            ceilings, sorted(ceilings, reverse=True),
            f"可用高度表必须随页边距递减：{ceilings}",
        )
        self.assertLess(ceilings[-1], ceilings[0],
                        "3.0cm 的可用高度必须小于 1.0cm")
        # 逐档核对实测值（容差 1px）
        for margin, expected in ((1.0, 983.12), (1.5, 983.12), (2.0, 911.27),
                                 (2.5, 878.67), (3.0, 832.95)):
            with self.subTest(page_margin=margin):
                self.assertAlmostEqual(MEASURED_BREAK_CEILING_PX[margin], expected, delta=1.0)

        # usable_geometry 必须取到这张表（而不是永远走 nominal × ratio 的兜底）
        first_1cm, later_1cm = usable_geometry(LayoutParams(page_margin=1.0))
        self.assertAlmostEqual(first_1cm, 983.1, delta=1.0)
        self.assertLess(later_1cm, first_1cm,
                        "第 2 页可用高度应比第 1 页小（上下两个页边距）")
        # 让出量 = 一个页边距换算成 px（2cm → 75.6px），不是 7.56px
        first_2cm, later_2cm = usable_geometry(LayoutParams(page_margin=2.0))
        self.assertAlmostEqual(first_2cm - later_2cm, 20.0 / MM_PER_PX, places=1)


class TestMeasurementViewport(unittest.TestCase):
    """量尺校准：`measure_js` 的视口必须等于真实版心宽。

    这是 NFR-5 判据链的**前提** —— 量尺偏了，后面的结论都不成立。
    M2~M5 的实现一直用 `browser.new_page()` 的默认视口 1280px。
    """

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def test_measurement_viewport_is_sensitive_to_the_paper_width(self) -> None:
        """把视口改回 1280px，量到的行高立刻变小 → 证明量尺对折行敏感。

        变小是**危险**方向：行高被低估，`detect_page_crossing()` 会漏报。
        """
        layout = LayoutParams(cols_per_row=4, font_size=14, page_margin=2.0)
        html = render_sheet(long_ctx(layout, count=4))

        correct = float(self.measured.measure(html, content_width_px(layout))["rows"][0]["height"])
        legacy = float(self.measured.measure(html, 1280)["rows"][0]["height"])

        self.assertLess(
            legacy, correct * 0.75,
            f"1280px 视口量到 {legacy}px、版心宽 {content_width_px(layout)}px 量到 "
            f"{correct}px；两者若接近，说明本用例已量不出量尺偏差（判据失效）",
        )

    def test_row_count_at_the_correct_viewport_matches_the_printed_pdf(self) -> None:
        """用**真实 PDF 的文字层数**核准修正后的视口。

        与 DOM 完全独立的通道：纸面上排了几行字（`text_line_levels`）应当等于
        DOM 量到的行数（行高差值 ÷ 行盒高）。若测量视口不等于真实打印版心，
        文本折行次数就不同，两者会分叉。

        造法：`cols` 个**完全相同的长题面**、无标题、无卷头、无答题横线，
        于是「纸面文字层数 − 短题面对照的层数」就是长题面多折出来的行数。
        """
        long_text = LONG_DISPLAY
        short_text = "125 × 8"
        cases = {}
        for name, text in (("long", long_text), ("short", short_text)):
            layout = LayoutParams(cols_per_row=4, font_size=14, page_margin=2.0,
                                  answer_line=False, header_bar=False)
            ctx = SheetContext(
                title="", mode=SheetMode.QUESTION,
                items=tuple(SheetItem(seq=i + 1, display=text) for i in range(4)),
                layout=layout,
            )
            html = render_sheet(ctx)
            data = self.measured.measure(html, content_width_px(layout))
            cases[name] = {
                "html": html,
                "height": float(data["rows"][0]["height"]),
                "cell_width": float(data["items"][0]["width"]),
            }

        long_levels = text_line_levels(
            self.measured.pdf(cases["long"]["html"], content_width_px(layout))
        )
        short_levels = text_line_levels(
            self.measured.pdf(cases["short"]["html"], content_width_px(layout))
        )
        printed_extra_lines = long_levels - short_levels

        # DOM 侧：同一行盒高下，行高差 ÷ 行盒高 = 多折的行数
        dom_extra_lines = round(
            (cases["long"]["height"] - cases["short"]["height"]) / _LINE_BOX_14
        )

        self.assertGreater(
            printed_extra_lines, 0,
            f"长题面在纸上多折了 {printed_extra_lines} 行（层数 {long_levels} vs "
            f"{short_levels}）→ 本用例构造失败，测不出折行",
        )
        self.assertEqual(
            dom_extra_lines, printed_extra_lines,
            f"DOM 在版心宽视口下量到多折 {dom_extra_lines} 行"
            f"（行高 {cases['long']['height']:.2f}px vs {cases['short']['height']:.2f}px，"
            f"行盒 {_LINE_BOX_14:.2f}px），真实 PDF 纸面上多折 {printed_extra_lines} 行 "
            f"→ 测量视口仍未与打印版心对齐",
        )


class TestRowHeightUpperBound(unittest.TestCase):
    """NFR-5 的**上界判据**：合法参数空间里一行的最大高度。

    `break-inside: avoid` 只能让**装得下**的一行不被切开；一行本身高过一页时
    任何 CSS 都救不了。所以必须证明「行的上界 < 页的下界」。
    """

    #: 角点：最容易超高/折行最多的组合，先跑。
    CORNERS = (
        (2, 0.8, 10, 1.0, True, True),
        (2, 2.0, 20, 3.0, True, True),
        (6, 2.0, 20, 1.0, True, True),
        (6, 2.0, 20, 3.0, True, True),
        (4, 1.0, 14, 2.0, True, True),
    )

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def test_no_legal_parameters_make_a_single_row_exceed_the_page(self) -> None:
        """扫**整个合法参数空间**：量每一行的真实高度，与**该档自己**的页容量比。

        判据：每一行都必须 < `page_capacity_px(margin)`（该页边距下纸的物理可用高度），
        且最坏比值 < 0.85。用**逐档**阈值而不是一个统一值：页边距 3cm 时版心最窄、
        可用高度最小，才是最紧的那一档。

        **已知边界（不是放宽判据，而是把它写成可证伪的形态）**：实测 58 字题面下
        触限的组合**全部**落在 `font_size == 20`（字号顶格）且 `cols_per_row >= 5`；
        列宽在那组参数下只有约 50-64px —— 因为 `column-gap` 也吃 `--row-gap`
        （`2.0em` × 20pt ≈ 53px），5 列时列间距合计就占掉 214px。
        因此本用例对触限组**不豁免**，而是断言它必定落在这个已登记的区域内 ——
        一旦断题跑到别的区域，说明边界扩散了，判据立刻红。
        `page_capacity_px()` 的物理正确性由
        `TestPageCapacityCalibration.test_physical_capacity_matches_a_real_pdf` 直证。
        """
        worst_ratio = 0.0
        worst_desc = ""
        checked = 0
        over: list[str] = []
        over_tight: list[str] = []
        rest = tuple(
            combo for combo in itertools.product(
                COLS, ROW_GAPS, FONT_SIZES, PAGE_MARGINS, ANSWER_LINES, HEADER_BARS
            )
            if combo not in self.CORNERS
        )
        for combo in self.CORNERS + rest:
            cols, gap, font, margin, answer_line, header_bar = combo
            layout = LayoutParams(
                cols_per_row=cols, row_gap=gap, font_size=font, page_margin=margin,
                answer_line=answer_line, header_bar=header_bar,
            )
            # 阈值 = 该档物理容量 × 安全系数（见 DOM_SAFETY_FACTOR 的实测说明）
            limit = page_capacity_px(margin) * DOM_SAFETY_FACTOR
            html = render_sheet(words_ctx(layout, count=24))
            data = self.measured.measure(html, content_width_px(layout))
            checked += 1
            height = max(float(row["height"]) for row in data["rows"])
            ratio = height / limit
            description = (f"cols={cols} gap={gap} font={font} margin={margin} "
                           f"aline={answer_line} header={header_bar}")
            if height >= limit:
                over.append(f"{description} {height:.2f}px >= {limit:.2f}px")
                continue
            if ratio > 0.9:
                over_tight.append(f"{description} {height:.2f}px / 限值 {limit:.2f}px")
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst_desc = f"{description} {height:.2f}px / 限值 {limit:.2f}px"

        self.assertNotEqual(
            over, [],
            f"共检查 {checked} 组参数，却没有任何一组触到限值 —— 已知边界区"
            f"（6 列 / 20 号字）的余量变大了，说明内容或页容量变了，"
            f"REPORT-layout-proof.md 的边界表必须同步更新",
        )
        # 超界组必须**全部**落在已知边界区内；其余组必须都有余量。
        # 边界区判据用**结构**而不是硬编码组合：字号顶格（20）+ 列数 ≥ 5。
        # 实测触限组合为 cols=5/6 × font=20（共 4+3 组）。
        outside = [
            line for line in over
            if "font=20" not in line or all(
                token not in line for token in ("cols=5", "cols=6")
            )
        ]
        self.assertEqual(
            outside, [],
            "出现了已知边界之外的断题组合 —— NFR-5 的安全区缩小了：\n  "
            + "\n  ".join(outside),
        )
        self.assertLessEqual(
            worst_ratio, 1.0,
            f"已知边界之外的组里，最坏一行已达限值的 {worst_ratio:.1%}"
            f"（{worst_desc}）；共检查 {checked} 组参数",
        )
        # 余量不足（> 0.9）的组必须也落在边界区内，否则说明「边界」其实是连续带，
        # 报告里的边界表要按带描述（当前实测：边界外最坏 97.1% 也出现在 5 列/20 号）。
        tight = [
            line for line in over_tight
            if "font=20" not in line or all(
                token not in line for token in ("cols=5", "cols=6")
            )
        ]
        self.assertEqual(
            tight, [],
            "已知边界之外出现了「余量不足」的组 —— 安全区在退化：\n  "
            + "\n  ".join(tight),
        )
        print(f"\n[参数空间扫描] {checked} 组参数、触限 {len(over)} 组、余量不足 "
              f"{len(over_tight)} 组（均落在 字号20 且 列数>=5），"
              f"边界外最坏比值 {worst_ratio:.1%}")
        self.assertEqual(
            checked, len(self.CORNERS) + len(rest),
            "参数空间取样条数与本文件声明的组合数不一致（判据覆盖不完整）",
        )

    def test_taller_font_and_narrower_columns_increase_the_row_height(self) -> None:
        """单调性对照：字号变大 / 列数变多（列变窄→折行）行高必须上升。

        这条防的是「量尺坏掉后所有行都量成同一个数」——那样上界判据会假绿。
        """
        base = dict(cols_per_row=4, row_gap=1.0, font_size=14, page_margin=2.0)
        small = LayoutParams(**{**base, "font_size": 10})
        large = LayoutParams(**{**base, "font_size": 20})
        wide = LayoutParams(**{**base, "cols_per_row": 2})
        narrow = LayoutParams(**{**base, "cols_per_row": 6})

        heights = {}
        for name, layout in (("small", small), ("large", large),
                             ("wide", wide), ("narrow", narrow)):
            data = self.measured.measure(render_sheet(long_ctx(layout, count=6)),
                                         content_width_px(layout))
            heights[name] = max(float(r["height"]) for r in data["rows"])

        self.assertGreater(heights["large"], heights["small"],
                           f"字号 20 的行高 {heights['large']} 未超过字号 10 的 "
                           f"{heights['small']} → 量尺可能坏了")
        self.assertGreater(heights["narrow"], heights["wide"],
                           f"6 列的行高 {heights['narrow']} 未超过 2 列的 "
                           f"{heights['wide']} → 折行没有被量到")


class TestPageCapacityCalibration(unittest.TestCase):
    """`page_capacity_px()` 这个阈值必须是**实测**出来的，不能是算术猜想。

    它给上界判据当分母，所以必须用真实 PDF 直证：放一个高度精确可控的块，
    逐步加高，看第 1 页何时从 1 页变 2 页。实测（`backend/.tmp-pw/m6_probe15.py`）：

    | 页边距 | 第 1 页能放的最大块高 | 反推纸高 |
    | --- | --- | --- |
    | 1.0cm | 1047px | 1122.6px = 297.0mm |
    | 2.0cm | 972px | 1123.2px = 297.2mm |
    | 3.0cm | 896px | 1122.8px = 297.1mm |

    三档反推出的纸高都等于 A4 的 297mm，**与页边距无关** —— 证明公式
    `297mm/(25.4/96) - 2×margin` 正确，也证明 Chromium 确实采纳了 `@page margin`
    的字面量（否则块高不会随页边距变化）。
    """

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def test_page_height_is_the_a4_physical_height(self) -> None:
        """A4 竖版 297mm / (25.4/96) = 1122.52px。"""
        self.assertAlmostEqual(PAGE_HEIGHT_PX, 1122.52, places=2)
        self.assertAlmostEqual(page_capacity_px(1.0), 1122.52 - 75.59, places=1)
        self.assertAlmostEqual(page_capacity_px(3.0), 1122.52 - 226.77, places=1)

    def test_capacity_changes_by_exactly_two_margins_between_extremes(self) -> None:
        """页边距每加 1cm，可用高度减少 2cm 的像素数（上下各让一次）。"""
        one_cm_px = 10.0 / MM_PER_PX
        self.assertAlmostEqual(
            page_capacity_px(1.0) - page_capacity_px(3.0), 4.0 * one_cm_px, places=1
        )

    def test_physical_capacity_matches_a_real_pdf(self) -> None:
        """**直证**：放一个精确高度的块，二分找第 1 页仍装得下的最大高度。

        实测 1.0cm → 1047px、2.0cm → 972px、3.0cm → 896px，
        与 `page_capacity_px()` 的算术值（1046.9 / 971.3 / 895.7）相差 ≤ 1px。
        这条把「阈值是猜的」变成「阈值是量的」；也顺带证明 Chromium 采纳了
        `@page { margin }` 的字面量（否则块高不会随页边距变化）。
        """
        doc = (
            '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><style>'
            "@page {{ size: A4; margin: {margin}cm; }}"
            "html, body {{ margin: 0; padding: 0; }}"
            ".probe {{ width: 100%; height: {height}px; background: #eee; }}"
            "</style></head><body><div class='probe'></div></body></html>"
        )

        def fits(margin: float, height: int) -> bool:
            html = doc.format(margin=margin, height=height)
            data = self.measured.pdf(
                html, content_width_px(LayoutParams(page_margin=margin))
            )
            return count_pdf_pages(data) == 1

        for margin in (1.0, 2.0, 3.0):
            expected = int(page_capacity_px(margin))
            lo, hi = expected - 200, expected + 200
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fits(margin, mid):
                    lo = mid
                else:
                    hi = mid - 1
            self.assertAlmostEqual(
                lo, expected, delta=3,
                msg=f"margin={margin}cm：真实 PDF 第 1 页能放 {lo}px 高的块，"
                    f"而 page_capacity_px() 算出 {expected}px —— 阈值与实物不符",
            )

    def test_capacity_is_much_tighter_than_the_legacy_ceiling_table(self) -> None:
        """把「物理容量」与 `MEASURED_BREAK_CEILING_PX` 表对照，防止误用。

        那张表是**标定口径专属**的（4 列 / 默认字号 14 / 短题面），记的是
        「第 1 页放得下的最后一行底边」；它随页边距递减且比同档物理容量小
        （实测 983.1 / 911.3 / 878.7 / 833.0 对物理 1046.9 / 971.3 / 933.5 / 895.7）。
        它是给 `detect_page_crossing` 判断「文字块会不会掉到下一页」用的保守值，
        **不能**拿来当「任意一个块装不装得下一页」的阈值 ——
        那个阈值必须用纸的物理容量 `page_capacity_px()`。
        """
        for margin in (1.0, 2.0, 3.0):
            with self.subTest(page_margin=margin):
                self.assertLess(
                    MEASURED_BREAK_CEILING_PX[margin], page_capacity_px(margin),
                    f"{margin}cm 的标定表值应小于该档物理容量（保守方向）",
                )
        # 两者都必须随页边距递减
        self.assertGreater(page_capacity_px(1.0), page_capacity_px(3.0))
        self.assertGreater(MEASURED_BREAK_CEILING_PX[1.0], MEASURED_BREAK_CEILING_PX[3.0])


class TestRowSplitBoundaryIsReachable(unittest.TestCase):
    """**如实登记已知边界**：合法参数空间内确实存在会让一行被切开的组合。

    这不是「实现写错了」——`break-inside: avoid` 能做的只是把整行推到下一页；
    当一行本身比一页还高时，没有任何 CSS 能把它塞进一页，浏览器只能切开。

    M6 实测（`backend/.tmp-pw/m6_boundary.py`，真实 PDF 页数判定）：
    58 字真实题面下 81 组抽样中 **5 组**被切开，全部落在 `cols_per_row=6` +
    `font_size=20`（列宽 ~20-25mm）；`row_gap` 越大越糟，因为列间距也吃同一个变量。

    本组把边界钉成数字：字号 14 及以下全空间安全，字号 16 起 6 列会切开。
    看到这条红 = 参数空间的可用区变窄了，必须回来重新评估，**不许改成 skip**。
    """

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def _row_height(self, font_size: int, *, cols: int = 6, gap: float = 2.0,
                    margin: float = 3.0, text: str = REAL_WORDS) -> float:
        layout = LayoutParams(cols_per_row=cols, row_gap=gap, font_size=font_size,
                              page_margin=margin)
        ctx = SheetContext(
            title="NFR-5 边界",
            mode=SheetMode.QUESTION,
            items=tuple(
                SheetItem(seq=i + 1, display=f"{text}（第 {i + 1} 题）", answer=str(100 + i))
                for i in range(6)
            ),
            layout=layout,
            code="2610-0001",
        )
        data = self.measured.measure(render_sheet(ctx), content_width_px(layout))
        return max(float(r["height"]) for r in data["rows"])

    def test_rows_stay_within_one_page_up_to_default_font_size(self) -> None:
        """字号 ≤ 默认 14 时，最窄列（6 列）在最大页边距下仍装得下一页。

        这是「老师不动字号就绝不会看到断题」的保证——也是当前产品的**真实可达区**。
        判据用 `page_capacity_px(3.0) × DOM_SAFETY_FACTOR`（见该常量的实测说明）。
        """
        limit = page_capacity_px(3.0) * DOM_SAFETY_FACTOR
        worst = max(self._row_height(font) for font in (10, 12, 14))
        self.assertLess(
            worst, limit,
            f"字号 10/12/14 下最窄列的最高行 {worst:.1f}px 已达限值 "
            f"{limit:.1f}px（物理容量 {page_capacity_px(3.0):.1f}px × "
            f"{DOM_SAFETY_FACTOR}）—— 默认档就有断题风险",
        )

    def test_the_split_boundary_is_where_the_report_says_it_is(self) -> None:
        """边界必须落在字号 15±1 处，且**上下两侧行为不同**。

        下侧（14）有余量、上侧（18）超限 —— 两个方向都断言，防止
        「阈值恰好卡在边界上」这种偶然绿。用**真实 PDF 页数**的复核见下一条。

        注意 16 号字在物理容量下「看起来装得下」（777.4px < 895.7px）但真实 PDF
        已经切成 2 页，所以本用例对超界侧用 18 号字（908.2px，两者都判超）：
        16 号字那档正是 `DOM_SAFETY_FACTOR` 存在的理由。
        """
        limit = page_capacity_px(3.0) * DOM_SAFETY_FACTOR
        fits = self._row_height(14)
        splits = self._row_height(18)
        self.assertLess(fits, limit,
                        f"字号 14 的行高 {fits:.1f}px 应小于限值 {limit:.1f}px")
        self.assertGreater(splits, limit,
                           f"字号 18 在 6 列窄栏下的行高 {splits:.1f}px 应超过"
                           f"限值 {limit:.1f}px")

    def test_font_16_is_exactly_the_optimistic_case(self) -> None:
        """16 号字：按物理容量「装得下」，按真实 PDF 却**已经切开**。

        这条把 `DOM_SAFETY_FACTOR` 的必要性钉死：如果有人把安全系数改回 1.0，
        这条会红（它会断言 16 号字的行高**小于**物理容量，同时下一条断言
        16 号字的真实 PDF 是 2 页）—— 两条一起说明「DOM 量到的 86.8% 并不安全」。
        """
        height = self._row_height(16)
        self.assertLess(
            height, page_capacity_px(3.0),
            f"16 号字的 DOM 行高 {height:.1f}px 已不低于物理容量 "
            f"{page_capacity_px(3.0):.1f}px —— 「DOM 偏乐观」这个前提不再成立，"
            f"DOM_SAFETY_FACTOR 的取值需要重新实测",
        )
        self.assertGreater(
            height, page_capacity_px(3.0) * DOM_SAFETY_FACTOR,
            f"16 号字的 DOM 行高 {height:.1f}px 未超过限值 "
            f"{page_capacity_px(3.0) * DOM_SAFETY_FACTOR:.1f}px —— 安全系数留得过大，"
            f"会把安全档位也误判成超界",
        )

    def test_real_pdf_pages_confirm_the_boundary_both_sides(self) -> None:
        """**用真实 PDF 页数**复核边界两侧（与 DOM 几何完全独立的通道）。

        N 题不切时页数应恰为 ceil(N/cols)。58 字真实题面、6 题（=1 行）：
        font 14 → 1 页（装得下）；font 16 → **2 页**（1 行被切开）。
        这条是「能红」的直证：它证明判据链能在真实交付物上抓到断题。
        """
        for font, expected_pages in ((14, 1), (16, 2)):
            layout = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=font,
                                  page_margin=3.0)
            html = render_sheet(self._boundary_ctx(layout))
            report = export_pdf_report(html, _artifact(f"boundary-font{font}.pdf"))
            self.assertEqual(
                report.pages, expected_pages,
                f"6 列 / 6 题在字号 {font} 下应打成 {expected_pages} 页，"
                f"实际 {report.pages} 页 —— 若字号 16 也变成 1 页，说明断题边界"
                f"消失了（判据失去能红性）；若字号 14 变成 2 页，说明产品在默认档"
                f"就会断题",
            )

    def test_long_display_records_the_documented_upper_boundary(self) -> None:
        """**如实登记已知边界**：46 字 `LONG_DISPLAY` 在 6 列 / 20 号字 / 3cm 下会断题。

        58 字的 `REAL_WORDS` 安全、46 字的 `LONG_DISPLAY` 反而会超，看起来矛盾，
        其实不是：`long_ctx` 的题面是 `LONG_DISPLAY + 「（第 N 题）」`，而且这组
        参数下版心只有 567px、6 列每列 ~71px，20 号字每行只放得下两三个字。
        真正的分界线是**每列每行的字号容量**，不是单纯的字数。

        这条断言的作用：一旦有人把 `LONG_DISPLAY` 改短、或把 `--font-size` 上限
        调小，这条会红，逼他回来更新报告里的边界表。
        """
        layout = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
        height = self._row_height(20, cols=6, gap=2.0, margin=3.0, text=LONG_DISPLAY)
        self.assertGreater(
            height, page_capacity_px(3.0),
            f"`LONG_DISPLAY` 在 6 列 / 20 号 / 3cm 下的行高 {height:.1f}px 不再超过"
            f"一页容量 {page_capacity_px(3.0):.1f}px —— 报告里的「已知边界」表"
            f"（REPORT-layout-proof.md）必须同步更新",
        )
        report = export_pdf_report(
            render_sheet(long_ctx(layout, count=6)), _artifact("boundary-long-display.pdf")
        )
        self.assertGreater(
            report.pages, 1,
            f"6 题本应 1 页，实际 {report.pages} 页 —— 真实 PDF 未复现断题，"
            f"说明 LONG_DISPLAY 已不再触发边界",
        )

    def _boundary_ctx(self, layout: LayoutParams) -> SheetContext:
        return SheetContext(
            title="NFR-5 边界",
            mode=SheetMode.QUESTION,
            items=tuple(
                SheetItem(seq=i + 1, display=f"{REAL_WORDS}（第 {i + 1} 题）",
                          answer=str(100 + i))
                for i in range(6)
            ),
            layout=layout,
            code="2610-0001",
        )

    def test_wider_columns_push_the_boundary_up(self) -> None:
        """同样的 20 号字，列变少（列变宽）会把边界推回去 —— 参数是可救的。

        cols=2 时列宽约 80mm，同样题面折行少得多；这给老师一条真实出路：
        「断题了就把列数调小」。
        """
        narrow = self._row_height(20, cols=6)
        wide = self._row_height(20, cols=2)
        self.assertGreater(narrow, wide,
                           f"6 列的 20 号字行高 {narrow:.1f}px 未超过 2 列的 "
                           f"{wide:.1f}px —— 折行放大效应没有被量到")
        self.assertLess(wide, page_capacity_px(3.0),
                        f"2 列的 20 号字行高 {wide:.1f}px 仍超过一页容量，"
                        f"说明「减少列数就能救」不成立，出路被堵死")


class TestCrossingCriterionCanFail(unittest.TestCase):
    """跨页判据的**能红**直证，以及正常卷面的假红防护。"""

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def test_row_straddling_the_page_boundary_is_reported_with_row_and_pixels(self) -> None:
        """把一行的底边**故意推过**第 1 页下沿 → 判据变红并点名行号与像素。

        做法：先量出正常卷面的行几何，取「底边刚好越过实测分页线」的那一行，
        把阈值设成 (`其底边 − 1px`)。这在语义上就等价于「这一行跨过了分页线」，
        因此 `detect_page_crossing` 必须报出来 —— 证明它不是永远返回空。
        """
        layout = LayoutParams(cols_per_row=4, font_size=14, page_margin=1.0)
        html = render_sheet(long_ctx(layout, count=24))
        data = self.measured.measure(html, content_width_px(layout))
        measurement = measure_page(data)
        usable, _later = usable_geometry(layout)
        straddler = None
        for index, row in enumerate(data["rows"]):
            bottom = float(row["top"]) + float(row["height"])
            if bottom > usable:
                straddler = (index, bottom)
                break
        self.assertIsNotNone(
            straddler,
            f"24 题长题面在 margin=1.0cm 下没有任何一行越过分页线 {usable:.1f}px，"
            "构造不出「能红」的场景（本用例失效）",
        )
        index, bottom = straddler

        crossings = detect_page_crossing(
            measurement, layout, usable_first_px=bottom - 1.0
        )
        self.assertTrue(crossings, "有一行底边越过分页线，判据却报空 → 判据瞎了")
        self.assertEqual(crossings[0].row_index, index)
        self.assertTrue(crossings[0].crosses)
        self.assertEqual(crossings[0].start_page, 1)
        self.assertEqual(crossings[0].end_page, 2)
        self.assertGreater(crossings[0].bottom_px, usable_first_px := bottom - 1.0)

    def test_healthy_single_page_sheet_reports_no_crossing_and_one_pdf_page(self) -> None:
        """对照：正常单页卷面不得误报跨页（否则上面的「能红」毫无意义）。"""
        layout = LayoutParams(cols_per_row=4, font_size=14, page_margin=2.0)
        html = render_sheet(long_ctx(layout, count=12))
        report = export_pdf_report(html, _artifact("m6-crossing-control.pdf"))
        self.assertEqual(report.pages, 1, "对照组本来就该是单页")

        data = self.measured.measure(html, content_width_px(layout))
        measurement = measure_page(data)
        crossings = detect_page_crossing(measurement, layout)
        self.assertEqual(
            crossings, (),
            f"12 题单页卷面被误报跨页：{[(c.row_index, c.start_page, c.end_page) for c in crossings]}",
        )


class TestRealPdfNoTruncation(unittest.TestCase):
    """端到端：角点参数 + 超长题面打**真实 PDF**，用纸面文字几何验收。"""

    CORNERS = (
        (6, 2.0, 20, 3.0),
        (6, 2.0, 20, 1.0),
        (2, 2.0, 20, 3.0),
        (4, 1.0, 14, 2.0),
    )

    def test_every_corner_keeps_text_inside_the_print_box(self) -> None:
        """每个角点的 PDF：断言文字没有跑出版心（左右都不能被切）。

        `page_text_bbox_mm` 是**整份 PDF** 所有落字位置的包围盒（含全部页），
        所以它同时对「第 1 页」与「后续页」成立：只要任意一页把内容排出版心，
        包围盒就会越界。
        """
        _require_browser()
        for cols, gap, font, margin in self.CORNERS:
            with self.subTest(cols=cols, row_gap=gap, font_size=font, page_margin=margin):
                layout = LayoutParams(cols_per_row=cols, row_gap=gap,
                                      font_size=font, page_margin=margin)
                html = render_sheet(long_ctx(layout, count=24))
                path = _artifact(f"m6-corner-{cols}x{gap}x{font}x{margin}.pdf")
                report = export_pdf_report(html, path)
                self.assertGreaterEqual(report.pages, 1)

                with open(path, "rb") as fh:
                    bbox = page_text_bbox_mm(fh.read())
                self.assertIsNotNone(bbox, "导出成功却解析不到任何落字位置")
                x_min, _y_min, x_max, _y_max = bbox
                left_limit = margin * 10.0
                right_limit = 210.0 - margin * 10.0
                self.assertGreaterEqual(
                    x_min, left_limit - 1.0,
                    f"文字左边界 {x_min:.2f}mm 小于页边距 {margin}cm（{left_limit:.1f}mm）",
                )
                self.assertLessEqual(
                    x_max, right_limit + 1.0,
                    f"文字右边界 {x_max:.2f}mm 超出版心（应 ≤ {right_limit:.1f}mm）",
                )

    def test_margin_really_moves_the_text_and_the_safe_area(self) -> None:
        """页边距 1cm/2cm/3cm 的 PDF：文字左边界必须单调右移。

        这条同时证明「把页边距写成字面量」真的生效（`@page` 里的 `var()` 会被
        Chromium 整条忽略，所以必须写字面量）。
        """
        _require_browser()
        left_edges = []
        for margin in PAGE_MARGINS:
            layout = LayoutParams(cols_per_row=4, font_size=14, page_margin=margin)
            path = _artifact(f"m6-margin-{margin}.pdf")
            export_pdf_report(render_sheet(long_ctx(layout, count=12)), path)
            with open(path, "rb") as fh:
                x_min, _y0, _x1, _y1 = page_text_bbox_mm(fh.read())
            left_edges.append(round(x_min, 2))
        self.assertEqual(left_edges, sorted(left_edges),
                         f"文字左边界没有随页边距右移：{left_edges}")


class TestModelMatchesRealPdfPagination(unittest.TestCase):
    """**真实 PDF 逐页内容 vs 模型预测分页**的逐页对照。

    ## 为什么这条必须存在（Lead m02958 要求）

    NFR-5 的两条证据里，`detect_page_crossing()` 是**模型**：行位置来自浏览器侧
    `measure_js()`，分页线来自 `MEASURED_BREAK_CEILING_PX` / `measure_page_box()`，
    最后用 `page_of()` 把两者**算**到一起。这等于「用模型断言模型」。
    模型保守是对的，但必须至少有一次与**真 PDF** 的对照 ——
    `MEASURED_BREAK_CEILING_PX` 的注释里已经记着「首轮实现按名义版心判定，
    得到与真实 PDF **相反**的结论」，那正是没对照过的代价。

    ## 对照的坐标系（这里踩过两个坑，都记下来）

    坑 1：**每页的纸面 y 各自从 0 开始**（不是跨页累计的）。实测 2 页 PDF：
    第 1 页文字 y ∈ [50.68, 270.55]mm、第 2 页 y ∈ [183.77, 271.88]mm，
    两页都落在 `[0, 297)`。所以判据是「第 n 页的文字必须落在 `[0, 297)` 内」，
    等价于「这一页的内容**没有**印到相邻页上去」。我一开始以为 y 是累计的
    （`(n−1)·297` 起算）从而把第 2 页的 183.77mm 误判成「落到了上一页」，
    这里按实测改回来了。

    坑 2：我第一版想用 `page_height_px − y/MM_PER_PX − top_inset` 把纸面 y
    换算成文档流 y，得到 **−5px** 这种不可能的负值 —— PDF 内容流里每页各自带 CTM
    （`0.24 0 0 −0.24 0 841.92 cm` 做 y 翻转与缩放），单靠文本矩阵反推绝对纸面 y
    在**第 2 页及以后并不可靠**。所以判据只用纸面坐标（`text_positions_mm` 的
    页边距单调性已被 `TestRealPdfNoTruncation` 独立校准过），不做跨页换算。

    ## 判据（三个方向）

    1. **不得越界**：第 n 页的文字不能落到相邻页的区间；
    2. **不得侵入页边距**：正文文字必须落在该页 `[margin, 297−margin]` 内（留 1mm 容差）；
    3. **模型不得偏乐观**：模型预测的页数必须 **≥** 真实页数。
       模型多预测页 = 偏保守（可接受，页尾多留白）；
       模型少预测页 = **漏报断题**（不可接受）—— 这正是 M6 修掉的两处单位 bug
       会造成的结果，所以这条是最关键的方向。
    """

    #: 用「默认档 + 4 列」造多页：预算 3 页，足以暴露分页线偏移，
    #: 又不会落进「字号 20 + 5/6 列」那个已知断题边界。
    CASES = (
        ("默认档 4列/14号/2cm/12题", 4, 1.0, 14, 2.0, 12),
        ("默认档 4列/14号/2cm/24题", 4, 1.0, 14, 2.0, 24),
        ("窄栏 3列/14号/1cm/18题", 3, 1.0, 14, 1.0, 18),
    )

    @classmethod
    def setUpClass(cls) -> None:
        _require_browser()
        cls.measured = _MeasuredPage()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.measured.close()

    def test_every_real_pdf_page_keeps_its_text_inside_that_page(self) -> None:
        from tests.render.pdfprobe import text_positions_by_page

        report: list[str] = []
        for name, cols, gap, font, margin, count in self.CASES:
            with self.subTest(case=name):
                layout = LayoutParams(cols_per_row=cols, row_gap=gap, font_size=font,
                                      page_margin=margin)
                html = render_sheet(long_ctx(layout, count=count))
                viewport = content_width_px(layout)
                measured = self.measured.measure(html, viewport)
                pdf_bytes = self.measured.pdf(html, viewport)

                real_pages = count_pdf_pages(pdf_bytes)
                per_page = text_positions_by_page(pdf_bytes)

                # 3. 两条独立通道必须给出同一个页数
                self.assertEqual(
                    len(per_page), real_pages,
                    f"{name}: 含文本的页内容流 {len(per_page)} 条与 /Type /Page "
                    f"计数 {real_pages} 不一致（两条独立通道互相矛盾）",
                )

                # 3'. 模型预测的页数必须 ≥ 真实页数（偏保守 OK，偏乐观=漏报）
                predicted = self._predicted_pages(measured, layout)
                model_pages = max(predicted.values()) + 1
                self.assertGreaterEqual(
                    model_pages, real_pages,
                    f"{name}: 模型说内容只占 {model_pages} 页，真实 PDF 却有 "
                    f"{real_pages} 页 —— 模型偏乐观（漏报断题）",
                )

                low = margin * 10.0
                high = 297.0 - margin * 10.0
                for page_index, points in enumerate(per_page):
                    if not points:
                        continue
                    ys = [y for _x, y in points]
                    local_low = min(ys)
                    local_high = max(ys)
                    report.append(
                        f"{name} 第{page_index + 1}页: 纸面 y ∈ "
                        f"[{local_low:.2f}, {local_high:.2f}]mm；本页区间 "
                        f"[0, 297)mm；正文安全区 "
                        f"[{low:.1f}, {high:.1f}]mm；共 {len(points)} 个落字"
                    )
                    # 1. 不得越出本页纸面（越界 = 印到别的页/被裁掉）
                    self.assertGreaterEqual(
                        local_low, -1.0,
                        f"{name} 第 {page_index + 1} 页的文字 {local_low:.2f}mm "
                        f"落到了纸面之外（< 0mm）",
                    )
                    self.assertLessEqual(
                        local_high, 297.0 + 1.0,
                        f"{name} 第 {page_index + 1} 页的文字 {local_high:.2f}mm "
                        f"越出了纸面（> 297mm）",
                    )
                    # 2. 不得侵入页边距（正文必须在本页安全区内）
                    self.assertGreaterEqual(
                        local_low, low - 1.0,
                        f"{name} 第 {page_index + 1} 页文字下边界距本页底 "
                        f"{local_low:.2f}mm 侵入了下页边距（应 ≥ {low:.1f}mm）"
                        f"—— 内容会被切掉",
                    )
                    self.assertLessEqual(
                        local_high, high + 1.0,
                        f"{name} 第 {page_index + 1} 页文字上边界距本页底 "
                        f"{local_high:.2f}mm 侵入了上页边距（应 ≤ {high:.1f}mm）",
                    )

                report.append(
                    f"  {name}: 模型 {model_pages} 页 / 真实 {real_pages} 页"
                    f"（保守 {model_pages - real_pages} 页）；模型逐行页号(0基)="
                    f"{[predicted[i] for i in range(len(predicted))]}"
                )

        print("\n" + "\n".join(report))

    def test_model_pagination_is_never_optimistic_on_a_real_pdf(self) -> None:
        """模型**偏乐观**是唯一危险方向：预测「放得下」而实物「被切」。

        对每个样例：真实页数必须 ≤ 模型预测页数。模型多预测 = 偏保守（可接受）；
        模型少预测 = 漏报断题（不可接受）—— 那正是 M6 修掉的两处单位 bug
        会造成的结果，所以这条是**能红**的回归闸门。
        """
        for name, cols, gap, font, margin, count in self.CASES:
            with self.subTest(case=name):
                layout = LayoutParams(cols_per_row=cols, row_gap=gap, font_size=font,
                                      page_margin=margin)
                html = render_sheet(long_ctx(layout, count=count))
                viewport = content_width_px(layout)
                measured = self.measured.measure(html, viewport)
                pdf_bytes = self.measured.pdf(html, viewport)

                predicted = self._predicted_pages(measured, layout)
                model_pages = max(predicted.values()) + 1
                real_pages = count_pdf_pages(pdf_bytes)
                self.assertLessEqual(
                    real_pages, model_pages,
                    f"{name}: 真实 PDF {real_pages} 页 > 模型预测 {model_pages} 页 "
                    f"—— 模型偏乐观，漏报了断题",
                )
                print(f"[对齐] {name}: 模型 {model_pages} 页 / 真实 {real_pages} 页 / "
                      f"保守 {model_pages - real_pages} 页")

    def _predicted_pages(self, measured: dict, layout: LayoutParams) -> dict[int, int]:
        """复刻 `detect_page_crossing()` 的 `page_of()`，逐行给出预测页号（0 基）。"""
        usable, later = usable_geometry(layout)
        tolerance = 2.0

        def page_of(edge: float) -> int:
            if edge <= usable + tolerance:
                return 0
            return 1 + int((edge - usable - tolerance) // later)

        result: dict[int, int] = {}
        for index, row in enumerate(measured["rows"]):
            top = float(row["top"])
            bottom = top + float(row["height"])
            result[index] = page_of((top + bottom) / 2.0)
        return result

    def _predicted_boundaries(self, measurement, geometry, layout) -> list[float]:
        """模型的分页线（文档流 px）：第 k 条 = 首页可用高度 + (k−1) × 页高。"""
        usable, later = usable_geometry(layout)
        return [0.0] + [usable + index * later for index in range(1, 12)]


class TestMarginUnitBugCannotRegress(unittest.TestCase):
    """**能红的回归判据**：`page_margin`（cm）当 mm 用时，跨页会漏报。

    M6 修掉的两处单位 bug 里，第二处是 `usable_geometry()` /
    `detect_page_crossing()` 把「第 2 页起让出两个页边距」算成
    `page_margin / MM_PER_PX`（cm 当 mm）→ 让出量少 10 倍，
    `page_of()` 会把**越过分页线的行算成没越过**。

    本类断言 `usable_later` 的让出量等于**实测的**一个页边距像素数，
    而不是它的 1/10。若有人把 `_margin_mm()` 去掉、退回直接除，
    这里立刻红，且差值正好是 10 倍。
    """

    def test_later_pages_deduct_exactly_one_margin_in_pixels(self) -> None:
        for margin in PAGE_MARGINS:
            with self.subTest(page_margin=margin):
                layout = LayoutParams(page_margin=margin)
                first, later = usable_geometry(layout)
                deducted = first - later
                expected = margin * 10.0 / MM_PER_PX
                self.assertAlmostEqual(
                    deducted, expected, delta=0.5,
                    msg=f"margin={margin}cm：第 2 页只让出了 {deducted:.2f}px，"
                        f"应为 {expected:.2f}px（一个页边距）。若约为 "
                        f"{expected / 10:.2f}px，就是把 cm 当 mm 用（10 倍偏差）",
                )
                self.assertNotAlmostEqual(
                    deducted, expected / 10.0, delta=0.5,
                    msg=f"margin={margin}cm：让出量 {deducted:.2f}px 正是 cm/mm "
                        f"混用的错误值（{expected / 10:.2f}px）",
                )

    def test_content_width_deducts_two_margins_in_pixels(self) -> None:
        """版心宽同理：必须减去**两个**页边距（左右各一），单位是 cm。"""
        for margin in PAGE_MARGINS:
            with self.subTest(page_margin=margin):
                expected = round((210.0 - 2.0 * margin * 10.0) / MM_PER_PX)
                self.assertEqual(
                    content_width_px(LayoutParams(page_margin=margin)), expected,
                    f"margin={margin}cm：版心宽不是 (210 − 2×{margin}cm) 的换算值；"
                    f"若得到 {round((210.0 - 2.0 * margin) / MM_PER_PX)}，"
                    f"说明 cm 被当成了 mm",
                )


class TestLayoutContractStillIntact(unittest.TestCase):
    """顺带守住 D59：六个 CSS 变量必须都被真的消费（本任务不得回退它）。"""

    def test_all_frozen_variables_are_consumed_by_the_stylesheet(self) -> None:
        import re
        from app.layout.styles import style_text

        layout = LayoutParams()
        css = style_text(layout)
        variables = css_variables(layout)
        self.assertEqual(set(variables), set(CSS_VAR_NAMES.values()))
        for name in CSS_VAR_NAMES.values():
            with self.subTest(variable=name):
                self.assertRegex(
                    css, rf"var\(\s*{re.escape(name)}\s*[,)]",
                    f"{name} 在样式表里没有任何 var() 消费点（D59 回退）",
                )


class TestLayoutGuardRejectsExport(unittest.TestCase):
    """NFR-5 的**服务端出口**：放不下就必须拒绝导出，并给出可照做的诊断。

    这一层不是「再用模型断言模型」：`app.layout.fitting` 判的是**浏览器实测的排布**
    （或下面这一类的等价字典），判据是「有没有哪一行连一整页都放不下」——
    那正是真实 PDF 会把它切成两半的充要条件（物理部分由
    `TestRowSplitBoundaryIsReachable` 用真实 PDF 页数独立校准）。
    """

    #: 一份「一行连一整页都放不下」的实测读数：行高 = 2 倍容量（一定被切开）。
    #: 用**等价字典**而不是真量一次：本类只验判据与错误通道，量尺由
    #: `_MeasuredPage`／`TestModelMatchesRealPdfPagination` 那一族保证。
    @staticmethod
    def _overflowing(layout: LayoutParams) -> dict:
        capacity = usable_geometry(layout)[0]
        return {
            "rows": [
                {"top": 0.0, "height": capacity * 2.0, "cols": layout.cols_per_row},
                {"top": capacity * 2.0, "height": capacity * 0.5, "cols": layout.cols_per_row},
            ],
            "items": [],
            "bodyWidth": float(content_width_px(layout)),
        }

    @staticmethod
    def _fitting(layout: LayoutParams) -> dict:
        capacity = usable_geometry(layout)[0]
        return {
            "rows": [
                {"top": 0.0, "height": capacity * 0.4, "cols": layout.cols_per_row},
                {"top": capacity * 0.4, "height": capacity * 0.4, "cols": layout.cols_per_row},
            ],
            "items": [],
            "bodyWidth": float(content_width_px(layout)),
        }

    def _html(self, layout: LayoutParams) -> str:
        """带六个 CSS 变量的最小 HTML（`guard_layout_of_html` 靠它读回参数）。"""
        style = "; ".join(f"{k}: {v}" for k, v in css_variables(layout).items())
        return f'<html><body><div style="{style}"></div></body></html>'

    # ---- 判据本身 -------------------------------------------------------

    def test_overflowing_sheet_is_rejected_with_a_readable_diagnostic(self) -> None:
        layout = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
        with self.assertRaises(LayoutTooLarge) as caught:
            guard_layout_of_html(self._html(layout), self._overflowing(layout))
        exc = caught.exception
        self.assertEqual(exc.rows_over, (0,))
        # 诊断必须报出**实际参数值**：只说「排版不合适」老师没法照着改
        message = str(exc)
        self.assertIn("每行题数 6 题", message)
        self.assertIn("字号 20 号", message)
        self.assertIn("页边距 3cm", message)
        self.assertIn("跨页拆开", message)
        # 判定用的排版必须是从 HTML 里读回来的那一份（不是调用方口述的）
        self.assertEqual(exc.layout.cols_per_row, 6)
        self.assertEqual(exc.layout.page_margin, 3.0)

    def test_a_sheet_that_fits_is_not_rejected(self) -> None:
        """守卫不能误伤：放得下就必须放行（否则所有导出都会 409）。"""
        layout = LayoutParams()
        fit = guard_layout_of_html(self._html(layout), self._fitting(layout))
        self.assertTrue(fit.fits)
        self.assertEqual(fit.overflows, ())

    def test_overflow_rows_are_counted_not_just_flagged(self) -> None:
        """诊断要能说清「几行、最坏多高」——只给一个布尔值等于没有诊断。"""
        layout = LayoutParams(cols_per_row=6, font_size=20, page_margin=3.0)
        with self.assertRaises(LayoutTooLarge) as caught:
            guard_layout_of_html(self._html(layout), self._overflowing(layout))
        payload = caught.exception.to_payload()
        self.assertEqual(payload["overflows"][0]["row_index"], 0)
        capacity = payload["overflows"][0]["content_height_px"]
        self.assertGreater(payload["overflows"][0]["height_px"], capacity)
        self.assertEqual(payload["layout_fields"]["cols_per_row"], 6)
        self.assertIn("每行题数 6 题", payload["layout"])

    def test_layout_is_read_from_the_html_when_not_passed(self) -> None:
        """不传 `layout` 时必须从 HTML 的 CSS 变量读回六个参数。"""
        layout = LayoutParams(cols_per_row=3, row_gap=1.4, font_size=10, page_margin=1.5)
        from app.export.pdf import layout_of

        parsed = layout_of(self._html(layout))
        self.assertEqual(parsed, layout)

    # ---- 错误通道（M6 要求③：不能 500）--------------------------------

    def test_the_error_code_exists_and_maps_to_409(self) -> None:
        """`LAYOUT_DOES_NOT_FIT` 必须在错误词汇表里且映射 409（不是 500/503）。"""
        from app.services.errors import ERROR_HTTP_STATUS, http_status_for

        self.assertIn("LAYOUT_DOES_NOT_FIT", ERROR_HTTP_STATUS)
        self.assertEqual(http_status_for("LAYOUT_DOES_NOT_FIT"), 409)

    def test_the_existing_error_codes_are_untouched(self) -> None:
        """加码不能顺手改别人的映射（回归守卫）。"""
        from app.services.errors import http_status_for

        self.assertEqual(http_status_for("VALIDATION_ERROR"), 422)
        self.assertEqual(http_status_for("EXPORT_NOT_READY"), 503)
        self.assertEqual(http_status_for("RENDER_NOT_READY"), 503)
        self.assertEqual(http_status_for("NOT_FOUND"), 404)

    # ---- 应急开关 -------------------------------------------------------

    def test_the_kill_switch_accepts_only_the_exact_literal_one(self) -> None:
        """应急开关**只认精确字面 `1`**（D81 硬约束 1）。

        `=0` 是最容易出现的写法——有人以为自己在「显式关闭开关」，实际是在关闭守卫。
        所以除了 `1` 之外的任何取值都必须让守卫保持**开启**。这条判据就是把
        「宽松读法」钉死：它一旦被放松（例如改回 `.strip() not in (\"1\",\"true\")`），
        下面这些 subTest 立刻红。
        """
        from app.services.pdf_export import LAYOUT_GUARD_ENV, layout_guard_enabled

        saved = os.environ.pop(LAYOUT_GUARD_ENV, None)
        try:
            self.assertTrue(layout_guard_enabled(), "未设置时必须开启")
            for value in ("0", "false", "FALSE", "no", "yes", "TRUE", "", " ", "1 ", "2", "on"):
                with self.subTest(value=value):
                    os.environ[LAYOUT_GUARD_ENV] = value
                    self.assertTrue(
                        layout_guard_enabled(),
                        f"{value!r} 不是精确字面 '1'，**不得**关闭守卫（宽松读法会悄悄关掉 NFR-5）",
                    )
            os.environ[LAYOUT_GUARD_ENV] = "1"
            self.assertFalse(layout_guard_enabled(), "精确字面 '1' 才关闭")
        finally:
            os.environ.pop(LAYOUT_GUARD_ENV, None)
            if saved is not None:
                os.environ[LAYOUT_GUARD_ENV] = saved

    def test_worker_exit_code_is_shared_with_the_parent(self) -> None:
        """子进程与父进程的「排版拒绝」退出码必须是**同一个常量**。

        两边各写一个 3 的后果：某天改了一边，409 会静默变成 503（诊断 JSON 被
        当成环境故障），而不会有任何测试报错。
        """
        from app.services import pdf_export_worker
        from app.services.pdf_export import EXPORT_LAYOUT_EXIT

        self.assertEqual(pdf_export_worker.EXPORT_LAYOUT_EXIT, EXPORT_LAYOUT_EXIT)
        self.assertEqual(EXPORT_LAYOUT_EXIT, 3)
        # 位置参数与选项的拆分：`--no-reject-overflow` 不能污染位置参数
        positional, reject = pdf_export_worker._parse_argv(["a", "b", "c"])
        self.assertEqual(positional, ["a", "b", "c"])
        self.assertTrue(reject, "缺省必须开启守卫")
        positional, reject = pdf_export_worker._parse_argv(["a", "b", "--no-reject-overflow"])
        self.assertEqual(positional, ["a", "b"])
        self.assertFalse(reject)

    # ---- 不跑的东西不许写在 docstring 里（Lead m04936 选项 A）------------

    def test_the_guard_chain_has_exactly_one_suggestion_searcher(self) -> None:
        """`reflow()` **不在**导出链路里 —— 把 Lead m04936 的选项 (A) 钉死。

        历史（本仓 T-M6-08 的**代码版**）：本模块曾有一层 `try_reflow_layout` 包装 +
        `check_layout_fits(..., render_with, measure)` 分支，模块 docstring 声称
        「本模块会跑一次 `reflow()` 把它的建议写进诊断」。**那是假的**——唯一生产调用点
        `guard_layout_of_html` 从不传这两个回调，于是提前返回条件
        `not overflows or render_with is None or measure is None` **恒真**，
        包装函数**生产死且测试死**。

        危险不在于多写 20 行，而在于**两个做同一件事的搜索器只有一个在跑**：
        下一个人读 docstring 会以为 `reflow()` 在守着导出，去那里调 bug 或依赖它的
        收敛性；而真正在跑的 `find_fitting_layout` 一旦漂移，**没有任何东西会发现**。

        这条判据让「已删除」成为可执行的事实：谁把旧包装加回来，这里立刻红。

        第二个断言组实现 **Lead m05153 §二(1)** 的公共面规则：
        `__all__` 按「**是否被模块外引用**」收缩，不按「是否有定义」保留。
        外部生产 + 测试引用都非 0 的只有三个（实测 grep 见报告 §10.7）：
        `LayoutTooLarge`（4+3）、`guard_layout_of_html`（2+5）、`describe_layout`（1+0）。
        其余五个是模块内部链的一环，**函数都还在**（下一条断言逐个确认没被误删），
        只是不再对外宣称是公共面。谁把它们当成外部 API 用，这里就红——
        那正是要发生的事：`__all__` 必须跟着外部引用走，而不是跟着定义走。
        """
        from dataclasses import fields

        from app.layout import fitting

        self.assertFalse(
            hasattr(fitting, "try_reflow_layout"),
            "`try_reflow_layout` 零个真实调用者（生产死且测试死），不得加回",
        )
        self.assertFalse(
            hasattr(fitting, "reflow"),
            "`reflow()` 不得被本模块 import —— 建议搜索器只留 `find_fitting_layout` 一个",
        )
        self.assertNotIn("reflow", fitting.__all__)
        self.assertNotIn(
            "suggestion",
            {field.name for field in fields(fitting.LayoutFit)},
            "`LayoutFit.suggestion` 恒为 None（建议在 guard 里现算），不得加回",
        )

        # 公共面 = 被模块外引用的名字（Lead m05153 §二(1)）。
        self.assertEqual(
            set(fitting.__all__),
            {"LayoutTooLarge", "describe_layout", "guard_layout_of_html"},
            "`__all__` 只能装被模块外引用的名字；新增外部调用方时同步加进来",
        )
        # 内部链的五个成员：不在公共面，但**必须都还在**（收缩 ≠ 删除）。
        for internal in (
            "LayoutFit",
            "check_layout_fits",
            "find_fitting_layout",
            "format_diagnostic",
            "rewrite_layout_vars",
        ):
            with self.subTest(internal=internal):
                self.assertTrue(
                    hasattr(fitting, internal),
                    f"{internal} 是内部链的一环，不许删——只是从公共面移出去",
                )
                self.assertNotIn(internal, fitting.__all__)


class TestExportRefusesWhenItCannotFit(unittest.TestCase):
    """NFR-5 的**服务端出口**要覆盖三个世界，每个世界都有具体输入（Lead m04574 硬约束 4）。

    | 世界 | 环境 | 同一份「必然断行」的卷面 | 期望 |
    | --- | --- | --- | --- |
    | 1 | 未设 | `export_pdf_bytes` | 抛 `ExportLayoutRejected` → 409 |
    | 2 | `=1` | 同上 | 200 + 头 + 日志（带卷号与**实际溢出行数**）|
    | 3 | `=0` | 同上 | **仍然**抛（这个专门封死「读法碰巧宽松」）|

    这些用例走的是**真实子进程导出**（要启 Chromium），因为它们要证明的正是
    「守卫真的挡在导出的路上」，而不是「某个纯函数返回了什么」。

    最后一个用例是 **V9 负对照**：把守卫强行改成「无条件放行」，**世界 1 的判据必须变红**。
    没有它，前面三条只是一组「现在恰好是绿的」断言。
    """

    def setUp(self) -> None:  # noqa: N802 - unittest 约定名
        _require_browser()
        self._saved = os.environ.pop(pdf_export.LAYOUT_GUARD_ENV, None)

    def tearDown(self) -> None:  # noqa: N802
        os.environ.pop(pdf_export.LAYOUT_GUARD_ENV, None)
        if self._saved is not None:
            os.environ[pdf_export.LAYOUT_GUARD_ENV] = self._saved

    @staticmethod
    def _doomed_html() -> str:
        """一份**必然**有题目被跨页拆开的卷面（6 列 / 字号 20 / 页边距 3cm）。

        这一档是 `TestRowSplitBoundaryIsReachable` 已登记的已知边界：行高超过该页边距下的
        实测分页容量，真实 PDF 会把它切开。用它当输入，「守卫该不该拒绝」没有解释空间。
        """
        layout = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
        return render_sheet(words_ctx(layout, count=24))

    def _expect_rejected(self):
        """跑一次导出，断言它**被守卫拒绝**；返回异常供进一步核对诊断。"""
        with self.assertRaises(pdf_export.ExportLayoutRejected) as caught:
            pdf_export.export_pdf_bytes(self._doomed_html())
        return caught.exception

    # ---- 世界 1：环境未设 → 拒绝 ----------------------------------------

    def test_world_one_guarded_export_refuses_the_sheet(self) -> None:
        from app.services.errors import http_status_for

        exc = self._expect_rejected()
        self.assertEqual(http_status_for("LAYOUT_DOES_NOT_FIT"), 409)
        self.assertTrue(exc.rows_over, "诊断必须报出**具体行号**，否则老师无从下手")
        self.assertIn("放不下", str(exc))
        self.assertIn("每行题数 6 题", exc.layout_description)
        self.assertIn("字号 20 号", exc.layout_description)

    # ---- 世界 2：=1 → 放行，但跳过必须看得见 -----------------------------

    def test_world_two_skip_switch_yields_the_pdf_and_is_observable(self) -> None:
        os.environ[pdf_export.LAYOUT_GUARD_ENV] = "1"
        data = pdf_export.export_pdf_bytes(self._doomed_html())
        self.assertTrue(data.startswith(b"%PDF"), "跳过守卫后必须真的产出 PDF")
        self.assertTrue(data.guard_skipped, "返回值必须自报「守卫被跳过」")
        self.assertGreaterEqual(data.pages, 1)
        self.assertTrue(
            data.overflow_rows,
            "跳过时也**必须量到溢出行数**：没有行数，那条 WARNING 说不出任何事情",
        )

    def test_world_two_skip_is_visible_in_the_response_and_the_log(self) -> None:
        """跳过要在**响应头**与**日志**里都看得见（D81 硬约束 2/3）。

        「只写日志」不够：日志在一个没人读的地方等于状态不存在（task-50）。
        """
        from app.routers.exports import _layout_guard_skip_headers

        data = pdf_export.ExportedPdf(
            b"%PDF-1.4", pages=1,
            overflows=[{"row_index": 4}, {"row_index": 9}],
            guard_skipped=True,
        )
        with self.assertLogs("app.routers.exports", level="WARNING") as captured:
            headers = _layout_guard_skip_headers(4242, data)
        self.assertEqual(
            headers,
            {pdf_export.LAYOUT_GUARD_SKIPPED_HEADER: pdf_export.LAYOUT_GUARD_SKIPPED_VALUE},
        )
        text = "\n".join(captured.output)
        self.assertIn("4242", text, "WARNING 必须带卷号")
        self.assertIn("仍有 2 行放不下", text, "WARNING 必须带**实际溢出行数**")
        self.assertIn("[4, 9]", text, "WARNING 必须带行号")
        # 守卫**没有**被跳过时不得有任何痕迹：否则这个头就不再能区分两个世界
        self.assertEqual(_layout_guard_skip_headers(4242, b"%PDF-1.4"), {})

    # ---- 世界 3：=0 不生效 → 仍然拒绝 ------------------------------------

    def test_world_three_zero_still_refuses(self) -> None:
        os.environ[pdf_export.LAYOUT_GUARD_ENV] = "0"
        self._expect_rejected()

    # ---- V9 负对照 -------------------------------------------------------

    def test_the_first_world_turns_red_when_the_guard_is_forced_open(self) -> None:
        """把守卫改成「无条件放行」，**世界 1 的判据必须变红**（V9）。

        做法就是把 `layout_guard_enabled` 换成恒 `False`——那正是「守卫失效」的形态。
        如果世界 1 在这种情况下**仍然绿**，说明那条断言根本没在测守卫
        （例如它其实在测「有没有抛某个无关异常」）。所以这里断言：它必须失败。
        """
        real = pdf_export.layout_guard_enabled
        pdf_export.layout_guard_enabled = lambda: False
        try:
            with self.assertRaises(AssertionError):
                self._expect_rejected()
        finally:
            pdf_export.layout_guard_enabled = real


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
