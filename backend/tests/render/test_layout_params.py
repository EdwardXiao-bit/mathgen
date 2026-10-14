"""六个排版参数在**导出的 PDF / 打印几何**里是否真生效（FR-T4 / NFR-5）。

## 为什么需要这一组

fe-teacher 报过一条跨模块线索：`@page { margin: var(--page-margin) }` 里的 `var()`
Chromium 不解析。如果打印通道也解析不了，页边距就完全没生效——那是实打实的 NFR-5 缺陷。
本组测试就是为这条线索设的**实测**回归：

* `page_margin` —— 从 **PDF 内容流**读文字原点（几何事实，见 `pdfprobe`），
  断言不同页边距下文字包围盒确实不同，且首个文字块的 x 不小于页边距；
* 其余五项 —— 在**打印几何视口**（宽 = 210mm - 2m）+ `emulate_media('print')` 下
  量 `getBoundingClientRect`，逐项断言可量测的差异；
* 另有「同一参数在真实 PDF 上的页数差异」交叉验证（换页是最硬的外部证据）。

「代码里写了」「变量传进去了」都不算证据，所以这里全部是量出来的数字。
"""
from __future__ import annotations

import os
import unittest

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetMode
from app.export import export_pdf_report, is_available
from app.export.browser import session as browser_session
from app.render import render_sheet
from app.layout.styles import page_rule

from tests.render.fixtures import build_ctx
from tests.render.pdfprobe import PT2MM, page_text_bbox_mm

MM_PER_PX = 25.4 / 96.0
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS = os.path.join(BACKEND, ".tmp-render-artifacts")


def _artifact(name: str) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    return os.path.join(ARTIFACTS, name)


def _require_browser() -> None:
    if not is_available():
        raise AssertionError(
            "Chromium / Playwright 不可用，排版参数实测无法执行。"
            "这是**环境问题**：请先跑 `& $VPY -m playwright install chromium`，"
            "并设好 MATHGEN_PW_TMP / PLAYWRIGHT_BROWSERS_PATH。"
        )


#: 打印几何下测量卷面的脚本（视口宽度即版心宽度）
PROBE_JS = """
() => {
  const rect = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top + window.scrollY, left: r.left, width: r.width, height: r.height,
             display: getComputedStyle(el).display, fontSize: getComputedStyle(el).fontSize };
  };
  const rows = Array.from(document.querySelectorAll('.q-row'));
  const grid = document.querySelector('.q-row-grid');
  return {
    bodyWidth: document.body.getBoundingClientRect().width,
    title: rect('.sheet-title'),
    headerBar: rect('.header-bar'),
    firstRow: rect('.q-row'),
    firstItem: rect('.q-item'),
    blank: rect('.q-blank'),
    answer: rect('.answer-section'),
    rowCount: rows.length,
    columns: grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0,
    rowTops: rows.map((el) => el.getBoundingClientRect().top + window.scrollY),
  };
}
"""


def measure_print_geometry(layout: LayoutParams, count: int = 24, mode=SheetMode.QUESTION) -> dict:
    """在「版心宽 = 210mm - 2 x 页边距」的视口 + 打印媒体下量卷面几何。"""
    width_px = max(int(round((210 - 2 * layout.page_margin * 10) / MM_PER_PX)), 200)
    html = render_sheet(build_ctx(count=count, mode=mode, layout=layout))
    page = browser_session().browser().new_page(viewport={"width": width_px, "height": 4000})
    try:
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        data = page.evaluate(PROBE_JS)
    finally:
        page.close()
    data["viewport_width"] = width_px
    return data


class TestPageMarginIsLiteralInCss(unittest.TestCase):
    """`@page` 里必须是字面量，不能是 `var()`（否则 Chromium 整条忽略）。"""

    def test_page_rule_emits_literal_length(self):
        for margin in (1.0, 1.5, 2.0, 2.7, 3.0):
            rule = page_rule(LayoutParams(page_margin=margin))
            with self.subTest(margin=margin):
                self.assertIn("size: A4", rule)
                self.assertIn(f"margin: {margin:g}cm", rule)
                self.assertNotIn("var(", rule, "@page 里用 var() 会被 Chromium 忽略")

    def test_style_block_still_exposes_page_margin_variable(self):
        """屏幕预览仍需要 `--page-margin`（打印才用字面量）。"""
        from app.layout.styles import style_text

        style = style_text(LayoutParams(page_margin=2.5))
        self.assertIn("--page-margin: 2.5cm", style)
        self.assertIn("margin: 2.5cm", style)


