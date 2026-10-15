"""task-36（T-M4-16）独立验收：卷库收纳（D78）—— 后端判据（判据 1–7）。

验收对象：`backend/app/**` 的归档（软删除）/ 取消归档 / 物理删除边界 / 老库护栏。
写域：本文件与 `_archive_support.py` 都在 `backend/tests/e2e_m4/**` 内；产品代码未改。

复跑（单独跑本模块）：

    & $VPY -m unittest discover -s backend/tests/e2e_m4 -t backend \
        -p "test_paper_archive_acceptance.py" -v

口径说明（为什么不全用 `make_client()`）：

* 判据 2/3/4/5 用**真机 uvicorn**（`ArchiveServer`）跑，因为本任务要求「用只读 SQLite
  直接查证」，那就必须有一个磁盘上的库文件；顺带证明产品能起来、静态/路由都正常。
* 判据 6/7 用进程内 `make_client()`，因为它们需要**注入**（老库形状 / SQL 事件计数），
  真机进程里够不着这些内部对象。
"""
from __future__ import annotations

import hashlib
import os
import unittest
import uuid

from tests.e2e_m4 import _archive_support as sup

#: 冻结契约哈希。M5 起点由 Lead 合法更新（D80：成绩接口与 `score_of` 进冻结包）——
#: 本文件**故意**不复用 `test_layering.EXPECTED_SHA256`，独立誊一份是为了让「两处必须一致」
#: 这件事本身成为判据（下面 `TestContractSurface` 会交叉核对两个文件里的值）。
FROZEN_API_SHA = "31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded"
API_PATH = os.path.join(sup.BACKEND, "app", "contracts", "api.py")
LAYERING_PATH = os.path.join(sup.BACKEND, "tests", "contract", "test_layering.py")


class RealBackendCase(unittest.TestCase):
    """一个类共用一个真实 uvicorn + 一个真实库文件（起服务有成本，别每个用例起一次）。"""

    server: sup.ArchiveServer

    @classmethod
    def setUpClass(cls) -> None:  # noqa: D102
        cls.server = sup.ArchiveServer()
        cls.server.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:  # noqa: D102
        cls.server.__exit__(None, None, None)

    @classmethod
    def title(cls, tag: str) -> str:
        """每个用例用独立标题，避免共享库里的互相干扰（列表断言一律带 q= 过滤）。"""
        return f"{tag}-{uuid.uuid4().hex[:8]}"

    @classmethod
    def new_nonce(cls) -> str:
        return f"attempt-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# 判据 1：契约面
# ---------------------------------------------------------------------------

class TestContractSurface(unittest.TestCase):
    """D78 的契约面：冻结哈希、三条路由、两个新字段、三口径 Literal。"""

    def test_contract_hash_matches_the_frozen_value(self):
        with open(API_PATH, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, FROZEN_API_SHA,
                         "backend/app/contracts/api.py 与 Lead 冻结的哈希不一致")
        # 冻结值必须是**判据来源**，不是抄在注释里的常量：与 task-28 的冻结表对账。
        with open(LAYERING_PATH, encoding="utf-8") as handle:
            self.assertIn(FROZEN_API_SHA, handle.read(),
                          "test_layering.py 里没有这个哈希 —— 我的常量可能与冻结表脱节")

    def test_d78_routes_and_fields_exist(self):
        from typing import get_args

        from app.contracts.api import ROUTES, ArchivedFilter, PaperSummary
        from app.services.paper_service import ARCHIVED_FILTERS

        self.assertEqual(ROUTES["archive_paper"], "/api/papers/{paper_id}/archive")
        self.assertEqual(ROUTES["unarchive_paper"], "/api/papers/{paper_id}/unarchive")
        self.assertEqual(ROUTES["delete_paper"], "/api/papers/{paper_id}")

        fields = set(PaperSummary.model_fields)
        self.assertIn("archived_at", fields, "PaperSummary 丢了 archived_at（D78）")
        self.assertIn("has_attempts", fields, "PaperSummary 丢了 has_attempts（D78）")

        self.assertEqual(set(ARCHIVED_FILTERS), {"exclude", "include", "only"})
        self.assertEqual(set(get_args(ArchivedFilter)), {"exclude", "include", "only"},
                         "ArchivedFilter 的三口径与服务的口径不一致")


