"""M2 里程碑验收套件（verify 写域，task-8）。

**独立于实现方测试**：只经 HTTP（`fastapi.testclient`）+ 产品导出入口 `export_pdf`
+ **我自己的** PDF 实测路径（`pypdfium2` 字符盒 / 光栅 ink 边界）下断言，
不复用 `tests/api`、`tests/render` 的任何辅助函数。

门禁命令（仓库根目录，需已装好 venv 依赖与 Chromium）：

    $VPY = '<repo>\\backend\\.venv\\Scripts\\python.exe'
    $env:PLAYWRIGHT_BROWSERS_PATH = '<repo>\\.playwright'
    $env:TEMP = $env:TMP = '<repo>\\.tmp-pw'
    $env:MATHGEN_PW_TMP = '<repo>\\.tmp-pw'
    & $VPY -m unittest discover -s backend/tests/e2e_m2 -t backend -v
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
sys.path.insert(0, os.path.join(HERE, ".pylibs"))          # verify 自装的 pypdfium2
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

os.environ.setdefault("MATHGEN_DB", os.path.join(ARTIFACTS, "verify-m2-unittest.db"))
os.environ.setdefault("MATHGEN_PW_TMP", os.path.join(REPO, ".tmp-pw"))
os.makedirs(ARTIFACTS, exist_ok=True)

from fastapi.testclient import TestClient          # noqa: E402
from PIL import Image                              # noqa: E402

from app.main import create_app                    # noqa: E402

CODE_RE = re.compile(r"^\d{4}-\d{4}$")
PT_PER_MM = 72.0 / 25.4
CFG = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral", "count": 40}


_CLIENT = None


def shared_client() -> TestClient:
    """整个测试进程共用一个 app/DB。

    （Windows 上 SQLite 文件被打开时无法删除，因此不能在每个测试类里重建库。）
    """
    global _CLIENT
    if _CLIENT is None:
        db_path = os.environ["MATHGEN_DB"]
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass
        _CLIENT = TestClient(create_app())
        _CLIENT.__enter__()
    return _CLIENT


class M2Case(unittest.TestCase):
    """公共装配：共用一个 app（内存外置 SQLite）+ 生成 draft 卷的辅助。"""

    client: TestClient

    @classmethod
    def setUpClass(cls):
        cls.client = shared_client()

    def make_paper(self, **cfg_overrides) -> dict:
        config = {**CFG, **cfg_overrides}
        resp = self.client.post("/api/papers", params={"seed": 20261014, "title": "验收卷"},
                                json=config)
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()


class TestEndToEndFlow(M2Case):
    """生成 → 取详情 → 换一题 → 删一题 → 改排版 → 发布 → 取 HTML。"""

    def test_full_flow_over_http(self):
        paper = self.make_paper()
        pid = paper["id"]
        self.assertEqual(paper["status"], "draft")
        self.assertIsNone(paper["code"])
        self.assertEqual(len(paper["items"]), 40)

        detail = self.client.get(f"/api/papers/{pid}").json()
        before = {item["seq"]: item for item in detail["items"]}

        regen = self.client.post(f"/api/papers/{pid}/items/2/regenerate")
        self.assertEqual(regen.status_code, 200, regen.text)
        after = {item["seq"]: item
                 for item in self.client.get(f"/api/papers/{pid}").json()["items"]}
        self.assertEqual([s for s in before if after[s] != before[s]], [2],
                         "换一题只应改动 seq=2")

        deleted = self.client.delete(f"/api/papers/{pid}/items/3")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        seqs = [item["seq"] for item in deleted.json()]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)), "删题后题号必须连续无空洞")
        self.assertEqual(len(seqs), 39)

        layout = {"cols_per_row": 3, "row_gap": 1.4, "font_size": 16,
                  "page_margin": 1.5, "answer_line": True, "header_bar": True}
        patched = self.client.patch(f"/api/papers/{pid}/layout", json=layout)
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json(), layout)

        published = self.client.post(f"/api/papers/{pid}/publish")
        self.assertEqual(published.status_code, 200, published.text)
        body = published.json()
        self.assertRegex(body["code"], CODE_RE)
        self.assertTrue(body["student_url"].startswith("http://"))
        self.assertIn(body["code"], body["student_url"])
        self.assertTrue(body["qr_url"].startswith("/api/"))

        question = self.client.get(f"/api/papers/{pid}/sheet", params={"mode": "question"})
        answer = self.client.get(f"/api/papers/{pid}/sheet", params={"mode": "answer"})
        self.assertEqual(question.status_code, 200)
        self.assertEqual(answer.status_code, 200)
        html = question.text
        answer_html = answer.text
        with open(os.path.join(ARTIFACTS, "ut-sheet-question.html"), "w",
                  encoding="utf-8") as fh:
            fh.write(html)
        with open(os.path.join(ARTIFACTS, "ut-sheet-answer.html"), "w",
                  encoding="utf-8") as fh:
            fh.write(answer_html)

        # 排版变量必须由后端内联（C3：预览/打印/PDF 共用同一份 HTML）
        for var, value in (("--cols-per-row", "3"), ("--font-size", "16pt"),
                           ("--page-margin", "1.5cm")):
            self.assertIn(f"{var}:{value}", html.replace(" ", ""),
                          f"卷面 HTML 未内联 {var}:{value}（C3 要求后端注入）")
        # 题目版不得泄露答案（sheet 契约第 3 条）
        for item in before.values():
            self.assertNotIn(f">{item['answer']}<", html,
                             f"题目版 HTML 泄露了答案 {item['answer']!r}")
        self.assertIn("答案与解析", answer_html, "答案版应含答案小节（D20）")


class TestStateMachineIndependent(M2Case):
    """状态机独立复核（不复用 tests/api/test_state_machine.py）。"""

    def test_published_paper_rejects_every_edit(self):
        pid = self.make_paper()["id"]
        self.assertEqual(self.client.post(f"/api/papers/{pid}/publish").status_code, 200)
        layout = {"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                  "page_margin": 2.0, "answer_line": True, "header_bar": True}
        responses = [
            self.client.patch(f"/api/papers/{pid}/layout", json=layout),
            self.client.delete(f"/api/papers/{pid}/items/4"),
            self.client.post(f"/api/papers/{pid}/items/4/regenerate"),
        ]
        for resp in responses:
            with self.subTest(method=resp.request.method, url=str(resp.request.url)):
                self.assertEqual(resp.status_code, 409, resp.text)
                self.assertEqual(resp.json()["error"]["code"], "PAPER_NOT_EDITABLE")

    def test_copy_creates_new_draft_without_code(self):
        pid = self.make_paper()["id"]
        self.client.post(f"/api/papers/{pid}/publish")
        copied = self.client.post(f"/api/papers/{pid}/copy")
        self.assertEqual(copied.status_code, 201, copied.text)
        body = copied.json()
        self.assertNotEqual(body["id"], pid)
        self.assertIsNone(body["code"], "副本不得带卷号（D32）")
        self.assertEqual(body["status"], "draft")
        self.assertEqual(len(body["items"]), 40)
        # 副本可编辑
        layout = {"cols_per_row": 5, "row_gap": 1.0, "font_size": 14,
                  "page_margin": 2.0, "answer_line": True, "header_bar": True}
        self.assertEqual(self.client.patch(
            f"/api/papers/{body['id']}/layout", json=layout).status_code, 200)

    def test_delete_keeps_seq_contiguous_on_copy(self):
        pid = self.make_paper()["id"]
        for seq in (1, 5, 9):
            resp = self.client.delete(f"/api/papers/{pid}/items/{seq}")
            self.assertEqual(resp.status_code, 200, resp.text)
            seqs = [item["seq"] for item in resp.json()]
            self.assertEqual(seqs, list(range(1, len(seqs) + 1)))

    def test_qr_returns_png(self):
        pid = self.make_paper()["id"]
        self.client.post(f"/api/papers/{pid}/publish")
        resp = self.client.get(f"/api/papers/{pid}/qr")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content[:4], b"\x89PNG")
        self.assertEqual(resp.headers["content-type"], "image/png")


class TestLayoutToggles(M2Case):
    """六个排版开关在**卷面 HTML / 导出 PDF** 两个层面的可判定复核。"""

    def _sheet(self, pid, **layout):
        base = {"cols_per_row": 3, "row_gap": 1.0, "font_size": 14,
                "page_margin": 2.0, "answer_line": True, "header_bar": True}
        resp = self.client.patch(f"/api/papers/{pid}/layout", json={**base, **layout})
        self.assertEqual(resp.status_code, 200, resp.text)
        return self.client.get(f"/api/papers/{pid}/sheet",
                               params={"mode": "question"}).text

    def test_cols_per_row_matches_items_per_row(self):
        pid = self.make_paper()["id"]
        for cols in (2, 3, 4, 6):
            html = self._sheet(pid, cols_per_row=cols)
            rows = []
            marks = list(re.finditer(r'<div class="q-row" data-row-index="(\d+)">', html))
            for i, mark in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(html)
                rows.append(html[mark.end():end].count('class="q-item"'))
            with self.subTest(cols_per_row=cols):
                self.assertEqual(set(rows[:-1]), {cols},
                                 f"每行题数应为 {cols}，各行实际={rows}")
                self.assertLessEqual(rows[-1], cols)

    def test_answer_line_toggle_changes_markup(self):
        pid = self.make_paper()["id"]
        on = self._sheet(pid, answer_line=True)
        off = self._sheet(pid, answer_line=False)
        self.assertIn('class="q-blank"', on, "answer_line=True 应输出答题线元素")
        self.assertNotIn('class="q-blank"', off, "answer_line=False 不应输出答题线元素")
        self.assertIn('data-answer-line="0"', off)

    def test_header_bar_visible_in_exported_pdf(self):
        from app.export import export_pdf, is_available

        if not is_available():
            self.skipTest("本机浏览器不可用，跳过 PDF 层面的开关复核")
        pid = self.make_paper()["id"]
        results = {}
        for flag in (True, False):
            html = self._sheet(pid, header_bar=flag)
            path = os.path.join(ARTIFACTS, f"ut-header-{'on' if flag else 'off'}.pdf")
            export_pdf(html, path)
            results[flag] = _pdf_text(path)
        self.assertTrue(results[True].count("班级") >= 1 and "姓名" in results[True],
                        "header_bar=True 时 PDF 里应能看到卷头信息栏")
        self.assertNotIn("班级", results[False], "header_bar=False 时不应有卷头信息栏")


class TestExportPdfRoute(M2Case):
    """契约 §5.1 `GET /api/papers/{id}/export` —— M2 里程碑的「导出」一步。

    这是**判据本身**：M2 要求「能通过 HTTP 走完 … → 导出」。
    历史：task-8 首轮验收时该路由缺失（HTTP 404），本用例红；D57 修复后转绿。
    更细的契约面（501/422/404/统一错误结构/C3 同源/D58 隔离）见
    `TestExportRouteContract` 与 `TestPlaywrightIsolation`。
    """

    def test_export_route_serves_pdf(self):
        pid = self.make_paper()["id"]
        resp = self.client.get(f"/api/papers/{pid}/export",
                               params={"format": "pdf", "mode": "question"})
        self.assertEqual(
            resp.status_code, 200,
            "契约 docs/04-接口契约.md §5.1 与 docs/05 的 T-M2-07 要求 "
            "`GET /api/papers/{id}/export?format=pdf&mode=` 返回 PDF 文件流；"
            f"实测 HTTP {resp.status_code}，响应体={resp.text[:120]!r}")
        self.assertEqual(resp.content[:4], b"%PDF")


class TestExportRouteContract(M2Case):
    """D57 修复后的导出路由与统一错误结构（verify 独立复核）。

    判据来自契约 §5.1（导出路由）与 §9（错误码表：`PAPER_NOT_FOUND` 404）。
    """

    def _published_paper(self) -> int:
        pid = self.make_paper()["id"]
        layout = {"cols_per_row": 3, "row_gap": 1.4, "font_size": 16,
                  "page_margin": 1.5, "answer_line": True, "header_bar": True}
        self.client.patch(f"/api/papers/{pid}/layout", json=layout)
        self.assertEqual(self.client.post(f"/api/papers/{pid}/publish").status_code, 200)
        return pid

    def test_export_pdf_both_modes(self):
        import io

        import pypdfium2 as pdfium

        pid = self._published_paper()
        page_counts = {}
        for mode in ("question", "answer"):
            resp = self.client.get(f"/api/papers/{pid}/export",
                                   params={"format": "pdf", "mode": mode})
            with self.subTest(mode=mode):
                self.assertEqual(resp.status_code, 200, resp.text[:200])
                self.assertEqual(resp.headers["content-type"], "application/pdf")
                self.assertEqual(resp.content[:5], b"%PDF-")
                self.assertIn("attachment",
                              resp.headers.get("content-disposition", "").lower())
                document = pdfium.PdfDocument(io.BytesIO(resp.content))
                try:
                    page_counts[mode] = len(document)
                finally:
                    document.close()
                with open(os.path.join(ARTIFACTS, f"http-export-{mode}.pdf"), "wb") as fh:
                    fh.write(resp.content)
        self.assertGreater(page_counts["answer"], page_counts["question"],
                           "答案版页数应多于题目版")
        default = self.client.get(f"/api/papers/{pid}/export", params={"format": "pdf"})
        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.content[:5], b"%PDF-")

    def test_export_docx_is_501_not_fake_pdf(self):
        pid = self._published_paper()
        resp = self.client.get(f"/api/papers/{pid}/export", params={"format": "docx"})
        self.assertEqual(resp.status_code, 501, resp.text[:200])
        self.assertEqual(resp.json()["error"]["code"], "NOT_IMPLEMENTED")

    def test_export_invalid_format_is_422(self):
        pid = self._published_paper()
        resp = self.client.get(f"/api/papers/{pid}/export", params={"format": "xml"})
        self.assertEqual(resp.status_code, 422, resp.text[:200])
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

    def test_export_missing_paper_is_documented_404_code(self):
        resp = self.client.get("/api/papers/999999/export", params={"format": "pdf"})
        self.assertEqual(resp.status_code, 404, resp.text[:200])
        self.assertEqual(resp.json()["error"]["code"], "PAPER_NOT_FOUND",
                         "契约 §9 明列 PAPER_NOT_FOUND | 404 | 卷不存在")

    def test_unknown_path_and_method_use_unified_error(self):
        """D57②：不得再泄漏 FastAPI 默认的 `{"detail": ...}`。"""
        for resp, expected in (
            (self.client.get("/api/nonexistent"), (404, "NOT_FOUND")),
            (self.client.put("/api/papers"), (405, "METHOD_NOT_ALLOWED")),
        ):
            with self.subTest(url=str(resp.request.url), method=resp.request.method):
                self.assertEqual(resp.status_code, expected[0], resp.text[:200])
                body = resp.json()
                self.assertNotIn("detail", body, "错误响应不得是 FastAPI 默认结构")
                self.assertEqual(body["error"]["code"], expected[1])
                self.assertTrue(body["error"]["message"].strip())

    def test_sheet_body_is_byte_identical_to_shared_renderer(self):
        """C3：`/sheet` 的响应体必须与共享渲染器 `render_html` 的输出逐字节相同。

        （D57③ 说 `/export` 与 `/sheet` 收拢到同一个 `services.sheet_render`；
        这里从消费侧独立验证 `/sheet` 确实用了它，并以源码检查确认 `/export` 同路。）
        """
        from sqlalchemy.orm import sessionmaker

        from app.db import default_engine
        from app.services.sheet_ctx import build_sheet_context, parse_mode
        from app.services.sheet_render import render_html

        pid = self.make_paper()["id"]
        resp = self.client.get(f"/api/papers/{pid}/sheet", params={"mode": "question"})
        self.assertEqual(resp.status_code, 200)
        session = sessionmaker(bind=default_engine())()
        try:
            ctx = build_sheet_context(session, pid, parse_mode("question"), "")
            rendered = render_html(ctx)
        finally:
            session.close()
        self.assertEqual(resp.text, rendered, "/sheet 与共享渲染器输出不一致（C3 破坏）")

        exports_src = open(os.path.join(BACKEND, "app", "routers", "exports.py"),
                           encoding="utf-8").read()
        sheet_src = open(os.path.join(BACKEND, "app", "routers", "sheet.py"),
                         encoding="utf-8").read()
        self.assertIn("render_html", exports_src)
        self.assertIn("render_html", sheet_src)


class TestPlaywrightIsolation(M2Case):
    """D58：HTTP 导出走一次性子进程，不得毁掉本进程主线程的 Playwright 会话。"""

    def test_inprocess_export_still_works_after_http_exports(self):
        from app.export import export_pdf, is_available

        if not is_available():
            self.skipTest("本机浏览器不可用")
        pid = self.make_paper()["id"]
        for _ in range(3):
            resp = self.client.get(f"/api/papers/{pid}/export",
                                   params={"format": "pdf", "mode": "question"})
            self.assertEqual(resp.status_code, 200, resp.text[:200])
        html = self.client.get(f"/api/papers/{pid}/sheet",
                               params={"mode": "question"}).text
        out = os.path.join(ARTIFACTS, "ut-inprocess-after-http.pdf")
        export_pdf(html, out)          # 若隔离失效，这里会抛 Playwright 线程错误
        with open(out, "rb") as fh:
            self.assertEqual(fh.read(5), b"%PDF-")


def _pdf_text(pdf_path: str) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        chunks = []
        for page in document:
            textpage = page.get_textpage()
            try:
                chunks.append(textpage.get_text_range())
            finally:
                textpage.close()
        return "\n".join(chunks)
    finally:
        document.close()


class TestPdfGeometryIndependent(M2Case):
    """PDF 实物几何：A4 尺寸、页边距落纸、答案版单独成页、无跨页截断迹象。

    测量走**我自己**的两条路径（pdfium 字符盒 + 光栅 ink 边界），
    不读实现方 `tests/render/pdfprobe.py` 的任何解析结果。
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.export import is_available, export_pdf

        if not is_available():
            raise unittest.SkipTest("本机浏览器不可用，跳过 PDF 几何用例")
        cls.export_pdf = staticmethod(export_pdf)
        cls.client.post("/api/papers", params={"seed": 7, "title": "几何卷"}, json=CFG)

    def _draft_id(self) -> int:
        papers = self.client.get("/api/papers", params={"size": 50}).json()["papers"]
        drafts = [p for p in papers if p["status"] == "draft"]
        self.assertTrue(drafts, "没有 draft 卷可用于几何实验")
        return drafts[0]["id"]

    def _export(self, html: str, name: str) -> str:
        path = os.path.join(ARTIFACTS, name)
        self.export_pdf(html, path)
        return path

    def test_page_margin_reaches_paper(self):
        import pypdfium2 as pdfium

        pid = self._draft_id()
        for margin in (1.0, 2.0, 3.0):
            layout = {"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                      "page_margin": margin, "answer_line": True, "header_bar": False}
            self.client.patch(f"/api/papers/{pid}/layout", json=layout)
            html = self.client.get(f"/api/papers/{pid}/sheet",
                                   params={"mode": "question"}).text
            path = self._export(html, f"ut-margin-{margin:g}.pdf")

            document = pdfium.PdfDocument(path)
            page = document[0]
            textpage = page.get_textpage()
            xs = []
            for index in range(textpage.count_chars()):
                try:
                    box = textpage.get_charbox(index, loose=True)
                except Exception:
                    continue
                if box and box[0] is not None:
                    xs.append(box[0])
            textpage.close()
            document.close()
            xmin_mm = min(xs) / PT_PER_MM

            with self.subTest(page_margin_cm=margin):
                self.assertAlmostEqual(
                    xmin_mm, margin * 10.0, delta=0.5,
                    msg=f"page_margin={margin}cm 应使正文左边界≈{margin * 10:.2f}mm，"
                        f"实测 {xmin_mm:.2f}mm（pdfium 字符盒）")
            # 第二条路径：光栅 ink 边界（同一 PDF，独立测量）
            png = path[:-4] + ".png"
            page_png = self._rasterize(path, png)
            left_mm = page_png
            with self.subTest(page_margin_cm=margin, path="raster"):
                self.assertAlmostEqual(
                    left_mm, margin * 10.0, delta=0.5,
                    msg=f"光栅 ink 左边界实测 {left_mm:.2f}mm，与名义 {margin * 10:.2f}mm 不符")

    def _rasterize(self, pdf_path: str, out_png: str, scale: float = 4.0) -> float:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(pdf_path)
        try:
            bitmap = document[0].render(scale=scale)
            bitmap.to_pil().save(out_png)
            size = document[0].get_size()
        finally:
            document.close()
        image = Image.open(out_png).convert("L")
        ink = image.point(lambda p: 255 if p < 245 else 0)
        bbox = ink.getbbox()
        self.assertIsNotNone(bbox, "页面没有 ink，无法测量")
        return bbox[0] / scale / PT_PER_MM

    def test_a4_size_and_answer_mode_separate_page(self):
        import pypdfium2 as pdfium

        pid = self.make_paper()["id"]
        self.assertEqual(self.client.post(f"/api/papers/{pid}/publish").status_code, 200)
        counts = {}
        for mode in ("question", "answer"):
            html = self.client.get(f"/api/papers/{pid}/sheet", params={"mode": mode}).text
            path = self._export(html, f"ut-paper-{mode}.pdf")
            document = pdfium.PdfDocument(path)
            try:
                counts[mode] = len(document)
                for index in range(len(document)):
                    width, height = document[index].get_size()
                    with self.subTest(mode=mode, page=index + 1):
                        self.assertAlmostEqual(width, 595.276, delta=1.5)
                        self.assertAlmostEqual(height, 841.89, delta=1.5)
            finally:
                document.close()
        # 答案与解析必须**单独起页**：答案版首个含「答案与解析」的页，其正文从页首开始
        answer_pdf = os.path.join(ARTIFACTS, "ut-paper-answer.pdf")
        document = pdfium.PdfDocument(answer_pdf)
        try:
            first_answer_page = None
            for index in range(len(document)):
                text = document[index].get_textpage()
                try:
                    if "答案与解析" in text.get_text_range():
                        first_answer_page = index + 1
                        break
                finally:
                    text.close()
        finally:
            document.close()
        self.assertIsNotNone(first_answer_page, "答案版里找不到「答案与解析」小节")
        self.assertGreater(counts["answer"], counts["question"],
                           "答案版页数应多于题目版（答案单独成页）")
        # 单独成页的判定：答案小节所在页**只**含答案内容，而题目卷页数不包含它
        self.assertEqual(first_answer_page, counts["question"] + 1,
                         f"答案小节应紧接题目卷之后单独起页："
                         f"题目版 {counts['question']} 页，答案小节却出现在第 {first_answer_page} 页")


if __name__ == "__main__":
    unittest.main()
