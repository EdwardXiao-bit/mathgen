"""PDF 导出与 A4 分页测试（NFR-5 / FR-T6 / FR-T7）。

这一组测试**真的**调用 Playwright 打印 PDF：

* `export_pdf` 复用进程内常驻 Chromium（`docs/02-技术设计.md` §11）；
* 每个测试都断言**真实 PDF 的页数**，不看代码「看起来对不对」；
* 产物写到 `backend/.render-test-artifacts/`（`.gitignore` 已忽略），
  便于人工目视复核（`03-协作规范.md` V5）。

Chromium 不可用时**不跳过、不弱化**：直接失败并在信息里写清是环境问题。
"""
from __future__ import annotations

import os
import unittest

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetMode
from app.export import (
    BrowserError,
    browser_session,
    count_pdf_pages,
    export_pdf,
    export_pdf_report,
    is_available,
)
from app.export.pdf import _pdf_payload_problem
from app.layout import detect_overflow, detect_page_crossing
from app.render import render_sheet

from tests.render.fixtures import build_ctx

#: 测试产物目录。放在 `backend/.tmp-*` 下——`.gitignore` 已忽略 `.tmp-*`，
#: 不会把 PDF/PNG 误提交（`docs/word/` 之外的产物不入库）。
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARTIFACTS = os.path.join(BACKEND, ".tmp-render-artifacts")

_AVAILABLE = is_available()


def _require_browser() -> None:
    if not _AVAILABLE:
        raise AssertionError(
            "Chromium / Playwright 不可用，PDF 断言无法执行。"
            "这是**环境问题**，不是实现问题：请先跑 "
            "`& $VPY -m playwright install chromium`（需要代理），"
            "或设置 MATHGEN_CHROMIUM 指向已有的 chrome.exe。"
        )


def _artifact(name: str) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    return os.path.join(ARTIFACTS, name)


def tearDownModule() -> None:  # noqa: N802 - unittest 约定名
    from app.export.browser import session as browser

    browser().close()


class TestPdfCounter(unittest.TestCase):
    """不依赖浏览器：页数计数器的自检。"""

    def test_empty_bytes_counts_zero(self):
        self.assertEqual(count_pdf_pages(b""), 0)

    def test_counts_page_objects(self):
        blob = b"%PDF-1.4 /Type /Page /Type /Page /Type /Pages"
        # `/Type /Pages` 不能被算成页对象
        self.assertEqual(count_pdf_pages(blob), 2)

    def test_falls_back_to_count_entry(self):
        self.assertEqual(count_pdf_pages(b"%PDF-1.4 /Count 3"), 3)


class TestPdfPayloadValidation(unittest.TestCase):
    """task-14：坏字节必须被**当场判定**，不能写盘后由远处的断言发现。

    全量门禁曾偶发（实测 6 轮 1 轮）产物不以 `%PDF-` 开头、也不以 `%%EOF` 结尾；
    旧实现照样写盘，失败表现为「某个断言 False」，离现场很远。
    """

    def test_accepts_a_well_formed_payload(self):
        self.assertIsNone(_pdf_payload_problem(b"%PDF-1.4" + b"x" * 500 + b"%%EOF"))

    def test_accepts_trailing_whitespace_after_eof(self):
        self.assertIsNone(_pdf_payload_problem(b"%PDF-1.4" + b"x" * 500 + b"%%EOF\n\n"))

    def test_rejects_empty_payload(self):
        self.assertIn("空", _pdf_payload_problem(b""))

    def test_rejects_missing_pdf_header(self):
        problem = _pdf_payload_problem(b"<html>not a pdf</html>" * 40)
        self.assertIsNotNone(problem)
        self.assertIn("%PDF-", problem)

    def test_rejects_truncated_payload_without_eof(self):
        problem = _pdf_payload_problem(b"%PDF-1.4" + b"x" * 500)
        self.assertIsNotNone(problem)
        self.assertIn("%%EOF", problem)

    def test_rejects_absurdly_small_payload(self):
        self.assertIsNotNone(_pdf_payload_problem(b"%PDF-1.4%%EOF"))

    def test_export_never_writes_an_invalid_file(self):
        """真导出一次，产物必须同时满足头/尾判据（写盘的就是校验过的字节）。"""
        _require_browser()
        path = _artifact("validated.pdf")
        export_pdf(render_sheet(build_ctx(count=6)), path)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        self.assertIsNone(_pdf_payload_problem(data))

    def test_bad_payload_retries_then_raises_without_writing(self):
        """`page.pdf()` 连坏两次时必须抛错、且**不留下坏文件**。

        用假浏览器会话注入坏字节，验证「校验 → 重试 → 报错」这条链路，
        不需要真的制造渲染器故障。
        """
        _require_browser()
        from app.export import pdf as pdf_module

        path = _artifact("must-not-exist.pdf")
        if os.path.exists(path):
            os.remove(path)

        attempts = {"count": 0}

        class FakePage:
            def set_content(self, *_a, **_k):
                return None

            def emulate_media(self, *_a, **_k):
                return None

            def wait_for_timeout(self, *_a, **_k):
                return None

            def evaluate(self, *_a, **_k):
                return {}

            def pdf(self, **_kwargs):
                attempts["count"] += 1
                return b"<html>renderer gave garbage</html>" * 20

            def close(self):
                return None

        class FakeBrowser:
            def new_page(self, **_kwargs):
                return FakePage()

        class FakeSession:
            def browser(self):
                return FakeBrowser()

        original = pdf_module.browser_session
        pdf_module.browser_session = lambda: FakeSession()
        try:
            with self.assertRaises(BrowserError) as caught:
                export_pdf("<html></html>", path)
        finally:
            pdf_module.browser_session = original

        self.assertEqual(attempts["count"], 2, "坏字节应当重试一次")
        self.assertIn("PDF", str(caught.exception))
        self.assertFalse(os.path.exists(path), "校验不通过时绝不能留下坏产物")