# ---------------------------------------------------------------------------
# 判据 2：归档往返（status 逐字不变是 D78 的核心）
# ---------------------------------------------------------------------------

class TestArchiveRoundTrip(RealBackendCase):
    def test_draft_and_published_round_trip_keeps_status(self):
        for label, publish in (("草稿", False), ("已发布", True)):
            with self.subTest(label=label):
                title = self.title(f"收纳-{label}")
                paper_id = self.server.new_paper(title=title)
                if publish:
                    self.server.publish(paper_id)
                before = self.server.paper_detail(paper_id)
                status_before = before["status"]

                archived = self.server.archive(paper_id)
                self.assertEqual(archived.status_code, 200, archived.text)
                archived_body = archived.json()

                lists = {name: self.server.list_papers(q=title, archived=name)
                         for name in ("exclude", "only", "include")}
                unarchived = self.server.unarchive(paper_id)
                self.assertEqual(unarchived.status_code, 200, unarchived.text)

                problems = sup.archive_round_trip_problems(
                    status_before=status_before,
                    archived=archived_body,
                    unarchived=unarchived.json(),
                    lists=lists,
                )
                self.assertEqual(problems, [], f"{label}：{problems}")
                # 卷号在往返后依然有效（published 的情形）：归档不是「撤回发布」。
                if publish:
                    self.assertIsNotNone(unarchived.json()["code"])
                    self.assertEqual(
                        self.server.request("GET", f"/api/papers/{paper_id}/qr").status_code, 200)

    def test_archive_is_idempotent_and_keeps_first_timestamp(self):
        paper_id = self.server.new_paper(title=self.title("收纳-幂等"))
        first = self.server.archive(paper_id).json()["archived_at"]
        second = self.server.archive(paper_id)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["archived_at"], first,
                         "重复归档改写了时间戳（应保留第一次的归档时刻）")
        third = self.server.unarchive(paper_id)
        self.assertEqual(third.status_code, 200, third.text)
        self.assertIsNone(third.json()["archived_at"])
        again = self.server.unarchive(paper_id)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertIsNone(again.json()["archived_at"])

    def test_invalid_archived_value_is_422_not_silently_ignored(self):
        response = self.server.request("GET", "/api/papers", params={"archived": "maybe"})
        self.assertEqual(response.status_code, 422,
                         "非法的 archived 取值应 422（Literal 校验），不能静默按 exclude 处理")
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_archive_missing_paper_is_404(self):
        response = self.server.archive(999_999)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    # ---- 负对照：同一条判据在「错误的世界」里必须红 ----------------------
    def test_negative_control_status_used_as_a_third_state(self):
        """把归档做成第三种 status 的实现 —— 判据必须点名它。"""
        good = {"id": 7, "status": "published", "archived_at": "2026-10-15T00:00:00+08:00"}
        bad = dict(good, status="archived")
        lists = {"exclude": {"papers": [], "total": 0},
                 "only": {"papers": [{"id": 7}], "total": 1},
                 "include": {"papers": [{"id": 7}], "total": 1}}
        problems = sup.archive_round_trip_problems(
            status_before="published", archived=bad,
            unarchived={"id": 7, "status": "published", "archived_at": None}, lists=lists)
        self.assertTrue(any("归档改变了 status" in item for item in problems),
                        f"负对照没制造出失败条件：{problems}")

    def test_negative_control_archive_did_nothing(self):
        """只把 archived_at 写空、status 未变 —— 判据也必须红。"""
        lists = {"exclude": {"papers": [{"id": 8}], "total": 1},
                 "only": {"papers": [], "total": 0},
                 "include": {"papers": [{"id": 8}], "total": 1}}
        problems = sup.archive_round_trip_problems(
            status_before="draft",
            archived={"id": 8, "status": "draft", "archived_at": None},
            unarchived={"id": 8, "status": "draft", "archived_at": None}, lists=lists)
        self.assertGreaterEqual(len(problems), 3,
                                f"负对照没制造出失败条件（应同时红出空归档与三档筛选）：{problems}")

    def test_negative_control_unarchive_must_clear_the_timestamp(self):
        lists = {"exclude": {"papers": [], "total": 0},
                 "only": {"papers": [{"id": 9}], "total": 1},
                 "include": {"papers": [{"id": 9}], "total": 1}}
        problems = sup.archive_round_trip_problems(
            status_before="draft",
            archived={"id": 9, "status": "draft", "archived_at": "2026-10-15T00:00:00+08:00"},
            unarchived={"id": 9, "status": "draft", "archived_at": "2026-10-15T00:00:00+08:00"},
            lists=lists)
        self.assertTrue(any("archived_at 不是 null" in item for item in problems),
                        f"负对照没制造出失败条件：{problems}")


