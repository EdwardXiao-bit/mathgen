"""屏幕多页预览的分页几何属性 —— 与打印通道同源（M13）。

## 这块判据长自哪次事故

多页预览上线时，页缝只有 33px（应当 169px），一页纸的上下留白糊成一条细线。
根因是 `_pagination_attrs()` 里把 `LayoutParams.page_margin`（单位 **cm**）直接除了
`MM_PER_PX`，2cm 于是算成 7.56px（真值 75.59px）——**小了 10 倍**。

同一个 cm/mm 混用在本仓库两个方向都踩过（`content_width_px()` 把版心算宽 20%、
`page_of()` 把越过分页线的行算成没越过），所以这里不只断言「数字不等于 7.56」，
而是断言：**属性里的页边距必须等于 `_margin_mm()` 那一条换算**、且第 2 页可用高度
必须小于第 1 页（一个页边距的差额可见）。

判据只读渲染出来的 HTML 字符串，不依赖浏览器（与 `test_sheet_html.py` 同层）。
"""

from __future__ import annotations

import re
import unittest

from app.contracts.layout import LayoutParams
from app.layout.pagination import MEASURED_BREAK_CEILING_PX, MM_PER_PX, page_margin_px, usable_geometry
from app.render import render_sheet

from tests.render.fixtures import build_ctx

#: A4 高 297mm，按 96dpi 折成 CSS 像素（与打印通道同一套换算）。
A4_HEIGHT_PX = 297.0 / MM_PER_PX

#: 1cm = 10mm，毫米换 CSS 像素。
MM_PER_CM = 10.0


def attr(html: str, name: str) -> float:
    """取出 `<main class="sheet" data-…>` 上的一个数值属性。"""
    match = re.search(r'%s="(-?\d+(?:\.\d+)?)"' % re.escape(name), html)
    if match is None:
        raise AssertionError(f"卷面里没有 {name}（分页几何没写进 DOM）")
    return float(match.group(1))


class TestPaginationGeometryAttributes(unittest.TestCase):
    """契约：屏幕预览的分页几何必须由后端给出，且与打印同一套单位。"""

    def test_default_paper_has_all_three_attributes(self):
        html = render_sheet(build_ctx())
        for name in ("data-usable-first-px", "data-usable-later-px", "data-page-margin-px"):
            with self.subTest(name=name):
                self.assertGreater(attr(html, name), 0.0)

    def test_page_margin_is_centimetres_converted_to_pixels(self):
        """2cm 页边距 → 75.59px（不是 7.56px）。三种取值逐个对。"""
        for margin_cm in (1.0, 2.0, 3.0):
            want = margin_cm * MM_PER_CM / MM_PER_PX
            layout = LayoutParams(page_margin=margin_cm)
            html = render_sheet(build_ctx(layout=layout))
            with self.subTest(margin_cm=margin_cm):
                self.assertAlmostEqual(attr(html, "data-page-margin-px"), want, places=2)

    def test_page_margin_does_not_reproduce_the_ten_times_bug(self):
        """负对照：把 cm 当 mm 的旧算法（2cm → 7.56px）必须对不上。"""
        html = render_sheet(build_ctx(layout=LayoutParams(page_margin=2.0)))
        buggy = 2.0 / MM_PER_PX
        self.assertGreater(attr(html, "data-page-margin-px") - buggy, 60.0)

    def test_first_page_room_is_measured_and_later_pages_are_smaller(self):
        layout = LayoutParams()
        html = render_sheet(build_ctx(layout=layout))
        first = attr(html, "data-usable-first-px")
        later = attr(html, "data-usable-later-px")
        self.assertLess(later, first)
        self.assertGreater(first, later + 20.0)  # 差额至少一个页边距的几分之一
        self.assertGreater(first, 0.0)
        self.assertLess(first, A4_HEIGHT_PX)

    def test_first_page_room_uses_the_measured_table(self):
        """页边距命中实测表时，第 1 页可用高度就是那个实测值（唯一来源）。"""
        layout = LayoutParams(page_margin=2.0)
        want = MEASURED_BREAK_CEILING_PX[2.0]
        html = render_sheet(build_ctx(layout=layout))
        self.assertAlmostEqual(attr(html, "data-usable-first-px"), want, places=2)

    def test_attributes_match_usable_geometry(self):
        """属性值必须与 `usable_geometry()` 逐位相同（前端不再自己推一套）。"""
        for margin_cm in (1.0, 2.0, 2.5, 3.0):
            layout = LayoutParams(page_margin=margin_cm)
            first, later = usable_geometry(layout)
            html = render_sheet(build_ctx(layout=layout))
            with self.subTest(margin_cm=margin_cm):
                self.assertAlmostEqual(attr(html, "data-usable-first-px"), first, places=2)
                self.assertAlmostEqual(attr(html, "data-usable-later-px"), later, places=2)

    def test_answer_version_carries_the_same_geometry(self):
        """答案版也要带这套属性：屏幕预览对两种版本都要能切页。"""
        from app.contracts.sheet import SheetMode

        html = render_sheet(build_ctx(mode=SheetMode.ANSWER))
        self.assertAlmostEqual(attr(html, "data-page-margin-px"), page_margin_px(LayoutParams()), places=2)
        self.assertGreater(attr(html, "data-usable-first-px"), 0.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
