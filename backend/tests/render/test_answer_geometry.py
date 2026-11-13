"""答案框几何判据（`app/render/geometry.py` / 门禁 J-10-04）。

测四件事：

1. **覆盖**：题目版每题一条框，seq 齐全，坐标落在页内（0–1、原点左上）；
2. **可印**：框在版心内（回加页边距后 y 起点 ≥ 页边距、底边 ≤ 纸高 − 页边距）；
3. **与渲染同源**：`render_sheet_with_boxes()` 写进 HTML 的 `data-answer-box`
   与 `geometry_of()` 的数逐字一致，且去掉这两个属性后与 `render_sheet()` 逐字节相同
   （注入不改排版）；
4. **分页**：一处多页卷面的题被分到 page ≥ 2，页号单调、同页内阅读顺序单调。

负对照留在文件里：`test_negative_control_swapped_y_is_detected` 与
`test_negative_control_page_bounds_detected` 用**判据自己的校验函数**去挑改坏的几何，
必须报红——避免「校验函数永远返回空列表」这种通过式实现。
"""
from __future__ import annotations

import re
import unittest
from dataclasses import replace

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.layout.pagination import page_margin_px
from app.render import geometry
from app.render.marking import sheet_checksum
from app.render.sheet import render_sheet

BOX_ATTR_RE = re.compile(r'data-answer-box="(\d+,[0-9.]+,[0-9.]+,[0-9.]+,[0-9.]+)"')
PAGE_ATTR_RE = re.compile(r'data-seq="(\d+)" data-answer-box="[^"]*" data-page="(\d+)"')


def make_items(count: int) -> tuple[SheetItem, ...]:
    return tuple(
        SheetItem(seq=index + 1, display=f"{85 + index} + {14 + index} = □")
        for index in range(count)
    )


def make_ctx(count: int = 12, layout: LayoutParams | None = None, mode: SheetMode = SheetMode.QUESTION) -> SheetContext:
    return SheetContext(
        title="答案框几何样本",
        mode=mode,
        items=make_items(count),
        layout=layout or LayoutParams(),
        code="2611-0001",
    )


def page_bounds_problems(boxes, margin_ratio: float) -> list[str]:
    """框必须落在「版心」里：上边 ≥ 页边距，下边 ≤ 纸高 − 页边距。"""
    problems: list[str] = []
    for box in boxes:
        if box["x"] < -1e-6 or box["x"] + box["w"] > 1 + 1e-6:
            problems.append(f"题 {box['seq']} 横向越界：x={box['x']} w={box['w']}")
        if box["y"] < margin_ratio - 0.002:
            problems.append(f"题 {box['seq']} 上边进了页边距：y={box['y']}")
        if box["y"] + box["h"] > 1 - margin_ratio + 0.002:
            problems.append(f"题 {box['seq']} 下边越过版心底：y+h={box['y'] + box['h']}")
    return problems


def ordering_problems(boxes) -> list[str]:
    """seq 升序时页号不减；同页内 y 严格递增（同一行不同列的 y 相同，允许相等）。"""
    problems: list[str] = []
    ordered = sorted(boxes, key=lambda entry: entry["seq"])
    last_page = 0
    last_y: dict[int, float] = {}
    for box in ordered:
        if box["page"] < last_page:
            problems.append(f"题 {box['seq']} 页号回退：{box['page']} < {last_page}")
        last_page = box["page"]
        previous = last_y.get(box["page"])
        if previous is not None and box["y"] < previous - 0.002:
            problems.append(f"题 {box['seq']} 同页 y 回退：{box['y']} < {previous}")
        last_y[box["page"]] = max(last_y.get(box["page"], 0.0), box["y"])
    return problems


class GeometryCase(unittest.TestCase):
    def setUp(self) -> None:
        geometry.clear_geometry_cache()