# ---------------------------------------------------------------------------
# 判据 3：删除的边界（只读 SQLite 查证）
# ---------------------------------------------------------------------------

def delete_boundary_problems(*, status: int, code: str | None, paper_rows: int,
                             item_rows: int, attempt_rows: int, attempt_snapshot_before: dict,
                             attempt_snapshot_after: dict) -> list[str]:
    """纯函数：无成绩必须真删干净；有成绩必须 409 且一行都不许少、分数不许变。"""
    problems: list[str] = []
    if status == 204:
        if paper_rows != 0:
            problems.append(f"删卷返回 204，但 paper 表还有 {paper_rows} 行")
        if item_rows != 0:
            problems.append(f"删卷返回 204，但 paper_item 表还有 {item_rows} 行")
    elif status == 409:
        if code != "PAPER_HAS_ATTEMPTS":
            problems.append(f"409 的错误码不是 PAPER_HAS_ATTEMPTS：{code!r}")
        if paper_rows != 1:
            problems.append(f"拒绝删除后 paper 行数不是 1：{paper_rows}")
        if attempt_rows < 1:
            problems.append("拒绝删除后 attempt 行没了")
        if attempt_snapshot_before != attempt_snapshot_after:
            problems.append(f"拒绝删除改动了成绩：{attempt_snapshot_before} → {attempt_snapshot_after}")
    else:
        problems.append(f"删卷的响应码既不是 204 也不是 409：{status}")
    return problems


