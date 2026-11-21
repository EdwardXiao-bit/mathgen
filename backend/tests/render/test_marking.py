"""模式 C 卷面标记的判据（`app/render/marking.py`）。

测四件事：

1. checksum 是**确定性**的（同输入同值、16 位小写十六进制）；
2. checksum 对「会改变版面的输入」敏感（改题干、加题、改标题、改字号）；
3. checksum 对**答案不敏感**（题目版不因答案变化改变指纹，且规范化 JSON 里不出现答案）；
4. 二维码内容是规范化 JSON（无页号）、可被解析回原字段，PNG 是内联 `data:` URI 且确定。

负对照留在文件里：`test_negative_control_flipping_one_hex_char_changes_qr_text`
把 checksum 改一个字符，二维码文本必须不同——防止「二维码内容根本没带 checksum」
这种通过式实现。
"""
from __future__ import annotations

import base64
import json
import re
import unittest
from dataclasses import replace

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.render import marking
from app.render.sheet import (
    CORNER_INSET_MM,
    CORNER_SIZE_MM,
    QR_INSET_MM,
    render_sheet,
)

SAMPLE_LAYOUT = LayoutParams()
SAMPLE_ITEMS = (
    SheetItem(seq=1, display="85 + 14 = □"),
    SheetItem(seq=2, display="48 - 47 = □"),
    SheetItem(seq=3, display="3 × 12 = □"),
)


def make_ctx(**overrides) -> SheetContext:
    base = dict(
        title="模式C spike 样本",
        mode=SheetMode.QUESTION,
        items=SAMPLE_ITEMS,
        layout=SAMPLE_LAYOUT,
        code="2611-0001",
    )
    base.update(overrides)
    return SheetContext(**base)


class TestChecksum(unittest.TestCase):
    def test_is_stable_and_hex16(self):
        first = marking.sheet_checksum(make_ctx())
        second = marking.sheet_checksum(make_ctx())
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{16}$")

    def test_changes_when_item_text_changes(self):
        base = marking.sheet_checksum(make_ctx())
        changed = marking.sheet_checksum(
            make_ctx(items=(SAMPLE_ITEMS[0], replace(SAMPLE_ITEMS[1], display="48 - 46 = □"), SAMPLE_ITEMS[2]))
        )
        self.assertNotEqual(base, changed)

    def test_changes_when_item_added_or_removed(self):
        base = marking.sheet_checksum(make_ctx())
        added = marking.sheet_checksum(make_ctx(items=SAMPLE_ITEMS + (SheetItem(seq=4, display="9 ÷ 9 = □"),)))
        removed = marking.sheet_checksum(make_ctx(items=SAMPLE_ITEMS[:2]))
        self.assertNotEqual(base, added)
        self.assertNotEqual(base, removed)
        self.assertNotEqual(added, removed)

    def test_changes_when_title_or_code_changes(self):
        base = marking.sheet_checksum(make_ctx())
        self.assertNotEqual(base, marking.sheet_checksum(make_ctx(title="另一个标题")))
        self.assertNotEqual(base, marking.sheet_checksum(make_ctx(code="2611-0002")))
        self.assertNotEqual(base, marking.sheet_checksum(make_ctx(code=None)))

    def test_changes_when_layout_changes(self):
        base = marking.sheet_checksum(make_ctx())
        for layout in (
            replace(SAMPLE_LAYOUT, font_size=16),
            replace(SAMPLE_LAYOUT, page_margin=2.5),
            replace(SAMPLE_LAYOUT, cols_per_row=2),
            replace(SAMPLE_LAYOUT, answer_line=False),
            replace(SAMPLE_LAYOUT, row_gap=1.5),
        ):
            with self.subTest(layout=layout):
                self.assertNotEqual(base, marking.sheet_checksum(make_ctx(layout=layout)))

    def test_explicit_layout_argument_overrides_ctx_layout(self):
        ctx = make_ctx(layout=SAMPLE_LAYOUT)
        widened = replace(SAMPLE_LAYOUT, page_margin=3.0)
        self.assertEqual(
            marking.sheet_checksum(ctx, widened),
            marking.sheet_checksum(make_ctx(layout=widened)),
        )


