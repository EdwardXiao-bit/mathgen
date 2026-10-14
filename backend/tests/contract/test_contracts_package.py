"""跨模块冻结契约的守卫测试（对应 docs/04-接口契约.md §4 / §5.1）。

这些断言的作用是「改字段名或改 CSS 变量名就立刻失败」——M2 起，
卷 API、渲染导出、前端界面由不同智能体并行实现，本文件是它们共同的地基。

本文件由 Lead 维护。
"""
import dataclasses
import unittest

from app.contracts.api import (
    ROUTES,
    LayoutIn,
    PaperConfigIn,
    PaperDetail,
    PaperItemOut,
    PaperSummary,
    PublishResult,
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


class TestApiContract(unittest.TestCase):
    def test_route_paths_are_frozen(self):
        self.assertEqual(ROUTES, {
            "create_paper": "/api/papers",
            "list_papers": "/api/papers",
            "get_paper": "/api/papers/{paper_id}",
            "delete_item": "/api/papers/{paper_id}/items/{seq}",
            "regenerate_item": "/api/papers/{paper_id}/items/{seq}/regenerate",
            "update_layout": "/api/papers/{paper_id}/layout",
            "publish_paper": "/api/papers/{paper_id}/publish",
            "copy_paper": "/api/papers/{paper_id}/copy",
            "sheet": "/api/papers/{paper_id}/sheet",
            "export": "/api/papers/{paper_id}/export",
            "qr": "/api/papers/{paper_id}/qr",
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
             "created_at", "published_at", "items"],
        )
        self.assertEqual(
            list(PaperSummary.model_fields),
            ["id", "code", "title", "status", "item_count", "created_at", "published_at"],
        )

    def test_publish_result_field_names(self):
        self.assertEqual(list(PublishResult.model_fields),
                         ["id", "code", "qr_url", "student_url"])


if __name__ == "__main__":
    unittest.main()