class TestDeleteBoundary(RealBackendCase):
    def test_no_attempts_paper_is_really_deleted(self):
        paper_id = self.server.new_paper(title=self.title("删除-无成绩"), count=3)
        self.assertEqual(sup.readonly_count(self.server.db_path, "paper_item",
                                           "paper_id = ?", (paper_id,)), 3)
        response = self.server.delete(paper_id)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"", "204 不该有响应体")

        paper_rows = sup.readonly_count(self.server.db_path, "paper", "id = ?", (paper_id,))
        item_rows = sup.readonly_count(self.server.db_path, "paper_item",
                                       "paper_id = ?", (paper_id,))
        problems = delete_boundary_problems(
            status=204, code=None, paper_rows=paper_rows, item_rows=item_rows,
            attempt_rows=0, attempt_snapshot_before={}, attempt_snapshot_after={})
        self.assertEqual(problems, [], problems)

    def test_paper_with_attempts_is_refused_and_nothing_changes(self):
        title = self.title("删除-有成绩")
        paper_id = self.server.new_paper(title=title, count=3)
        published = self.server.publish(paper_id)
        student_id = self.server.student_id("验收学生", f"2024{uuid.uuid4().hex[:8]}")
        sup.submit_attempt(self.server, student_id=student_id, paper_id=paper_id,
                           uuid_hex=self.new_nonce())

        snapshot_sql = ("SELECT total, correct, duration_ms, submitted_at FROM attempt "
                        "WHERE paper_id = ? ORDER BY id")
        before = sup.readonly_rows(self.server.db_path, snapshot_sql, (paper_id,))
        self.assertEqual(len(before), 1, "构造有成绩卷失败：库里没有 attempt 行")
        attempt_id = sup.readonly_value(self.server.db_path,
                                        "SELECT id FROM attempt WHERE paper_id = ?", (paper_id,))
        self.assertGreater(
            sup.readonly_count(self.server.db_path, "attempt_item", "attempt_id = ?", (attempt_id,)),
            0, "构造有成绩卷失败：attempt_item 没有行")

        response = self.server.delete(paper_id)
        self.assertEqual(response.status_code, 409, response.text)
        code = response.json()["error"]["code"]

        after = sup.readonly_rows(self.server.db_path, snapshot_sql, (paper_id,))
        problems = delete_boundary_problems(
            status=409, code=code,
            paper_rows=sup.readonly_count(self.server.db_path, "paper", "id = ?", (paper_id,)),
            item_rows=sup.readonly_count(self.server.db_path, "paper_item",
                                         "paper_id = ?", (paper_id,)),
            attempt_rows=len(after), attempt_snapshot_before=before[0],
            attempt_snapshot_after=after[0])
        self.assertEqual(problems, [], problems)
        # 403 与 409 的语义差别要说清：这里是「有成绩」而不是「没权限」。
        self.assertIn("成绩", response.json()["error"]["message"])
        self.assertIn("归档", response.json()["error"]["message"], "拒绝时没给下一步（改用归档）")
        # 卷号在拒绝删除后依然有效。
        self.assertEqual(self.server.request("GET", f"/api/student/paper/{published['code']}")
                         .status_code, 200)

    def test_delete_missing_paper_is_404(self):
        response = self.server.delete(999_999)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    # ---- 负对照 ---------------------------------------------------------
    def test_negative_control_silent_delete_of_a_graded_paper(self):
        """有成绩却返回 204 —— 判据必须红（否则「删除边界」等于没守护）。"""
        problems = delete_boundary_problems(
            status=204, code=None, paper_rows=1, item_rows=3, attempt_rows=1,
            attempt_snapshot_before={"total": 3}, attempt_snapshot_after={"total": 3})
        self.assertTrue(any("paper 表还有 1 行" in item for item in problems),
                        f"负对照没制造出失败条件：{problems}")

    def test_negative_control_score_changed_by_a_refused_delete(self):
        problems = delete_boundary_problems(
            status=409, code="PAPER_HAS_ATTEMPTS", paper_rows=1, item_rows=3, attempt_rows=1,
            attempt_snapshot_before={"correct": 2}, attempt_snapshot_after={"correct": 0})
        self.assertTrue(any("改动了成绩" in item for item in problems),
                        f"负对照没制造出失败条件：{problems}")

    def test_negative_control_wrong_error_code(self):
        problems = delete_boundary_problems(
            status=409, code="PAPER_NOT_EDITABLE", paper_rows=1, item_rows=3, attempt_rows=1,
            attempt_snapshot_before={}, attempt_snapshot_after={})
        self.assertTrue(any("PAPER_HAS_ATTEMPTS" in item for item in problems),
                        f"负对照没制造出失败条件：{problems}")


# ---------------------------------------------------------------------------
# 判据 4：has_attempts 的真假两向（V11）
# ---------------------------------------------------------------------------

class TestHasAttemptsTruth(RealBackendCase):
    def test_list_reports_true_and_false_for_the_two_worlds(self):
        graded_title = self.title("has_attempts-有成绩")
        plain_title = self.title("has_attempts-无成绩")
        graded_id = self.server.new_paper(title=graded_title, count=3)
        self.server.new_paper(title=plain_title, count=4)
        self.server.publish(graded_id)
        student_id = self.server.student_id("真假学生", f"2024{uuid.uuid4().hex[:8]}")
        sup.submit_attempt(self.server, student_id=student_id, paper_id=graded_id,
                           uuid_hex=self.new_nonce())

        graded = self.server.list_papers(q=graded_title)["papers"]
        plain = self.server.list_papers(q=plain_title)["papers"]
        self.assertEqual(len(graded), 1, graded)
        self.assertEqual(len(plain), 1, plain)

        observed = {"with_attempts": graded[0]["has_attempts"],
                    "without_attempts": plain[0]["has_attempts"]}
        problems = sup.has_attempts_problems(observed)
        self.assertEqual(problems, [], f"{problems}；原始观测：{observed}")
        # 详情接口不带这个字段（它属于列表项），这里顺手钉住，避免有人把语义搬到详情上。
        detail = self.server.paper_detail(graded_id)
        self.assertIn("archived_at", detail)
        self.assertNotIn("has_attempts", detail)

    def test_negative_controls_both_constant_worlds(self):
        for world in ({"with_attempts": True, "without_attempts": True},
                      {"with_attempts": False, "without_attempts": False}):
            with self.subTest(world=world):
                problems = sup.has_attempts_problems(world)
                self.assertTrue(any("恒值字段" in item for item in problems),
                                f"恒值世界没被判据抓住：{world} → {problems}")

    def test_negative_control_swapped_values(self):
        """取值互换（有成绩的报 false、没成绩的报 true）同样必须红。

        注意这里**不该**报「恒值」：两个观测不相等，所以恰好红两条方向性错误 ——
        若哪天它报满三条，说明判据把「非恒值」也算错了（那时恒值负对照会失真）。
        """
        problems = sup.has_attempts_problems(
            {"with_attempts": False, "without_attempts": True})
        self.assertEqual(len(problems), 2, problems)
        self.assertFalse(any("恒值字段" in item for item in problems), problems)