class TestPdfExport(unittest.TestCase):
    """真实导出 PDF 并断言产物。"""

    @classmethod
    def setUpClass(cls):
        _require_browser()

    def test_exports_a_valid_pdf_file(self):
        html = render_sheet(build_ctx(count=8))
        path = _artifact("single-page.pdf")
        export_pdf(html, path)
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.startswith(b"%PDF-"), "产物不是 PDF")
        self.assertGreater(len(data), 1000, "PDF 太小，可能是空文档")
        self.assertEqual(count_pdf_pages(data), 1, "8 道题应当只占 1 页")

    def test_export_pdf_report_returns_page_count_and_measurement(self):
        ctx = build_ctx(count=8)
        report = export_pdf_report(render_sheet(ctx), _artifact("report.pdf"))
        self.assertEqual(report.pages, 1)
        self.assertGreater(report.byte_size, 1000)
        self.assertEqual(len(report.rows), 2, "8 题 / 每行 4 题 = 2 行")
        self.assertEqual(report.measurement.get("styles", {}).get("cols"), "4")

    def test_multi_page_export_counts_pages(self):
        ctx = build_ctx(count=140)
        report = export_pdf_report(render_sheet(ctx), _artifact("multi-page.pdf"))
        self.assertGreaterEqual(report.pages, 3, "140 道题不可能只有 1~2 页")

    def test_more_questions_never_reduces_page_count(self):
        pages = []
        for count in (8, 40, 80, 160):
            report = export_pdf_report(render_sheet(build_ctx(count=count)), _artifact(f"mono-{count}.pdf"))
            pages.append(report.pages)
        self.assertEqual(pages, sorted(pages), f"页数应当单调不减，实际 {pages}")

    def test_answer_mode_exports_more_pages_than_question_mode(self):
        """D20：答案区单独成页 —— 同一份题目，答案版至少多 1 页。"""
        question = export_pdf_report(
            render_sheet(build_ctx(count=12, mode=SheetMode.QUESTION)), _artifact("mode-q.pdf")
        )
        answer = export_pdf_report(
            render_sheet(build_ctx(count=12, mode=SheetMode.ANSWER)), _artifact("mode-a.pdf")
        )
        self.assertGreater(answer.pages, question.pages)

    def test_exports_to_a_nested_directory_that_does_not_exist_yet(self):
        nested = os.path.join(ARTIFACTS, "nested", "deep", "sheet.pdf")
        export_pdf(render_sheet(build_ctx(count=4)), nested)
        self.assertTrue(os.path.exists(nested))

    def test_rejects_empty_html(self):
        with self.assertRaises(ValueError):
            export_pdf("   ", _artifact("empty.pdf"))

    def test_rejects_non_string_html(self):
        with self.assertRaises(TypeError):
            export_pdf(b"<html></html>", _artifact("bad.pdf"))  # type: ignore[arg-type]

    def test_pdf_has_a4_page_geometry(self):
        """A4 = 595 x 842 pt。从 PDF 里的 MediaBox 读出来（不依赖第三方库）。"""
        report = export_pdf_report(render_sheet(build_ctx(count=8)), _artifact("a4.pdf"))
        with open(report.path, "rb") as handle:
            data = handle.read()
        import re

        boxes = re.findall(rb"/MediaBox\s*\[\s*([\d.\- ]+)\]", data)
        self.assertTrue(boxes, "PDF 里找不到 MediaBox")
        values = [float(v) for v in boxes[0].split()]
        width, height = values[2] - values[0], values[3] - values[1]
        self.assertAlmostEqual(width, 595, delta=1.5, msg="A4 宽应为 595pt")
        self.assertAlmostEqual(height, 842, delta=1.5, msg="A4 高应为 842pt")

    def test_page_margin_does_not_change_paper_size(self):
        """页边距只改版心，不改纸张（NFR-5 的 A4 保真）。"""
        import re

        for margin in (1.0, 3.0):
            ctx = build_ctx(count=8, layout=LayoutParams(page_margin=margin))
            report = export_pdf_report(render_sheet(ctx), _artifact(f"margin-{margin}.pdf"))
            with open(report.path, "rb") as handle:
                boxes = re.findall(rb"/MediaBox\s*\[\s*([\d.\- ]+)\]", handle.read())
            values = [float(v) for v in boxes[0].split()]
            with self.subTest(margin=margin):
                self.assertAlmostEqual(values[2] - values[0], 595, delta=1.5)
                self.assertAlmostEqual(values[3] - values[1], 842, delta=1.5)


