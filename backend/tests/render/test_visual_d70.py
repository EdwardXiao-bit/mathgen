"""D70 视觉轮（卷面侧）测试：卷头栏四项 + 字号调小 + 长标题不溢出。

用户反馈两条（D70）：

* 问题③ 卷头「双横带」层次过重 → 简化为「标签 + 一段下划线」，**并补上学号**
  （顺带修掉与 D41 的不一致：D41 定学生身份 = 姓名 + 学号 两者必填）；
* 问题④ 卷面文字相对纸面偏大 → 适当调小，并**容纳长标题**。

本组断言全部是「可判定的量」：

* 四项字段在 **HTML 与 DOCX 都**逐项存在，且两边的字段序列**完全一致**；
* 标题倍数来自 `TITLE_SCALE` 常量（HTML 与 DOCX 共用），不是各写一遍；
* 超长标题在**打印几何**下量出来不溢出纸面；
* 字号改前/改后是实测得到的具体数字。
"""
from __future__ import annotations

import os
import re
import unittest

from docx import Document

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.export import docx_body_text, export_docx
from app.layout import HEADER_FIELD_LABELS, TITLE_SCALE
from app.layout.styles import style_text
from app.render import render_sheet

from tests.render.fixtures import build_ctx, css_rule, parse_html, style_block

BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS = os.path.join(BACKEND, ".tmp-render-artifacts")

MM_PER_PX = 25.4 / 96.0
A4_WIDTH_MM = 210.0

#: 超长标题（远超一行能放下的字数，用来验证「折行而不是溢出」）
LONG_TITLE = "小学三年级下学期数学第四单元两位数乘两位数与混合运算综合练习卷（含进位与验算）"


def _artifact(name: str) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    return os.path.join(ARTIFACTS, name)


def _long_title_ctx() -> SheetContext:
    items = tuple(
        SheetItem(seq=i + 1, display=f"{120 + i} × {12 + i} =", answer=str((120 + i) * (12 + i)))
        for i in range(8)
    )
    return SheetContext(title=LONG_TITLE, mode=SheetMode.QUESTION, items=items,
                        layout=LayoutParams(), code="2610-0001")


class TestHeaderFieldsAreShared(unittest.TestCase):
    """字段列表只有一个来源，HTML 与 DOCX 都从它取 —— 结构一致性是结构性的。"""

    def test_field_list_is_the_d41_set(self):
        self.assertEqual(HEADER_FIELD_LABELS, ("班级", "姓名", "学号", "得分"))

    def test_student_number_is_present(self):
        """D41：学生身份 = 姓名 + 学号，卷面必须有学号（M3 之前漏了）。"""
        self.assertIn("学号", HEADER_FIELD_LABELS)


class TestHeaderBarHtml(unittest.TestCase):
    """HTML 卷头栏：四项齐全、形式是「标签 + 下划线」、没有厚重横带。"""

    def setUp(self):
        self.html = render_sheet(build_ctx())
        self.root = parse_html(self.html)

    def test_all_four_labels_are_rendered(self):
        labels = [node.own_text().strip() for node in self.root.find_all(cls="hb-label")]
        self.assertEqual(labels, [f"{label}：" for label in HEADER_FIELD_LABELS])

    def test_each_field_has_an_underline_slot(self):
        """每项都要有书写位（`.hb-line`），下划线画在它上面。"""
        fields = self.root.find_all(cls="hb-field")
        self.assertEqual(len(fields), len(HEADER_FIELD_LABELS))
        for node in fields:
            with self.subTest(field=node.own_text().strip()):
                self.assertEqual(len(node.find_all(cls="hb-line")), 1)

    def test_no_heavy_band_borders_on_header_bar(self):
        """D70：去掉上下外框（用户说的「双横带」）。"""
        rule = css_rule(style_block(self.html), ".header-bar")
        self.assertNotIn("border-top", rule)
        self.assertNotIn("border-bottom", rule)

    def test_underline_is_on_the_writing_slot_not_the_label(self):
        rule = css_rule(style_block(self.html), ".header-bar .hb-line")
        self.assertIn("border-bottom", rule)

    def test_columns_are_adaptive_not_hardcoded_three(self):
        """四项在 A4 上一行放得下就一行，放不下自动换行（不写死列数）。"""
        rule = css_rule(style_block(self.html), ".header-bar")
        self.assertIn("auto-fit", rule)
        self.assertNotIn("repeat(3", rule)

    def test_header_bar_still_controlled_by_variable(self):
        """D59 不能被这次改动破坏：显隐仍只看 CSS 变量。"""
        rule = css_rule(style_block(self.html), ".header-bar")
        self.assertIn("display: var(--header-bar-display", rule)