# ---------------------------------------------------------------------------
# 判据 5：归档不影响学生端（正对照在真机，负对照注入 archived 检查）
# ---------------------------------------------------------------------------

class TestArchivedPaperStaysAnswerable(RealBackendCase):
    def test_students_can_still_open_and_submit_a_graded_archived_paper(self):
        paper_id = self.server.new_paper(title=self.title("学生端-归档"), count=3)
        code = self.server.publish(paper_id)["code"]
        self.assertEqual(self.server.archive(paper_id).status_code, 200)

        student_id = self.server.student_id("归档学生", f"2024{uuid.uuid4().hex[:8]}")
        get_response = self.server.student_paper(code)
        submit_response = self.server.submit(
            student_id=student_id, paper_id=paper_id, attempt_uuid=self.new_nonce())
        attempt_rows = sup.readonly_count(self.server.db_path, "attempt",
                                          "paper_id = ?", (paper_id,))

        problems = sup.student_flow_problems(
            get_status=get_response.status_code, submit_status=submit_response.status_code,
            attempt_rows_after=attempt_rows)
        self.assertEqual(problems, [], problems)
        # 卷仍然是归档态（学生端可用不等于自动取消归档）。
        self.assertIsNotNone(self.server.paper_detail(paper_id)["archived_at"])
        # 交卷响应是**判分结果**，不是「卷不存在」的伪装。
        self.assertIn("correct", submit_response.json())

    def test_negative_control_injected_archived_check_turns_the_same_criterion_red(self):
        """注入「归档 = 学生端不可见」的桩，同一条判据必须红（V9.2）。"""
        from tests.api._support import fresh_engine, make_client, session_of, url_of

        engine = fresh_engine()
        with make_client(engine) as client:
            created = client.post(url_of("create_paper"), json=dict(sup.CONFIG, count=3),
                                  params={"seed": 7})
            self.assertEqual(created.status_code, 201, created.text)
            paper_id = created.json()["id"]
            code = client.post(url_of("publish_paper", paper_id=paper_id)).json()["code"]
            self.assertEqual(client.post(url_of("archive_paper", paper_id=paper_id)).status_code,
                             200)
            student = client.post(url_of("create_student"),
                                  json={"name": "桩学生", "student_no": "20240001"})
            student_id = student.json()["student_id"]

            def observe() -> dict:
                get_response = client.get(url_of("student_paper", code=code))
                submit = client.post(url_of("submit_attempt"), json={
                    "student_id": student_id, "attempt_uuid": "attempt-injected-0001",
                    "answers": [], "duration_ms": 10, "paper_id": paper_id})
                with session_of(engine) as session:
                    from sqlalchemy import func, select

                    from app.models import Attempt
                    rows = session.execute(
                        select(func.count()).select_from(Attempt)
                        .where(Attempt.paper_id == paper_id)).scalar_one()
                return {"get_status": get_response.status_code,
                        "submit_status": submit.status_code, "attempt_rows_after": int(rows)}

            healthy = observe()
            self.assertEqual(sup.student_flow_problems(**healthy), [],
                             f"未注入时判据就红了 —— 判据本身有问题：{healthy}")

            with sup.archived_blocks_students():
                broken = observe()
            problems = sup.student_flow_problems(**broken)
            self.assertGreaterEqual(len(problems), 1,
                                    f"注入归档检查后判据没红（判据没在守护学生端）：{broken}")
            self.assertIn("GET /api/student/paper", problems[0])
        engine.dispose()

    def test_negative_control_student_flow_problems_reject_silent_failure(self):
        problems = sup.student_flow_problems(get_status=200, submit_status=201,
                                             attempt_rows_after=0)
        self.assertTrue(any("成绩没真的写进去" in item for item in problems), problems)


