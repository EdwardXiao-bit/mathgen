"""D78 卷库收纳的后端用例：归档 / 取消归档 / 有成绩不许物理删除。

判据来源：契约 `docs/04-接口契约.md:198-243`（卷库列表参数 + 「卷库收纳（D78）」整段）
与 §9 的 `PAPER_HAS_ATTEMPTS`（`:385`）。三条容易被做坏的性质在这里各钉一遍：

1. **`archived_at` 与 `status` 正交**——归档不改 `status`，所以取消归档后回到原来的
   draft/published（`test_*_round_trip_*`）。若有人把它做成第三种 `status`，这些用例会红。
2. **归档不影响学生端**——已归档的已发布卷照常取卷、照常交卷
   （`ArchivedPaperStillWorksForStudentsTests`）。这类「不许加检查」的判据最容易
   被后来者“顺手”破坏，所以配了一条**留在仓库里**的负对照证明它能失败。
3. **列表不做 N+1**——`has_attempts` 若逐行现查，分页一页 20 行就是 40 条 SQL。
   `SummaryQueryCountTests` 用真实的 SQL 计数把它钉死，并用负对照证明计数器有辨别力。
"""
from __future__ import annotations

import contextlib
import unittest
from datetime import datetime
from unittest import mock

from sqlalchemy import event, func, select

from app.models import Attempt, Paper, PaperItem
from app.services import paper_service
from app.services.errors import ApiError

from tests.api._student_support import StudentApiTestCase, assert_public_paper
from tests.api._support import ApiTestCase, url_of


def archive_url(paper_id: int) -> str:
    return url_of("archive_paper", paper_id=paper_id)


def unarchive_url(paper_id: int) -> str:
    return url_of("unarchive_paper", paper_id=paper_id)


def delete_url(paper_id: int) -> str:
    return url_of("delete_paper", paper_id=paper_id)


def _assert_student_flow_works(tc: StudentApiTestCase, code: str, paper_id: int) -> None:
    """学生端「取卷 → 交卷」整条链路的判据（正例与负对照共用同一份判据）。"""
    response = tc.student_paper(code)
    tc.assertEqual(response.status_code, 200, response.text)
    payload = response.json()
    assert_public_paper(tc, payload)
    tc.assertEqual(payload["total"], len(payload["items"]))

    student_id = tc.student_id()
    submitted = tc.submit(
        student_id=student_id,
        paper_id=paper_id,
        answers=[{"seq": item["seq"], "answer": "1"} for item in payload["items"]],
    )
    tc.assertEqual(submitted.status_code, 201, submitted.text)


