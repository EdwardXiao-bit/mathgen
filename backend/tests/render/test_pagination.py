"""分页几何与自动微调测试 —— 纯计算，不需要浏览器。

覆盖：行切分、A4 几何、溢出判定、跨页判定、`reflow` 自动微调、以及
`MEASURED_BREAK_CEILING_PX` 实测表的形状约束。
"""
from __future__ import annotations

import unittest

from app.contracts.layout import LayoutParams
from app.layout import (
    MEASURED_BREAK_CEILING_PX,
    MM_PER_PX,
)
from app.layout.pagination import (
    USABLE_FALLBACK_RATIO,
    PageMeasurement,
    detect_overflow,
    detect_page_crossing,
    geometry_for,
    measure_page,
    reflow,
    rows_for,
    usable_geometry,
)
from app.layout.styles import A4_HEIGHT_MM, A4_WIDTH_MM


def measurement(rows, doc_height=0.0, body_width=754.0):
    """构造一份 `measure_js` 形态的测量结果。"""
    return {
        "rows": [{"top": top, "height": height} for top, height in rows],
        "items": [],
        "docHeight": doc_height or (rows[-1][0] + rows[-1][1] if rows else 0.0),
        "bodyWidth": body_width,
        "styles": {},
    }


class TestGeometry(unittest.TestCase):
    def test_a4_constants(self):
        self.assertAlmostEqual(A4_WIDTH_MM, 210.0)
        self.assertAlmostEqual(A4_HEIGHT_MM, 297.0)

    def test_content_box_follows_margin(self):
        from app.layout.pagination import MM_PER_PX

        geometry = geometry_for(LayoutParams(page_margin=2.0))
        # `content_*_px` 是 CSS 像素；毫米换算按 96dpi（1mm = 96/25.4 px）。
        # 版心 = 纸张尺寸 - 2 x 页边距；这里直接用几何体里的 margin_mm 保持自洽。
        self.assertAlmostEqual(geometry.margin_mm, 2.0, places=6)
        self.assertAlmostEqual(
            geometry.content_width_px,
            (geometry.page_width_mm - 2 * geometry.margin_mm) / MM_PER_PX,
            places=6,
        )
        self.assertAlmostEqual(
            geometry.content_height_px,
            (geometry.page_height_mm - 2 * geometry.margin_mm) / MM_PER_PX,
            places=6,
        )

    def test_mm_per_px_is_96dpi(self):
        self.assertAlmostEqual(MM_PER_PX, 25.4 / 96.0, places=12)

    def test_rows_for_rounds_up(self):
        for count, cols, expected in ((0, 4, 0), (1, 4, 1), (4, 4, 1), (5, 4, 2), (12, 6, 2)):
            with self.subTest(count=count, cols=cols):
                self.assertEqual(rows_for(count, cols), expected)

    def test_rows_for_rejects_zero_columns(self):
        with self.assertRaises(ValueError):
            rows_for(10, 0)


class TestUsableGeometry(unittest.TestCase):
    """实测表与保守回退。"""

    def test_measured_table_covers_contract_margin_range(self):
        """排版契约的页边距范围 1.0..3.0cm，实测表必须全覆盖（0.1 步长）。"""
        covered = {round(m, 1) for m in MEASURED_BREAK_CEILING_PX}
        expected = {round(1.0 + 0.1 * i, 1) for i in range(21)}
        self.assertEqual(covered, expected)

    def test_measured_capacity_decreases_as_margin_grows(self):
        values = [MEASURED_BREAK_CEILING_PX[m] for m in sorted(MEASURED_BREAK_CEILING_PX)]
        self.assertEqual(values, sorted(values, reverse=True), "页边距越大，可用高度不应变大")

    def test_measured_capacity_is_never_larger_than_nominal(self):
        for margin, ceiling in MEASURED_BREAK_CEILING_PX.items():
            nominal = (A4_HEIGHT_MM - margin) / MM_PER_PX
            with self.subTest(margin=margin):
                self.assertLessEqual(ceiling, nominal + 1.0)

    def test_fallback_ratio_applied_outside_measured_range(self):
        layout = LayoutParams(page_margin=1.0)
        # 1.0 在实测表内 → 用实测值
        measured_first, _ = usable_geometry(layout)
        self.assertAlmostEqual(measured_first, MEASURED_BREAK_CEILING_PX[1.0], places=6)

    def test_second_and_later_pages_are_smaller_than_first(self):
        for margin in (1.0, 1.6, 2.0, 2.5, 3.0):
            first, later = usable_geometry(LayoutParams(page_margin=margin))
            with self.subTest(margin=margin):
                self.assertLess(later, first, "后续页要重新让出上下页边距")

    def test_fallback_ratio_is_documented_and_conservative(self):
        self.assertLess(USABLE_FALLBACK_RATIO, 1.0)
        self.assertGreater(USABLE_FALLBACK_RATIO, 0.5)


class TestOverflowDetection(unittest.TestCase):
    def test_flags_row_taller_than_a_page(self):
        layout = LayoutParams()
        first, _ = usable_geometry(layout)
        data = measurement([(10.0, 50.0), (80.0, first + 200.0)])
        overflow = detect_overflow(data, layout)
        self.assertEqual([item.row_index for item in overflow], [1])

    def test_accepts_rows_that_fit(self):
        layout = LayoutParams()
        first, _ = usable_geometry(layout)
        data = measurement([(10.0, 50.0), (80.0, first - 100.0)])
        self.assertEqual(detect_overflow(data, layout), ())

    def test_overflow_reports_how_much_it_exceeds(self):
        layout = LayoutParams()
        first, _ = usable_geometry(layout)
        data = measurement([(0.0, first + 50.0)])
        overflow = detect_overflow(data, layout)
        self.assertAlmostEqual(overflow[0].overflow_px, 50.0, places=3)