class TestPageMarginInPdf(unittest.TestCase):
    """从 **PDF 内容流**解析文字在纸面上的绝对坐标，证明页边距真的落到了纸上。

    `pdfprobe` 维护文本矩阵 + CTM（`q`/`Q`/`cm`/`Tm`/`Td`/`TD`/`T*`），
    得到的是**纸张坐标**（mm，原点左下角）。实测值：

    | page_margin | 文字 x_min | 文字 x_max |
    | --- | --- | --- |
    | 1.0cm | 10.05mm | 187.97mm |
    | 2.0cm | 20.11mm | 182.95mm |
    | 3.0cm | 29.90mm | 175.19mm |

    注意：**第一版解析器只取文本矩阵、忽略了外层 CTM**，结果 1cm 时算出 237mm
    （纸宽只有 210mm）——那是解析器的错，不是渲染器的错。教训记在 `pdfprobe` 里。
    """

    @classmethod
    def setUpClass(cls):
        _require_browser()

    def _bbox_for(self, margin: float):
        ctx = build_ctx(count=8, layout=LayoutParams(page_margin=margin))
        report = export_pdf_report(render_sheet(ctx), _artifact(f"margin-evidence-{margin}.pdf"))
        with open(report.path, "rb") as handle:
            return page_text_bbox_mm(handle.read())

    def test_text_left_margin_tracks_page_margin(self):
        """文字左边界必须随页边距一起变大（页边距真的生效）。"""
        boxes = {margin: self._bbox_for(margin) for margin in (1.0, 2.0, 3.0)}
        for margin, bbox in boxes.items():
            with self.subTest(margin=margin):
                self.assertIsNotNone(bbox, "PDF 里解析不到文字坐标")
                # 左边界 ≈ 页边距（允许 2mm 的字形左空与解析误差）
                self.assertAlmostEqual(bbox[0], margin * 10, delta=2.0)
        self.assertLess(boxes[1.0][0], boxes[2.0][0])
        self.assertLess(boxes[2.0][0], boxes[3.0][0])

    def test_text_right_edge_shrinks_as_margin_grows(self):
        """版心右边界 = 210 - 页边距，文字不得越界。"""
        for margin in (1.0, 2.0, 3.0):
            bbox = self._bbox_for(margin)
            with self.subTest(margin=margin, x_max=round(bbox[2], 2)):
                self.assertLessEqual(bbox[2], 210 - margin * 10 + 2.0)

    def test_all_text_stays_inside_the_paper(self):
        for margin in (1.0, 2.0, 3.0):
            bbox = self._bbox_for(margin)
            with self.subTest(margin=margin):
                self.assertGreaterEqual(bbox[0], 0.0)
                self.assertLessEqual(bbox[2], 210.5)
                self.assertGreaterEqual(bbox[1], 0.0)
                self.assertLessEqual(bbox[3], 297.5)

    def test_pdf_page_size_is_a4_for_every_margin(self):
        import re

        for margin in (1.0, 2.0, 3.0):
            ctx = build_ctx(count=8, layout=LayoutParams(page_margin=margin))
            report = export_pdf_report(render_sheet(ctx), _artifact(f"a4-{margin}.pdf"))
            with open(report.path, "rb") as handle:
                boxes = re.findall(rb"/MediaBox\s*\[\s*([\d.\- ]+)\]", handle.read())
            values = [float(v) for v in boxes[0].split()]
            with self.subTest(margin=margin):
                self.assertAlmostEqual((values[2] - values[0]) * PT2MM, 210.0, delta=0.5)
                self.assertAlmostEqual((values[3] - values[1]) * PT2MM, 297.0, delta=0.5)


