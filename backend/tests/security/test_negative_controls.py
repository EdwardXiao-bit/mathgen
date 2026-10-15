"""V9.1 负对照：守卫必须真的有牙——并且**写清它证明到哪、证明不到哪**。

任务书 (C) 的原话：用自造的响应对象喂同一个校验函数，证明的是「**校验函数能抓**」，
**不能**证明「**真实响应一定被抓**」。

因此本文件分两层，且每层都标明边界：

* `TestSyntheticNegativeControls` —— 自造对象喂守卫。证明「校验函数能抓」，并顺带证明
  「只查顶层的实现会放行嵌套越权」。**边界：合成对象的形状是我写的，不是产品产出的。**
* `TestNegativeControlOnRealProductPath` —— 让**真实产品管线**自己产出违规：
  只把 `app.services.student_service` 的**唯一投影入口**（`public_item`）或唯一出参装配点
  （`paper_payload`）替换掉，其余（真实 SQLite 里的 `paper_item` 快照、真实路由、真实
  `response_model`、真实 TestClient）全部不动。
  **边界：能证明「管线上真的出现越权时门禁会红」，但不能证明「任何未来缺陷都会被这支门禁抓到」**
  ——尤其是「投影多写键」，它会被 `response_model` 过滤掉，HTTP 层看不到（下面有实测）。
"""

from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

from tests.security._harness import (
    ARTIFACTS,
    SecurityHarness,
    create_paper,
    dump_evidence,
    publish_paper,
    record,
    student_paper_body,
)
from tests.security._whitelist import (
    STUDENT_PAPER_TOP_KEYS,
    STUDENT_PRACTICE_TOP_KEYS,
    WhitelistViolation,
    assert_guard_rejects,
    assert_student_payload,
    assert_top_keys,
)


class TestSyntheticNegativeControls(unittest.TestCase):
    """自造对象（边界见模块 docstring 第 1 条）。每条都要求守卫**指名**违规位置。"""

    BASE_ITEM = {"seq": 1, "display": "12+7", "question_type": "oral"}

    def _body(self, items: list[dict], **extra: object) -> dict:
        body: dict = {"paper_id": 1, "title": "t", "total": len(items), "items": items}
        body.update(extra)
        return body

    def test_item_with_extra_key_is_rejected(self) -> None:
        body = self._body([{**self.BASE_ITEM, "tags": ["加法"]}])
        reason = assert_guard_rejects(body, where="合成：items[0].tags", expected_items=1)
        self.assertIn("items[0]", reason)
        self.assertIn("tags", reason)

    def test_item_missing_a_whitelist_key_is_rejected(self) -> None:
        body = self._body([{"seq": 1, "display": "12+7"}])
        reason = assert_guard_rejects(body, where="合成：缺少 question_type", expected_items=1)
        self.assertIn("question_type", reason)

    def test_deeply_nested_answer_is_rejected(self) -> None:
        body = {
            "paper_id": 1,
            "title": "t",
            "total": 1,
            "items": [dict(self.BASE_ITEM)],
            "data": {"steps": [{"seq": 1, "answer": "19"}]},
        }
        reason = assert_guard_rejects(body, where="合成：data.steps[0].answer", expected_items=1)
        self.assertIn("data.steps[0]", reason)
        self.assertIn("answer", reason)

    def test_answer_next_to_item_in_a_sibling_container_is_rejected(self) -> None:
        body = self._body(
            [dict(self.BASE_ITEM)],
            meta={"questions": [{"seq": 1, "display": "12+7", "answer": "19"}]},
        )
        reason = assert_guard_rejects(body, where="合成：meta.questions[0].answer", expected_items=1)
        self.assertIn("meta.questions[0]", reason)

    def test_item_objects_are_found_even_without_an_items_container(self) -> None:
        """题目对象藏在非 `items` 键下也要被找到（识别靠「长得像题目」）。"""
        body = {
            "total": 1,
            "payload": {
                "questions": [
                    {"seq": 1, "display": "12+7", "question_type": "oral", "answer": "19"}
                ]
            },
        }
        reason = assert_guard_rejects(body, where="合成：payload.questions[0].answer")
        self.assertIn("answer", reason)

    def test_empty_or_itemless_response_fails_instead_of_passing_vacuously(self) -> None:
        for label, body in (
            ("完全没有 items", {"paper_id": 1, "title": "t", "total": 3}),
            ("items 是空列表", {"paper_id": 1, "title": "t", "total": 3, "items": []}),
        ):
            with self.subTest(body=label):
                reason = assert_guard_rejects(body, where=f"合成：{label}", expected_items=3)
                self.assertTrue(reason)

    def test_item_count_must_match_total(self) -> None:
        body = {"paper_id": 1, "title": "t", "total": 5, "items": [dict(self.BASE_ITEM)]}
        reason = assert_guard_rejects(body, where="合成：total=5 但只有 1 题")
        self.assertIn("total=5", reason)

    def test_non_object_item_is_rejected(self) -> None:
        body = {"paper_id": 1, "title": "t", "total": 1, "items": ["12+7"]}
        reason = assert_guard_rejects(body, where="合成：items[0] 是字符串", expected_items=1)
        self.assertIn("不是对象", reason)

    def test_top_level_field_table_is_enforced(self) -> None:
        body = {
            "paper_id": 1,
            "title": "t",
            "total": 1,
            "items": [dict(self.BASE_ITEM)],
            "secret": 1,
        }
        with self.assertRaises(WhitelistViolation) as caught:
            assert_top_keys(body, STUDENT_PAPER_TOP_KEYS, where="合成：顶层多一个 secret")
        self.assertIn("secret", str(caught.exception))
        with self.assertRaises(WhitelistViolation):
            assert_top_keys(
                {"seed": 1, "total": 0, "items": [], "paper_id": 1},
                STUDENT_PRACTICE_TOP_KEYS,
                where="合成：练习响应混入 paper_id",
            )
        record("synthetic_negative_controls", cases=10, result="全部按预期拒绝")


