"""卷面 HTML 渲染测试 —— 不依赖浏览器（纯字符串/树断言）。

对应验收标准：契约 1（完整文档、无外部资源）、契约 2（六个参数注入 CSS 变量）、
契约 3（QUESTION 不含 answer / steps）、契约 4（ANSWER 单独成页）、契约 5（确定性）。
"""
from __future__ import annotations

import re
import unittest

from app.contracts.layout import CSS_VAR_NAMES, LayoutParams, css_variables
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.render import SheetRenderError, render_sheet, render_with_layout

from tests.render.fixtures import (
    ANSWER_CLASSES,
    build_ctx,
    css_rule,
    parse_html,
    style_block,
)


class TestDocumentShape(unittest.TestCase):
    """渲染契约 1：返回**完整 HTML 文档**，且不依赖任何外部资源。"""

    def test_returns_complete_html_document(self):
        html = render_sheet(build_ctx())
        for marker in ("<!DOCTYPE html>", '<html lang="zh-CN">', "<head>", "<style>", "</style>", "<body", "</html>"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertTrue(html.endswith("</html>\n"))

    def test_declares_utf8_and_viewport(self):
        html = render_sheet(build_ctx())
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn("width=device-width", html)

    def test_has_no_external_resource_reference(self):
        """NFR-9：离线可用——不得出现 http(s) 链接、CDN、在线字体。"""
        for mode in (SheetMode.QUESTION, SheetMode.ANSWER):
            with self.subTest(mode=mode):
                html = render_sheet(build_ctx(mode=mode))
                self.assertNotIn("http://", html)
                self.assertNotIn("https://", html)
                self.assertNotIn("//cdn", html)
                self.assertNotIn("@import", html)

    def test_no_src_or_href_attributes_at_all(self):
        """更严的断言：连 `src=` / `href=` 都不出现（含相对路径的外部资源）。"""
        for mode in (SheetMode.QUESTION, SheetMode.ANSWER):
            with self.subTest(mode=mode):
                root = parse_html(render_sheet(build_ctx(mode=mode)))
                offenders = [
                    f"<{node.tag} {node.attrs}>"
                    for node in root.walk()
                    if "src" in node.attrs or "href" in node.attrs
                ]
                self.assertEqual(offenders, [])

    def test_escapes_user_supplied_text(self):
        """标题/题干/答案必须转义，不能把标签或属性注进去。"""
        item = SheetItem(
            seq=1,
            display='<script>alert("x")</script>',
            answer='"><img src=x>',
            tags=("<b>标签</b>",),
        )
        ctx = SheetContext(
            title="<h1>标题</h1>",
            mode=SheetMode.ANSWER,
            items=(item,),
            layout=LayoutParams(),
            code='1"><script>',
            header_note="<i>note</i>",
        )
        html = render_sheet(ctx)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)


