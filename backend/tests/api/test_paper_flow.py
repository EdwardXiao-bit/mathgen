"""卷 API 主流程测试（契约 §5.1 / §6）：生成 → 详情 → 换一题 → 删题 → 排版 → 发布 → 被拒 → 另存副本。

这是 M2 后端最关键的「一条龙」——验收标准 2 的主干。
"""
from __future__ import annotations

import unittest

from tests.api._support import CONFIG_ADD_SUB, ApiTestCase, url_of


class TestPaperFlow(ApiTestCase):
    def test_create_paper_returns_frozen_schema(self):
        """创建返回 201，且字段名与冻结的 `PaperDetail` / `PaperItemOut` 逐字一致。"""
        detail = self.create_paper({"count": 5})

        self.assertEqual(
            set(detail),
            {"id", "code", "title", "status", "seed", "config", "layout",
             "created_at", "published_at", "items"},
        )
        self.assertEqual(detail["status"], "draft")
        self.assertIsNone(detail["code"])
        self.assertIsNone(detail["published_at"])
        self.assertEqual(detail["seed"], 2025)
        self.assertEqual(len(detail["items"]), 5)

        item = detail["items"][0]
        self.assertEqual(
            set(item),
            {"seq", "display", "expr", "answer", "difficulty", "tags", "steps", "origin"},
        )
        self.assertEqual(item["seq"], 1)
        self.assertEqual(item["origin"], "generated")
        self.assertIsInstance(item["tags"], list)
        self.assertIsInstance(item["steps"], list)
        self.assertGreaterEqual(item["difficulty"], 1)
        self.assertLessEqual(item["difficulty"], 5)

        # 时间必须是 ISO 8601 带时区
        self.assertRegex(detail["created_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}$")

    def test_config_round_trips_through_storage(self):
        """存储的 config 快照必须与入参等价（出入参字段名同源）。"""
        payload = {
            "ops": ["+", "-"], "ranges": ["R1", "R2"], "question_type": "oral",
            "max_value": None, "require_carry": False, "require_borrow": False,
            "exact_division": False, "allow_remainder": False, "parentheses": False,
            "count": 4,
        }
        detail = self.create_paper(payload)
        self.assertEqual(detail["config"], payload)

    def test_same_seed_yields_identical_paper(self):
        """同 (config, seed) 必得逐题相同的结果（FR-E8，快照入库前的一致性）。"""
        first = self.create_paper({"count": 6}, seed=777)
        second = self.create_paper({"count": 6}, seed=777)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            [item["expr"] for item in first["items"]],
            [item["expr"] for item in second["items"]],
        )

    def test_detail_matches_create_and_is_readable_by_id(self):
        created = self.create_paper({"count": 6})
        fetched = self.detail(created["id"])
        self.assertEqual(fetched, created)

    def test_list_papers_paginates(self):
        for _ in range(3):
            self.create_paper({"count": 2})

        response = self.client.get(url_of("list_papers"), params={"page": 1, "size": 2})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["papers"]), 2)
        self.assertEqual(
            set(body["papers"][0]),
            {"id", "code", "title", "status", "item_count", "created_at", "published_at"},
        )
        self.assertEqual(body["papers"][0]["item_count"], 2)

        page2 = self.client.get(url_of("list_papers"), params={"page": 2, "size": 2}).json()
        self.assertEqual(len(page2["papers"]), 1)

    def test_list_papers_filters_by_status(self):
        draft = self.create_paper({"count": 2})
        published = self.create_paper({"count": 2})
        self.publish(published["id"])

        only_published = self.client.get(
            url_of("list_papers"), params={"status": "published"}
        ).json()
        self.assertEqual(only_published["total"], 1)
        self.assertEqual(only_published["papers"][0]["id"], published["id"])
        self.assertEqual(only_published["papers"][0]["code"], self.detail(published["id"])["code"])

        only_draft = self.client.get(url_of("list_papers"), params={"status": "draft"}).json()
        self.assertEqual(only_draft["total"], 1)
        self.assertEqual(only_draft["papers"][0]["id"], draft["id"])

    def test_regenerate_replaces_only_target_item(self):
        """换一题：只有第 seq 题变，其余题逐字不变（FR-E9）。"""
        created = self.create_paper({"count": 6})
        before = created["items"]

        response = self.client.post(
            url_of("regenerate_item", paper_id=created["id"], seq=3)
        )
        self.assertEqual(response.status_code, 200, response.text)
        fresh = response.json()
        self.assertEqual(fresh["seq"], 3)
        self.assertEqual(fresh["origin"], "regenerated")

        after = self.detail(created["id"])["items"]
        self.assertEqual(len(after), 6)
        self.assertNotEqual(before[2], after[2])
        for index in (0, 1, 3, 4, 5):
            self.assertEqual(before[index], after[index], f"第 {index + 1} 题不应被改动")

    def test_delete_item_shifts_following_seqs(self):
        """删题后后续题号前移（契约 §6 冻结语义）。"""
        created = self.create_paper({"count": 5})
        before = created["items"]

        response = self.client.delete(url_of("delete_item", paper_id=created["id"], seq=2))
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()

        self.assertEqual([item["seq"] for item in items], [1, 2, 3, 4])
        # 第 3 题前移到 seq=2，内容逐字保留
        self.assertEqual(items[1]["display"], before[2]["display"])
        self.assertEqual(items[1]["expr"], before[2]["expr"])
        self.assertEqual(items[1]["answer"], before[2]["answer"])
        self.assertEqual(items[3]["display"], before[4]["display"])

    def test_delete_first_and_last_item(self):
        created = self.create_paper({"count": 4})
        first_removed = self.client.delete(url_of("delete_item", paper_id=created["id"], seq=1)).json()
        self.assertEqual([item["seq"] for item in first_removed], [1, 2, 3])

        last_removed = self.client.delete(url_of("delete_item", paper_id=created["id"], seq=3)).json()
        self.assertEqual([item["seq"] for item in last_removed], [1, 2])

    def test_layout_update_persists_every_field(self):
        created = self.create_paper({"count": 3})
        payload = {
            "cols_per_row": 3, "row_gap": 1.5, "font_size": 16,
            "page_margin": 2.5, "answer_line": False, "header_bar": False,
        }
        response = self.client.patch(
            url_of("update_layout", paper_id=created["id"]), json=payload
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), payload)
        self.assertEqual(self.detail(created["id"])["layout"], payload)

    def test_full_flow_then_edit_rejected_and_copy_editable(self):
        """验收标准 2 的完整链路：生成 → 换一题 → 删题 → 排版 → 发布 → 409 → 另存副本。"""
        created = self.create_paper({"count": 6})
        paper_id = created["id"]

        self.assertEqual(
            self.client.post(url_of("regenerate_item", paper_id=paper_id, seq=1)).status_code, 200
        )
        self.assertEqual(
            self.client.delete(url_of("delete_item", paper_id=paper_id, seq=2)).status_code, 200
        )
        layout = {"cols_per_row": 5, "row_gap": 1.2, "font_size": 15,
                  "page_margin": 1.5, "answer_line": True, "header_bar": True}
        self.assertEqual(
            self.client.patch(url_of("update_layout", paper_id=paper_id), json=layout).status_code,
            200,
        )

        published = self.publish(paper_id)
        self.assertEqual(published.status_code, 200, published.text)
        published_body = published.json()
        self.assertEqual(
            set(published_body), {"id", "code", "qr_url", "student_url"}
        )
        self.assertRegex(published_body["code"], r"^\d{4}-\d{4}$")
        # D54：student_url 必须是绝对 URL（由请求 Host 构造），qr_url 保持相对
        self.assertEqual(
            published_body["student_url"],
            f"http://testserver/student/paper/{published_body['code']}",
        )
        self.assertEqual(
            published_body["qr_url"], url_of("qr", paper_id=paper_id)
        )

        frozen = self.detail(paper_id)
        self.assertEqual(frozen["status"], "published")
        self.assertEqual(frozen["code"], published_body["code"])
        self.assertIsNotNone(frozen["published_at"])
        self.assertEqual(len(frozen["items"]), 5)
        self.assertEqual(frozen["layout"], layout)
        self.assertEqual(frozen["items"][0]["origin"], "regenerated")

        # 发布后任何编辑操作 → 409 PAPER_NOT_EDITABLE
        for response in (
            self.client.delete(url_of("delete_item", paper_id=paper_id, seq=1)),
            self.client.post(url_of("regenerate_item", paper_id=paper_id, seq=1)),
            self.client.patch(url_of("update_layout", paper_id=paper_id), json=layout),
        ):
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_EDITABLE")

        # 另存为可编辑副本：新 id、新 draft、无卷号、题目快照逐字一致
        copied = self.client.post(f"/api/papers/{paper_id}/copy")
        self.assertEqual(copied.status_code, 201, copied.text)
        clone = copied.json()
        self.assertNotEqual(clone["id"], paper_id)
        self.assertEqual(clone["status"], "draft")
        self.assertIsNone(clone["code"])
        self.assertIsNone(clone["published_at"])
        self.assertEqual(
            [(item["seq"], item["expr"], item["answer"]) for item in clone["items"]],
            [(item["seq"], item["expr"], item["answer"]) for item in frozen["items"]],
        )
        self.assertTrue(clone["title"].endswith("（副本）"))

        # 副本可继续编辑
        self.assertEqual(
            self.client.delete(url_of("delete_item", paper_id=clone["id"], seq=1)).status_code, 200
        )
        # 原卷完全不受影响
        self.assertEqual(self.detail(paper_id)["items"], frozen["items"])
        self.assertEqual(self.detail(paper_id)["code"], published_body["code"])

    def test_copy_preserves_layout_and_config(self):
        created = self.create_paper({"count": 3})
        layout = {"cols_per_row": 2, "row_gap": 2.0, "font_size": 20,
                  "page_margin": 3.0, "answer_line": False, "header_bar": False}
        self.client.patch(url_of("update_layout", paper_id=created["id"]), json=layout)

        clone = self.client.post(f"/api/papers/{created['id']}/copy").json()
        self.assertEqual(clone["layout"], layout)
        self.assertEqual(clone["config"], created["config"])
        self.assertEqual(clone["seed"], created["seed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
