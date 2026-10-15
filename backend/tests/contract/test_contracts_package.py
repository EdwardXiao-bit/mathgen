"""跨模块冻结契约的守卫测试（对应 docs/04-接口契约.md §4 / §5.1）。

这些断言的作用是「改字段名或改 CSS 变量名就立刻失败」——M2 起，
卷 API、渲染导出、前端界面由不同智能体并行实现，本文件是它们共同的地基。

本文件由 Lead 维护。
"""
import dataclasses
import unittest

from app.contracts.api import (
    ROUTES,
    AnswerLayoutIn,
    AttemptDetailOut,
    AttemptItemOut,
    AttemptListOut,
    AttemptSummary,
    LayoutIn,
    PaperConfigIn,
    PaperDetail,
    PaperItemOut,
    PaperListOut,
    PaperSummary,
    PublishResult,
    score_of,
)
from app.contracts.layout import (
    BOUNDS,
    CSS_VAR_NAMES,
    LayoutParams,
    css_variables,
    validate_layout,
)
from app.contracts.sheet import SheetContext, SheetItem, SheetMode


class TestLayoutParamsContract(unittest.TestCase):
    def test_defaults_match_contract(self):
        lp = LayoutParams()
        self.assertEqual(lp.cols_per_row, 4)
        self.assertEqual(lp.row_gap, 1.0)
        self.assertEqual(lp.font_size, 14)
        self.assertEqual(lp.page_margin, 2.0)
        self.assertIs(lp.answer_line, True)
        self.assertIs(lp.header_bar, True)

    def test_six_fields_exactly(self):
        self.assertEqual(
            [f.name for f in dataclasses.fields(LayoutParams)],
            ["cols_per_row", "row_gap", "font_size", "page_margin",
             "answer_line", "header_bar"],
        )

    def test_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            LayoutParams().font_size = 99  # type: ignore[misc]

    def test_css_var_names_are_frozen(self):
        self.assertEqual(CSS_VAR_NAMES, {
            "cols_per_row": "--cols-per-row",
            "row_gap": "--row-gap",
            "font_size": "--font-size",
            "page_margin": "--page-margin",
            "answer_line": "--answer-line-display",
            "header_bar": "--header-bar-display",
        })

    def test_bounds(self):
        self.assertEqual(BOUNDS["cols_per_row"], (2, 6))
        self.assertEqual(BOUNDS["row_gap"], (0.8, 2.0))
        self.assertEqual(BOUNDS["font_size"], (10, 20))
        self.assertEqual(BOUNDS["page_margin"], (1.0, 3.0))

    def test_validate_layout_rejects_out_of_range(self):
        bad = [
            LayoutParams(cols_per_row=1), LayoutParams(cols_per_row=7),
            LayoutParams(row_gap=0.5), LayoutParams(row_gap=2.5),
            LayoutParams(font_size=9), LayoutParams(font_size=21),
            LayoutParams(page_margin=0.5), LayoutParams(page_margin=3.5),
        ]
        for lp in bad:
            with self.subTest(lp=lp):
                with self.assertRaises(ValueError):
                    validate_layout(lp)

    def test_validate_layout_accepts_defaults(self):
        validate_layout(LayoutParams())

    def test_css_variables_render_units_and_booleans(self):
        css = css_variables(LayoutParams(font_size=16, page_margin=1.5,
                                         answer_line=False, header_bar=True))
        self.assertEqual(css["--font-size"], "16pt")
        self.assertEqual(css["--page-margin"], "1.5cm")
        self.assertEqual(css["--cols-per-row"], "4")
        self.assertEqual(len(css), 6)

    def test_boolean_variables_carry_display_keywords(self):
        """D59：布尔项必须输出 CSS **可直接用作 display 的关键字**。

        原先输出 `1`/`0`，而样式表没有任何规则读取它们（隐藏走的是渲染期烘死的
        `data-*` 属性），导致老师端面板改这两个开关时预览毫无反应。
        """
        on = css_variables(LayoutParams(answer_line=True, header_bar=True))
        off = css_variables(LayoutParams(answer_line=False, header_bar=False))
        self.assertEqual(on["--answer-line-display"], "block")
        self.assertEqual(off["--answer-line-display"], "none")
        self.assertEqual(on["--header-bar-display"], "grid")
        self.assertEqual(off["--header-bar-display"], "none")
        for name, value in list(on.items()) + list(off.items()):
            if name.endswith("-display"):
                self.assertIn(value, ("block", "none", "grid", "flex"),
                              msg=f"{name} 的值必须是 display 关键字，实际 {value!r}")