class TestCoverage(GeometryCase):
    def test_every_item_has_one_box_inside_the_page(self):
        ctx = make_ctx(12)
        result = geometry.geometry_of(ctx)
        self.assertEqual(result["layout_version"], geometry.LAYOUT_VERSION)
        self.assertEqual(result["checksum"], sheet_checksum(ctx))
        self.assertEqual([box["seq"] for box in result["boxes"]], list(range(1, 13)))
        self.assertEqual(result["pages"], 1)
        for box in result["boxes"]:
            self.assertGreater(box["w"], 0)
            self.assertGreater(box["h"], 0)
            self.assertGreaterEqual(box["x"], -1e-6)
            self.assertLessEqual(box["x"] + box["w"], 1 + 1e-6)

    def test_boxes_stay_inside_the_printable_area(self):
        ctx = make_ctx(12)
        margin_ratio = page_margin_px(ctx.layout) / geometry.PAGE_HEIGHT_PX
        problems = page_bounds_problems(geometry.geometry_of(ctx)["boxes"], margin_ratio)
        self.assertEqual(problems, [])

    def test_reading_order_is_monotonic(self):
        result = geometry.geometry_of(make_ctx(24))
        self.assertEqual(ordering_problems(result["boxes"]), [])

    def test_three_layouts_stay_consistent(self):
        """J-10-04 的「≥3 种排版」：换列数 / 字号 / 页边距，覆盖与单调性都成立。"""
        layouts = {
            "默认": LayoutParams(),
            "窄栏": LayoutParams(cols_per_row=3, page_margin=1.0),
            "大字号": LayoutParams(cols_per_row=5, font_size=18, row_gap=1.5),
        }
        for name, layout in layouts.items():
            with self.subTest(layout=name):
                ctx = make_ctx(24, layout=layout)
                result = geometry.geometry_of(ctx)
                self.assertEqual([box["seq"] for box in result["boxes"]], list(range(1, 25)))
                self.assertEqual(ordering_problems(result["boxes"]), [])
                self.assertGreater(result["pages"], 0)

    def test_cached_geometry_is_measured_once(self):
        ctx = make_ctx(12)
        first = geometry.geometry_of(ctx)
        self.assertEqual(geometry.cached_geometry_count(), 1)
        second = geometry.geometry_of(ctx)
        self.assertEqual(first, second)
        self.assertEqual(geometry.cached_geometry_count(), 1)

    def test_answer_mode_has_no_boxes(self):
        with self.assertRaises(ValueError):
            geometry.geometry_of(make_ctx(6, mode=SheetMode.ANSWER))


class TestMultiPage(GeometryCase):
    def test_long_paper_splits_pages_and_numbers_them(self):
        ctx = make_ctx(64)
        result = geometry.geometry_of(ctx)
        self.assertGreaterEqual(result["pages"], 2)
        pages = {box["page"] for box in result["boxes"]}
        self.assertIn(1, pages)
        self.assertTrue(any(page >= 2 for page in pages))
        self.assertEqual(ordering_problems(result["boxes"]), [])

    def test_hidden_answer_line_falls_back_to_item_rect(self):
        ctx = make_ctx(6, layout=replace(LayoutParams(), answer_line=False))
        result = geometry.geometry_of(ctx)
        self.assertEqual(len(result["boxes"]), 6)
        self.assertTrue(all(box["h"] > 0 for box in result["boxes"]))


class TestInjection(GeometryCase):
    def test_marked_html_matches_geometry_and_keeps_layout(self):
        ctx = make_ctx(12)
        marked = geometry.render_sheet_with_boxes(ctx)
        plain = render_sheet(ctx)
        parsed = dict((int(seq), int(page)) for seq, page in PAGE_ATTR_RE.findall(marked))
        self.assertEqual(parsed, {box["seq"]: box["page"] for box in geometry.geometry_of(ctx)["boxes"]})
        attributes = BOX_ATTR_RE.findall(marked)
        self.assertEqual(len(attributes), 12)
        stripped = re.sub(r' data-answer-box="[^"]*" data-page="\d+"', "", marked)
        self.assertEqual(stripped, plain, "注入答案框不能改变除这两个属性以外的任何字节")

    def test_answer_mode_render_is_untouched(self):
        ctx = make_ctx(6, mode=SheetMode.ANSWER)
        self.assertEqual(geometry.render_sheet_with_boxes(ctx), render_sheet(ctx))


class TestNegativeControls(GeometryCase):
    def test_negative_control_swapped_y_is_detected(self):
        """把相隔一行的两题 y 对调（seq 保持不动）：顺序判据必须报红。

        注意两个坑（第一版都踩过）：① 同一行的两题 y 本来就相同，换了看不出来；
        ② 若连 seq 一起对调，那是一次「自洽的置换」，判据没有理由报红。
        """
        result = geometry.geometry_of(make_ctx(12))
        tampered = [dict(box) for box in result["boxes"]]
        first, later = tampered[0], tampered[4]
        first["y"], later["y"] = later["y"], first["y"]
        self.assertNotEqual(ordering_problems(tampered), [], "把相隔一行的两题 y 对调后，顺序判据必须报红")

    def test_negative_control_page_bounds_detected(self):
        result = geometry.geometry_of(make_ctx(12))
        tampered = [dict(box) for box in result["boxes"]]
        tampered[0]["y"] = 0.95
        margin_ratio = 2.0 / 29.7  # 默认 2cm 页边距
        self.assertNotEqual(page_bounds_problems(tampered, margin_ratio), [], "把框推到纸底外，边界判据必须报红")


if __name__ == "__main__":
    unittest.main()