class TestPageCrossingDetection(unittest.TestCase):
    def test_row_inside_first_page_is_not_flagged(self):
        layout = LayoutParams(page_margin=2.0)
        first, _ = usable_geometry(layout)
        self.assertEqual(detect_page_crossing(measurement([(0.0, first - 20.0)]), layout), ())

    def test_row_starting_before_the_break_and_ending_after_is_flagged(self):
        """一行横跨分页线：起点在页内、终点越过分页线。"""
        layout = LayoutParams(page_margin=2.0)
        first, _ = usable_geometry(layout)
        data = measurement([(first - 10.0, 40.0)])
        crossings = detect_page_crossing(data, layout)
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].start_page, 1)
        self.assertEqual(crossings[0].end_page, 2)
        self.assertTrue(crossings[0].crosses)

    def test_row_entirely_on_second_page_is_not_flagged(self):
        layout = LayoutParams(page_margin=2.0)
        first, _ = usable_geometry(layout)
        data = measurement([(first + 5.0, 40.0)])
        self.assertEqual(detect_page_crossing(data, layout), ())

    def test_row_taller_than_a_page_is_flagged_across_pages(self):
        layout = LayoutParams(page_margin=2.0)
        first, later = usable_geometry(layout)
        data = measurement([(0.0, first + later)])
        crossings = detect_page_crossing(data, layout)
        self.assertTrue(crossings)
        self.assertGreater(crossings[0].end_page, crossings[0].start_page)

    def test_measured_first_page_override_is_honoured(self):
        layout = LayoutParams(page_margin=2.0)
        data = measurement([(0.0, 900.0)])
        self.assertEqual(detect_page_crossing(data, layout, usable_first_px=1044.0), ())
        self.assertTrue(detect_page_crossing(data, layout, usable_first_px=700.0))

    def test_safety_factor_makes_detection_stricter(self):
        """安全系数越小 → 判据越保守（更多行被判为跨界）。"""
        layout = LayoutParams(page_margin=2.0)
        data = measurement([(0.0, 900.0)])
        lenient = detect_page_crossing(data, layout, usable_first_px=930.0, safety_factor=1.0)
        strict = detect_page_crossing(data, layout, usable_first_px=930.0, safety_factor=0.5)
        self.assertEqual(lenient, ())
        self.assertTrue(strict)

    def test_accepts_page_measurement_object(self):
        layout = LayoutParams()
        page = measure_page(PageMeasurement.from_raw(measurement([(0.0, 10.0)])))
        self.assertEqual(page.rows[0]["height"], 10.0)
        self.assertEqual(detect_page_crossing(page, layout), ())

    def test_measure_page_passes_through(self):
        original = PageMeasurement(rows=(), items=(), doc_height=1.0, body_width=2.0, styles={})
        self.assertIs(measure_page(original), original)


class TestReflow(unittest.TestCase):
    """自动微调（`02-技术设计.md` §9.2 要求的回溯调整）。"""

    @staticmethod
    def _renderer(layout):
        return f"rows:{layout.row_gap}:{layout.font_size}:{layout.cols_per_row}:{layout.page_margin}"

    def test_returns_first_layout_when_everything_fits(self):
        layout = LayoutParams()
        height = usable_geometry(layout)[0] - 10
        result = reflow(self._renderer, lambda html: measurement([(0.0, height)]), layout)
        self.assertTrue(result.ok)
        self.assertEqual(result.layout, layout)
        self.assertEqual(result.attempts, 1)

    def test_shrinks_parameters_until_rows_fit(self):
        layout = LayoutParams(row_gap=2.0, font_size=20, cols_per_row=2, page_margin=3.0)

        def fake_measure(html):
            # 行高随字号与行距变化：字号越小、行距越小 → 行越矮
            _, row_gap, font_size, _cols, _margin = html.split(":")
            height = (float(font_size) * 3.0) + (float(row_gap) * 20.0)
            return measurement([(0.0, height)])

        result = reflow(self._renderer, fake_measure, layout, max_attempts=8)
        with self.subTest(diagnostics=result.diagnostics):
            self.assertTrue(result.ok, "自动微调应当把行压到一个版心之内")
        self.assertLess(result.attempts, 8)

    def test_gives_up_after_max_attempts_and_explains(self):
        layout = LayoutParams(page_margin=3.0)

        def always_too_tall(html):
            return measurement([(0.0, 100000.0)])

        result = reflow(self._renderer, always_too_tall, layout, max_attempts=3)
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 3)
        self.assertTrue(any("上限" in line for line in result.diagnostics))

    def test_diagnostics_are_recorded(self):
        layout = LayoutParams(row_gap=2.0, page_margin=3.0)
        calls = {"n": 0}

        def measure(html):
            calls["n"] += 1
            return measurement([(0.0, 5000.0 if calls["n"] < 3 else 10.0)])

        result = reflow(self._renderer, measure, layout, max_attempts=5)
        self.assertTrue(result.ok)
        self.assertTrue(result.diagnostics)

    def test_never_produces_out_of_bounds_layout(self):
        from app.contracts.layout import BOUNDS

        layout = LayoutParams(row_gap=0.8, font_size=10, cols_per_row=6, page_margin=1.0)

        def all_overflow(html):
            return measurement([(0.0, 100000.0)])

        result = reflow(self._renderer, all_overflow, layout, max_attempts=10)
        for name, (low, high) in BOUNDS.items():
            with self.subTest(parameter=name):
                value = getattr(result.layout, name)
                self.assertGreaterEqual(value, low)
                self.assertLessEqual(value, high)


if __name__ == "__main__":
    unittest.main()