# ---------------------------------------------------------------------------
# 判据 6：老库护栏（没有 archived_at 列的库）
# ---------------------------------------------------------------------------

class TestOldDatabaseGuard(unittest.TestCase):
    """与 D76 的 `ensure_attempt_schema` 同款手法的**反向**构造：先有数据，再去掉列。

    为什么老库要落成**磁盘文件**：护栏用 `WeakSet` 记住「这个引擎已经查过列」，所以必须在
    DROP 列之后换一个**新引擎对象**打开它，才等价于「新进程遇到旧库」。内存库换引擎就换库了。
    """

    def setUp(self) -> None:  # noqa: D102
        self.db_path = sup.old_database_path()
        self.built = sup.build_old_paper_db(self.db_path)
        self.engines: list = []

    def tearDown(self) -> None:  # noqa: D102
        for engine in self.engines:
            engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def engine(self):
        engine = sup.file_engine(self.db_path)
        self.engines.append(engine)
        return engine

    def test_old_database_without_the_column_is_repaired_and_keeps_data(self):
        from app.services.paper_schema import ARCHIVED_AT_COLUMN
        from tests.api._support import make_client, url_of

        built = self.built
        self.assertFalse(built["archived_at_present"],
                         f"老库构造失败，paper 表仍有 {ARCHIVED_AT_COLUMN}：{built['columns']}")

        # 负对照：没有护栏时，产品的读法（select(Paper) 摊平所有列）**必然**炸。
        failure = sup.raw_select_paper_fails(self.engine())
        self.assertIsNotNone(failure, "构造出的老库居然能直接查 —— 这不是老库形状")
        self.assertIn(ARCHIVED_AT_COLUMN, failure, failure)

        engine = self.engine()
        with make_client(engine) as client:
            listed = client.get(url_of("list_papers"))
            self.assertEqual(listed.status_code, 200, listed.text)
            body = listed.json()
            rows = [row for row in body["papers"] if row["id"] == built["paper_id"]]
            self.assertEqual(len(rows), 1, f"老库里已有的卷读不出来了：{body}")
            self.assertEqual(rows[0]["title"], built["title"])
            self.assertEqual(rows[0]["item_count"], built["item_rows"],
                             "老库里的题目行丢了")
            self.assertIsNone(rows[0]["archived_at"], "补列后历史卷应自动是「未归档」")

            archived = client.post(url_of("archive_paper", paper_id=built["paper_id"]))
            self.assertEqual(archived.status_code, 200, archived.text)
            self.assertIsNotNone(archived.json()["archived_at"])
            self.assertEqual(archived.json()["status"], "draft", "归档不该改 status")

            only = client.get(url_of("list_papers"), params={"archived": "only"})
            self.assertEqual([row["id"] for row in only.json()["papers"]],
                             [built["paper_id"]])

            restored = client.post(url_of("unarchive_paper", paper_id=built["paper_id"]))
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertIsNone(restored.json()["archived_at"])

        columns_after = sup.readonly_columns(self.db_path, "paper")
        self.assertIn(ARCHIVED_AT_COLUMN, columns_after,
                      "护栏没有真的把列补上（只是恰好没报错）")
        # 数据没丢：题目行、卷号、seed 都还在（从库文件直接读，不信 HTTP 响应）。
        self.assertEqual(
            sup.readonly_count(self.db_path, "paper_item", "paper_id = ?", (built["paper_id"],)),
            built["item_rows"])
        row = sup.readonly_rows(self.db_path, "SELECT title, seed FROM paper WHERE id = ?",
                                (built["paper_id"],))[0]
        self.assertEqual(row["title"], built["title"])
        self.assertEqual(int(row["seed"]), built["seed"])

    def test_negative_control_the_guard_is_about_this_column(self):
        """把列名换成一个不存在的名字，产品读法仍然失败 —— 判据盯的是**这一列**，不是「补过列」。"""
        from app.services.paper_schema import ADD_ARCHIVED_AT_SQL

        self.assertIn("archived_at", ADD_ARCHIVED_AT_SQL)
        self.assertFalse(self.built["archived_at_present"])
        connection = sup.sqlite3.connect(self.db_path)
        try:
            connection.execute("ALTER TABLE paper ADD COLUMN archived_typo VARCHAR(40)")
            connection.commit()
        finally:
            connection.close()
        failure = sup.raw_select_paper_fails(self.engine())
        self.assertIsNotNone(failure, "补了错名的列之后居然能查 —— 负对照失效")
        self.assertIn("archived_at", failure)