class ArchiveRoundTripTests(ApiTestCase):
    """归档 / 取消归档：任何状态都能归档，且**不碰 status**。"""

    def test_draft_round_trip_keeps_status_draft(self):
        paper_id = self.create_paper()["id"]

        archived = self.client.post(archive_url(paper_id))
        self.assertEqual(archived.status_code, 200, archived.text)
        body = archived.json()
        self.assertIsNotNone(body["archived_at"], "归档后 archived_at 必须非空")
        self.assertEqual(body["status"], "draft", "归档不许改 status（D78 正交）")

        restored = self.client.post(unarchive_url(paper_id))
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["archived_at"])
        self.assertEqual(restored.json()["status"], "draft")

    def test_published_round_trip_keeps_status_and_code(self):
        paper_id = self.create_paper()["id"]
        published = self.publish(paper_id)
        self.assertEqual(published.status_code, 200, published.text)
        code = published.json()["code"]

        archived = self.client.post(archive_url(paper_id))
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["status"], "published")
        self.assertEqual(archived.json()["code"], code, "归档不许动卷号")

        restored = self.client.post(unarchive_url(paper_id))
        self.assertEqual(restored.json()["status"], "published",
                         "归档前是 published，取消归档必须回到 published")
        self.assertEqual(restored.json()["code"], code)

        # 回到 published 之后卷号仍然有效（按卷号能打开）
        by_code = self.client.get(url_of("get_paper_by_code", code=code))
        self.assertEqual(by_code.status_code, 200, by_code.text)

    def test_repeated_archive_is_idempotent_and_keeps_first_timestamp(self):
        """钉死口径：重复归档**保留第一次**的 `archived_at`，不改写。"""
        paper_id = self.create_paper()["id"]
        first = self.client.post(archive_url(paper_id)).json()["archived_at"]

        # 先把时间戳改成一个可辨认的哨兵值：若实现「重复归档会刷新时间」，
        # 第二次调用就会把它覆盖掉，这条断言立刻能看出来（不靠时钟差）。
        sentinel = "2020-01-01T00:00:00+00:00"
        session = self.session()
        session.get(Paper, paper_id).archived_at = sentinel
        session.commit()
        session.close()

        second = self.client.post(archive_url(paper_id))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["archived_at"], sentinel,
                         "重复归档不该改写第一次的归档时间")
        self.assertNotEqual(first, sentinel)  # 哨兵确实换掉了原值（否则上一条是假绿）

    def test_repeated_unarchive_is_idempotent(self):
        paper_id = self.create_paper()["id"]
        for _ in range(2):
            response = self.client.post(unarchive_url(paper_id))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["archived_at"])

    def test_archive_does_not_touch_other_papers(self):
        keep = self.create_paper()["id"]
        target = self.create_paper()["id"]
        self.client.post(archive_url(target))

        self.assertIsNone(self.detail(keep)["archived_at"])
        self.assertIsNotNone(self.detail(target)["archived_at"])

    def test_archived_at_is_iso8601_with_timezone(self):
        paper_id = self.create_paper()["id"]
        raw = self.client.post(archive_url(paper_id)).json()["archived_at"]
        self.assertIsInstance(raw, str)
        self.assertIsNotNone(datetime.fromisoformat(raw).tzinfo,
                             "契约要求 ISO 8601 **带时区**")

    def test_archived_at_survives_on_detail_and_list(self):
        paper_id = self.create_paper()["id"]
        archived_at = self.client.post(archive_url(paper_id)).json()["archived_at"]

        self.assertEqual(self.detail(paper_id)["archived_at"], archived_at)
        listing = self.client.get(url_of("list_papers"), params={"archived": "only"}).json()
        self.assertEqual(listing["papers"][0]["archived_at"], archived_at)

    def test_unknown_paper_is_404_paper_not_found(self):
        for url in (archive_url(999), unarchive_url(999)):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")


class DeletePaperTests(ApiTestCase):
    """`DELETE /api/papers/{id}`：无成绩 → 204；有成绩 → 409 且什么都不许丢。"""

    def _counts(self) -> tuple[int, int]:
        session = self.session()
        papers = session.execute(select(func.count()).select_from(Paper)).scalar_one()
        items = session.execute(select(func.count()).select_from(PaperItem)).scalar_one()
        session.close()
        return int(papers), int(items)

    def test_delete_paper_without_attempts_is_204_and_rows_really_gone(self):
        paper_id = self.create_paper()["id"]
        before_papers, before_items = self._counts()
        self.assertGreaterEqual(before_items, 1)

        response = self.client.delete(delete_url(paper_id))
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"", "204 必须无响应体")

        after_papers, after_items = self._counts()
        self.assertEqual(after_papers, before_papers - 1)
        self.assertEqual(after_items, 0, "题目行必须跟着卷一起删掉（不留孤儿）")

        self.assertEqual(self.client.get(url_of("get_paper", paper_id=paper_id)).status_code, 404)

    def test_delete_published_paper_without_attempts_is_allowed(self):
        paper_id = self.create_paper()["id"]
        self.assertEqual(self.publish(paper_id).status_code, 200)
        self.assertEqual(self.client.delete(delete_url(paper_id)).status_code, 204)

    def test_delete_unknown_paper_is_404(self):
        response = self.client.delete(delete_url(999))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_archived_paper_can_still_be_deleted_when_it_has_no_attempts(self):
        """归档是软删除，不构成「不能删」的理由——有成绩才是。"""
        paper_id = self.create_paper()["id"]
        self.client.post(archive_url(paper_id))
        self.assertEqual(self.client.delete(delete_url(paper_id)).status_code, 204)