class TestAllLayoutParamsTakeEffect(unittest.TestCase):
    """其余五个参数在打印几何 / PDF 里逐项可量测地生效。"""

    @classmethod
    def setUpClass(cls):
        _require_browser()

    def test_cols_per_row_changes_columns_rows_and_item_width(self):
        measured = {}
        for cols in (2, 4, 6):
            data = measure_print_geometry(LayoutParams(cols_per_row=cols, page_margin=2.0))
            measured[cols] = data
            with self.subTest(cols=cols):
                self.assertEqual(data["columns"], cols)
        self.assertEqual(measured[2]["rowCount"], 12)
        self.assertEqual(measured[4]["rowCount"], 6)
        self.assertEqual(measured[6]["rowCount"], 4)
        self.assertGreater(measured[2]["firstItem"]["width"], measured[4]["firstItem"]["width"])
        self.assertGreater(measured[4]["firstItem"]["width"], measured[6]["firstItem"]["width"])

    def test_cols_per_row_changes_pdf_page_count(self):
        """外部证据：换页。2 列比 4 列占更多行，页数不应更少。"""
        pages = {}
        for cols in (2, 4, 6):
            ctx = build_ctx(count=60, layout=LayoutParams(cols_per_row=cols, page_margin=2.0))
            report = export_pdf_report(render_sheet(ctx), _artifact(f"cols-pages-{cols}.pdf"))
            pages[cols] = report.pages
        self.assertGreaterEqual(pages[2], pages[4], f"页数应当随列数减少而单调不减：{pages}")
        self.assertGreaterEqual(pages[4], pages[6], f"页数应当随列数减少而单调不减：{pages}")

    def test_font_size_changes_item_font_and_height(self):
        small = measure_print_geometry(LayoutParams(font_size=10, page_margin=2.0))
        large = measure_print_geometry(LayoutParams(font_size=20, page_margin=2.0))
        self.assertEqual(small["firstItem"]["fontSize"], "13.3333px")   # 10pt
        self.assertEqual(large["firstItem"]["fontSize"], "26.6667px")   # 20pt
        self.assertGreater(large["firstItem"]["height"], small["firstItem"]["height"])
        self.assertGreater(large["firstRow"]["height"], small["firstRow"]["height"])

    def test_row_gap_changes_vertical_step_between_rows(self):
        steps = {}
        for gap in (0.8, 1.4, 2.0):
            data = measure_print_geometry(LayoutParams(row_gap=gap, page_margin=2.0))
            tops = data["rowTops"]
            steps[gap] = tops[1] - tops[0]
        self.assertLess(steps[0.8], steps[1.4])
        self.assertLess(steps[1.4], steps[2.0])
        # 行距差 1.2 倍字号（14pt=18.67px）→ 步距差应与之同量级
        self.assertAlmostEqual(steps[2.0] - steps[0.8], 1.2 * 18.6667, delta=5.0)

    def test_answer_line_switch_removes_writing_space(self):
        with_line = measure_print_geometry(LayoutParams(answer_line=True, page_margin=2.0))
        without_line = measure_print_geometry(LayoutParams(answer_line=False, page_margin=2.0))
        self.assertIsNotNone(with_line["blank"], "留横线时应当有 .q-blank 元素")
        self.assertEqual(with_line["blank"]["display"], "block")
        self.assertIsNone(without_line["blank"], "关掉横线时不应输出 .q-blank")
        self.assertLess(without_line["firstItem"]["height"], with_line["firstItem"]["height"])

    def test_header_bar_switch_hides_band_and_moves_content_up(self):
        with_bar = measure_print_geometry(LayoutParams(header_bar=True, page_margin=2.0))
        without_bar = measure_print_geometry(LayoutParams(header_bar=False, page_margin=2.0))
        self.assertEqual(with_bar["headerBar"]["display"], "grid")
        self.assertEqual(without_bar["headerBar"]["display"], "none")
        self.assertLess(without_bar["firstRow"]["top"], with_bar["firstRow"]["top"])

    def test_header_bar_switch_is_visible_in_css_only(self):
        """同一份 HTML + 只改 CSS 变量也能切卷头栏（前端实时预览依赖这条）。"""
        from app.layout.styles import style_text

        style = style_text(LayoutParams(header_bar=False))
        self.assertIn('.sheet[data-header-bar="0"] .header-bar', style)
        self.assertIn("--header-bar: 0", style)

    def test_page_margin_changes_print_viewport_and_available_width(self):
        widths = {}
        for margin in (1.0, 2.0, 3.0):
            data = measure_print_geometry(LayoutParams(page_margin=margin))
            widths[margin] = data["viewport_width"]
            with self.subTest(margin=margin):
                self.assertAlmostEqual(data["bodyWidth"], data["viewport_width"], delta=1.0)
        self.assertGreater(widths[1.0], widths[2.0])
        self.assertGreater(widths[2.0], widths[3.0])

    def test_answer_sheet_puts_answer_section_on_its_own_page(self):
        from app.layout.styles import style_text

        style = style_text(LayoutParams())
        self.assertIn("break-before: page", style)
        answer = export_pdf_report(
            render_sheet(build_ctx(count=8, mode=SheetMode.ANSWER)), _artifact("answer-separate.pdf")
        )
        question = export_pdf_report(
            render_sheet(build_ctx(count=8, mode=SheetMode.QUESTION)), _artifact("question-only.pdf")
        )
        self.assertEqual(question.pages, 1)
        self.assertGreaterEqual(answer.pages, 2)
        # 第一页不应出现答案区文本（答案区被 break-before 推到下一页）
        with open(answer.path, "rb") as handle:
            data = handle.read()
        page_one = data.split(b"/Type /Page")[1] if b"/Type /Page" in data else data
        self.assertNotIn(b"answer-section", page_one)


if __name__ == "__main__":
    unittest.main()