class TestSheetContract(unittest.TestCase):
    def test_modes(self):
        self.assertEqual([m.value for m in SheetMode], ["question", "answer"])

    def test_sheet_item_defaults_hide_answer_fields(self):
        it = SheetItem(seq=1, display="7 + □ = 15")
        self.assertEqual(it.answer, "")
        self.assertEqual(it.steps, ())
        self.assertEqual(it.tags, ())
        self.assertEqual(it.difficulty, 0)

    def test_sheet_context_is_constructible_and_frozen(self):
        ctx = SheetContext(
            title="三位数乘两位数",
            mode=SheetMode.QUESTION,
            items=(SheetItem(seq=1, display="37 + 45 = ____"),),
            layout=LayoutParams(),
        )
        self.assertIsNone(ctx.code)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ctx.title = "改标题"  # type: ignore[misc]

    def test_sheet_context_carries_answer_layout(self):
        """M3 起渲染器要知道「答案版形式」，所以它必须是 SheetContext 的一部分。"""
        ctx = SheetContext(
            title="答案版形式",
            mode=SheetMode.ANSWER,
            items=(SheetItem(seq=1, display="7 + 8 = ____", answer="15"),),
            layout=LayoutParams(),
        )
        self.assertIn("answer_layout", [f.name for f in dataclasses.fields(SheetContext)])
        self.assertIs(ctx.answer_layout.separate_page, True)

    def test_answer_layout_default_is_not_shared(self):
        """默认值必须用 default_factory——pydantic 模型可变，共享实例会被串改。"""
        a = SheetContext(title="a", mode=SheetMode.ANSWER,
                         items=(SheetItem(seq=1, display="1+1"),), layout=LayoutParams())
        b = SheetContext(title="b", mode=SheetMode.ANSWER,
                         items=(SheetItem(seq=2, display="2+2"),), layout=LayoutParams())
        self.assertIsNot(a.answer_layout, b.answer_layout)
        a.answer_layout.include_steps = False
        self.assertIs(b.answer_layout.include_steps, True)