class TestNegativeControlOnRealProductPath(SecurityHarness):
    """V9.1：违规由**真实产品管线**产出，而不是我手写的 dict。"""

    COUNT = 4

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.paper_id = create_paper(cls.client, count=cls.COUNT, seed=4242)
        cls.code = publish_paper(cls.client, cls.paper_id)

    def _widen_public_item(self):
        """上下文管理器：把**唯一投影入口**换成「多写 tags / answer」的版本。

        `svc.public_item` 在 `with` 的参数里求值，所以拿到的是**未被替换**的原函数。
        """
        from app.services import student_service as svc

        real = svc.public_item

        def widened(seq, display, question_type):
            payload = real(seq, display, question_type)
            payload["tags"] = ["加法"]     # 越权键：真实投影多写出来的字段
            payload["answer"] = "19"
            return payload

        return mock.patch.object(svc, "public_item", widened)

    def _service_payload(self) -> dict:
        from app.services import student_service as svc

        with self.session() as db:
            return svc.paper_payload(db, self.code)

    def test_injection_really_widens_the_projection(self) -> None:
        """先证明注入生效——否则后面的「抓到」可能是假绿。"""
        with self._widen_public_item():
            payload = self._service_payload()
        self.assertEqual(payload["total"], self.COUNT)
        self.assertIn("tags", payload["items"][0], "注入没生效，负对照无意义")
        self.assertIn("answer", payload["items"][0])

    def test_service_layer_guard_catches_the_widened_projection(self) -> None:
        """真实服务层投影被改宽 → 守卫抓到（真实 DB 快照、真实服务代码路径）。"""
        with self._widen_public_item():
            payload = self._service_payload()
        reason = assert_guard_rejects(
            payload, where="真实服务层投影（注入 tags+answer）", expected_items=self.COUNT
        )
        self.assertIn("tags", reason)
        record("real_path_service_projection", violation=reason, result="守卫变红")

    def test_http_exit_is_filtered_so_the_body_stays_clean(self) -> None:
        """**诚实边界**：同一注入下 HTTP 响应体仍然干净——`response_model` 把模型外的键丢掉了。

        所以「只测 HTTP 响应体」查不出「投影多写键」这一类缺陷（除非同时改宽 wire 模型）。
        这条不是通过项，而是**边界证据**：报告里必须写出来，不能假装 HTTP 用例覆盖了它。
        """
        with self._widen_public_item():
            body = student_paper_body(self.client, self.code)
        raw = json.dumps(body, ensure_ascii=False)
        self.assertNotIn('"tags"', raw)
        self.assertNotIn('"answer"', raw)
        assert_student_payload(
            body, where="HTTP 响应体（注入被 response_model 过滤）", expected_items=self.COUNT
        )
        record(
            "real_path_http_boundary",
            injected=["tags", "answer"],
            http_body_item_keys=[sorted(item) for item in body["items"]],
            conclusion="response_model 过滤掉了越权键 → 只查 HTTP 体查不出『投影多写键』",
        )

    def test_layer_a_guard_catches_a_real_route_that_drops_an_item(self) -> None:
        """**真实 HTTP 路径**上的负对照：真路由少发一题，HTTP 层判据必须变红。

        注入 `paper_payload`（唯一出参装配点）让它丢掉最后一题但保留 `total`——
        这个响应**完全合法地穿过** `response_model`（每个键都在白名单内），
        只有「题目数 == total」这一条会抓到它。这就是 HTTP 层判据的牙。
        """
        from app.services import student_service as svc

        real = svc.paper_payload

        def dropping(db, code):
            payload = real(db, code)
            payload["items"] = payload["items"][:-1]
            return payload

        with mock.patch.object(svc, "paper_payload", dropping):
            body = student_paper_body(self.client, self.code)
        self.assertEqual(len(body["items"]), self.COUNT - 1, "注入没生效")
        reason = assert_guard_rejects(
            body,
            where="真实 HTTP 响应（路由少发一题）",
            expected_items=self.COUNT,
        )
        self.assertIn("只探测到", reason)
        record("real_path_http_dropped_item", violation=reason, result="HTTP 层判据变红")

    def test_http_shaped_leak_from_the_widened_projection_would_be_caught(self) -> None:
        """构造「如果出口模型没过滤会长什么样」：真实响应外壳 + 真实被改宽的题目对象。

        **边界**：这是构造对象（外壳取自真实响应，题目对象取自真实被改宽的投影），
        证明的是「HTTP 层判据遇到真正泄漏时会红」，不是「某个真实响应已泄漏」。
        """
        with self._widen_public_item():
            widened = self._service_payload()
            clean = student_paper_body(self.client, self.code)
        leaked = copy.deepcopy(clean)
        leaked["items"][0] = copy.deepcopy(widened["items"][0])
        reason = assert_guard_rejects(
            leaked, where="构造：真实外壳 + 真实被改宽的题目对象", expected_items=self.COUNT
        )
        self.assertIn("tags", reason)
        record("constructed_http_leak", violation=reason, result="HTTP 层判据会红")


def tearDownModule() -> None:
    path = dump_evidence(ARTIFACTS / "evidence-tests.json")
    print(f"\n[task-25] 证据落盘：{path}")
