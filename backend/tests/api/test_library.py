"""卷库检索测试（契约 §5.1 `GET /api/papers` + `by-code`，M3 / FR-T14）。

覆盖：
- `total` 是**过滤后的总数**，不受 `limit`/`offset` 影响
- `limit`/`offset` 边界（0 / 101 / 负数 → 422）
- `q` 标题模糊（含 LIKE 通配符转义）、`status` 过滤、两者组合
- 排序：新卷在前
- **M2 旧参数 `page`/`size` 已移除**（D64）：显式传 → 422，不静默返回第一页
- `GET /api/papers/by-code/{code}`：与按 id 取到的卷一致；卷号无效 → 404 `CODE_NOT_FOUND`
"""
from __future__ import annotations

import unittest

from tests.api._support import CONFIG_ADD_SUB, ApiTestCase, url_of


class TestPaperLibrary(ApiTestCase):
    # ---- 造数据 ---------------------------------------------------------

    def make(self, title: str, count: int = 2) -> dict:
        response = self.client.post(
            url_of("create_paper"),
            json=dict(CONFIG_ADD_SUB, count=count),
            params={"seed": 2025, "title": title},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def listing(self, **params) -> dict:
        response = self.client.get(url_of("list_papers"), params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ---- 分页 -----------------------------------------------------------

    def test_total_is_independent_of_pagination(self):
        for index in range(5):
            self.make(f"卷 {index}")

        first = self.listing(limit=2, offset=0)
        self.assertEqual(first["total"], 5)
        self.assertEqual(len(first["papers"]), 2)
        self.assertEqual((first["limit"], first["offset"]), (2, 0))

        middle = self.listing(limit=2, offset=2)
        self.assertEqual(middle["total"], 5)
        self.assertEqual(len(middle["papers"]), 2)

        tail = self.listing(limit=2, offset=4)
        self.assertEqual(tail["total"], 5)
        self.assertEqual(len(tail["papers"]), 1)

        beyond = self.listing(limit=2, offset=50)
        self.assertEqual(beyond["total"], 5, "越界 offset 也必须是同一 total")
        self.assertEqual(beyond["papers"], [])

    def test_offset_windows_do_not_overlap(self):
        for index in range(4):
            self.make(f"窗口卷 {index}")
        ids = [paper["id"] for paper in self.listing(limit=1, offset=0)["papers"]]
        ids += [paper["id"] for paper in self.listing(limit=2, offset=1)["papers"]]
        ids += [paper["id"] for paper in self.listing(limit=2, offset=3)["papers"]]
        self.assertEqual(len(ids), len(set(ids)), "不同 offset 不应返回同一张卷")

    def test_default_limit_is_20(self):
        body = self.listing()
        self.assertEqual(body["limit"], 20)
        self.assertEqual(body["offset"], 0)

    def test_limit_and_offset_bounds_are_422(self):
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            with self.subTest(params=params):
                response = self.client.get(url_of("list_papers"), params=params)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_legacy_page_size_are_rejected_not_aliased(self):
        """D64：M2 的 `page`/`size` 已移除，不得再作为别名生效。

        护栏的价值在于**不静默**：FastAPI 默认会忽略未知查询参数，
        那会让 `?page=2` 拿到第一页却以为翻页成功。
        """
        for index in range(5):
            self.make(f"迁移卷 {index}")

        for params in ({"size": 2}, {"page": 2}, {"page": 2, "size": 2},
                       {"limit": 3, "page": 2}, {"offset": 1, "size": 2}):
            with self.subTest(params=params):
                response = self.client.get(url_of("list_papers"), params=params)
                self.assertEqual(response.status_code, 422, response.text)
                body = response.json()
                self.assertEqual(body["error"]["code"], "VALIDATION_ERROR")
                self.assertIn("limit", body["error"]["message"])
                self.assertIn("offset", body["error"]["message"])

    def test_modern_paging_still_works_after_removal(self):
        for index in range(3):
            self.make(f"新分页卷 {index}")
        body = self.listing(limit=2, offset=2)
        self.assertEqual((body["limit"], body["offset"], body["total"]), (2, 2, 3))
        self.assertEqual(len(body["papers"]), 1)

    def test_legacy_params_marked_deprecated_in_openapi(self):
        """旧参数仍在 schema 里（这样护栏才有参数可校验），但标记 deprecated。"""
        params = self.client.get("/openapi.json").json()["paths"]["/api/papers"]["get"]["parameters"]
        by_name = {param["name"]: param for param in params}
        self.assertTrue(by_name["page"].get("deprecated"))
        self.assertTrue(by_name["size"].get("deprecated"))
        self.assertFalse(by_name["limit"].get("deprecated"))
        self.assertFalse(by_name["offset"].get("deprecated"))

    # ---- 过滤 -----------------------------------------------------------

    def test_filter_by_title_substring(self):
        self.make("口算练习 周一")
        self.make("口算练习 周二")
        self.make("脱式练习 周三")

        body = self.listing(q="口算")
        self.assertEqual(body["total"], 2)
        self.assertTrue(all("口算" in paper["title"] for paper in body["papers"]))

        self.assertEqual(self.listing(q="周三")["total"], 1)
        self.assertEqual(self.listing(q="不存在的关键字")["total"], 0)

    def test_q_filter_respects_pagination(self):
        for index in range(4):
            self.make(f"目标卷 {index}")
        self.make("别的卷")

        body = self.listing(q="目标卷", limit=3, offset=0)
        self.assertEqual(body["total"], 4)
        self.assertEqual(len(body["papers"]), 3)

    def test_blank_q_returns_everything(self):
        self.make("空白查询 A")
        self.make("空白查询 B")
        self.assertEqual(self.listing(q="   ")["total"], 2)

    def test_q_escapes_like_wildcards(self):
        """老师搜「50%」时，`%` 必须按字面匹配，不能变成「匹配一切」。"""
        self.make("正确率 50% 专项")
        self.make("普通卷")

        body = self.listing(q="50%")
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["papers"][0]["title"], "正确率 50% 专项")

        underscore = self.listing(q="_")
        self.assertEqual(underscore["total"], 0, "下划线不得被当成单字符通配符")

    def test_filter_by_status_and_combination(self):
        draft_a = self.make("状态卷 草稿一")
        draft_b = self.make("状态卷 草稿二")
        published = self.make("状态卷 已发")
        self.publish(published["id"])

        self.assertEqual(self.listing(status="draft")["total"], 2)
        self.assertEqual(self.listing(status="published")["total"], 1)

        combined = self.listing(status="draft", q="草稿二")
        self.assertEqual(combined["total"], 1)
        self.assertEqual(combined["papers"][0]["id"], draft_b["id"])

        self.assertEqual([p["id"] for p in self.listing(q="状态卷")["papers"]],
                         sorted([draft_a["id"], draft_b["id"], published["id"]], reverse=True))

    def test_invalid_status_is_422(self):
        response = self.client.get(url_of("list_papers"), params={"status": "archived"})
        self.assertEqual(response.status_code, 422, response.text)

    def test_newest_paper_comes_first(self):
        first = self.make("排序卷 一")
        second = self.make("排序卷 二")
        third = self.make("排序卷 三")

        ids = [paper["id"] for paper in self.listing(limit=10)["papers"]]
        self.assertEqual(ids, [third["id"], second["id"], first["id"]])


class TestGetPaperByCode(ApiTestCase):
    def test_by_code_returns_same_paper_as_by_id(self):
        created = self.create_paper({"count": 3})
        self.publish(created["id"])
        code = self.detail(created["id"])["code"]
        self.assertRegex(code, r"^\d{4}-\d{4}$")

        response = self.client.get(url_of("get_paper_by_code", code=code))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body, self.detail(created["id"]))
        self.assertEqual(body["code"], code)
        self.assertEqual(len(body["items"]), 3)

    def test_unknown_code_is_404_code_not_found(self):
        """契约 §9：**卷号无效**用 `CODE_NOT_FOUND`（与按 id 的 `PAPER_NOT_FOUND` 区分）。"""
        for code in ("2599-0001", "not-a-code", "0000-0000"):
            with self.subTest(code=code):
                response = self.client.get(url_of("get_paper_by_code", code=code))
                self.assertEqual(response.status_code, 404, response.text)
                body = response.json()
                self.assertEqual(set(body), {"error"})
                self.assertEqual(body["error"]["code"], "CODE_NOT_FOUND")
                self.assertNotIn("detail", body)

    def test_by_id_keeps_paper_not_found(self):
        """两条路径的错误码不可混用：按 id → PAPER_NOT_FOUND。"""
        response = self.client.get(url_of("get_paper", paper_id=999999))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_draft_paper_cannot_be_found_by_code(self):
        """draft 没有卷号，所以任何卷号都打不开它 —— 这是正确语义，不是缺陷。"""
        created = self.create_paper({"count": 2})
        self.assertIsNone(created["code"])
        response = self.client.get(url_of("get_paper_by_code", code="2510-0001"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "CODE_NOT_FOUND")

    def test_code_is_not_confused_with_paper_id_route(self):
        """`by-code` 与 `/{paper_id}` 的形状不同，不会互相抢路由。"""
        created = self.create_paper({"count": 2})
        self.assertEqual(self.detail(created["id"])["id"], created["id"])
        self.assertEqual(
            self.client.get(url_of("get_paper_by_code", code="2599-9999")).status_code, 404
        )
        # 非数字 id → 路径参数校验失败
        self.assertEqual(self.client.get("/api/papers/not-a-number").status_code, 422)

    def test_by_code_after_copy_opens_the_original(self):
        created = self.create_paper({"count": 2})
        code = self.publish(created["id"]).json()["code"]
        clone = self.client.post(url_of("copy_paper", paper_id=created["id"])).json()

        opened = self.client.get(url_of("get_paper_by_code", code=code)).json()
        self.assertEqual(opened["id"], created["id"])
        self.assertNotEqual(opened["id"], clone["id"])
        self.assertIsNone(clone["code"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