# ---------------------------------------------------------------------------
# 判据 7：has_attempts 是批量取数，不是每行一条查询
# ---------------------------------------------------------------------------

class TestHasAttemptsIsBatched(unittest.TestCase):
    """自己数 SQL，不采信实现方的汇报（任务卡要求）。"""

    def _client_with_papers(self, count: int):
        from tests.api._support import fresh_engine, make_client, url_of

        engine = fresh_engine()
        client_context = make_client(engine)
        client = client_context.__enter__()
        created = []
        for index in range(count):
            response = client.post(url_of("create_paper"),
                                   json=dict(sup.CONFIG, count=2),
                                   params={"seed": 1000 + index})
            self.assertEqual(response.status_code, 201, response.text)
            created.append(response.json()["id"])
        return engine, client_context, client, created

    def test_list_page_issues_one_attempt_query_regardless_of_row_count(self):
        engine, context, client, paper_ids = self._client_with_papers(5)
        try:
            from tests.api._support import url_of

            with sup.statement_log(engine) as statements:
                listed = client.get(url_of("list_papers"), params={"limit": 20})
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertGreaterEqual(len(listed.json()["papers"]), 5)

            queries = sup.attempt_selects(statements)
            self.assertEqual(len(queries), 1,
                             "列表页访问 attempt 表的语句不是 1 条：\n" + "\n---\n".join(queries))
            self.assertIn("IN (", queries[0].upper().replace("\n", " "),
                          f"批量取数应当用 IN (...) 一次取回：{queries[0]}")
        finally:
            context.__exit__(None, None, None)
            engine.dispose()

    def test_negative_control_per_row_calls_are_counted(self):
        """同一条计数口径下，逐行调用**必须**被数出 N 条 —— 证明计数器真的在数。"""
        engine, context, client, paper_ids = self._client_with_papers(5)
        try:
            from app.services.paper_service import has_attempts
            from tests.api._support import session_of

            with sup.statement_log(engine) as statements:
                with session_of(engine) as session:
                    for paper_id in paper_ids:
                        has_attempts(session, paper_id)
            self.assertEqual(len(sup.attempt_selects(statements)), len(paper_ids),
                             f"逐行调用没被数出 {len(paper_ids)} 条 —— 计数口径失效")
        finally:
            context.__exit__(None, None, None)
            engine.dispose()

    def test_negative_control_a_second_page_does_not_multiply_queries(self):
        """单页 5 行与单页 1 行相比，attempt 查询数不增长（真 N+1 会）。"""
        engine, context, client, paper_ids = self._client_with_papers(5)
        try:
            from tests.api._support import url_of

            with sup.statement_log(engine) as one:
                client.get(url_of("list_papers"), params={"limit": 1})
            with sup.statement_log(engine) as five:
                client.get(url_of("list_papers"), params={"limit": 5})
            self.assertEqual(len(sup.attempt_selects(one)), 1)
            self.assertEqual(len(sup.attempt_selects(five)), 1,
                             "行数变多后 attempt 查询数增长了（N+1）")
        finally:
            context.__exit__(None, None, None)
            engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
