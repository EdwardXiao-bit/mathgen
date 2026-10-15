"""task-36（T-M4-16）独立验收的公共设施：只读 SQLite 取证、真实服务、SQL 计数、判据函数。

为什么把**判据**（`*_problems` 纯函数）与**世界**（真实服务 / 注入桩）分开写：

    task-36 最大的风险是写出「看起来在守护、实际恒真或恒红」的判据（V11）。
    如果负对照只是把断言再抄一遍，它证明不了判据本身会失败。这里让负对照把
    **错误的世界**（伪造的观测值、注入 archived 检查的桩）喂给**同一条**判据函数，
    于是「判据在坏世界里真的会红」成为可执行事实（V9.1 / V9.2）。

写域纪律：本模块与同目录的用例只新建/修改 `backend/tests/e2e_m4/**` 下的文件，
不改 `backend/app/**`、`backend/tests/api/**`、`frontend/**`。注入桩全部是**进程内
运行时替换**（monkeypatch 服务层函数），不落任何产品代码改动。
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(BACKEND)
ARTIFACTS = os.path.join(HERE, "artifacts")
os.makedirs(ARTIFACTS, exist_ok=True)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests.e2e_m4 import _harness  # noqa: E402

#: 出题配置（6 题太少时便于观察题量；这里 3 题够用且生成快）
CONFIG = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral"}

#: 卷库每页大小（与 `frontend/src/shared/api.ts` 的 `LIST_BOUNDS.defaultLimit` 一致）
PAGE_SIZE = 20


# ---------------------------------------------------------------------------
# 只读 SQLite 取证（不看 HTTP 响应，直接读库文件）
# ---------------------------------------------------------------------------

def readonly_rows(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    """以 `mode=ro` 打开库文件跑一条 SELECT，返回 dict 列表。

    用 `mode=ro` 而不是普通连接：判据本身**不可能**写库，取证动作不带副作用。
    产品用默认 journal mode（非 WAL），因此只读连接能直接读到已提交内容。
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


def readonly_value(db_path: str, sql: str, params: tuple = ()):
    rows = readonly_rows(db_path, sql, params)
    return rows[0][list(rows[0])[0]] if rows else None


def readonly_count(db_path: str, table: str, where: str = "1=1", params: tuple = ()) -> int:
    return int(readonly_value(db_path, f"SELECT COUNT(*) FROM {table} WHERE {where}", params) or 0)


def readonly_columns(db_path: str, table: str) -> list[str]:
    return [row["name"] for row in readonly_rows(db_path, f"PRAGMA table_info({table})")]