class TestAnswersStayOut(unittest.TestCase):
    def test_checksum_ignores_answer_payload(self):
        """题目版不因答案变化改变指纹——指纹只描述「老师看到的那张卷子」。"""
        plain = SheetItem(seq=1, display="85 + 14 = □")
        with_answer = SheetItem(
            seq=1,
            display="85 + 14 = □",
            answer="99",
            steps=("85 + 14 = 99",),
            difficulty=2,
            tags=("进位",),
        )
        self.assertEqual(
            marking.sheet_checksum(make_ctx(items=(plain,))),
            marking.sheet_checksum(make_ctx(items=(with_answer,))),
        )

    def test_canonical_json_has_no_answer_text(self):
        item = SheetItem(seq=1, display="85 + 14 = □", answer="99", steps=("85 + 14 = 99",))
        text = marking.canonical_json(marking.canonical_payload(make_ctx(items=(item,))))
        self.assertNotIn("99", text)
        self.assertNotIn('"answer"', text)
        self.assertNotIn('"steps"', text)

    def test_canonical_json_sorts_keys(self):
        payload = {"b": 1, "a": {"z": 2, "y": 3}}
        self.assertEqual(marking.canonical_json(payload), '{"a":{"y":3,"z":2},"b":1}')


class TestQrPayload(unittest.TestCase):
    def setUp(self):
        self.checksum = marking.sheet_checksum(make_ctx())

    def test_roundtrip_fields(self):
        text = marking.qr_payload(1619, self.checksum, pages=2)
        data = json.loads(text)
        self.assertEqual(data["v"], marking.QR_FORMAT)
        self.assertEqual(data["paper"], 1619)
        self.assertEqual(data["checksum"], self.checksum)
        self.assertEqual(data["layout"], marking.LAYOUT_VERSION)
        self.assertEqual(data["pages"], 2)

    def test_has_no_page_field(self):
        """页号不进二维码：同一枚码要在每一页重复打印。"""
        data = json.loads(marking.qr_payload(1619, self.checksum))
        self.assertNotIn("page", data)
        self.assertNotIn("pages", data)

    def test_rejects_bad_checksum(self):
        for bad in ("", "ABC", "0" * 15, "g" * 16):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    marking.qr_payload(1619, bad)

    def test_accepts_string_paper_code(self):
        data = json.loads(marking.qr_payload("2611-0001", self.checksum))
        self.assertEqual(data["paper"], "2611-0001")

    def test_negative_control_flipping_one_hex_char_changes_qr_text(self):
        flipped = ("0" if self.checksum[0] != "0" else "1") + self.checksum[1:]
        self.assertNotEqual(
            marking.qr_payload(1619, self.checksum),
            marking.qr_payload(1619, flipped),
        )


class TestQrImage(unittest.TestCase):
    def test_data_uri_is_png_and_deterministic(self):
        text = marking.qr_payload(1619, "0123456789abcdef")
        uri = marking.qr_data_uri(text)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        raw = base64.b64decode(uri.split(",", 1)[1])
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(raw), 100)
        self.assertEqual(uri, marking.qr_data_uri(text))

    def test_different_paper_changes_image(self):
        one = marking.qr_data_uri(marking.qr_payload(1, "0123456789abcdef"))
        two = marking.qr_data_uri(marking.qr_payload(2, "0123456789abcdef"))
        self.assertNotEqual(one, two)

    def test_no_page_number_literal_in_payload_json(self):
        text = marking.qr_payload(1619, "0123456789abcdef")
        self.assertTrue(re.fullmatch(r'\{"checksum":"[0-9a-f]{16}","layout":"1","paper":1619,"v":1\}', text))