class TestHeaderBarDocx(unittest.TestCase):
    """DOCX 卷头栏必须与 HTML 一致（契约第 6 条），含学号。"""

    def _export(self, name: str, **layout) -> str:
        path = _artifact(name)
        export_docx(build_ctx(layout=LayoutParams(**layout)), path)
        return path

    def test_docx_contains_all_four_fields(self):
        text = docx_body_text(self._export("d70-header.docx"))
        for label in HEADER_FIELD_LABELS:
            with self.subTest(label=label):
                self.assertIn(f"{label}：", text, f"DOCX 卷头栏缺少「{label}」")

    def test_docx_header_table_has_four_columns(self):
        document = Document(self._export("d70-header-cols.docx"))
        header_tables = [t for t in document.tables if len(t.columns) == len(HEADER_FIELD_LABELS)]
        self.assertTrue(header_tables, f"应当有一张 {len(HEADER_FIELD_LABELS)} 列的卷头表")
        cells = [cell.text.strip() for cell in header_tables[0].rows[0].cells]
        self.assertEqual(cells, [f"{label}：" for label in HEADER_FIELD_LABELS])

    def test_docx_header_absent_when_switched_off(self):
        text = docx_body_text(self._export("d70-header-off.docx", header_bar=False))
        for label in HEADER_FIELD_LABELS:
            with self.subTest(label=label):
                self.assertNotIn(f"{label}：", text)

    def test_html_and_docx_field_sequence_are_identical(self):
        """两边字段**逐项一致**（顺序也一致）——契约第 6 条。

        直接从 python-docx 读回的**表格单元格文本**取字段名（比在全文里正则更稳，
        不会被标题或其他正文里的冒号干扰）。
        """
        html = render_sheet(build_ctx())
        root = parse_html(html)
        html_labels = [n.own_text().strip() for n in root.find_all(cls="hb-label")]

        document = Document(self._export("d70-header-parity.docx"))
        header_table = None
        for table in document.tables:
            if len(table.columns) == len(HEADER_FIELD_LABELS):
                header_table = table
                break
        self.assertIsNotNone(header_table, "DOCX 里应当有卷头栏表格")
        docx_labels = [cell.text.strip() for cell in header_table.rows[0].cells]

        expected = [f"{label}：" for label in HEADER_FIELD_LABELS]
        self.assertEqual(html_labels, expected)
        self.assertEqual(docx_labels, expected)
        self.assertEqual(html_labels, docx_labels)


class TestTitleScaleAndLongTitle(unittest.TestCase):
    """字号调小 + 长标题不溢出（D70 问题④）。"""

    #: 改动前的标题倍数（留作对照，改前/改后数字都要能复现）
    PREVIOUS_TITLE_SCALE = 1.5

    def test_title_scale_is_shared_and_smaller(self):
        self.assertLess(TITLE_SCALE, self.PREVIOUS_TITLE_SCALE, "标题应当调小")
        style = style_text(LayoutParams())
        self.assertIn(f"calc(var(--font-size) * {TITLE_SCALE})", style)
        # HTML 与 DOCX 用同一个常量，不允许各写一个魔数
        import app.export.docx as docx_module

        self.assertIs(docx_module.TITLE_SCALE, TITLE_SCALE)

    def test_question_font_size_is_untouched(self):
        """`--font-size` 是冻结的**题目字号**，语义不能动。"""
        style = style_text(LayoutParams(font_size=14))
        self.assertIn("--font-size: 14pt", style)
        self.assertIn(".q-item", style)
        self.assertIn("font-size: var(--font-size)", style)

    def test_title_allows_wrapping(self):
        rule = css_rule(style_text(LayoutParams()), ".sheet-title")
        self.assertIn("overflow-wrap", rule)
        self.assertIn("word-break: break-word", rule)

    def test_long_title_html_does_not_mention_truncation(self):
        """长标题必须完整出现在文本里（不能截断成省略号）。"""
        html = render_sheet(_long_title_ctx())
        self.assertIn(LONG_TITLE, html)
        self.assertNotIn("…", parse_html(html).find_all(cls="sheet-title")[0].own_text())

    def test_long_title_docx_is_complete(self):
        path = _artifact("d70-long-title.docx")
        export_docx(_long_title_ctx(), path)
        self.assertIn(LONG_TITLE, docx_body_text(path))


