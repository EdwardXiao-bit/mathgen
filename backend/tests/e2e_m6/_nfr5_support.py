"""task-52 判据支撑：NFR-5 服务端排版守卫的独立验收夹具（verify-m1 自建）。

三条真实路径都在这里走通：
  * **API 路径**：`GET /api/papers/{id}/export`（200 / 409 / 503 三个分支都在一条路由里）
  * **打印路径**：同一份卷面 HTML 交给 Chromium 自己的打印通道（打印按钮 / Ctrl+P 走的就是它）
  * **模型路径**：产品自己的 `measure_js()` + `detect_overflow()`（守卫实际用的那条判据）

为什么要合成题面：真实生成器能产出的最长题面 = **26 个字符**（714 个配置全扫，见报告 §5），
在最坏合法档下守卫量到 561px << 833px 阈值 —— 用真实内容**造不出被拒世界**。
所以本文件里的 `chars=N` 夹具全是合成的，报告逐条标注了哪一组是合成的、哪一组是真实的。

判据取向（V9）：每个 checker 都是纯函数，喂给它一个「本该被抓到」的合成输入必须变红。
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
REPO = BACKEND.parent
ARTIFACTS = HERE / "artifacts"
VENDORED_PYLIBS = BACKEND / "tests" / "e2e_m2" / ".pylibs"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(VENDORED_PYLIBS) not in sys.path:
    sys.path.insert(0, str(VENDORED_PYLIBS))

from app.contracts.layout import LayoutParams                      # noqa: E402
from app.contracts.sheet import SheetContext, SheetItem, SheetMode  # noqa: E402
from app.export import browser as _browser_mod                     # noqa: E402
from app.export.pdf import export_pdf_report, layout_of            # noqa: E402
from app.layout.pagination import content_width_px, detect_overflow  # noqa: E402
from app.services.sheet_render import render_html                  # noqa: E402

# Chromium 的启动位置与临时目录（与 mathgen AGENTS.md 的环境约定一致）：
# 沙箱下 tempfile.mkdtemp() 会被拒，Playwright 的 Node 驱动必须把 TMP 指到工作区目录。
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(REPO / ".playwright"))
os.environ.setdefault("MATHGEN_PW_TMP", str(REPO / ".tmp-pw"))
for _name in ("TMP", "TEMP", "TMPDIR"):
    os.environ.setdefault(_name, str(REPO / ".tmp-pw"))
_browser_mod._ensure_tmp_env()

__all__ = [
    "ARTIFACTS", "BACKEND", "REPO", "CJK", "ITEMS", "REAL_DISPLAY",
    "WORST", "DEFAULT_LAYOUT", "EXTREME_ACCEPTED",
    "filler", "make_items", "make_html", "model_case", "model_verdicts",
    "pdf_page_texts", "pdf_marker_crossings", "pdf_text_bboxes",
    "GuardClient", "guard_env", "guard_warnings",
    "guard_pair_problems", "parse_suggestion", "suggestion_problems",
    "miss_scan_problems", "client_guard_problems", "print_trigger_sites",
    "pdf_response_problems", "error_code", "error_message",
    "write_evidence", "asdict",
]

#: 8 题：默认排版 4 列 → 2 行；最坏档 6 列 → 2 行（最后一行 2 题）
ITEMS = 8
#: 真实生成器实测的最长题面（714 配置全扫，26 个 ASCII 字符）
REAL_DISPLAY = "942848 - (718286 + 148111)"
#: 真实可生成内容的最坏合法档（六个参数全取边界上限）
WORST = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
DEFAULT_LAYOUT = LayoutParams()
#: 守卫判「放得下」的极端档：最坏排版 + 真实内容（实测 561px，阈值 833px）
EXTREME_ACCEPTED = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)

CJK = "商店运来一批练习本，第一天卖出总数的三分之一，第二天卖出的比第一天多二十四本。"


def asdict(layout: LayoutParams) -> dict:
    return dataclasses.asdict(layout)


# --------------------------------------------------------------------------
# 卷面夹具（合成题面 + 真实题面）
# --------------------------------------------------------------------------

def filler(chars: int) -> str:
    """确定性的 CJK 填充串（长度正好 chars）。"""
    return (CJK * (chars // len(CJK) + 1))[:chars]


def make_items(*, chars: int | None = None, display: str | None = None, count: int = ITEMS):
    """两端带 marker 的题块：marker 用来在被打印的 PDF 里找「同一个题块在哪几页」。"""
    body = display if display is not None else filler(int(chars or 0))
    return tuple(
        SheetItem(seq=i + 1, display=f"MKR{i:02d}A{body}MKR{i:02d}B",
                  answer=str(i + 1), difficulty=1, tags=("nfr5",), steps=(f"第 {i + 1} 步",))
        for i in range(count)
    )


def make_html(*, chars: int | None = None, display: str | None = None,
              layout: LayoutParams = DEFAULT_LAYOUT, count: int = ITEMS,
              mode: SheetMode = SheetMode.QUESTION, title: str = "NFR-5 独立验收卷",
              code: str | None = "2610-5204", header_note: str = "") -> str:
    ctx = SheetContext(title=title, mode=mode, items=make_items(chars=chars, display=display, count=count),
                       layout=layout, code=code, header_note=header_note)
    return render_html(ctx)


# --------------------------------------------------------------------------
# 模型路径：产品自己的测量 + 判据
# --------------------------------------------------------------------------

def model_case(*, chars: int | None = None, display: str | None = None,
               layout: LayoutParams, count: int = ITEMS, tag: str = "case") -> dict:
    """走**产品自己的**打印+测量通道，拿模型判决，再数真实 PDF 的页内结构。

    `reject_overflow=False`：这里要的是「测量值」，不是「被拒绝」，所以不让守卫中断导出。
    """
    html = make_html(chars=chars, display=display, layout=layout, count=count)
    out = ARTIFACTS / f"nfr5-{tag}-{uuid.uuid4().hex[:8]}.pdf"
    report = export_pdf_report(html, str(out), measure=True, reject_overflow=False)
    rows = [round(float(row.get("height", 0.0)), 1) for row in (report.measurement.get("rows") or ())]
    overflows = detect_overflow(report.measurement, layout)
    facts = pdf_marker_crossings(out, items=count, cols=layout.cols_per_row)
    bboxes = pdf_text_bboxes(out)
    return {
        "tag": tag,
        "chars": chars,
        "display_is_real": display is not None,
        "layout": asdict(layout),
        "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "pdf": str(out),
        "pdf_bytes": out.stat().st_size,
        "pdf_pages": report.pages,
        "model_rows_px": rows,
        "model_max_row_px": max(rows) if rows else None,
        "model_overflow_rows": [o.row_index for o in overflows],
        "model_fits": not overflows,
        "real_bboxes": bboxes,
        "real_max_text_height_px": max([b["text_height_px"] or 0.0 for b in bboxes] or [0.0]),
        **{k: v for k, v in facts.items() if k != "pages"},
        "real_pages": facts["pages"],
    }


def model_verdicts(cases: Sequence[Mapping[str, Any]]) -> dict:
    """把若干 `model_case` 结果汇总成「模型 vs 真实」对照表（判据 5 的输入）。"""
    records = []
    for case in cases:
        records.append({
            "tag": case["tag"],
            "model_fits": bool(case["model_fits"]),
            "model_max_row_px": case["model_max_row_px"],
            "real_pages": case["real_pages"],
            "real_crossings": len(case["crossings"]),
            "real_crossing_rows": case["row_crossings"],
            "markers_missing": len(case["missing"]),
        })
    return {"records": records}


# --------------------------------------------------------------------------
# 真实打印通道的读数（pypdfium2，逐页取文本与字符盒）
# --------------------------------------------------------------------------

def pdf_page_texts(path: str | Path) -> list[str]:
    """逐页取文本并去掉所有空白：marker 可能被抽成多段，去空白后才能子串匹配。"""
    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(str(path))
    try:
        return [re.sub(r"\s+", "", doc[i].get_textpage().get_text_range()) for i in range(len(doc))]
    finally:
        doc.close()


def pdf_marker_crossings(path: str | Path, *, items: int = ITEMS, cols: int = 4) -> dict:
    """每个题块两端 marker 是否落在**同一页**；任一题块跨页 → 该行跨页。"""
    pages = pdf_page_texts(path)
    found: list[dict] = []
    missing: list[dict] = []
    crossings: list[dict] = []
    for index in range(items):
        head, tail = f"MKR{index:02d}A", f"MKR{index:02d}B"
        head_page = next((n + 1 for n, text in enumerate(pages) if head in text), None)
        tail_page = next((n + 1 for n, text in enumerate(pages) if tail in text), None)
        if head_page is None or tail_page is None:
            missing.append({"item": index, "head_page": head_page, "tail_page": tail_page})
            continue
        found.append({"item": index, "head_page": head_page, "tail_page": tail_page})
        if head_page != tail_page:
            crossings.append({"item": index, "from_page": head_page, "to_page": tail_page})
    return {
        "pages": len(pages),
        "markers": found,
        "missing": missing,
        "crossings": crossings,
        "row_crossings": sorted({c["item"] // cols for c in crossings}),
    }


def pdf_text_bboxes(path: str | Path) -> list[dict]:
    """每页文本的包围盒（px，96dpi，y 从页顶算起）——用来把「模型行高」与真实纸面比。"""
    import pypdfium2 as pdfium  # type: ignore

    doc = pdfium.PdfDocument(str(path))
    try:
        boxes = []
        for index in range(len(doc)):
            page = doc[index]
            height_px = float(page.get_height()) * 96 / 72
            textpage = page.get_textpage()
            tops, bottoms = [], []
            for i in range(textpage.count_chars()):
                box = textpage.get_charbox(i)
                if not box:
                    continue
                left, bottom, right, top = (float(v) * 96 / 72 for v in box)
                tops.append(height_px - top)
                bottoms.append(height_px - bottom)
            boxes.append({
                "page": index + 1,
                "text_top_px": round(min(tops), 1) if tops else None,
                "text_bottom_px": round(max(bottoms), 1) if bottoms else None,
                "text_height_px": round(max(bottoms) - min(tops), 1) if tops else None,
                "chars": len(tops),
            })
        return boxes
    finally:
        doc.close()


# --------------------------------------------------------------------------
# API 路径：临时库 + TestClient（自建，不借别的套件的 harness）
# --------------------------------------------------------------------------

class GuardClient:
    """临时 SQLite + TestClient；库里直接写 paper/paper_item（合成题面的唯一入口）。"""

    def __init__(self, *, layout: LayoutParams | None = None, chars: int = 0,
                 display: str | None = None, count: int = ITEMS, title: str = "NFR-5 独立验收卷"):
        self.db_path = str(ARTIFACTS / f"nfr5-{uuid.uuid4().hex[:8]}.db")
        self.layout = layout or DEFAULT_LAYOUT
        self.chars = chars
        self.display = display
        self.count = count
        self.title = title
        self.engine = None
        self.client = None
        self.paper_id: int | None = None

    def __enter__(self) -> "GuardClient":
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine

        from app.db import enable_sqlite_foreign_keys, get_db, init_db, session_factory
        from app.main import create_app

        self.engine = enable_sqlite_foreign_keys(create_engine(
            f"sqlite+pysqlite:///{self.db_path}", connect_args={"check_same_thread": False}, future=True))
        init_db(self.engine)
        factory = session_factory(self.engine)
        app = create_app(engine=self.engine, static_dir=False)

        def _override():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = _override
        self.client = TestClient(app)
        self.paper_id = self._insert_paper(factory)
        return self

    def __exit__(self, *exc):
        if self.engine is not None:
            self.engine.dispose()
        return False

    # -- 夹具写入 ---------------------------------------------------------
    def _insert_paper(self, factory) -> int:
        from app.models import Paper, PaperItem
        from app.services.snapshot import dump_payload, question_from_dict

        body = self.display if self.display is not None else filler(self.chars)
        session = factory()
        try:
            paper = Paper(code="2610-5204", title=self.title, status="draft", seed=20261015,
                          config_json=json.dumps({"ops": ["+", "-"], "count": self.count}),
                          layout_json=json.dumps(asdict(self.layout), ensure_ascii=False),
                          answer_layout="{}", created_at="2026-10-16T00:00:00+08:00")
            session.add(paper)
            session.flush()
            for index in range(self.count):
                question = question_from_dict({
                    "expr": f"{index + 1}+{index + 2}", "display": f"MKR{index:02d}A{body}MKR{index:02d}B",
                    "answer": str(index + 1), "steps": [f"第 {index + 1} 步"],
                    "difficulty": 1, "tags": ["nfr5"], "signature": f"sig-{index}",
                })
                session.add(PaperItem(paper_id=paper.id, seq=index + 1,
                                      payload=dump_payload(question), origin="fixture"))
            session.commit()
            return int(paper.id)
        finally:
            session.close()

    def content_fingerprint(self) -> str:
        """卷面内容指纹（八个题目的 payload 逐字节拼接）——证明两个世界只差排版参数。"""
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import PaperItem

        session = session_factory(self.engine)()
        try:
            rows = session.execute(select(PaperItem).where(PaperItem.paper_id == self.paper_id)
                                   .order_by(PaperItem.seq)).scalars().all()
            return hashlib.sha256("|".join(row.payload for row in rows).encode("utf-8")).hexdigest()
        finally:
            session.close()

    # -- 三个真实分支 -----------------------------------------------------
    def export(self, *, file_format: str = "pdf", mode: str = "question", **kwargs):
        params = {"format": file_format, "mode": mode}
        return self.client.get(f"/api/papers/{self.paper_id}/export", params=params)

    def layout_params(self) -> dict:
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import Paper

        session = session_factory(self.engine)()
        try:
            row = session.execute(select(Paper).where(Paper.id == self.paper_id)).scalar_one()
            return json.loads(row.layout_json)
        finally:
            session.close()

    def set_layout(self, **fields) -> dict:
        response = self.client.patch(f"/api/papers/{self.paper_id}/layout", json=dict(fields))
        assert response.status_code == 200, response.text
        return response.json()


@contextlib.contextmanager
def guard_env(**values):
    """临时改环境变量（值为 None → 删除）。测试结束后逐字还原。"""
    saved = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.contextmanager
def guard_warnings():
    """收集 `app.routers.exports` 的 WARNING 记录（应急出口的机器可读证据）。"""
    records: list[logging.LogRecord] = []
    logger = logging.getLogger("app.routers.exports")
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[assignment]
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# --------------------------------------------------------------------------
# 纯判据
# --------------------------------------------------------------------------

def pdf_response_problems(response, *, label: str = "200 世界") -> list[str]:
    problems = []
    if response.status_code != 200:
        problems.append(f"{label}: 期望 200，实际 {response.status_code}：{response.text[:200]}")
        return problems
    if not response.content.startswith(b"%PDF-"):
        problems.append(f"{label}: 响应不是 PDF（前 8 字节 {response.content[:8]!r}）")
    if "application/pdf" not in response.headers.get("content-type", ""):
        problems.append(f"{label}: content-type 不是 application/pdf：{response.headers.get('content-type')!r}")
    return problems


def error_code(response) -> str | None:
    try:
        body = response.json()
    except Exception:
        return None
    if isinstance(body, dict):
        for key in ("code", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and isinstance(value.get("code"), str):
                return value["code"]
    return None


def error_message(response) -> str:
    try:
        body = response.json()
    except Exception:
        return response.text
    if isinstance(body, dict):
        for key in ("message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for inner in ("message", "detail"):
                    if isinstance(value.get(inner), str):
                        return value[inner]
    return response.text


def guard_pair_problems(*, default_status: int, overflow_status: int, content_same: bool,
                        default_code: str | None = None, overflow_code: str | None = None) -> list[str]:
    """判据 1：同一内容、只差六个排版参数，两个世界的判决必须相反。"""
    problems = []
    if not content_same:
        problems.append("两个世界的题目内容不同 —— 判决差异可能来自内容而不是排版")
    if default_status != 200:
        problems.append(f"默认排版应 200，实际 {default_status}")
    if overflow_status != 409:
        problems.append(f"会溢出的排版应 409，实际 {overflow_status}")
    if overflow_code != "LAYOUT_DOES_NOT_FIT":
        problems.append(f"409 的业务码应为 LAYOUT_DOES_NOT_FIT，实际 {overflow_code!r}")
    return problems


_SUGGEST_RE = re.compile(
    r"请把(每行题数|字号|行距倍数|页边距)从\s*([0-9]+(?:\.[0-9]+)?)\s*"
    r"(?:题|号|倍|cm)?\s*调到\s*([0-9]+(?:\.[0-9]+)?)")


def parse_suggestion(message: str) -> dict:
    """把 409 文案里「实测这样能放下」的目标参数原样取出来。"""
    targets = {}
    for label, _old, new in _SUGGEST_RE.findall(message or ""):
        targets[{"每行题数": "cols_per_row", "字号": "font_size",
                 "行距倍数": "row_gap", "页边距": "page_margin"}[label]] = float(new)
    return {"targets": targets, "has_target": bool(targets),
            "claims_direction_only": "请把字号调小，或把每行题数减少" in (message or "")}


def suggestion_problems(message: str) -> list[str]:
    parsed = parse_suggestion(message)
    problems = []
    if parsed["has_target"] and parsed["claims_direction_only"]:
        problems.append("文案同时给出具体目标值和「只给方向」两种形态 —— 形态互斥")
    if not parsed["has_target"] and not parsed["claims_direction_only"]:
        problems.append("文案既没有具体目标值，也没有可识别的「只给方向」表述")
    return problems


def miss_scan_problems(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """判据 5：**漏报方向**＝模型说放得下、真实却切开。只要有一条就是问题。

    同时拒绝「没量到真实数据」的静默通过：没有真实页数/切开数就不算证据。
    """
    problems = []
    seen = 0
    for record in records:
        seen += 1
        tag = record.get("tag", "?")
        if record.get("real_pages") in (None, 0):
            problems.append(f"{tag}: 没有真实页数，判据等于没跑")
        if record.get("model_fits") is None:
            problems.append(f"{tag}: 没有模型判决")
        if record.get("model_fits") and record.get("real_crossing_rows"):
            problems.append(
                f"[漏报] {tag}: 模型判「放得下」，真实 PDF 却把行 {record['real_crossing_rows']} 切开了")
    if seen == 0:
        problems.append("没有样本：空集不许通过")
    return problems


_GUARD_TOKENS = ("LAYOUT_DOES_NOT_FIT", "X-Mathgen-Layout-Guard", "MATHGEN_SKIP_LAYOUT_GUARD",
                 "detect_overflow", "overflow_rows", "layout-guard", "guard_layout_of_html")
_PRINT_TOKENS = (".print(", "window.print", "print()")


def print_trigger_sites(sources: Mapping[str, str]) -> list[dict]:
    """列出客户端里所有「触发打印」的位置。"""
    sites = []
    for path, text in sources.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if any(token in line for token in _PRINT_TOKENS):
                sites.append({"path": path, "line": number, "text": line.strip()[:160]})
    return sites


def client_guard_problems(sources: Mapping[str, str]) -> list[str]:
    """判据 6：客户端侧**不得存在**与服务端等价的排版拦截。

    规则：同一个文件里既有打印触发、又出现守卫标记（业务码 / 跳过开关 / 溢出字段 /
    导出前校验），就是「客户端侧守卫」——那会让「Ctrl+P 没有第二层」这句话失真。
    """
    problems = []
    if not sources:
        problems.append("没有读到任何客户端源码：空集不许通过")
        return problems
    triggers = print_trigger_sites(sources)
    if not triggers:
        problems.append("客户端里找不到任何打印触发 —— 扫描范围或口径错了")
    for path, text in sources.items():
        has_print = any(token in text for token in _PRINT_TOKENS)
        hits = [token for token in _GUARD_TOKENS if token in text]
        if has_print and hits:
            problems.append(f"{path}: 客户端在打印路径上出现了服务端守卫标记 {hits}")
    return problems


def write_evidence(name: str, payload: Mapping[str, Any]) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