class TestQrSvg(unittest.TestCase):
    def test_svg_inline_no_external_ref(self):
        svg = marking.qr_svg(marking.qr_payload(1619, "0123456789abcdef"))
        self.assertTrue(svg.startswith('<svg class="sheet-qr"'))
        # 内联 SVG 不带 xmlns：HTML 解析器会按 SVG 命名空间处理，
        # 而带 `http://` 的 xmlns 会触发「无外部资源」判据（NFR-9）。
        self.assertNotIn("http://", svg)
        self.assertNotIn("xmlns", svg)
        self.assertIn('shape-rendering="crispEdges"', svg)
        self.assertIn("<path", svg)
        # 渲染契约第 1 条：不能出现外部资源位（data: 也不行）
        self.assertNotIn("src=", svg)
        self.assertNotIn("href=", svg)

    def test_svg_deterministic_and_different_per_paper(self):
        one = marking.qr_svg(marking.qr_payload(1, "0123456789abcdef"))
        self.assertEqual(one, marking.qr_svg(marking.qr_payload(1, "0123456789abcdef")))
        self.assertNotEqual(one, marking.qr_svg(marking.qr_payload(2, "0123456789abcdef")))

    def test_svg_size_has_quiet_zone(self):
        svg = marking.qr_svg(marking.qr_payload(1619, "0123456789abcdef"))
        match = re.search(r'viewBox="0 0 (\d+) \1"', svg)
        self.assertIsNotNone(match)
        modules = int(match.group(1))
        # 二维码模块数 = 17 + 4×版本；再加两侧各 4 模块静区，故 (n - 17) % 4 == 0 且 ≥ 29
        self.assertGreaterEqual(modules, 29)
        self.assertEqual((modules - 17) % 4, 0)
        self.assertIn(f'width="20.0mm" height="20.0mm"', svg)


class TestMarksPlacement(unittest.TestCase):
    """打印期标记的**位置**约束（D92 实测修正后钉住）。"""

    def test_corner_blocks_sit_close_to_the_paper_edge(self):
        """四角块距纸边 `CORNER_INSET_MM`，且不侵入常见打印机的不可打印区。"""
        html = render_sheet(make_ctx())
        for corner, edges in (("tl", ("top", "left")), ("tr", ("top", "right")),
                              ("bl", ("bottom", "left")), ("br", ("bottom", "right"))):
            with self.subTest(corner=corner):
                rule = re.search(rf"\.sheet-corner\.{corner} \{{([^}}]*)\}}", html)
                self.assertIsNotNone(rule, f"缺少 .sheet-corner.{corner} 规则")
                body = rule.group(1)
                for edge in edges:
                    self.assertIn(f"{edge}: {CORNER_INSET_MM:g}mm", body)
        # 6mm 是用户 2026-11-13 反馈「太偏内」后从 10mm 收进来的；再小会掉进
        # 多数喷墨/激光 3–5mm 的不可打印区，等于四角缺块。
        self.assertGreaterEqual(CORNER_INSET_MM, 5.0)
        self.assertLess(CORNER_INSET_MM, 10.0)

    def test_qr_clears_the_corner_block_band(self):
        """二维码必须让出角块带（inset + size），否则会把右下角块盖掉。

        实测证据：`right:10mm; bottom:10mm` 时渲染图里右下角块被二维码白底覆盖
        （`.tmp-rel10/qr-probe.png` 第 1 版），改成右移后四角块齐全。
        """
        html = render_sheet(make_ctx())
        match = re.search(r"\.sheet-qr-wrap \{[^}]*right:\s*([\d.]+)mm", html)
        self.assertIsNotNone(match, "打印期样式里找不到二维码定位规则")
        band_end = CORNER_INSET_MM + CORNER_SIZE_MM
        self.assertGreaterEqual(float(match.group(1)), band_end, "二维码距右边必须越过角块带")
        self.assertGreaterEqual(QR_INSET_MM, band_end)
        for corner in ("tl", "tr", "bl", "br"):
            with self.subTest(corner=corner):
                self.assertIn(f".sheet-corner.{corner}", html)

    def test_marks_are_print_only(self):
        """标记不能出现在屏幕预览里（否则挡住预览切页与灰缝）。"""
        html = render_sheet(make_ctx())
        marks = re.search(r"@media print \{(?P<body>.*?)\n\}", html, re.S)
        self.assertIsNotNone(marks)
        self.assertIn(".sheet-marks", marks.group("body"))
        screen = re.search(r"@media screen \{(?P<body>.*?)\n    \}", html, re.S)
        if screen is not None:
            self.assertNotIn(".sheet-corner", screen.group("body"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
