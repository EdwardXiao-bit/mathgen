"""统一错误结构、非法入参与卷面路由测试（契约 §5.3 / §9；task-5 验收标准 2/4）。

覆盖：
- 所有错误出口都是 `{"error": {"code", "message"}}`，绝无 FastAPI 默认的 `{"detail": ...}`
- 非法排版参数（越界 / 类型错）→ 422，且不落库
- 非法出题参数 → 400 `CONFIG_INVALID`；请求体缺字段 → 422 `VALIDATION_ERROR`
- 未知卷 / 未知题号 → 404
- 渲染模块未就绪 → **503 `RENDER_NOT_READY` 而非 500**
- 渲染模块就绪后：卷面路由把快照组装成 `SheetContext` 交给渲染器，并返回 HTML
"""
from __future__ import annotations

import unittest

from app.contracts.sheet import SheetContext, SheetMode
from app.services.errors import ERROR_HTTP_STATUS, ApiError, http_status_for
from tests.api._support import (
    CONFIG_ADD_SUB,
    ApiTestCase,
    render_unavailable,
    render_with,
    url_of,
)


class TestErrorShape(ApiTestCase):
    def assert_error(self, response, status: int, code: str):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"error"}, body)
        self.assertEqual(set(body["error"]), {"code", "message"}, body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"].strip())
        self.assertNotIn("detail", body)
        return body["error"]

    def test_unknown_paper_is_404(self):
        self.assert_error(self.client.get(url_of("get_paper", paper_id=424242)), 404,
                          "PAPER_NOT_FOUND")

    def test_unknown_paper_on_publish_delete_regenerate_layout(self):
        paper_id = 987654
        cases = [
            self.client.post(url_of("publish_paper", paper_id=paper_id)),
            self.client.delete(url_of("delete_item", paper_id=paper_id, seq=1)),
            self.client.post(url_of("regenerate_item", paper_id=paper_id, seq=1)),
            self.client.patch(url_of("update_layout", paper_id=paper_id),
                              json={"cols_per_row": 4}),
            self.client.post(f"/api/papers/{paper_id}/copy"),
        ]
        for response in cases:
            self.assert_error(response, 404, "PAPER_NOT_FOUND")

    def test_unknown_seq_is_404_item_not_found(self):
        created = self.create_paper({"count": 3})
        self.assert_error(
            self.client.delete(url_of("delete_item", paper_id=created["id"], seq=99)),
            404, "ITEM_NOT_FOUND",
        )
        self.assert_error(
            self.client.post(url_of("regenerate_item", paper_id=created["id"], seq=99)),
            404, "ITEM_NOT_FOUND",
        )

    def test_config_invalid_is_400(self):
        bad = dict(CONFIG_ADD_SUB, exact_division=True, allow_remainder=True)
        response = self.client.post(url_of("create_paper"), json=bad)
        self.assert_error(response, 400, "CONFIG_INVALID")

    def test_duplicate_ops_rejected(self):
        bad = dict(CONFIG_ADD_SUB, ops=["+", "+"])
        self.assert_error(
            self.client.post(url_of("create_paper"), json=bad), 400, "CONFIG_INVALID"
        )

    def test_insufficient_questions_is_422(self):
        """约束组合出不满题量 → 422 `INSUFFICIENT_QUESTIONS`（真实引擎路径）。

        实测：20 以内加法 + require_carry=True 的可用题量不足 50，引擎在
        `MAX_ATTEMPTS_PER_QUESTION` 内终止并抛 `InsufficientQuestions`（约 8ms）。
        """
        bad = dict(CONFIG_ADD_SUB, ops=["+"], ranges=["R1"], require_carry=True, count=50)
        response = self.client.post(url_of("create_paper"), json=bad)
        self.assert_error(response, 422, "INSUFFICIENT_QUESTIONS")

    def test_stepwise_requires_two_ops_or_parentheses(self):
        bad = dict(CONFIG_ADD_SUB, question_type="stepwise", ops=["+"], parentheses=False)
        self.assert_error(
            self.client.post(url_of("create_paper"), json=bad), 400, "CONFIG_INVALID"
        )

    def test_missing_body_field_is_422_validation_error(self):
        response = self.client.post(url_of("create_paper"), json={"count": 5})
        self.assert_error(response, 422, "VALIDATION_ERROR")

    def test_bad_count_is_422_validation_error(self):
        bad = dict(CONFIG_ADD_SUB, count=0)
        self.assert_error(
            self.client.post(url_of("create_paper"), json=bad), 422, "VALIDATION_ERROR"
        )

    def test_bad_path_type_is_422(self):
        response = self.client.get("/api/papers/not-a-number")
        self.assert_error(response, 422, "VALIDATION_ERROR")

    def test_illegal_layout_is_rejected_for_each_field(self):
        created = self.create_paper({"count": 2})
        legal = {"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                 "page_margin": 2.0, "answer_line": True, "header_bar": True}
        illegal = [
            {"cols_per_row": 1}, {"cols_per_row": 7},
            {"row_gap": 0.5}, {"row_gap": 2.5},
            {"font_size": 9}, {"font_size": 21},
            {"page_margin": 0.9}, {"page_margin": 3.1},
        ]
        for patch in illegal:
            with self.subTest(patch=patch):
                body = dict(legal, **patch)
                response = self.client.patch(
                    url_of("update_layout", paper_id=created["id"]), json=body
                )
                self.assert_error(response, 422, "VALIDATION_ERROR")

        # 全部被拒后，库里仍是最初的合法排版
        self.assertEqual(self.detail(created["id"])["layout"], legal)

    def test_error_codes_cover_contract(self):
        """契约 §9 的错误码必须都在映射表里，且 HTTP 与契约一致。"""
        contract = {
            "CONFIG_INVALID": 400,
            "INSUFFICIENT_QUESTIONS": 422,
            "PAPER_NOT_FOUND": 404,
            "PAPER_NOT_EDITABLE": 409,
            "PAPER_NOT_PUBLISHED": 409,
            "CODE_NOT_FOUND": 404,
            "ATTEMPT_DUPLICATE": 200,
            "VALIDATION_ERROR": 422,
        }
        for code, status in contract.items():
            with self.subTest(code=code):
                self.assertEqual(http_status_for(code), status)

    def test_api_error_body_helper(self):
        error = ApiError("PAPER_NOT_EDITABLE", "已发布")
        self.assertEqual(error.status, 409)
        self.assertEqual(error.body(), {"error": {"code": "PAPER_NOT_EDITABLE", "message": "已发布"}})
        self.assertEqual(http_status_for("SOMETHING_ELSE"), 500)
        self.assertIn("RENDER_NOT_READY", ERROR_HTTP_STATUS)


class TestSheetRoute(ApiTestCase):
    def test_sheet_returns_503_when_render_missing(self):
        created = self.create_paper({"count": 3})
        with render_unavailable():
            response = self.client.get(url_of("sheet", paper_id=created["id"]))
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["error"]["code"], "RENDER_NOT_READY")
        self.assertEqual(set(response.json()), {"error"})

    def test_sheet_503_is_not_500_for_both_modes(self):
        created = self.create_paper({"count": 3})
        with render_unavailable():
            for mode in ("question", "answer"):
                with self.subTest(mode=mode):
                    response = self.client.get(
                        url_of("sheet", paper_id=created["id"]), params={"mode": mode}
                    )
                    self.assertEqual(response.status_code, 503, response.text)

    def test_sheet_404_beats_render_readiness(self):
        """卷不存在时必须是 404，不能被 503 掩盖（错误优先级）。"""
        with render_unavailable():
            response = self.client.get(url_of("sheet", paper_id=123456))
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"]["code"], "PAPER_NOT_FOUND")

    def test_sheet_bad_mode_is_422(self):
        created = self.create_paper({"count": 2})
        response = self.client.get(url_of("sheet", paper_id=created["id"]), params={"mode": "x"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")

    def test_sheet_passes_snapshot_context_to_renderer(self):
        created = self.create_paper({"count": 3})
        self.client.patch(
            url_of("update_layout", paper_id=created["id"]),
            json={"cols_per_row": 6, "row_gap": 2.0, "font_size": 10,
                  "page_margin": 1.0, "answer_line": False, "header_bar": False},
        )
        captured: list[SheetContext] = []

        def fake_render(ctx: SheetContext) -> str:
            captured.append(ctx)
            return f"<html><body>{len(ctx.items)} 题</body></html>"

        with render_with(fake_render):
            response = self.client.get(
                url_of("sheet", paper_id=created["id"]),
                params={"mode": "answer", "header_note": "限时 10 分钟"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertEqual(response.text, "<html><body>3 题</body></html>")

        self.assertEqual(len(captured), 1)
        ctx = captured[0]
        self.assertIsInstance(ctx, SheetContext)
        self.assertEqual(ctx.mode, SheetMode.ANSWER)
        self.assertEqual(ctx.title, created["title"])
        self.assertEqual(ctx.header_note, "限时 10 分钟")
        self.assertIsNone(ctx.code)
        self.assertEqual([item.seq for item in ctx.items], [1, 2, 3])
        self.assertEqual(ctx.items[0].display, created["items"][0]["display"])
        self.assertEqual(ctx.items[0].answer, created["items"][0]["answer"])
        self.assertEqual(ctx.layout.cols_per_row, 6)
        self.assertEqual(ctx.layout.row_gap, 2.0)
        self.assertEqual(ctx.layout.font_size, 10)
        self.assertEqual(ctx.layout.page_margin, 1.0)
        self.assertFalse(ctx.layout.answer_line)
        self.assertFalse(ctx.layout.header_bar)

    def test_sheet_question_mode_defaults_to_question(self):
        created = self.create_paper({"count": 2})
        seen: list[SheetMode] = []

        def fake_render(ctx: SheetContext) -> str:
            seen.append(ctx.mode)
            return "<html></html>"

        with render_with(fake_render):
            response = self.client.get(url_of("sheet", paper_id=created["id"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [SheetMode.QUESTION])

    def test_sheet_after_publish_carries_code(self):
        created = self.create_paper({"count": 2})
        code = self.publish(created["id"]).json()["code"]
        seen: list[SheetContext] = []

        with render_with(lambda ctx: seen.append(ctx) or "<html></html>"):
            self.client.get(url_of("sheet", paper_id=created["id"]))

        self.assertEqual(seen[0].code, code)

    def test_sheet_renders_real_html_when_render_is_ready(self):
        """与 task-6 的真实渲染器集成：一旦 `app.render.sheet` 就绪，必须真出 HTML。

        未就绪时跳过（此时 `test_sheet_returns_503_when_render_missing` 覆盖 503 路径）。
        """
        try:
            import app.render.sheet  # noqa: F401
        except ImportError:
            self.skipTest("app.render.sheet 尚未实现（task-6 进行中），503 路径已由其他用例覆盖")

        created = self.create_paper({"count": 4})
        question = self.client.get(url_of("sheet", paper_id=created["id"]))
        answer = self.client.get(
            url_of("sheet", paper_id=created["id"]), params={"mode": "answer"}
        )

        self.assertEqual(question.status_code, 200, question.text)
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertIn("text/html", question.headers["content-type"])
        self.assertIn("<!DOCTYPE html", question.text)
        self.assertIn("<!DOCTYPE html", answer.text)
        self.assertIn('data-mode="question"', question.text)
        self.assertIn('data-mode="answer"', answer.text)
        # 答案版必须真的给出答案（证明 mode 传导有效）
        self.assertIn(created["items"][0]["answer"], answer.text)
        self.assertGreater(len(answer.text), len(question.text))
        # 题目版不得含答案区（严格的安全契约测试归 task-8 / tests/security）
        self.assertNotIn("答案与解析", question.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