class TestLayoutVariables(unittest.TestCase):
    """渲染契约 2：六个排版参数必须经 `css_variables()` 注入。"""

    def test_all_six_variables_present_with_exact_values(self):
        layout = LayoutParams(
            cols_per_row=3, row_gap=1.4, font_size=16,
            page_margin=1.5, answer_line=False, header_bar=False,
        )
        html = render_sheet(build_ctx(layout=layout))
        expected = css_variables(layout)
        style = style_block(html)
        for name in CSS_VAR_NAMES.values():
            with self.subTest(variable=name):
                self.assertIn(name, style)
        for name, value in expected.items():
            with self.subTest(variable=name):
                self.assertRegex(style, re.escape(f"{name}: {value}"))

    def test_variable_names_come_from_frozen_contract(self):
        """不得自造变量名：样式里出现的 `--xxx` 必须都在冻结清单内。"""
        html = render_sheet(build_ctx())
        style = style_block(html)
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", style))
        used = set(re.findall(r"var\((--[a-z0-9-]+)\)", style))
        allowed = set(CSS_VAR_NAMES.values()) | {"--mathgen-unused"}
        self.assertLessEqual(declared, allowed, f"自造的 CSS 变量：{declared - allowed}")
        self.assertLessEqual(used, allowed, f"引用了未知 CSS 变量：{used - allowed}")

    def test_each_layout_parameter_changes_its_own_css(self):
        """六个参数各自生效（逐项对照，不受其它参数干扰）。"""
        base = LayoutParams()
        cases = {
            "cols_per_row": (5, "--cols-per-row: 5"),
            "row_gap": (1.8, "--row-gap: 1.8"),
            "font_size": (18, "--font-size: 18pt"),
            "page_margin": (2.5, "--page-margin: 2.5cm"),
            # D59：布尔项输出 CSS 可直接当 display 用的关键字
            "answer_line": (False, "--answer-line-display: none"),
            "header_bar": (False, "--header-bar-display: none"),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(parameter=name):
                layout = LayoutParams(**{name: value})
                style = style_block(render_sheet(build_ctx(layout=layout)))
                self.assertIn(expected, style)
                baseline = style_block(render_sheet(build_ctx(layout=base)))
                self.assertNotEqual(style, baseline, f"{name} 改了但样式没变")

    def test_boolean_switches_are_consumed_as_display_values(self):
        """D59：两个布尔项必须被 `display: var(...)` 真正消费。

        第一版把它们写成 `--answer-line: 1|0`，样式表里**一条规则都没读**，
        隐藏改走渲染期烘死的 `data-*` 属性 —— 老师端面板改开关时预览无反应，
        而面板仍显示「已保存」。这条用例把「变量必须被消费」钉住。
        """
        for on, off in ((True, "--answer-line-display: block"), (False, "--answer-line-display: none")):
            with self.subTest(answer_line=on):
                style = style_block(render_sheet(build_ctx(layout=LayoutParams(answer_line=on))))
                self.assertIn("display: var(--answer-line-display, block)", style)
                self.assertIn(off, style)
        for on, off in ((True, "--header-bar-display: grid"), (False, "--header-bar-display: none")):
            with self.subTest(header_bar=on):
                style = style_block(render_sheet(build_ctx(layout=LayoutParams(header_bar=on))))
                self.assertIn("display: var(--header-bar-display, grid)", style)
                self.assertIn(off, style)

    def test_no_rendered_attribute_selectors_take_over_hiding(self):
        """隐藏职责必须只在 CSS 变量这一条通路上，不能再有 `data-*` 属性选择器。"""
        style = style_block(render_sheet(build_ctx(layout=LayoutParams(answer_line=False, header_bar=False))))
        self.assertNotIn('data-answer-line="0"', style)
        self.assertNotIn('data-header-bar="0"', style)

    def test_page_margin_is_consumed_by_screen_preview_box(self):
        """`--page-margin` 在屏幕上也要有可见效果（A4 纸面 + 内边距）。

        `@page` 只对打印生效，所以第一版里 `--page-margin` 在预览中是死变量（D59）。
        """
        style = style_block(render_sheet(build_ctx(layout=LayoutParams(page_margin=2.5))))
        self.assertIn("@media screen", style)
        preview = css_rule(style, ".sheet")
        self.assertIn("width: 210mm", style)
        self.assertRegex(style, r"padding:\s*var\(--page-margin\)")

    def test_page_rule_is_a4_with_margin(self):
        """A4 页面几何（NFR-5）。"""
        style = style_block(render_sheet(build_ctx(layout=LayoutParams(page_margin=2.5))))
        rule = css_rule(style, "@page")
        self.assertIn("size: A4", rule)
        self.assertRegex(rule, r"margin:\s*2\.5cm")

    def test_grid_columns_driven_by_variable(self):
        """每行题数由 CSS Grid + `--cols-per-row` 驱动（FR-T4）。"""
        style = style_block(render_sheet(build_ctx()))
        grid = css_rule(style, ".q-row-grid")
        self.assertIn("display: grid", grid)
        self.assertIn("repeat(var(--cols-per-row), 1fr)", grid)
        self.assertIn("row-gap: calc(var(--row-gap) * 1em)", grid)

    def test_row_and_item_have_break_inside_avoid(self):
        """NFR-5：题块与行容器都要 `break-inside: avoid`。"""
        style = style_block(render_sheet(build_ctx()))
        for selector in (".q-row", ".q-item", ".a-item"):
            with self.subTest(selector=selector):
                rule = css_rule(style, selector)
                self.assertIn("break-inside: avoid", rule)
                self.assertIn("page-break-inside: avoid", rule)

    def test_answer_section_breaks_to_new_page(self):
        """D20 / 契约 4：答案区 `break-before: page`。"""
        style = style_block(render_sheet(build_ctx(mode=SheetMode.ANSWER)))
        rule = css_rule(style, ".answer-section")
        self.assertIn("break-before: page", rule)
        self.assertIn("page-break-before: always", rule)

    def test_header_bar_switch_is_in_css(self):
        """卷头栏显隐由 `.header-bar` 自己的 `display` 读变量（D59）。"""
        style = style_block(render_sheet(build_ctx()))
        rule = css_rule(style, ".header-bar")
        self.assertIn("display: var(--header-bar-display, grid)", rule)

    def test_answer_line_switch_is_in_css(self):
        """答题横线显隐由 `.q-blank` 自己的 `display` 读变量（D59）。"""
        style = style_block(render_sheet(build_ctx()))
        rule = css_rule(style, ".q-blank")
        self.assertIn("display: var(--answer-line-display, block)", rule)

    def test_switched_off_elements_are_still_rendered(self):
        """元素**始终输出** —— 面板只覆盖 CSS 变量才能实时生效。

        第一版在 `answer_line=False` / `header_bar=False` 时干脆不输出元素，
        于是面板改变量永远看不到变化（那正是 D59 修的那个 bug）。
        """
        for layout, selectors in (
            (LayoutParams(answer_line=False), ("q-blank",)),
            (LayoutParams(header_bar=False), ("header-bar",)),
            (
                LayoutParams(answer_line=False, header_bar=False),
                ("q-blank", "header-bar"),
            ),
        ):
            root = parse_html(render_sheet(build_ctx(layout=layout)))
            for cls in selectors:
                with self.subTest(layout=layout, cls=cls):
                    self.assertTrue(root.find_all(cls=cls), f"{cls} 应当始终输出")


class TestQuestionModeSafety(unittest.TestCase):
    """渲染契约 3：`mode=QUESTION` **不得**出现 answer / steps（契约 §9 安全边界）。"""

    def test_no_answer_text_anywhere_in_dom(self):
        """递归遍历 HTML 树：文本、属性值、注释都不能带答案。"""
        ctx = build_ctx(count=10, mode=SheetMode.QUESTION)
        html = render_sheet(ctx)
        root = parse_html(html)
        for item in ctx.items:
            with self.subTest(seq=item.seq):
                self.assertNotIn(item.answer, root.own_text())
                self.assertNotIn(item.answer, " ".join(n.attr_text() for n in root.walk()))

    def test_no_step_text_anywhere_in_dom(self):
        ctx = build_ctx(count=6, mode=SheetMode.QUESTION)
        html = render_sheet(ctx)
        root = parse_html(html)
        for item in ctx.items:
            for step in item.steps:
                with self.subTest(seq=item.seq, step=step):
                    self.assertNotIn(step, root.own_text())

    def test_no_answer_only_class_is_rendered(self):
        """答案版专用 class 一个都不能出现。"""
        html = render_sheet(build_ctx(mode=SheetMode.QUESTION))
        root = parse_html(html)
        rendered = {cls for node in root.walk() for cls in node.classes}
        leaked = rendered.intersection(ANSWER_CLASSES)
        self.assertEqual(leaked, set(), f"题目版泄露了答案版结构：{sorted(leaked)}")

    def test_no_tag_text_or_difficulty_marker(self):
        ctx = build_ctx(count=5, mode=SheetMode.QUESTION, tag_text="独特考点标签ZZZ")
        root = parse_html(render_sheet(ctx))
        text = root.own_text()
        self.assertNotIn("独特考点标签ZZZ", text)
        self.assertNotIn("难度", text)

    def test_question_mode_has_no_answer_section_element(self):
        root = parse_html(render_sheet(build_ctx(mode=SheetMode.QUESTION)))
        self.assertEqual(root.find_all(cls="answer-section"), [])

    def test_answer_mode_does_contain_answers(self):
        """反向确认：同一条断言在 ANSWER 模式下**必须**失败（否则测试是空转的）。"""
        ctx = build_ctx(count=4, mode=SheetMode.ANSWER)
        text = parse_html(render_sheet(ctx)).own_text()
        for item in ctx.items:
            with self.subTest(seq=item.seq):
                self.assertIn(item.answer, text)

    def test_question_mode_keeps_question_content(self):
        """安全边界不能把题目内容也一起吞掉。"""
        ctx = build_ctx(count=4, mode=SheetMode.QUESTION)
        text = parse_html(render_sheet(ctx)).own_text()
        for item in ctx.items:
            with self.subTest(seq=item.seq):
                self.assertIn(item.display, text)


class TestAnswerMode(unittest.TestCase):
    """渲染契约 4：`mode=ANSWER` 含答案、难度、考点标签、逐步过程，且单独成页。"""

    def setUp(self):
        self.ctx = build_ctx(count=4, mode=SheetMode.ANSWER)
        self.html = render_sheet(self.ctx)
        self.root = parse_html(self.html)

    def test_contains_answer_difficulty_tags_and_steps(self):
        for item in self.ctx.items:
            with self.subTest(seq=item.seq):
                self.assertIn(item.answer, self.root.own_text())
                self.assertIn(f"难度 {item.difficulty}/5", self.root.own_text())
                for tag in item.tags:
                    self.assertIn(tag, self.root.own_text())
                for step in item.steps:
                    self.assertIn(step, self.root.own_text())

    def test_answer_items_carry_seq(self):
        items = self.root.find_all(cls="a-item")
        self.assertEqual([node.attrs.get("data-seq") for node in items],
                         [str(item.seq) for item in self.ctx.items])

    def test_steps_are_rendered_as_ordered_list(self):
        lists = self.root.find_all(tag="ol", cls="a-steps")
        self.assertEqual(len(lists), len(self.ctx.items))
        for node, item in zip(lists, self.ctx.items):
            self.assertEqual(len(node.find_all(tag="li")), len(item.steps))

    def test_answer_section_is_last_and_after_questions(self):
        """答案区必须在题目之后（配合 `break-before: page` 才能单独成页）。"""
        html = self.html
        self.assertIn('class="answer-section"', html)
        self.assertLess(html.index('class="q-row"'), html.index('class="answer-section"'))

    def test_answer_mode_still_includes_all_questions(self):
        text = self.root.own_text()
        for item in self.ctx.items:
            with self.subTest(seq=item.seq):
                self.assertIn(item.display, text)


class TestDeterminism(unittest.TestCase):
    """渲染契约 5：纯函数 —— 同一份 `SheetContext` 两次渲染完全一致。"""

    def test_render_is_byte_identical_across_calls(self):
        for mode in (SheetMode.QUESTION, SheetMode.ANSWER):
            ctx = build_ctx(count=12, mode=mode)
            with self.subTest(mode=mode):
                self.assertEqual(render_sheet(ctx), render_sheet(ctx))

    def test_equal_contexts_render_identically(self):
        self.assertEqual(render_sheet(build_ctx(count=7)), render_sheet(build_ctx(count=7)))

    def test_render_with_layout_matches_render_sheet(self):
        ctx = build_ctx(count=5)
        self.assertEqual(render_sheet(ctx), render_with_layout(ctx, ctx.layout))

    def test_render_does_not_mutate_input(self):
        ctx = build_ctx(count=5)
        before = (ctx.title, ctx.mode, ctx.items, ctx.layout, ctx.code, ctx.header_note)
        render_sheet(ctx)
        self.assertEqual(before, (ctx.title, ctx.mode, ctx.items, ctx.layout, ctx.code, ctx.header_note))

    def test_layout_fingerprint_changes_with_layout(self):
        a = style_block(render_sheet(build_ctx(layout=LayoutParams(cols_per_row=4))))
        b = style_block(render_sheet(build_ctx(layout=LayoutParams(cols_per_row=5))))
        self.assertNotEqual(a, b)


class TestRowChunking(unittest.TestCase):
    """每行题数决定 `.q-row` 的数量（FR-T4）。"""

    def test_row_count_follows_cols_per_row(self):
        for count, cols, expected in ((8, 4, 2), (9, 4, 3), (2, 6, 1), (12, 2, 6)):
            layout = LayoutParams(cols_per_row=cols)
            root = parse_html(render_sheet(build_ctx(count=count, layout=layout)))
            with self.subTest(count=count, cols=cols):
                self.assertEqual(len(root.find_all(cls="q-row")), expected)

    def test_question_order_is_preserved(self):
        ctx = build_ctx(count=9, layout=LayoutParams(cols_per_row=4))
        root = parse_html(render_sheet(ctx))
        rendered = [node.attrs.get("data-seq") for node in root.find_all(cls="q-item")]
        self.assertEqual(rendered, [str(item.seq) for item in ctx.items])


class TestEdgeCases(unittest.TestCase):
    def test_empty_sheet_renders_without_crashing(self):
        for mode in (SheetMode.QUESTION, SheetMode.ANSWER):
            ctx = build_ctx(count=0, mode=mode)
            with self.subTest(mode=mode):
                html = render_sheet(ctx)
                self.assertIn("<!DOCTYPE html>", html)
                self.assertIn("本卷暂无题目", html)

    def test_sheet_without_code_or_note(self):
        ctx = build_ctx(code=None, header_note="")
        html = render_sheet(ctx)
        self.assertNotIn("卷号", html)
        self.assertIn("sheet-title", html)

    def test_item_without_answer_fields_still_renders(self):
        ctx = SheetContext(
            title="t",
            mode=SheetMode.ANSWER,
            items=(SheetItem(seq=1, display="1 + 1 ="),),
            layout=LayoutParams(),
        )
        html = render_sheet(ctx)
        self.assertIn("1 + 1 =", html)

    def test_rejects_non_context_input(self):
        with self.assertRaises(SheetRenderError):
            render_sheet("not a ctx")  # type: ignore[arg-type]

    def test_rejects_non_item_in_items(self):
        ctx = SheetContext(title="t", mode=SheetMode.QUESTION, items=("nope",), layout=LayoutParams())  # type: ignore[arg-type]
        with self.assertRaises(SheetRenderError):
            render_sheet(ctx)

    def test_fill_in_blank_display_keeps_box(self):
        ctx = SheetContext(
            title="t",
            mode=SheetMode.QUESTION,
            items=(SheetItem(seq=1, display="12 + □ = 20"),),
            layout=LayoutParams(),
        )
        self.assertIn("□", render_sheet(ctx))


if __name__ == "__main__":
    unittest.main()
