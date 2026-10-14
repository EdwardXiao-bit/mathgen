"""M3 里程碑验收套件（verify 写域，task-13）。

**非写者验收**：只经 HTTP（`fastapi.testclient` / 真实 uvicorn）+ 产品导出入口 +
**我自己**的读回路径（`python-docx` 解析 / `pypdfium2` 计页 / SQLite 直读快照）下断言。

三条核心判据：
  1. **历史卷可复现**：发布 → 按**卷号**取回 → 与发布时**逐字一致**；
     并区分「快照复现」（读库存 `paper_item.payload`）与「重跑引擎」（`generate` 同参同结果）。
  2. **Word 可二次编辑**：`python-docx` 读回真文本/结构 + LibreOffice 转 PDF 能打开。
  3. **一条命令启动**：只用 uvicorn（无 Vite）验证 `/teacher`、深链接、`/api`、`/docs`，
     以及 **静态目录缺失时仍能启动**。

门禁命令（仓库根目录，**裸跑即可**）：

    $VPY = '<repo>\\backend\\.venv\\Scripts\\python.exe'
    & $VPY -m unittest discover -s backend/tests/e2e_m3 -t backend -v
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
sys.path.insert(0, HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
# pypdfium2 由 verify 自装在 `tests/e2e_m2/.pylibs`（M2 时装的）；这里**只读引用**，
# 避免在仓库里重复放一份 8MB 的轮子。
_PYLIBS = os.path.join(os.path.dirname(HERE), "e2e_m2", ".pylibs")
if os.path.isdir(_PYLIBS):
    sys.path.insert(0, _PYLIBS)

os.environ.setdefault("MATHGEN_DB", os.path.join(ARTIFACTS, "verify-m3.db"))
os.environ.setdefault("MATHGEN_PW_TMP", os.path.join(REPO, ".tmp-pw"))
os.makedirs(ARTIFACTS, exist_ok=True)

from fastapi.testclient import TestClient          # noqa: E402

from app.main import create_app                    # noqa: E402

VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):                        # 非 Windows / 非常规布局时退回当前解释器
    VPY = sys.executable

#: 打包进来的 LibreOffice Kit（AGENTS.md 记录的调用方式；不搜索系统 soffice）
LO_NODE = os.environ.get(
    "MATHGEN_LIBREOFFICE_NODE",
    r"C:\Users\Ed\AppData\Local\Programs\DeepSeek Harness\resources\runtime"
    r"\primary-runtime\dependencies\node\bin\node.exe",
)
LO_CLI = os.environ.get(
    "MATHGEN_LIBREOFFICE_CLI",
    r"C:\Users\Ed\AppData\Local\Programs\DeepSeek Harness\resources\app.asar.unpacked"
    r"\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js",
)

CFG_ORAL = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral", "count": 20}
CFG_STEP = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "stepwise", "count": 6}
CODE_RE = re.compile(r"^\d{4}-\d{4}$")
ANSWER_LAYOUT_DEFAULT = {"separate_page": True, "include_steps": True,
                         "include_difficulty": True, "include_tags": True}

_CLIENT = None


def shared_client() -> TestClient:
    """整进程共用一个 app/DB（Windows 上 SQLite 打开时无法删除文件）。"""
    global _CLIENT
    if _CLIENT is None:
        db_path = os.environ["MATHGEN_DB"]
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except PermissionError:
                pass
        _CLIENT = TestClient(create_app(static_dir=False))
        _CLIENT.__enter__()
    return _CLIENT


# --------------------------------------------------------------------------
# 读回工具（全部由测试侧实现，不复用实现方测试的辅助函数）
# --------------------------------------------------------------------------

def docx_text(blob: bytes) -> str:
    """把 .docx 的**全部可读文本**取出来（正文段落 + 表格单元格，含嵌套表格）。"""
    import docx

    document = docx.Document(io.BytesIO(blob))
    chunks = [p.text for p in document.paragraphs]

    def walk_tables(tables):
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    chunks.extend(p.text for p in cell.paragraphs)
                    walk_tables(cell.tables)

    walk_tables(document.tables)
    return "\n".join(chunks)


def docx_structure(blob: bytes) -> dict:
    import docx

    document = docx.Document(io.BytesIO(blob))
    return {
        "paragraphs": len(document.paragraphs),
        "tables": len(document.tables),
        "non_empty_paragraphs": sum(1 for p in document.paragraphs if p.text.strip()),
        "page_breaks": len(re.findall(
            r'<w:br[^>]*w:type="page"', document.element.xml)),
    }


def docx_to_pdf(docx_path: str) -> str:
    """用打包的 LibreOffice Kit 转 PDF（给子进程可写的 TMP/TEMP —— 本机实测坑）。"""
    pdf_path = docx_path[:-5] + ".pdf"
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    temp_dir = os.path.join(ARTIFACTS, ".lo-tmp")
    os.makedirs(temp_dir, exist_ok=True)
    env = dict(os.environ)
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = temp_dir
    result = subprocess.run(
        [LO_NODE, LO_CLI, "convert", "--input", docx_path, "--output", pdf_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env)
    if result.returncode != 0 or not os.path.exists(pdf_path):
        raise AssertionError(f"LibreOffice 转换失败：rc={result.returncode} "
                             f"stderr={result.stderr[:400]}")
    return pdf_path


def pdf_pages(path_or_blob) -> int:
    import pypdfium2 as pdfium

    source = path_or_blob
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    document = pdfium.PdfDocument(source)
    try:
        return len(document)
    finally:
        document.close()


def db_payloads(paper_id: int) -> list[dict]:
    """**直读库存快照**（`paper_item.payload`）——判「快照复现」而非「请求时重算」。"""
    from sqlalchemy import text

    from app.db import default_engine

    engine = default_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT seq, payload FROM paper_item WHERE paper_id = :pid ORDER BY seq"),
            {"pid": paper_id}).fetchall()
    return [{"seq": row[0], "payload": json.loads(row[1])} for row in rows]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _step_marker(item: dict) -> str:
    """挑一个**只在步骤里出现**的字符串当标记。

    不能直接用 `steps[0]`（它通常就是原式，题目正文里本来就有），
    否则「关掉 include_steps」的断言会因为题目正文仍然含该算式而误判。
    """
    display = item.get("display", "")
    for step in item.get("steps", []):
        marker = step.strip()
        if marker and marker not in display and marker != item.get("answer"):
            return marker
    return ""


class M3Case(unittest.TestCase):
    client: TestClient

    @classmethod
    def setUpClass(cls):
        cls.client = shared_client()

    def make_paper(self, cfg=None, title="M3 验收卷", seed=20261015):
        resp = self.client.post("/api/papers", params={"seed": seed, "title": title},
                                json=cfg or CFG_ORAL)
        self.assertEqual(resp.status_code, 201, resp.text[:300])
        return resp.json()

    def publish(self, pid: int) -> dict:
        resp = self.client.post(f"/api/papers/{pid}/publish")
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        return resp.json()

    def export(self, pid: int, fmt: str, mode: str = "question"):
        return self.client.get(f"/api/papers/{pid}/export",
                               params={"format": fmt, "mode": mode})


# --------------------------------------------------------------------------
# 判据 1：历史卷可复现
# --------------------------------------------------------------------------

class TestHistoricalPaperReproducible(M3Case):
    def test_by_code_returns_identical_content(self):
        paper = self.make_paper()
        pid = paper["id"]
        # 以**发布前**的详情为基准（发布后 status/published_at/code 本就会变）
        draft_detail = self.client.get(f"/api/papers/{pid}").json()
        published = self.publish(pid)
        self.assertRegex(published["code"], CODE_RE)

        fetched = self.client.get(f"/api/papers/by-code/{published['code']}")
        self.assertEqual(fetched.status_code, 200, fetched.text[:300])
        body = fetched.json()

        # 题目内容逐字一致（含顺序）
        self.assertEqual(body["id"], pid)
        self.assertEqual(body["code"], published["code"])
        self.assertEqual(body["status"], "published")
        self.assertEqual(len(body["items"]), len(draft_detail["items"]))
        for before, after in zip(draft_detail["items"], body["items"]):
            with self.subTest(seq=before["seq"]):
                self.assertEqual(after, before, "按卷号取回的内容与发布时不一致")
        for field in ("seed", "config", "layout", "answer_layout", "title"):
            self.assertEqual(body[field], draft_detail[field], f"{field} 不一致")

    def test_snapshot_is_stored_and_read_verbatim(self):
        """**快照复现**：库里的 `paper_item.payload` 与 API 返回逐字段相同。"""
        paper = self.make_paper()
        pid = paper["id"]
        published = self.publish(pid)
        rows = db_payloads(pid)
        self.assertEqual(len(rows), len(paper["items"]), "纸面题目数与库存快照行数不符")
        api_items = self.client.get(f"/api/papers/by-code/{published['code']}").json()["items"]
        for row, item in zip(rows, api_items):
            with self.subTest(seq=row["seq"]):
                self.assertEqual(row["seq"], item["seq"])
                payload = row["payload"]
                for field in ("expr", "display", "answer", "difficulty", "tags", "steps"):
                    self.assertIn(field, payload, f"快照缺字段 {field}")
                    self.assertEqual(payload[field], item[field],
                                     f"库存快照的 {field} 与 API 返回不一致")

    def test_engine_rerun_matches_snapshot(self):
        """**重跑引擎**：同 (config, seed) 重新出题，与快照内容一致（两条路径互证）。"""
        from app.engine import generate
        from app.engine.types import GenerateConfig, Op, QuestionType, RangeId

        paper = self.make_paper(seed=4242)
        cfg = paper["config"]
        config = GenerateConfig(
            ops={Op(op) for op in cfg["ops"]}, ranges=[RangeId(r) for r in cfg["ranges"]],
            question_type=QuestionType(cfg["question_type"]), max_value=cfg["max_value"],
            require_carry=cfg["require_carry"], require_borrow=cfg["require_borrow"],
            exact_division=cfg["exact_division"], allow_remainder=cfg["allow_remainder"],
            parentheses=cfg["parentheses"], count=cfg["count"])
        rerun = generate(config, paper["seed"])
        self.assertEqual(len(rerun), len(paper["items"]))
        for question, item in zip(rerun, paper["items"]):
            with self.subTest(seq=item["seq"]):
                self.assertEqual(question.expr, item["expr"])
                self.assertEqual(question.display, item["display"])
                self.assertEqual(question.answer, item["answer"])
                self.assertEqual(list(question.tags), item["tags"])

    def test_edit_on_copy_does_not_change_original(self):
        paper = self.make_paper()
        pid = paper["id"]
        code = self.publish(pid)["code"]
        copied = self.client.post(f"/api/papers/{pid}/copy")
        self.assertEqual(copied.status_code, 201, copied.text[:200])
        copy_id = copied.json()["id"]

        # 在副本上删题 + 换题
        self.assertEqual(self.client.delete(
            f"/api/papers/{copy_id}/items/2").status_code, 200)
        self.assertEqual(self.client.post(
            f"/api/papers/{copy_id}/items/1/regenerate").status_code, 200)

        again = self.client.get(f"/api/papers/by-code/{code}").json()
        self.assertEqual(again["items"], paper["items"], "副本上的改动影响到了原卷")

    def test_published_paper_is_frozen(self):
        """发布后原卷不可改（409），保证「按卷号取回」的稳定性。"""
        pid = self.make_paper()["id"]
        self.publish(pid)
        self.assertEqual(self.client.delete(f"/api/papers/{pid}/items/1").status_code, 409)
        self.assertEqual(self.client.post(
            f"/api/papers/{pid}/items/1/regenerate").status_code, 409)

    def test_unknown_code_returns_code_not_found(self):
        """契约 §9：卷号无效 → 404 `CODE_NOT_FOUND`（与「按 id 找不到」的 PAPER_NOT_FOUND 区分）。"""
        for bad in ("9999-9999", "not-a-code", "0000-0000"):
            with self.subTest(code=bad):
                resp = self.client.get(f"/api/papers/by-code/{bad}")
                self.assertEqual(resp.status_code, 404, resp.text[:200])
                self.assertEqual(resp.json()["error"]["code"], "CODE_NOT_FOUND")

    def test_id_lookup_uses_paper_not_found(self):
        """两种 404 不可混用：按 **id** 找不到 → `PAPER_NOT_FOUND`。"""
        resp = self.client.get("/api/papers/999999")
        self.assertEqual(resp.status_code, 404, resp.text[:200])
        self.assertEqual(resp.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_removed_page_size_params_are_rejected_not_ignored(self):
        """M3 口径：`page` / `size` 别名已删除，改为**拒绝式护栏**（422），不是静默忽略。

        理由（Lead 采纳的实现方论证）：FastAPI 会静默忽略未知查询参数，
        只删参数会让老调用方拿到第一页却以为翻页成功 —— 报错好过静默错。
        """
        for stale in ({"page": 2}, {"size": 10}, {"page": 1, "size": 5}):
            with self.subTest(params=stale):
                resp = self.client.get("/api/papers", params=stale)
                self.assertEqual(resp.status_code, 422,
                                 f"{stale} 应被拒绝（422），而不是被忽略")
                message = resp.json()["error"]["message"]
                self.assertIn("limit", message)
                self.assertIn("offset", message)
        # 新参数正常
        ok = self.client.get("/api/papers", params={"limit": 5, "offset": 0})
        self.assertEqual(ok.status_code, 200, ok.text[:200])
        self.assertEqual(ok.json()["limit"], 5)


# --------------------------------------------------------------------------
# 判据 2：Word 可二次编辑
# --------------------------------------------------------------------------

class TestWordEditable(M3Case):
    def test_docx_is_real_text_and_structure(self):
        paper = self.make_paper()
        pid = paper["id"]
        self.publish(pid)
        resp = self.export(pid, "docx", "answer")
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        docx_path = os.path.join(ARTIFACTS, "m3-answer.docx")
        with open(docx_path, "wb") as fh:
            fh.write(resp.content)

        structure = docx_structure(resp.content)
        text = docx_text(resp.content)
        self.assertGreaterEqual(structure["paragraphs"], 5,
                                f"段落太少，结构可疑：{structure}")
        self.assertGreaterEqual(structure["non_empty_paragraphs"], 3)
        self.assertGreater(len(text), 200, "docx 文本量过少，疑似空壳")

        # 真文本：能搜到**具体算式**与答案（不是图片、不是空段落）
        first = paper["items"][0]
        numbers = re.findall(r"\d+", first["expr"])
        for number in numbers[:2]:
            with self.subTest(number=number):
                self.assertIn(number, text, f"docx 里搜不到算式数字 {number}")
        self.assertIn(first["answer"], text, "docx 里搜不到首题答案")
        self.assertIn("难度", text, "答案版应含难度标记")

        # LibreOffice 能打开：转 PDF 成功、页数合理
        pdf_path = docx_to_pdf(docx_path)
        pages = pdf_pages(pdf_path)
        self.assertGreaterEqual(pages, 1, "LibreOffice 转出的 PDF 至少 1 页")
        self.assertLess(pages, 20, f"6 题答案版不应超过 20 页，实际 {pages}")
        self.assertIn(pages, range(1, 21))

    def test_docx_mime_and_zip_shape(self):
        import zipfile

        pid = self.make_paper()["id"]
        resp = self.export(pid, "docx", "question")
        self.assertEqual(resp.content[:4], b"PK\x03\x04")
        self.assertNotEqual(resp.content[:4], b"%PDF")
        self.assertEqual(
            resp.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            names = set(archive.namelist())
        self.assertIn("word/document.xml", names)
        self.assertIn("[Content_Types].xml", names)


# --------------------------------------------------------------------------
# 判据 2b：四个 answer_layout 开关逐项独立复核
# --------------------------------------------------------------------------

class TestAnswerLayoutSwitches(M3Case):
    """逐项关掉一个开关，断言**该项内容确实不出现**、其余仍在（docx + PDF 双通道）。

    注意用**草稿**卷：发布后整卷只读（状态机），`PATCH /answer-layout` 会 409
    —— 那是产品正确的行为，不该被我的用例当成失败。
    """

    def setUp(self):
        self.paper = self.make_paper(CFG_STEP, title="答案版形式卷", seed=777)
        self.pid = self.paper["id"]

    def _set_answer_layout(self, **overrides):
        payload = {**ANSWER_LAYOUT_DEFAULT, **overrides}
        resp = self.client.patch(f"/api/papers/{self.pid}/answer-layout", json=payload)
        self.assertEqual(resp.status_code, 200,
                         f"草稿卷改答案版形式应 200，实得 {resp.status_code}：{resp.text[:200]}")
        return resp.json()

    def _answer_docx(self, tag: str):
        resp = self.export(self.pid, "docx", "answer")
        self.assertEqual(resp.status_code, 200, resp.text[:200])
        path = os.path.join(ARTIFACTS, f"m3-answer-{tag}.docx")
        with open(path, "wb") as fh:
            fh.write(resp.content)
        return path, docx_text(resp.content), docx_structure(resp.content)

    def test_switches_change_docx_content(self):
        baseline_path, baseline_text, baseline_struct = self._answer_docx("baseline")
        first = self.paper["items"][0]
        tag_text = first["tags"][0]
        step_marker = _step_marker(first)
        self.assertTrue(step_marker, f"首题没有可用的步骤标记：{first['steps']}")

        # baseline：四项内容都在
        self.assertIn("难度", baseline_text, "include_difficulty=True 应出现难度")
        self.assertIn(tag_text, baseline_text, f"include_tags=True 应出现考点 {tag_text}")
        self.assertIn(step_marker, baseline_text,
                      f"include_steps=True 应出现脱式步骤 {step_marker!r}")

        cases = [
            ("include_difficulty", "难度", "难度标记"),
            ("include_tags", tag_text, "考点标签"),
            ("include_steps", step_marker, "脱式步骤"),
        ]
        for field, needle, label in cases:
            with self.subTest(field=field):
                self._set_answer_layout(**{field: False})
                _path, text, _structure = self._answer_docx(f"{field}-off")
                self.assertNotIn(needle, text, f"关掉 {field} 后仍出现{label}：{needle!r}")
                # 其余两项仍在（独立性）
                for other_field, other_needle, _other_label in cases:
                    if other_field != field:
                        self.assertIn(other_needle, text,
                                      f"关掉 {field} 后不该连带影响 {other_field}")

        # 复原
        self._set_answer_layout()

    def test_separate_page_switch_changes_pdf_pagination(self):
        self._set_answer_layout(separate_page=True)
        _path, _text, struct_on = self._answer_docx("sep-on")
        pdf_on = docx_to_pdf(_path)
        pages_on = pdf_pages(pdf_on)

        self._set_answer_layout(separate_page=False)
        _path2, _text2, struct_off = self._answer_docx("sep-off")
        pdf_off = docx_to_pdf(_path2)
        pages_off = pdf_pages(pdf_off)

        self._set_answer_layout()

        self.assertGreater(struct_on["page_breaks"], struct_off["page_breaks"],
                           "separate_page=True 应在答案前插入分页符")
        self.assertGreaterEqual(pages_on, pages_off,
                                f"答案单独成页不应比不单独成页更少页：{pages_on} vs {pages_off}")

    def test_answer_layout_is_persisted_and_returned(self):
        layout = self._set_answer_layout(include_tags=False, include_difficulty=False)
        detail = self.client.get(f"/api/papers/{self.pid}").json()
        self.assertEqual(detail["answer_layout"], layout)
        # 恢复默认，避免影响后续用例
        self._set_answer_layout()


# --------------------------------------------------------------------------
# 判据 3：一条命令启动（无 Vite）
# --------------------------------------------------------------------------

class TestSingleCommandStartup(unittest.TestCase):
    """只用 `uvicorn app.main:app --app-dir backend`（不借助 Vite）验证四条路径。"""

    def _start(self, port: int, extra_env: dict | None = None):
        db = os.path.join(ARTIFACTS, f"startup-{port}.db")
        env = dict(os.environ)
        env.update({
            "MATHGEN_DB": db,
            "PYTHONIOENCODING": "utf-8",
            "MATHGEN_PW_TMP": os.path.join(REPO, ".tmp-pw"),
        })
        env.pop("MATHGEN_STATIC_DIR", None)
        if extra_env:
            env.update(extra_env)
        process = subprocess.Popen(
            [VPY, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace")
        return process

    def _wait_ready(self, port: int, process, timeout=40.0):
        import httpx

        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                self.fail(f"uvicorn 提前退出（rc={process.returncode}）：{output[-800:]}")
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/api/papers", timeout=2.0)
                if response.status_code == 200:
                    return
                last = response.status_code
            except Exception as exc:                       # noqa: BLE001
                last = type(exc).__name__
            time.sleep(0.4)
        self.fail(f"服务未在 {timeout}s 内就绪（最后一次：{last}）")

    def _stop(self, process):
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

    def test_one_command_serves_ui_api_and_docs(self):
        import httpx

        port = free_port()
        process = self._start(port)
        try:
            self._wait_ready(port, process)
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, timeout=15.0, follow_redirects=False) as client:
                for path, check in self._checks():
                    check(client, path, base)
        finally:
            self._stop(process)

    def _checks(self):
        def teacher(client, path, base):
            resp = client.get("/teacher")
            self.assertEqual(resp.status_code, 200, f"/teacher → {resp.status_code}")
            self.assertIn("text/html", resp.headers.get("content-type", ""))

        def deep_link(client, path, base):
            resp = client.get("/teacher/paper/1")
            self.assertEqual(resp.status_code, 200,
                             f"SPA 深链接 /teacher/paper/1 → {resp.status_code}")
            self.assertIn("text/html", resp.headers.get("content-type", ""))
            self.assertIn("<div id=", resp.text[:2000].replace("\n", " "),
                          "深链接应回落到同一份 SPA 外壳")

        def api(client, path, base):
            resp = client.get("/api/papers")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("papers", resp.json())

        def docs(client, path, base):
            resp = client.get("/docs")
            self.assertEqual(resp.status_code, 200, f"/docs → {resp.status_code}")
            self.assertIn("swagger", resp.text.lower())

        return [("GET /teacher", teacher), ("GET /teacher/paper/1", deep_link),
                ("GET /api/papers", api), ("GET /docs", docs)]

    def test_starts_without_dist_and_still_serves_api(self):
        """静态目录缺失时**照常启动**（只提供 API）—— 用 MATHGEN_STATIC_DIR 指向不存在的目录。"""
        import httpx

        port = free_port()
        missing = os.path.join(ARTIFACTS, "no-such-dist")
        process = self._start(port, {"MATHGEN_STATIC_DIR": missing})
        try:
            self._wait_ready(port, process)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0) as client:
                self.assertEqual(client.get("/api/papers").status_code, 200)
                ui = client.get("/teacher")
                self.assertEqual(ui.status_code, 404,
                                 "没有静态产物时 /teacher 应为统一结构的 404")
                body = ui.json()
                self.assertEqual(body["error"]["code"], "NOT_FOUND")
        finally:
            self._stop(process)


if __name__ == "__main__":
    unittest.main()