class DeletePaperWithAttemptTests(StudentApiTestCase):
    """有成绩的卷：409 `PAPER_HAS_ATTEMPTS`，卷与成绩**原样保留**。"""

    def test_delete_with_attempt_is_409_and_nothing_is_lost(self):
        paper_id, code, detail = self.published_paper()
        student_id = self.student_id()
        submitted = self.submit(
            student_id=student_id, paper_id=paper_id, answers=self.answers_of(detail)
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)

        response = self.client.delete(delete_url(paper_id))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_HAS_ATTEMPTS")
        self.assertTrue(response.json()["error"]["message"], "错误必须带一句人话")

        # 卷还在、题目还在、成绩还在，而且仍然能打开
        self.assertEqual(self.detail(paper_id)["items"][0]["seq"], 1)
        self.assertEqual(self.student_paper(code).status_code, 200)
        session = self.session()
        self.assertEqual(
            session.execute(
                select(func.count()).select_from(Attempt).where(Attempt.paper_id == paper_id)
            ).scalar_one(),
            1,
        )
        session.close()

    def test_archived_paper_with_attempts_is_still_protected(self):
        """归档不等于可以删：有成绩 → 依然 409（归档是收纳，不是解锁删除）。"""
        paper_id, _code, detail = self.published_paper()
        self.submit(student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail))
        self.assertEqual(self.client.post(archive_url(paper_id)).status_code, 200)

        response = self.client.delete(delete_url(paper_id))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_HAS_ATTEMPTS")

    def test_practice_attempt_does_not_block_deleting_a_paper(self):
        """自主练习的 attempt 行 `paper_id` 为 NULL，不该算到任何卷头上。"""
        from tests.api._support import CONFIG_ADD_SUB

        submitted = self.submit(
            student_id=self.student_id(),
            config=dict(CONFIG_ADD_SUB),
            seed=2025,
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)

        paper_id = self.create_paper()["id"]
        self.assertEqual(self.client.delete(delete_url(paper_id)).status_code, 204)

        session = self.session()
        self.assertEqual(
            session.execute(
                select(func.count()).select_from(Attempt).where(Attempt.paper_id.is_(None))
            ).scalar_one(),
            1,
            "练习成绩不该被删卷连带删掉",
        )
        session.close()


class ListArchivedFilterTests(ApiTestCase):
    """`GET /api/papers?archived=exclude|include|only`（默认 exclude）。"""

    def setUp(self):
        super().setUp()
        self.live = [self.create_paper()["id"] for _index in range(2)]
        self.hidden = self.create_paper()["id"]
        self.assertEqual(self.client.post(archive_url(self.hidden)).status_code, 200)

    def _list(self, **params) -> dict:
        response = self.client.get(url_of("list_papers"), params=params or None)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_exclude_is_the_default_and_hides_archived_papers(self):
        default = self._list()
        explicit = self._list(archived="exclude")
        for body in (default, explicit):
            self.assertEqual(body["total"], 2)
            self.assertEqual({p["id"] for p in body["papers"]}, set(self.live))

    def test_include_returns_everything(self):
        body = self._list(archived="include")
        self.assertEqual(body["total"], 3)
        self.assertEqual({p["id"] for p in body["papers"]}, set(self.live + [self.hidden]))

    def test_only_returns_just_the_archived_ones(self):
        body = self._list(archived="only")
        self.assertEqual(body["total"], 1, "total 必须跟着 archived 走，而不只是 papers")
        self.assertEqual([p["id"] for p in body["papers"]], [self.hidden])
        self.assertIsNotNone(body["papers"][0]["archived_at"])

    def test_total_and_papers_stay_consistent_for_every_filter(self):
        for mode in ("exclude", "include", "only"):
            body = self._list(archived=mode)
            self.assertEqual(body["total"], len(body["papers"]), mode)

    def test_archived_combines_with_status_filter(self):
        published = self.create_paper()["id"]
        self.assertEqual(self.publish(published).status_code, 200)
        self.client.post(archive_url(published))

        body = self._list(archived="only", status="published")
        self.assertEqual(body["total"], 1)
        self.assertEqual([p["id"] for p in body["papers"]], [published])

    def test_invalid_archived_value_is_422(self):
        for bad in ("all", "true", "EXCLUDE", "1", ""):
            response = self.client.get(url_of("list_papers"), params={"archived": bad})
            if bad == "":
                continue  # 空串是「没给参数」的另一种写法，见下一条
            self.assertEqual(response.status_code, 422, f"{bad!r} 应 422：{response.text}")
            self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_omitting_the_parameter_is_not_an_error(self):
        self.assertEqual(self.client.get(url_of("list_papers")).status_code, 200)