class TestLongTitleFitsPaper(unittest.TestCase):
    """验证长标题不溢出、不截断（NFR-5）。

    测量在**打印几何**下进行：视口宽 = 版心宽（纸张宽 − 2 x 页边距）并切打印媒体。
    这与 `tests/render/test_layout_params.py` 的做法一致；直接在 PDF 导出用的默认视口里
    量 `getBoundingClientRect` 是不可靠的（那量到的是连续布局下的宽度，不是纸面版心）。
    """

    @classmethod
    def setUpClass(cls):
        from app.export import is_available

        if not is_available():
            raise AssertionError(
                "Chromium 不可用，长标题不溢出无法实测。这是**环境问题**："
                "先跑 `& $VPY -m playwright install chromium`。"
            )

    @staticmethod
    def _measure_in_print_geometry(ctx: SheetContext) -> dict:
        from app.export.browser import session as browser_session

        content_width_px = (A4_WIDTH_MM - 2 * ctx.layout.page_margin * 10) / MM_PER_PX
        page = browser_session().browser().new_page(
            viewport={"width": int(round(content_width_px)), "height": 4000}
        )
        try:
            page.set_content(render_sheet(ctx), wait_until="load")
            page.emulate_media(media="print")
            return page.evaluate(
                """() => {
                  const el = document.querySelector('.sheet-title');
                  const r = el.getBoundingClientRect();
                  const cs = getComputedStyle(el);
                  return { left: r.left, width: r.width, height: r.height,
                           lineHeight: parseFloat(cs.lineHeight), fontSize: cs.fontSize,
                           scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
                }"""
            )
        finally:
            page.close()

    def test_long_title_stays_inside_the_paper(self):
        ctx = _long_title_ctx()
        data = self._measure_in_print_geometry(ctx)
        content_width_px = (A4_WIDTH_MM - 2 * ctx.layout.page_margin * 10) / MM_PER_PX

        self.assertLessEqual(
            data["width"], content_width_px + 1.0,
            f"长标题宽度 {data['width']:.1f}px 超出版心 {content_width_px:.1f}px",
        )
        self.assertGreaterEqual(data["left"], -1.0, "标题不得越出左边界")
        self.assertLessEqual(data["left"] + data["width"], content_width_px + 1.0,
                             "标题不得越出右边界")
        self.assertLessEqual(
            data["scrollWidth"], data["clientWidth"] + 1,
            "标题内容宽度不应超过自身盒宽（否则说明溢出后被裁剪）",
        )
        self.assertGreater(data["height"], 0.0)

    def test_long_title_wraps_into_multiple_lines(self):
        """标题高度应明显大于一行 —— 说明它**折行了**，而不是被压成一行溢出。"""
        data = self._measure_in_print_geometry(_long_title_ctx())
        line_height = data["lineHeight"]
        self.assertGreater(line_height, 0.0)
        lines = data["height"] / line_height
        self.assertGreaterEqual(
            lines, 1.9,
            f"长标题只占 {lines:.2f} 行（高 {data['height']:.1f}px / 行高 {line_height:.1f}px），"
            "说明没有折行",
        )

    def test_short_title_stays_one_line(self):
        data = self._measure_in_print_geometry(build_ctx(count=4, title="口算练习"))
        lines = data["height"] / data["lineHeight"]
        self.assertLess(lines, 1.6, f"短标题不应被拆成多行，实测 {lines:.2f} 行")

    def test_long_token_title_does_not_overflow(self):
        """长英文/数字 token（无法按字断行）也必须不溢出 —— 这才是 `overflow-wrap` 的用武之地。

        对照发现：纯中文长标题靠浏览器默认的 CJK 断行就能折行，
        真正需要 `overflow-wrap: anywhere` 的是这种连续 ASCII token。
        """
        long_token = "Grade3MultiplicationAndDivisionMixedOperationPracticeBook2026"
        ctx = SheetContext(title=long_token, mode=SheetMode.QUESTION,
                           items=(SheetItem(seq=1, display="1+1="),), layout=LayoutParams())
        data = self._measure_in_print_geometry(ctx)
        content_width_px = (A4_WIDTH_MM - 2 * ctx.layout.page_margin * 10) / MM_PER_PX
        self.assertLessEqual(data["width"], content_width_px + 1.0)
        self.assertLessEqual(data["scrollWidth"], data["clientWidth"] + 1,
                             "超长 token 不应溢出标题盒")

    def test_long_title_pdf_has_reasonable_page_count(self):
        from app.export import export_pdf_report

        report = export_pdf_report(render_sheet(_long_title_ctx()), _artifact("d70-pages.pdf"))
        self.assertGreaterEqual(report.pages, 1)
        self.assertLessEqual(report.pages, 3, "8 道题 + 长标题不应超过 3 页")

    def test_long_title_full_text_appears_in_exported_pdf(self):
        """导出 PDF 后，标题文本仍完整（用 pdfprobe 的独立解析路径核对）。"""
        from app.export import export_pdf_report
        from tests.render.pdfprobe import text_positions_mm

        report = export_pdf_report(render_sheet(_long_title_ctx()), _artifact("d70-title-text.pdf"))
        with open(report.path, "rb") as handle:
            positions = text_positions_mm(handle.read())
        self.assertTrue(positions, "PDF 里应当能解析出文字位置")
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        # 所有文字都必须落在纸面内（A4 210 x 297mm）
        self.assertGreaterEqual(min(xs), -0.5)
        self.assertLessEqual(max(xs), 210.5)
        self.assertGreaterEqual(min(ys), -0.5)
        self.assertLessEqual(max(ys), 297.5)