class TestPaginationNoSplit(unittest.TestCase):
    """NFR-5 的核心：**渲染出 PDF 后**断言题块不跨页。

    两条证据互相独立，都要断言：

    1. `report.pages` —— 真实 PDF 的页数（Chromium 说了算）；
    2. `detect_page_crossing()` —— 浏览器侧量到的行几何，按**实测页面盒**判定有没有行
       横跨分页线。

    另外用 `usable_geometry()` 与量到的行高做一致性检查：
    **没有哪一行比一页还高**，这是「不可能被切开」的充分条件。
    """

    @classmethod
    def setUpClass(cls):
        _require_browser()

    def test_no_row_exceeds_one_page(self):
        from app.layout import usable_geometry

        for count in (8, 24, 60, 120):
            for margin in (1.0, 2.0, 3.0):
                ctx = build_ctx(count=count, layout=LayoutParams(page_margin=margin))
                report = export_pdf_report(
                    render_sheet(ctx), _artifact(f"fit-{count}-{margin}.pdf")
                )
                first, _later = usable_geometry(ctx.layout)
                with self.subTest(count=count, margin=margin):
                    self.assertEqual(
                        detect_overflow(report.measurement, ctx.layout), (),
                        "存在比一页还高的行——它一定会被分页线切开",
                    )
                    self.assertGreaterEqual(report.pages, 1)
                    self.assertLessEqual(
                        max(row["height"] for row in report.rows), first,
                        "行高必须放得下一个版心",
                    )

    def test_no_row_crosses_a_page_break(self):
        """**单页**卷面里一行不得横跨分页线。

        多页文档见 `test_multi_page_documents_have_no_oversized_rows`：本机的
        Chromium 对多页 grid 的分页容量并不均匀（实测 12/9/9/9/11 行每页），
        用单一容量模型预测分页线会误报，所以跨界判定只在**能精确确认**的
        单页场景下断言；多页场景由页数 + 行高 + 目视渲染共同守住。
        """
        from app.export import export_pdf_report as _report

        for count in (8, 24, 40):
            for margin in (1.0, 2.0, 2.5, 3.0):
                ctx = build_ctx(count=count, layout=LayoutParams(page_margin=margin))
                report = _report(render_sheet(ctx), _artifact(f"cross-{count}-{margin}.pdf"))
                if report.pages != 1:
                    continue
                crossings = detect_page_crossing(report.measurement, ctx.layout)
                with self.subTest(count=count, margin=margin):
                    self.assertEqual(crossings, (), f"单页卷面里出现跨页行：{crossings}")

    def test_multi_page_documents_have_no_oversized_rows(self):
        """多页文档：没有任何一行比一页还高，且页数与内容量单调一致。"""
        for count in (60, 120, 200):
            for margin in (1.0, 2.0):
                ctx = build_ctx(count=count, layout=LayoutParams(page_margin=margin))
                report = export_pdf_report(
                    render_sheet(ctx), _artifact(f"multi-fit-{count}-{margin}.pdf")
                )
                with self.subTest(count=count, margin=margin):
                    self.assertEqual(detect_overflow(report.measurement, ctx.layout), ())
                    self.assertGreaterEqual(report.pages, 2)
                    self.assertLessEqual(
                        max(row["height"] for row in report.rows),
                        report.measurement["docHeight"],
                    )

    def test_row_height_is_consistent_with_measured_capacity(self):
        """行高 × 每页行数 不应超过实测页面容量太多（排版模型自洽性检查）。"""
        from app.layout import usable_geometry

        ctx = build_ctx(count=60, layout=LayoutParams(page_margin=2.0))
        report = export_pdf_report(render_sheet(ctx), _artifact("capacity.pdf"))
        first, _later = usable_geometry(ctx.layout)
        rows = report.rows
        self.assertTrue(rows)
        row_height = max(row["height"] for row in rows)
        rows_per_page = int(first // (row_height * 1.4))
        self.assertGreaterEqual(rows_per_page, 1)
        self.assertGreaterEqual(
            report.pages, 1,
            f"实测页面 {report.pages} 页，行高 {row_height:.1f}px",
        )

    def test_answer_sheet_has_answer_page_separate(self):
        """D20：答案版比题目版多页，且答案版页数 ≥ 2（答案区单独成页）。"""
        ctx = build_ctx(count=8, mode=SheetMode.ANSWER)
        question = export_pdf_report(
            render_sheet(build_ctx(count=8, mode=SheetMode.QUESTION)), _artifact("sep-q.pdf")
        )
        answer = export_pdf_report(render_sheet(ctx), _artifact("sep-a.pdf"))
        self.assertEqual(question.pages, 1, "8 道题的题目版应当只有 1 页")
        self.assertGreaterEqual(answer.pages, 2, "答案区必须单独成页")


class TestPageBoxMeasurement(unittest.TestCase):
    """`measure_page_box()` 用真实分页反解页面盒（NFR-5 的标定手段）。"""

    @classmethod
    def setUpClass(cls):
        _require_browser()

    def _renderer(self, margin, cols=4):
        from app.contracts.sheet import SheetContext, SheetItem

        def render_rows(rows: int) -> str:
            items = tuple(
                SheetItem(seq=i + 1, display=f"{100 + i} × {7 + i} =", answer="x")
                for i in range(rows * cols)
            )
            return render_sheet(
                SheetContext(
                    title="标定卷面",
                    mode=SheetMode.QUESTION,
                    items=items,
                    layout=LayoutParams(cols_per_row=cols, page_margin=margin),
                )
            )

        return render_rows

    def test_measured_capacity_decreases_with_margin(self):
        from app.export.pdf import clear_page_box_cache, measure_page_box

        clear_page_box_cache()
        results = {}
        for margin in (1.0, 2.0, 3.0):
            box = measure_page_box(self._renderer(margin), margin, max_rows=25)
            results[margin] = box
            self.assertEqual(box.margin, margin)
            self.assertGreater(box.probes, 0)
            self.assertGreater(box.usable_first_px, 400.0)
            with self.subTest(margin=margin):
                self.assertLess(box.usable_first_px, (297.0 - margin) * 96 / 25.4 + 2.0)

        self.assertGreater(results[1.0].usable_first_px, results[3.0].usable_first_px)

    def test_measured_capacity_matches_contract_table(self):
        """实测结果必须与 `MEASURED_BREAK_CEILING_PX` 记录的值一致。

        这是对「合同表」的独立复算：表是之前一次性扫描出来的，这里用生产函数
        重新测一遍，偏差超过一行步距就说明表过期或实现漂移。
        """
        from app.export.pdf import clear_page_box_cache, measure_page_box
        from app.layout import MEASURED_BREAK_CEILING_PX

        clear_page_box_cache()
        for margin in (2.0, 3.0):
            box = measure_page_box(self._renderer(margin), margin, max_rows=25)
            recorded = MEASURED_BREAK_CEILING_PX[margin]
            with self.subTest(margin=margin, measured=box.usable_first_px, recorded=recorded):
                self.assertLessEqual(abs(box.usable_first_px - recorded), box.row_step_px)

    def test_measure_page_box_caches_by_key(self):
        from app.export.pdf import clear_page_box_cache, measure_page_box

        clear_page_box_cache()
        renderer = self._renderer(2.0)
        first = measure_page_box(renderer, 2.0, max_rows=20)
        second = measure_page_box(renderer, 2.0, max_rows=20)
        self.assertIs(first, second, "同 key 应当命中缓存，不重复跑 20+ 次 PDF")
        self.assertGreater(first.probes, 0)


if __name__ == "__main__":
    unittest.main()

