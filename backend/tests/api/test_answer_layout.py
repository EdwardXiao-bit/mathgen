"""答案版形式（M3 第六维，契约 §5.1 `AnswerLayoutIn` / D53③）。

覆盖：
- 新卷落 D20 默认（四项全 true），并在**数据库**里可读到
- `PATCH /answer-layout` 平铺入参 → 回显 + 落库 + `PaperDetail.answer_layout` 反映
- 部分字段的请求体按**整体替换**语义（与 `/layout` 一致），未给的字段回落契约默认
- 已发布卷 → 409 `PAPER_NOT_EDITABLE`；卷不存在 → 404；字段类型错 → 422
- 另存副本保留答案版形式；发布不改动它
- `SheetContext.answer_layout` 随卷携带（渲染器据此决定答案版形态）
"""
from __future__ import annotations

import json
import unittest

from app.contracts.api import ROUTES
from app.models import Paper
from tests.api._support import ApiTestCase, render_with, url_of

D20_DEFAULT = {
    "separate_page": True,
    "include_steps": True,
    "include_difficulty": True,
    "include_tags": True,
}


class TestAnswerLayout(ApiTestCase):
    def test_new_paper_uses_d20_default(self):
        created = self.create_paper({"count": 2})
        self.assertEqual(created["answer_layout"], D20_DEFAULT)

    def test_default_is_persisted_in_database(self):
        created = self.create_paper({"count": 2})
        with self.session() as db:
            row = db.get(Paper, created["id"])
            stored = json.loads(row.answer_layout)
        self.assertEqual(stored, D20_DEFAULT, "M3 起答案版形式要真的落库，不是内存默认值")

    def test_patch_echoes_persists_and_shows_in_detail(self):
        created = self.create_paper({"count": 3})
        payload = {
            "separate_page": False,
            "include_steps": False,
            "include_difficulty": True,
            "include_tags": False,
        }
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), payload)

        self.assertEqual(self.detail(created["id"])["answer_layout"], payload)
        with self.session() as db:
            stored = json.loads(db.get(Paper, created["id"]).answer_layout)
        self.assertEqual(stored, payload)

    def test_patch_partial_body_replaces_with_contract_defaults(self):
        """PATCH 采用**整体替换**语义（与 `/layout` 一致）：没给的字段回落契约默认。"""
        created = self.create_paper({"count": 2})
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]),
            json={"separate_page": False},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {"separate_page": False, "include_steps": True,
             "include_difficulty": True, "include_tags": True},
        )

    def test_patch_is_idempotent(self):
        created = self.create_paper({"count": 2})
        payload = {"separate_page": True, "include_steps": False,
                   "include_difficulty": False, "include_tags": True}
        first = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        ).json()
        second = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        ).json()
        self.assertEqual(first, second)
        self.assertEqual(self.detail(created["id"])["answer_layout"], payload)

    def test_published_paper_is_409(self):
        created = self.create_paper({"count": 2})
        self.publish(created["id"])
        before = self.detail(created["id"])["answer_layout"]

        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]),
            json={"separate_page": False, "include_steps": False,
                  "include_difficulty": False, "include_tags": False},
        )
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["error"]["code"], "PAPER_NOT_EDITABLE")
        self.assertNotIn("detail", body)
        self.assertEqual(self.detail(created["id"])["answer_layout"], before)

    def test_unknown_paper_is_404(self):
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=999999), json=D20_DEFAULT
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_bad_field_type_is_422(self):
        created = self.create_paper({"count": 2})
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]),
            json={"separate_page": "maybe"},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_pydantic_lenient_bool_coercion_is_documented(self):
        """pydantic 宽松模式会把 "yes"/"no"/1/0 收成布尔（契约模型未声明 strict）。

        记录这个行为：前端如果传字符串开关也能work，但传别的词会 422。
        """
        created = self.create_paper({"count": 2})
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]),
            json={"separate_page": "no", "include_steps": 0,
                  "include_difficulty": 1, "include_tags": True},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {"separate_page": False, "include_steps": False,
             "include_difficulty": True, "include_tags": True},
        )

    def test_unknown_body_field_is_ignored_by_pydantic_default(self):
        """多送字段不应污染落库内容（pydantic 默认忽略未声明字段）。"""
        created = self.create_paper({"count": 2})
        response = self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]),
            json=dict(D20_DEFAULT, color="red"),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), D20_DEFAULT)
        with self.session() as db:
            self.assertEqual(json.loads(db.get(Paper, created["id"]).answer_layout),
                             D20_DEFAULT)

    def test_copy_preserves_answer_layout(self):
        created = self.create_paper({"count": 2})
        payload = {"separate_page": False, "include_steps": True,
                   "include_difficulty": False, "include_tags": False}
        self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        )
        clone = self.client.post(url_of("copy_paper", paper_id=created["id"])).json()
        self.assertEqual(clone["answer_layout"], payload)

    def test_publish_keeps_answer_layout(self):
        created = self.create_paper({"count": 2})
        payload = {"separate_page": False, "include_steps": False,
                   "include_difficulty": True, "include_tags": False}
        self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        )
        self.publish(created["id"])
        self.assertEqual(self.detail(created["id"])["answer_layout"], payload)

    def test_sheet_context_carries_answer_layout(self):
        """渲染器拿到的 `SheetContext.answer_layout` 必须是落库的那一份。"""
        created = self.create_paper({"count": 3})
        payload = {"separate_page": False, "include_steps": False,
                   "include_difficulty": False, "include_tags": True}
        self.client.patch(
            url_of("update_answer_layout", paper_id=created["id"]), json=payload
        )

        seen = []

        def fake_render(ctx):
            seen.append(ctx)
            return "<html>fake</html>"

        with render_with(fake_render):
            for mode in ("question", "answer"):
                response = self.client.get(
                    url_of("sheet", paper_id=created["id"]), params={"mode": mode}
                )
                self.assertEqual(response.status_code, 200, response.text)

        self.assertEqual(len(seen), 2)
        for ctx in seen:
            self.assertEqual(
                ctx.answer_layout.model_dump(), payload,
                "答案版形式必须随卷进入 SheetContext（HTML/PDF/Word 三条路同源）",
            )

    def test_answer_layout_snapshot_tolerates_legacy_placeholder(self):
        """M2 时期的 `{}` 占位必须按 D20 默认解释（老卷不需要迁移）。"""
        created = self.create_paper({"count": 2})
        with self.session() as db:
            row = db.get(Paper, created["id"])
            row.answer_layout = "{}"
            db.commit()
        self.assertEqual(self.detail(created["id"])["answer_layout"], D20_DEFAULT)

    def test_route_is_reachable_from_frozen_constant(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertIn(ROUTES["update_answer_layout"].replace("{paper_id}", "{paper_id}"), paths)
        self.assertIn("patch", paths["/api/papers/{paper_id}/answer-layout"])
        self.assertIn("/api/papers/by-code/{code}", paths)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