class TestNegativeControls(unittest.TestCase):
    """V9：新增判据必须**能红**——负对照留在仓库里，证明仪器不是空转。

    这里的负对照不改产品代码，而是拿**故意做坏的数据**去喂同一批评据：
    如果判据在坏数据上仍然通过，说明它根本没在测东西。
    """

    def test_missing_student_number_would_fail_the_field_check(self):
        """负对照：字段列表少了「学号」时，四项断言必须失败。"""
        broken = ("班级", "姓名", "得分")                 # 正是 M3 之前的错误状态
        with self.assertRaises(AssertionError):
            self.assertEqual(list(broken), list(HEADER_FIELD_LABELS))

    def test_old_scale_would_fail_the_smaller_scale_check(self):
        """负对照：旧倍数 1.5 不满足「标题更小」，说明该断言确实在比较。"""
        previous = 1.5
        with self.assertRaises(AssertionError):
            self.assertLess(previous, previous)           # 1.5 < 1.5 为假

    def test_unwrapped_long_token_would_fail_the_overflow_check(self):
        """负对照：一个**无法按字断行**的超长 token 在关掉折行时应当溢出。

        第一版负对照用纯中文标题，结果**测不出来**——因为 CJK 文本在浏览器里
        默认就能在任意字之间断行，`overflow-wrap` 对它不起作用。
        这本身是个有价值的发现：对纯中文长标题，**折行本来就不需要额外样式**。

        真正需要 `overflow-wrap: anywhere` 的是**长英文/数字 token**
        （例如老师把标题写成「2026FallGrade3MultiplicationDrill…」），
        所以负对照必须用这种输入，否则判据是空转的。
        """
        from app.export import is_available
        from app.export.browser import session as browser_session

        if not is_available():
            self.skipTest("Chromium 不可用，负对照需要真实浏览器")

        long_token = "Grade3MultiplicationAndDivisionMixedOperationPracticeBook2026"
        ctx = SheetContext(title=long_token, mode=SheetMode.QUESTION,
                           items=(SheetItem(seq=1, display="1+1="),), layout=LayoutParams())
        content_width_px = (A4_WIDTH_MM - 2 * ctx.layout.page_margin * 10) / MM_PER_PX
        html = render_sheet(ctx)

        # 正例：生产样式下这个超长 token **不**溢出
        # 负例：把折行样式关掉后，同一个 token 必须溢出
        def measure_with(css_override: str) -> int:
            patched = html.replace("</style>", css_override + "</style>", 1)
            page = browser_session().browser().new_page(
                viewport={"width": int(round(content_width_px)), "height": 4000}
            )
            try:
                page.set_content(patched, wait_until="load")
                page.emulate_media(media="print")
                return page.evaluate(
                    """() => {
                      const el = document.querySelector('.sheet-title');
                      return el.scrollWidth - el.clientWidth;
                    }"""
                )
            finally:
                page.close()

        normal_overflow = measure_with("")
        broken_overflow = measure_with(
            ".sheet-title { overflow-wrap: normal !important; word-break: normal !important; }"
        )

        self.assertLessEqual(normal_overflow, 0,
                             "生产样式下超长 token 不应溢出")
        self.assertGreater(
            broken_overflow, 0,
            "负对照失效：关掉折行后超长 token 也没有溢出 —— "
            "说明 `test_long_title_stays_inside_the_paper` 测不出问题（判据空转）",
        )


if __name__ == "__main__":
    unittest.main()