class TestApiContract(unittest.TestCase):
    def test_route_paths_are_frozen(self):
        self.assertEqual(ROUTES, {
            "create_paper": "/api/papers",
            "list_papers": "/api/papers",
            "get_paper": "/api/papers/{paper_id}",
            "get_paper_by_code": "/api/papers/by-code/{code}",
            "delete_item": "/api/papers/{paper_id}/items/{seq}",
            "regenerate_item": "/api/papers/{paper_id}/items/{seq}/regenerate",
            "update_layout": "/api/papers/{paper_id}/layout",
            "update_answer_layout": "/api/papers/{paper_id}/answer-layout",
            "publish_paper": "/api/papers/{paper_id}/publish",
            # ---- 卷库收纳（D78）----
            "archive_paper": "/api/papers/{paper_id}/archive",
            "unarchive_paper": "/api/papers/{paper_id}/unarchive",
            "delete_paper": "/api/papers/{paper_id}",
            "copy_paper": "/api/papers/{paper_id}/copy",
            "sheet": "/api/papers/{paper_id}/sheet",
            "export": "/api/papers/{paper_id}/export",
            "qr": "/api/papers/{paper_id}/qr",
            # ---- M4 学生端（D74 / D75 / D76）----
            "create_student": "/api/students",
            "student_paper": "/api/student/paper/{code}",
            "student_practice": "/api/student/practice",
            "submit_attempt": "/api/attempts",
            # ---- M5 成绩（FR-T11 / FR-T12 / FR-T13）----
            "export_attempts": "/api/attempts/export",
            "list_attempts": "/api/attempts",
            "get_attempt": "/api/attempts/{attempt_id}",
        })

    def test_item_out_exposes_answer_for_teacher(self):
        """老师端**包含** answer / steps（学生端必须剔除，契约 §9）。"""
        self.assertIn("answer", PaperItemOut.model_fields)
        self.assertIn("steps", PaperItemOut.model_fields)

    def test_paper_config_defaults(self):
        cfg = PaperConfigIn(ops=["+"], ranges=["R1"], question_type="oral")
        self.assertEqual(cfg.count, 20)
        self.assertIsNone(cfg.max_value)
        self.assertIs(cfg.exact_division, False)

    def test_layout_in_bounds_are_enforced_by_schema(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            LayoutIn(cols_per_row=9)

    def test_detail_and_summary_field_names(self):
        self.assertEqual(
            list(PaperDetail.model_fields),
            ["id", "code", "title", "status", "seed", "config", "layout",
             "answer_layout", "created_at", "published_at", "archived_at", "items"],
        )
        self.assertEqual(
            list(PaperSummary.model_fields),
            ["id", "code", "title", "status", "item_count", "created_at", "published_at",
             "archived_at", "has_attempts"],
        )
        self.assertEqual(
            list(PaperListOut.model_fields),
            ["total", "limit", "offset", "papers"],
        )

    def test_attempt_field_names(self):
        """M5 成绩三件套的字段名与顺序（前端按同一份写，改一个就红）。"""
        self.assertEqual(
            list(AttemptSummary.model_fields),
            ["id", "student_id", "student_name", "student_no",
             "paper_id", "paper_code", "paper_title",
             "total", "correct", "score", "duration_ms", "submitted_at"],
        )
        self.assertEqual(
            list(AttemptItemOut.model_fields),
            ["seq", "display", "question_type", "answer", "student_answer", "is_correct"],
        )
        self.assertEqual(
            list(AttemptListOut.model_fields),
            ["total", "limit", "offset", "attempts"],
        )
        # 明细 = 概要 + items，顺序也必须一致（继承字段排在最前）
        self.assertEqual(
            list(AttemptDetailOut.model_fields),
            list(AttemptSummary.model_fields) + ["items"],
        )

    def test_attempt_item_detail_carries_the_answer(self):
        """教师端明细**必须**含正确答案（FR-T12）——它和学生端的三键白名单（D74）不是一回事。"""
        self.assertIn("answer", AttemptItemOut.model_fields)
        self.assertIn("student_answer", AttemptItemOut.model_fields)

    def test_export_route_is_declared_before_the_id_route(self):
        """`/api/attempts/export` 必须排在 `/api/attempts/{attempt_id}` 之前。

        否则 `attempt_id: int` 解析不了 "export"，FastAPI 会直接 422 而不是继续匹配——
        字面量路由被参数路由吃掉。这条顺序是契约的一部分，不是实现细节。
        """
        keys = list(ROUTES)
        self.assertLess(keys.index("export_attempts"), keys.index("get_attempt"))

    def test_answer_layout_defaults_match_d20(self):
        """六维中的第六维（M3 起接入）：默认与 D20 一致——答案单独成页。"""
        al = AnswerLayoutIn()
        self.assertIs(al.separate_page, True)
        self.assertIs(al.include_steps, True)
        self.assertIs(al.include_difficulty, True)
        self.assertIs(al.include_tags, True)
        self.assertEqual(
            list(AnswerLayoutIn.model_fields),
            ["separate_page", "include_steps", "include_difficulty", "include_tags"],
        )

    def test_publish_result_field_names(self):
        self.assertEqual(list(PublishResult.model_fields),
                         ["id", "code", "qr_url", "student_url"])


class TestScoreRule(unittest.TestCase):
    """得分口径（D80）：`score_of` 是**唯一**实现，且必须与 M4 学生端的显示逐位一致。"""

    def test_is_exact_half_up_rounding(self):
        """精确 half-up：`.5` 一律进位，且**不经过浮点**（与实现是同一个整数公式，
        这里独立重写一遍是为了让「规则」本身而不是「某次算出来的数」被钉住）。"""
        for total in range(0, 61):
            for correct in range(0, total + 1):
                expected = (correct * 200 + total) // (2 * total) if total else 0
                self.assertEqual(score_of(total, correct), expected,
                                 f"total={total} correct={correct}")

    def test_half_a_point_always_rounds_up(self):
        self.assertEqual(score_of(40, 23), 58)   # 57.5 → 58
        self.assertEqual(score_of(80, 46), 58)   # 57.5 → 58
        self.assertEqual(score_of(8, 1), 13)     # 12.5 → 13
        self.assertEqual(score_of(40, 33), 83)   # 82.5 → 83

    def test_float_floor_x_plus_half_would_be_off_by_one_exactly_where_python_round_is(self):
        """旧学生端 JS 口径（`floor(x/t*100 + 0.5)`）与精确口径的差异**必须有名有姓**：
        在 `total ≤ 100`（真实题量上限）内只有 2 个点，且都是浮点把 `.5` 存小了。

        这条用例把「D80 改掉的到底是什么」写成可执行的记录——若哪天有人把实现换回
        浮点写法，它会立刻红，并直接指出是哪两个点。
        """
        import math
        divergent = [
            (total, correct)
            for total in range(1, 101)
            for correct in range(0, total + 1)
            if math.floor(correct / total * 100 + 0.5) != score_of(total, correct)
        ]
        self.assertEqual(divergent, [(40, 23), (80, 46)])

    def test_is_not_python_banker_rounding(self):
        """**这正是不能用内建 `round()` 的原因**：Python 的 round 是银行家舍入，
        在 .5 上会与 JS 差 1 分（1/8 → 12 而不是 13）。这条用例把这个差异钉住。"""
        self.assertEqual(score_of(8, 1), 13)
        self.assertNotEqual(score_of(8, 1), round(1 / 8 * 100))       # round(12.5) == 12
        self.assertEqual(score_of(40, 33), 83)
        self.assertNotEqual(score_of(40, 33), round(33 / 40 * 100))   # round(82.5) == 82

    def test_clamps_inconsistent_data_instead_of_going_over_100(self):
        self.assertEqual(score_of(10, 12), 100)
        self.assertEqual(score_of(10, -3), 0)
        self.assertEqual(score_of(0, 5), 0)
        self.assertEqual(score_of(0, 0), 0)

    def test_full_marks_and_zero(self):
        self.assertEqual(score_of(10, 10), 100)
        self.assertEqual(score_of(10, 0), 0)


if __name__ == "__main__":
    unittest.main()