class SummaryHasAttemptsTests(StudentApiTestCase):
    """`PaperSummary.has_attempts` 必须如实反映「有没有成绩」。"""

    def test_has_attempts_is_false_until_a_student_submits(self):
        paper_id, _code, detail = self.published_paper()
        listing = self.client.get(url_of("list_papers")).json()
        row = next(p for p in listing["papers"] if p["id"] == paper_id)
        self.assertFalse(row["has_attempts"])

        self.submit(student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail))

        listing = self.client.get(url_of("list_papers")).json()
        row = next(p for p in listing["papers"] if p["id"] == paper_id)
        self.assertTrue(row["has_attempts"], "交卷后列表必须能看出这套卷有成绩")

    def test_practice_attempts_are_not_counted_for_any_paper(self):
        from tests.api._support import CONFIG_ADD_SUB

        paper_id = self.create_paper()["id"]
        submitted = self.submit(
            student_id=self.student_id(), config=dict(CONFIG_ADD_SUB), seed=2025
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)

        listing = self.client.get(url_of("list_papers")).json()
        row = next(p for p in listing["papers"] if p["id"] == paper_id)
        self.assertFalse(row["has_attempts"], "练习成绩不属于任何一套卷")


class SummaryQueryCountTests(StudentApiTestCase):
    """列表页不许 N+1：一页 1 行与 6 行发出的 SELECT 条数必须相同。"""

    def setUp(self):
        super().setUp()
        paper_id, _code, detail = self.published_paper()
        self.submit(student_id=self.student_id(), paper_id=paper_id, answers=self.answers_of(detail))

    @contextlib.contextmanager
    def _count_selects(self):
        statements: list[str] = []

        def _before(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", _before)
        try:
            yield statements
        finally:
            event.remove(self.engine, "before_cursor_execute", _before)

    def _selects_for_list(self) -> list[str]:
        with self._count_selects() as statements:
            response = self.client.get(url_of("list_papers"))
            self.assertEqual(response.status_code, 200, response.text)
        return statements

    def test_select_count_does_not_grow_with_page_size(self):
        one_row = len(self._selects_for_list())
        for index in range(5):
            self.create_paper()
        six_rows = len(self._selects_for_list())
        self.assertEqual(
            six_rows, one_row,
            f"列表查询条数随页内行数增长（{one_row} → {six_rows}），说明 has_attempts/item_count 在做 N+1",
        )

    def test_query_counter_notices_a_per_row_regression(self):
        """负对照（留在仓库）：把「批量取数」退回「逐行现查」→ 同一条判据必须红。

        这条证明上一条有辨别力：计数器不是恒定的，它真的会因为退回 N+1 而增长。
        模拟方式选的是**最容易发生的那种退化**——`to_summary` 丢掉了调用方批量算好的
        `item_count` / `attempts`（也就是某次重构把预计算参数删了），于是退回每行两次查询。
        """
        baseline = len(self._selects_for_list())
        for index in range(5):
            self.create_paper()
        original = paper_service.to_summary

        def per_row(db, paper, *, item_count=None, attempts=None):
            # 故意丢掉预计算值：落到 to_summary 内部的「按需现查」分支（N+1 的形状）
            return original(db, paper)

        with mock.patch.object(paper_service, "to_summary", per_row):
            regressed = len(self._selects_for_list())
        self.assertGreater(
            regressed, baseline,
            "逐行查询居然没有让 SELECT 变多——说明这个计数器根本没有辨别力",
        )


class ArchivedPaperStillWorksForStudentsTests(StudentApiTestCase):
    """D78 明确要求：**归档不影响学生端**（不许在学生路径上加 archived 检查）。"""

    def test_archived_published_paper_still_serves_students(self):
        paper_id, code, _detail = self.published_paper()
        self.assertEqual(self.client.post(archive_url(paper_id)).status_code, 200)

        _assert_student_flow_works(self, code, paper_id)

    def test_the_student_criterion_would_catch_an_archived_check(self):
        """负对照（留在仓库）：在学生路径上注入一条 archived 检查 → 同一条判据必须红。"""
        paper_id, code, _detail = self.published_paper()
        self.assertEqual(self.client.post(archive_url(paper_id)).status_code, 200)

        from app.services import student_service

        original = student_service.paper_payload

        def blocked(db, wanted_code):
            row = db.execute(select(Paper).where(Paper.code == wanted_code)).scalar_one_or_none()
            if row is not None and row.archived_at is not None:
                raise ApiError("CODE_NOT_FOUND", "已归档的卷不发给学生")
            return original(db, wanted_code)

        with mock.patch.object(student_service, "paper_payload", blocked):
            with self.assertRaises(AssertionError):
                _assert_student_flow_works(self, code, paper_id)

    def test_unarchived_paper_is_unaffected_either_way(self):
        """正例对照：没归档的卷本来就能用（证明上面两条不是靠「一律失败」通过的）。"""
        paper_id, code, _detail = self.published_paper()
        _assert_student_flow_works(self, code, paper_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