def readonly_tables(db_path: str) -> list[str]:
    return [row["name"] for row in readonly_rows(
        db_path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


# ---------------------------------------------------------------------------
# 真实后端（真机 uvicorn + 真实库文件）
# ---------------------------------------------------------------------------

class ArchiveServer(_harness.UiServer):
    """真实 `uvicorn app.main:app` + 真实库文件。

    为什么验收要用它而不是 `make_client()`：本任务要求「用只读 SQLite 直接查证」，
    那就必须有一个**磁盘上的库文件**；`TestClient` 的内存库做不到（见用例里的说明）。
    另外真机 uvicorn 也顺带证明了产品能起来。
    """

    def request(self, method: str, path: str, *, params=None, json_body=None,
                expect: int | None = None):
        import httpx

        response = httpx.request(method, f"{self.base_url}{path}", params=params,
                                 json=json_body, timeout=20.0)
        if expect is not None:
            assert response.status_code == expect, \
                f"{method} {path} → {response.status_code}（期望 {expect}）：{response.text[:300]}"
        return response

    # ---- 数据构造（全走真实 HTTP） ------------------------------------
    def new_paper(self, *, title: str, seed: int = 20261015, count: int = 3) -> int:
        response = self.request("POST", "/api/papers", params={"seed": seed, "title": title},
                                json_body=dict(CONFIG, count=count), expect=201)
        return int(response.json()["id"])

    def archive(self, paper_id: int):
        return self.request("POST", f"/api/papers/{paper_id}/archive")

    def unarchive(self, paper_id: int):
        return self.request("POST", f"/api/papers/{paper_id}/unarchive")

    def delete(self, paper_id: int):
        return self.request("DELETE", f"/api/papers/{paper_id}")

    def paper_detail(self, paper_id: int) -> dict:
        return self.request("GET", f"/api/papers/{paper_id}", expect=200).json()

    def list_papers(self, **params) -> dict:
        return self.request("GET", "/api/papers", params=params, expect=200).json()

    def student_id(self, name: str, student_no: str) -> int:
        response = self.request("POST", "/api/students",
                                json_body={"name": name, "student_no": student_no})
        assert response.status_code in (200, 201), response.text
        return int(response.json()["student_id"])

    def student_paper(self, code: str):
        return self.request("GET", f"/api/student/paper/{code}")

    def submit(self, *, student_id: int, paper_id: int, attempt_uuid: str,
               answers: list | None = None, duration_ms: int = 1234):
        return self.request("POST", "/api/attempts", json_body={
            "student_id": student_id,
            "attempt_uuid": attempt_uuid,
            "answers": answers or [],
            "duration_ms": duration_ms,
            "paper_id": paper_id,
        })


def submit_attempt(server: ArchiveServer, *, student_id: int, paper_id: int, uuid_hex: str,
                   answers: list | None = None):
    """提交一次作答并断言成功（201 新建 / 200 幂等重放）。"""
    response = server.submit(student_id=student_id, paper_id=paper_id,
                             attempt_uuid=uuid_hex, answers=answers)
    assert response.status_code in (200, 201), response.text
    return response


# ---------------------------------------------------------------------------
# 判据 2：归档往返 —— status 与 archived_at 各自独立变化
# ---------------------------------------------------------------------------

def archive_round_trip_problems(*, status_before: str, archived: dict, unarchived: dict,
                                lists: dict) -> list[str]:
    """纯函数：把归档往返的四个观测点变成问题清单（空 = 通过）。

    `lists` = `{"exclude": PaperListOut, "only": PaperListOut, "include": PaperListOut}`，
    `paper_id` 从 `archived["id"]` 取。任何一项观测缺失都算问题，不静默放过。

    D78 的核心在第一条：归档**只**动 `archived_at`。把归档做成第三种 status 的实现
    会在这里红（`status` 变了），而只断言「archived_at 非空」的实现**不会**红。
    """
    problems: list[str] = []
    paper_id = archived.get("id")

    if archived.get("archived_at") in (None, ""):
        problems.append("归档后 archived_at 仍是空 —— 归档没发生")
    if archived.get("status") != status_before:
        problems.append(
            f"归档改变了 status：{status_before!r} → {archived.get('status')!r}"
            "（D78 要求归档与 status 正交）")
    if unarchived.get("archived_at") is not None:
        problems.append(f"取消归档后 archived_at 不是 null：{unarchived.get('archived_at')!r}")
    if unarchived.get("status") != status_before:
        problems.append(
            f"取消归档后 status 与归档前不一致：{status_before!r} → {unarchived.get('status')!r}")

    seen = {name: [row["id"] for row in body.get("papers", [])]
            for name, body in lists.items()}
    if paper_id in seen.get("exclude", []):
        problems.append("archived=exclude（默认档）里仍看得见已归档的卷")
    if paper_id not in seen.get("only", []):
        problems.append("archived=only 里看不见已归档的卷")
    if paper_id not in seen.get("include", []):
        problems.append("archived=include 里看不见已归档的卷")

    # 三口径必须自洽：同一套过滤条件下 include = exclude + only，且 total >= 返回条数。
    # （早先这里写成「only 的 total 必须小于 include」是错的：当 q 过滤只命中这一张卷时，
    #   only 与 include 都是 1，会对自己造出假红。）
    totals = {name: body.get("total") for name, body in lists.items()}
    if None not in totals.values():
        if totals["exclude"] + totals["only"] != totals["include"]:
            problems.append(
                f"三口径的 total 不自洽：exclude({totals['exclude']}) + "
                f"only({totals['only']}) != include({totals['include']})")
    for name, body in lists.items():
        papers = body.get("papers")
        if papers is None:
            problems.append(f"{name}：响应里没有 papers 字段")
            continue
        if body.get("total", 0) < len(papers):
            problems.append(f"{name}：total({body.get('total')}) 小于返回条数({len(papers)})")
    return problems


# ---------------------------------------------------------------------------
# 判据 4：has_attempts 必须真的能真能假（V11：恒真也能让 UI 看起来正常）
# ---------------------------------------------------------------------------

def has_attempts_problems(observed: dict[str, bool]) -> list[str]:
    """纯函数：`{"with_attempts": bool, "without_attempts": bool}` → 问题清单。

    一次断言三件事：有成绩的为真、没成绩的为假、**两者不相等**（非恒值）。
    第三条是 V11 的关键：恒真的实现同样能让「有成绩时禁用删除」看起来在工作。
    """
    problems: list[str] = []
    truthy = observed.get("with_attempts")
    falsy = observed.get("without_attempts")
    if truthy is not True:
        problems.append(f"有成绩的卷 has_attempts 不是 true：{truthy!r}")
    if falsy is not False:
        problems.append(f"没成绩的卷 has_attempts 不是 false：{falsy!r}")
    if truthy is not None and falsy is not None and truthy == falsy:
        problems.append(f"has_attempts 在两个应当不同的输入上取值相同（{truthy!r}）—— 恒值字段")
    return problems


# ---------------------------------------------------------------------------
# 判据 5：归档不影响学生端（配「注入 archived 检查」的负对照）
# ---------------------------------------------------------------------------

def student_flow_problems(*, get_status: int, submit_status: int,
                          attempt_rows_after: int) -> list[str]:
    """纯函数：学生端在**已归档的已发布卷**上照常取题、交卷、并真的写进成绩。"""
    problems: list[str] = []
    if get_status != 200:
        problems.append(f"GET /api/student/paper/{{code}} 在已归档卷上返回 {get_status}，期望 200")
    if submit_status not in (200, 201):
        problems.append(f"POST /api/attempts 在已归档卷上返回 {submit_status}，期望 201/200")
    if attempt_rows_after < 1:
        problems.append("交卷返回成功，但库里没有 attempt 行 —— 成绩没真的写进去")
    return problems


@contextlib.contextmanager
def archived_blocks_students():
    """注入「归档 = 学生端不可见」的错误实现（**只改运行时函数，不动源码**）。

    两个注入点就是这种错法最自然的落点：

    * `student_service.get_paper_by_code`：按卷号取题（GET）时把归档卷当成卷号无效；
    * `attempt_service.get_paper_or_404`：交卷（POST）时把归档卷当成不存在。

    用它跑同一条 `student_flow_problems`，若判据不变红，就说明判据其实没在守护学生端。
    """
    from app.services import attempt_service, student_service
    from app.services.errors import ApiError

    real_by_code = student_service.get_paper_by_code
    real_get_paper = attempt_service.get_paper_or_404

    def guarded_by_code(db, code):
        paper = real_by_code(db, code)
        if paper.archived_at is not None:
            raise ApiError("CODE_NOT_FOUND", "卷号无效（注入桩：归档即对学生端隐藏）")
        return paper

    def guarded_get_paper(db, paper_id):
        paper = real_get_paper(db, paper_id)
        if paper.archived_at is not None:
            raise ApiError("PAPER_NOT_FOUND", "卷不存在（注入桩：归档即对学生端隐藏）")
        return paper

    student_service.get_paper_by_code = guarded_by_code
    attempt_service.get_paper_or_404 = guarded_get_paper
    try:
        yield
    finally:
        student_service.get_paper_by_code = real_by_code
        attempt_service.get_paper_or_404 = real_get_paper


# ---------------------------------------------------------------------------
# 判据 7：has_attempts 不是每行一条查询（N+1）
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def statement_log(engine):
    """记录该 engine 上执行过的 SQL 语句（供 N+1 判据计数）。"""
    from sqlalchemy import event

    seen: list[str] = []

    def _record(_conn, _cursor, statement, _parameters, _context, _executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def attempt_selects(statements: list[str]) -> list[str]:
    """从语句清单里挑出**访问 `attempt` 表**的 SELECT（N+1 的观测对象）。"""
    hits = []
    for statement in statements:
        lowered = statement.lower()
        if "select" in lowered and "attempt" in lowered and "attempt_item" not in lowered:
            hits.append(statement)
    return hits


# ---------------------------------------------------------------------------
# 老库护栏（判据 6）
# ---------------------------------------------------------------------------

def old_database_path(tag: str = "old-paper") -> str:
    """老库验收用的库文件路径（放 artifacts，用例结束时删）。"""
    return os.path.join(ARTIFACTS, f"task36-{tag}-{uuid.uuid4().hex[:8]}.db")


def file_engine(db_path: str):
    """指向磁盘库文件的新引擎。

    为什么非要用**新引擎对象**：`ensure_paper_schema` 用一个 `WeakSet` 记住「这个引擎已经查过列」。
    如果我在同一个引擎上先建列、再 DROP 掉，护栏的缓存是热的，于是「老库」这一步会被缓存短路 ——
    那是我构造的假象，不是产品的行为。换一个引擎对象才等价于「新进程打开一个旧库」。
    """
    from sqlalchemy import create_engine

    from app.db import enable_sqlite_foreign_keys

    return enable_sqlite_foreign_keys(
        create_engine(f"sqlite+pysqlite:///{db_path}", future=True))


def build_old_paper_db(db_path: str, *, title: str = "老库遗留卷", seed: int = 4242) -> dict:
    """把**库文件**造成 D78 之前的形状：有 paper 行与 paper_item 行，但没有 `archived_at` 列。

    做法（与 D76 的 `ensure_attempt_schema` 同款思路的**反向**操作）：

    1. 用**产品自己的建卷接口**造出真实数据（这样 `paper_item.payload` 一定是产品认得的
       题目形状，不会出现「我编的 payload 产品读不了」这种假象）；
    2. `ALTER TABLE paper DROP COLUMN archived_at` 把库退回旧形状；
    3. 关掉这个引擎 —— 之后用例必须**新建**引擎打开它（见 `file_engine` 的说明）。

    返回后续判据要用的 id 与「旧形状」自证信息（全部从库里读回，不是常量）。
    """
    from tests.api._support import make_client, url_of

    engine = file_engine(db_path)
    try:
        with make_client(engine) as client:
            created = client.post(url_of("create_paper"), json=dict(CONFIG, count=2),
                                  params={"seed": seed, "title": title})
            assert created.status_code == 201, created.text
            paper_id = int(created.json()["id"])
    finally:
        engine.dispose()

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("ALTER TABLE paper DROP COLUMN archived_at")
        connection.commit()
    finally:
        connection.close()

    row = readonly_rows(db_path, "SELECT title, seed FROM paper WHERE id = ?", (paper_id,))[0]
    columns = readonly_columns(db_path, "paper")
    return {
        "paper_id": paper_id,
        "title": row["title"],
        "seed": int(row["seed"]),
        "item_rows": readonly_count(db_path, "paper_item", "paper_id = ?", (paper_id,)),
        "columns": columns,
        "archived_at_present": "archived_at" in columns,
    }


def _pragma_columns(engine, table: str) -> list[str]:
    from sqlalchemy import text

    with engine.connect() as connection:
        rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [row[1] for row in rows]


def raw_select_paper_fails(engine) -> str | None:
    """不走产品护栏、直接 `select(Paper)`：老库上**必然**报错，返回错误原文。

    这是判据 6 的负对照：它证明「缺列」这件事在没有护栏时真的会炸，
    而不是我假设它会炸（V9.2）。返回 None 表示居然没炸 —— 那说明构造的老库形状不对。
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.models import Paper

    with engine.connect() as connection:
        try:
            connection.execute(select(Paper)).fetchall()
        except SQLAlchemyError as exc:  # noqa: BLE001 - 要的就是它
            return f"{type(exc).__name__}: {exc.orig if hasattr(exc, 'orig') else exc}"
        return None


# ---------------------------------------------------------------------------
# 判据 8：教师端界面 —— 只伪造 `/api/papers` 的桩（真实 dist + 真实浏览器）
# ---------------------------------------------------------------------------

def stub_paper(paper_id: int, *, title: str | None = None, has_attempts: bool = False,
               archived: bool = False, status: str = "draft", item_count: int = 3) -> dict:
    """一条 `PaperSummary` 形状的假数据（字段与 `frontend/src/shared/api.ts:195-213` 对齐）。"""
    return {
        "id": paper_id,
        "code": f"2610-{paper_id:04d}" if status == "published" else None,
        "title": title or f"桩卷 {paper_id}",
        "status": status,
        "item_count": item_count,
        "created_at": "2026-10-15T10:00:00+08:00",
        "published_at": "2026-10-15T10:05:00+08:00" if status == "published" else None,
        "archived_at": "2026-10-15T11:00:00+08:00" if archived else None,
        "has_attempts": has_attempts,
    }


class LibraryStubServer:
    """真实构建产物 + **只伪造 `/api/papers`** 的桩 —— UI 判据的负对照世界。

    为什么需要它：`frontend/**` 不在本任务的写域内，不能给页面注入任何东西；
    而 `_harness.degraded_dist()` 只往 `student.html` 注入 CSS，动不了教师端 JS。
    于是换一个方向：**前端源码一个字不改**，只把列表数据换掉，看渲染结果是否如判据所料。

    端口用 `bind(("127.0.0.1", 0))` 让内核分配（不 `free_port()` 预探测 —— 那是 TOCTOU，
    见 task-33 / `tests/_uvicorn_boot.py` 的说明）。所有 `/api/papers` 请求都记进
    `self.requests`，供「切档回第一页」这类判据直接核对查询参数。
    """

    def __init__(self, papers: list[dict]):
        self.papers = list(papers)
        self.requests: list[dict] = []
        #: 所有请求的原文（含静态资源与别的 `/api/*`）——诊断「页面为什么不渲染」用
        self.all_requests: list[str] = []
        self.httpd = None
        self.thread = None
        self.port = None

    # ---- 请求处理 --------------------------------------------------------
    def _papers_payload(self, query: dict) -> dict:
        archived = (query.get("archived") or ["exclude"])[0]
        status = (query.get("status") or [None])[0]
        q = ((query.get("q") or [""])[0]).strip().lower()
        limit = int((query.get("limit") or ["20"])[0])
        offset = int((query.get("offset") or ["0"])[0])

        rows = []
        for paper in self.papers:
            if archived == "exclude" and paper["archived_at"] is not None:
                continue
            if archived == "only" and paper["archived_at"] is None:
                continue
            if status and paper["status"] != status:
                continue
            if q and q not in paper["title"].lower():
                continue
            rows.append(paper)
        page = rows[offset:offset + limit]
        return {"total": len(rows), "limit": limit, "offset": offset, "papers": page}

    def _send_json(self, handler, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def __enter__(self):
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        import threading
        from urllib.parse import parse_qs, urlparse

        stub = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=_harness.DIST, **kwargs)

            def do_GET(self):                # noqa: N802
                parsed = urlparse(self.path)
                stub.all_requests.append(f"GET {self.path}")
                if parsed.path == "/api/papers":
                    stub.requests.append(parse_qs(parsed.query))
                    return stub._send_json(self, 200, stub._papers_payload(
                        parse_qs(parsed.query)))
                if parsed.path.startswith("/api/"):
                    return stub._send_json(self, 404, {"error": {
                        "code": "STUB_UNIMPLEMENTED",
                        "message": f"验收桩只实现 /api/papers：{parsed.path}"}})
                if not os.path.exists(self.translate_path(self.path)) and "." not in self.path:
                    self.path = "/index.html"      # SPA 回落
                return super().do_GET()

            def log_message(self, *args):          # 静音
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # ---- 供判据读取 ------------------------------------------------------
    def last_papers_request(self) -> dict:
        return self.requests[-1] if self.requests else {}

    def papers_requests_summary(self) -> list[dict]:
        return [{key: (value[0] if value else None) for key, value in request.items()}
                for request in self.requests]


#: 教师端卷库页面里「有成绩 → 事先禁用删除」那条理由的可见文本（`LibraryPage.tsx:56`）
DELETE_BLOCKED_SHORT = "已有学生成绩，不能删除"
#: tooltip 里的完整提示（`LibraryPage.tsx:55`），用来核对「说了为什么 + 给了下一步」
DELETE_BLOCKED_HINT = "这套卷已有学生成绩，不能删除；可以归档。"


def row_facts(page, paper_id: int) -> dict:
    """把**渲染后**的一行读成事实（判据只吃事实，不吃页面对象）。"""
    row = page.query_selector(f'[data-paper-id="{paper_id}"]')
    if row is None:
        return {"id": paper_id, "present": False, "delete_disabled": None,
                "guard": None, "guard_title": None, "reason_text": None}
    button = row.query_selector('[data-testid="library-delete"]')
    guard = row.query_selector("[data-delete-guard]")
    reason = row.query_selector('[data-testid="library-delete-blocked-reason"]')
    return {
        "id": paper_id,
        "present": True,
        "delete_disabled": button.is_disabled() if button is not None else None,
        "guard": guard.get_attribute("data-delete-guard") if guard is not None else None,
        "guard_title": guard.get_attribute("title") if guard is not None else None,
        "reason_text": reason.inner_text().strip() if reason is not None else None,
    }


def archive_ui_problems(graded: dict, clean: dict) -> list[str]:
    """纯函数：把两行渲染事实变成问题清单（空 = 通过）。

    `graded` = 有学生成绩的卷那一行；`clean` = 没有成绩的卷那一行。
    两侧任何一行读不到都算问题 —— 不允许「DOM 没渲染出来」被当成通过。
    """
    problems: list[str] = []
    for label, facts in (("有成绩的卷", graded), ("没成绩的卷", clean)):
        if not facts.get("present"):
            problems.append(f"{label}：列表里没有这一行（读不到 DOM 事实）")
    if problems:
        return problems

    if not graded["delete_disabled"]:
        problems.append("有成绩的卷：删除按钮**没有事先禁用**（点下去才吃 409）")
    if graded["guard"] != "blocked":
        problems.append(f"有成绩的卷：data-delete-guard={graded['guard']!r}，期望 'blocked'")
    reason = (graded.get("reason_text") or "").strip()
    if not reason:
        problems.append("有成绩的卷：没有**可见**的禁用理由文本")
    elif "成绩" not in reason or "不能删除" not in reason:
        problems.append(f"有成绩的卷：可见理由没说清为什么不能删：{reason!r}")
    if "归档" not in (reason + " " + (graded.get("guard_title") or "")):
        problems.append("有成绩的卷：只说了不能删，没给下一步（可以归档）")

    if clean["delete_disabled"]:
        problems.append("没成绩的卷：删除按钮也被禁用了 —— 恒禁用等于所有卷都删不掉")
    if clean["guard"] != "allowed":
        problems.append(f"没成绩的卷：data-delete-guard={clean['guard']!r}，期望 'allowed'")
    if (clean.get("reason_text") or "").strip():
        problems.append(f"没成绩的卷：不该出现禁用理由：{clean['reason_text']!r}")
    return problems
