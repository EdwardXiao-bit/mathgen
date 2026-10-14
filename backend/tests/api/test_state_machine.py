"""卷状态机测试（契约 §6 冻结，D32）：`draft` → `published` 单向不可逆。

覆盖：
- 发布后再编辑一律 409 `PAPER_NOT_EDITABLE`（删题 / 换题 / 改排版）
- 发布**不会**改变题目与排版（冻结的是内容，不是入口）
- 重复发布幂等（返回同一卷号）
- 另存副本只读原卷、不修改原卷；副本可编辑可再发布
- 题目快照在发布前后逐字一致
- 学生端凭卷号取卷必须先发布（`PAPER_NOT_PUBLISHED` 语义在 M4，这里只守状态迁移）
"""
from __future__ import annotations

import unittest

from app.models import PAPER_STATUS_DRAFT, PAPER_STATUS_PUBLISHED
from app.services.paper_service import get_paper_or_404
from tests.api._support import ApiTestCase, url_of


class TestPaperStateMachine(ApiTestCase):
    LAYOUT = {"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
              "page_margin": 2.0, "answer_line": True, "header_bar": True}

    def test_publish_sets_status_code_and_timestamp(self):
        created = self.create_paper({"count": 3})
        self.assertIsNone(created["code"])
        self.assertEqual(created["status"], "draft")

        body = self.publish(created["id"]).json()
        detail = self.detail(created["id"])
        self.assertEqual(detail["status"], "published")
        self.assertEqual(detail["code"], body["code"])
        self.assertRegex(body["code"], r"^\d{4}-\d{4}$")
        self.assertIsNotNone(detail["published_at"])

        with self.session() as db:
            row = get_paper_or_404(db, created["id"])
            self.assertEqual(row.status, PAPER_STATUS_PUBLISHED)
            self.assertEqual(row.code, body["code"])
            self.assertIsNotNone(row.published_at)

    def test_publish_does_not_touch_items_or_layout(self):
        created = self.create_paper({"count": 4})
        self.client.patch(url_of("update_layout", paper_id=created["id"]), json=self.LAYOUT)
        before = self.detail(created["id"])

        self.publish(created["id"])
        after = self.detail(created["id"])

        self.assertEqual(after["items"], before["items"])
        self.assertEqual(after["layout"], before["layout"])
        self.assertEqual(after["config"], before["config"])
        self.assertEqual(after["seed"], before["seed"])

    def test_publish_is_idempotent(self):
        created = self.create_paper({"count": 3})
        first = self.publish(created["id"]).json()
        stamp = self.detail(created["id"])["published_at"]
        second = self.publish(created["id"]).json()
        self.assertEqual(first["code"], second["code"])
        self.assertEqual(first["qr_url"], second["qr_url"])
        # 重复发布不得刷新发布时间
        self.assertEqual(self.detail(created["id"])["published_at"], stamp)

    def test_publish_assigns_distinct_codes(self):
        codes = set()
        for _ in range(3):
            paper = self.create_paper({"count": 2})
            codes.add(self.publish(paper["id"]).json()["code"])
        self.assertEqual(len(codes), 3)

    def test_published_paper_rejects_delete(self):
        created = self.create_paper({"count": 4})
        self.publish(created["id"])
        response = self.client.delete(url_of("delete_item", paper_id=created["id"], seq=2))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_EDITABLE")
        # 拒绝之后内容必须原封不动
        self.assertEqual([item["seq"] for item in self.detail(created["id"])["items"]], [1, 2, 3, 4])

    def test_published_paper_rejects_regenerate(self):
        created = self.create_paper({"count": 4})
        self.publish(created["id"])
        before = self.detail(created["id"])["items"]
        response = self.client.post(url_of("regenerate_item", paper_id=created["id"], seq=1))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_EDITABLE")
        self.assertEqual(self.detail(created["id"])["items"], before)

    def test_published_paper_rejects_layout_change(self):
        created = self.create_paper({"count": 4})
        self.publish(created["id"])
        response = self.client.patch(
            url_of("update_layout", paper_id=created["id"]), json=self.LAYOUT
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_EDITABLE")

    def test_draft_can_still_be_edited_after_failed_publish_attempt(self):
        """发布被拒（这里用未知卷模拟失败）不应影响别的 draft 的编辑能力。"""
        draft = self.create_paper({"count": 3})
        self.assertEqual(
            self.client.post(url_of("publish_paper", paper_id=999999)).status_code, 404
        )
        self.assertEqual(
            self.client.delete(url_of("delete_item", paper_id=draft["id"], seq=1)).status_code, 200
        )

    def test_copy_of_published_paper_is_draft_without_code(self):
        created = self.create_paper({"count": 4})
        published = self.publish(created["id"]).json()

        clone = self.client.post(f"/api/papers/{created['id']}/copy").json()
        self.assertEqual(clone["status"], PAPER_STATUS_DRAFT)
        self.assertIsNone(clone["code"])
        self.assertNotEqual(clone["code"], published["code"])
        # 原卷不受影响
        original = self.detail(created["id"])
        self.assertEqual(original["status"], PAPER_STATUS_PUBLISHED)
        self.assertEqual(original["code"], published["code"])

    def test_copy_can_be_published_independently(self):
        created = self.create_paper({"count": 4})
        original_code = self.publish(created["id"]).json()["code"]
        clone = self.client.post(f"/api/papers/{created['id']}/copy").json()

        new_code = self.publish(clone["id"]).json()["code"]
        self.assertNotEqual(new_code, original_code)
        self.assertEqual(self.detail(clone["id"])["code"], new_code)
        self.assertEqual(self.detail(created["id"])["code"], original_code)

    def test_draft_has_no_code_until_published(self):
        created = self.create_paper({"count": 2})
        with self.session() as db:
            row = get_paper_or_404(db, created["id"])
            self.assertIsNone(row.code)
            self.assertEqual(row.status, PAPER_STATUS_DRAFT)
            self.assertIsNone(row.published_at)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
