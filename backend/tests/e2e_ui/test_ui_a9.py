"""A9（预览 = 打印的等比缩放）独立验收 —— task-18 / T-UI-VER-B。

**核心思路**：只量预览只能证明「看起来像 A4」。本模块用一条**与实现无关的第二条路径**——
把同一份卷面导出 **PDF**，量 PDF 里的**相对几何**（首列左边界/纸宽、列间距/纸宽），
与**预览 iframe 内部**量到的同一比值比对。比值对缩放不敏感，因此能直接判定「预览 == 打印的等比缩放」。

在真实 A4 内容视口下：`210mm @96dpi = 793.7008px`；`2cm @96dpi = 75.5906px`（比值 0.095238）。

    & $VPY -m unittest discover -s backend/tests/e2e_ui -t backend -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_PYLIBS = os.path.join(os.path.dirname(HERE), "e2e_m2", ".pylibs")
if os.path.isdir(_PYLIBS):
    sys.path.insert(0, _PYLIBS)

from test_ui_structure import (  # noqa: E402
    ARTIFACTS,
    RealServer,
    browser_page,
    ensure_dist_fresh,
    parse_rules,
    STYLES,
)

A4_PX = 210 / 25.4 * 96            # 793.7008
PADDING_RATIO = 20 / 210           # 0.095238
#: 比值容差：纸宽的 1%（≈8px / ≈6pt）。**判据仍具区分力**——
#: 修前状态首列左边界比值是 0.1645，与打印的 0.0985 相差 0.066 ≫ 0.01（见正对照）。
RATIO_TOL = 0.01

PREVIEW_JS = r"""
() => {
  const sheet = document.querySelector('.sheet');
  const sr = sheet.getBoundingClientRect();
  const cs = getComputedStyle(sheet);
  const grid = document.querySelector('.q-row-grid');
  const items = Array.from(document.querySelectorAll('.q-item'));
  const rects = items.map(el => el.getBoundingClientRect());
  const inkHeight = (el) => {
    const range = document.createRange(); range.selectNodeContents(el);
    const rs = Array.from(range.getClientRects());
    if (!rs.length) return null;
    return Math.max(...rs.map(r => r.bottom)) - Math.min(...rs.map(r => r.top));
  };
  return {
    docWidth: document.documentElement.scrollWidth,
    sheetWidth: parseFloat(cs.width),
    sheetPaddingLeft: parseFloat(cs.paddingLeft),
    sheetPaddingRight: parseFloat(cs.paddingRight),
    sheetRect: {x: sr.x, y: sr.y, w: sr.width, h: sr.height},
    grid: grid ? {cols: getComputedStyle(grid).gridTemplateColumns.split(' ').map(parseFloat),
                  gap: parseFloat(getComputedStyle(grid).gap)} : null,
    itemCount: items.length,
    itemHeights: [...new Set(rects.map(r => Math.round(r.height * 100) / 100))],
    itemInkHeights: [...new Set(items.map(inkHeight).map(h => h && Math.round(h * 100) / 100))],
    itemOverflow: items.filter(el => el.scrollHeight > el.clientHeight + 1).length,
    firstItemX: rects.length ? rects[0].x : null,
    lastItemX: rects.length ? Math.max(...rects.map(r => r.right)) : null,
  };
}
"""

#: **外层**壳的量测：等比缩放画在 iframe 自身的 transform 上（`.mg-paper-frame > .mg-paper-stage
#: > .mg-paper-scaler > iframe`），所以必须在**父文档**里量，不能在 iframe 内量。
STAGE_JS = r"""
() => {
  const frame = document.getElementById('sheet-frame');
  const stage = document.querySelector('.mg-paper-stage');
  const cs = getComputedStyle(frame);
  let scaleX = null, scaleY = null;
  if (cs.transform && cs.transform.startsWith('matrix(')) {
    const parts = cs.transform.slice(7, -1).split(',').map(parseFloat);
    scaleX = parts[0]; scaleY = parts[3];
  }
  const fr = frame.getBoundingClientRect();
  return {
    transform: cs.transform, scaleX, scaleY,
    iframeClient: {w: frame.clientWidth, h: frame.clientHeight},
    iframeRect: {x: fr.x, y: fr.y, w: fr.width, h: fr.height},
    stage: stage ? {cls: stage.className,
                    focusWithin: stage.matches(':focus-within'),
                    outlineStyle: getComputedStyle(stage).outlineStyle,
                    outlineWidth: parseFloat(getComputedStyle(stage).outlineWidth) || 0,
                    rect: stage.getBoundingClientRect().toJSON()} : null,
  };
}
"""


def pdf_question_row(pdf_bytes: bytes) -> dict:
    """从 PDF 量**题目行**（恰好 4 列的那一行）的相对几何。"""
    import io

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        page = document[0]
        page_w, page_h = page.get_size()
        textpage = page.get_textpage()
        chars = []
        for index in range(textpage.count_chars()):
            char = textpage.get_text_range(index, 1)
            if not char.strip():
                continue
            left, bottom, right, top = textpage.get_charbox(index)
            chars.append({"ch": char, "x0": left, "x1": right, "y0": bottom, "y1": top})
        if not chars:
            raise AssertionError("PDF 第 1 页取不到任何字符（文本层为空？）")
        bands: dict[int, list] = {}
        for item in chars:
            bands.setdefault(round(item["y0"] / 3.0), []).append(item)
        rows = []
        for key in sorted(bands, reverse=True):
            row = sorted(bands[key], key=lambda c: c["x0"])
            clusters, current = [], [row[0]]
            for prev, item in zip(row, row[1:]):
                if item["x0"] - prev["x1"] > 25:
                    clusters.append(current)
                    current = [item]
                else:
                    current.append(item)
            clusters.append(current)
            xs = [round(c[0]["x0"], 2) for c in clusters]
            rows.append({"y": min(c["y0"] for c in row), "xs": xs,
                         "text": " ".join("".join(x["ch"] for x in c)[:18] for c in clusters)})
        four = [row for row in rows if len(row["xs"]) == 4]
        if not four:
            raise AssertionError(f"PDF 里找不到 4 列的题目行：{[r['xs'] for r in rows]}")
        top = four[0]
        return {
            "page_w": page_w, "page_h": page_h,
            "left_ratio": top["xs"][0] / page_w,
            "pitch_ratio": (top["xs"][1] - top["xs"][0]) / page_w,
            "right_edge_ratio": top["xs"][-1] / page_w,
            "row_text": top["text"],
            "all_rows": [r["xs"] for r in rows],
        }
    finally:
        document.close()


class A9Case(unittest.TestCase):
    server: RealServer

    @classmethod
    def setUpClass(cls):
        cls.build_note = ensure_dist_fresh()
        cls.server = RealServer()
        cls.server.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.server.__exit__(None, None, None)

    def make_paper(self) -> int:
        import httpx

        with httpx.Client(base_url=self.server.base_url, timeout=60.0) as client:
            paper = client.post("/api/papers", params={"seed": 20261015, "title": "A9 验收卷"},
                                json={"ops": ["+", "-"], "ranges": ["R2"],
                                      "question_type": "oral", "count": 12}).json()
        return paper["id"]

    def open_preview(self, page, pid: int):
        page.goto(f"{self.server.base_url}/teacher/paper/{pid}", wait_until="networkidle",
                  timeout=60_000)
        page.wait_for_selector("#sheet-frame", timeout=30_000)
        frame = page.query_selector("#sheet-frame").content_frame()
        return frame

    def measure(self, page, pid: int) -> tuple[dict, dict]:
        """返回 (iframe 内几何, 外层壳几何)。"""
        frame = self.open_preview(page, pid)
        inner = frame.evaluate(PREVIEW_JS)
        outer = page.evaluate(STAGE_JS)
        return inner, outer

    def export_pdf(self, pid: int) -> bytes:
        import httpx

        with httpx.Client(base_url=self.server.base_url, timeout=120.0) as client:
            resp = client.get(f"/api/papers/{pid}/export",
                              params={"format": "pdf", "mode": "question"})
        self.assertEqual(resp.status_code, 200, resp.text[:200])
        self.assertEqual(resp.content[:5], b"%PDF-")
        return resp.content


class TestA9PreviewGeometry(A9Case):
    """A9-1/2/3：纸按真实 A4 渲染、边距比 = 2cm/210mm、宽高共用同一缩放系数。"""

    def test_a9_sheet_is_real_a4_and_uniform_scale(self):
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                data, outer = self.measure(page, pid)
            finally:
                browser.close()

        with open(os.path.join(ARTIFACTS, "a9-preview.json"), "w", encoding="utf-8") as fh:
            json.dump({"inner": data, "outer": outer}, fh, ensure_ascii=False, indent=2)

        self.assertAlmostEqual(data["sheetWidth"], A4_PX, delta=2.0,
                               msg=f"iframe 内 .sheet 宽应为 210mm≈{A4_PX:.1f}px，实测 "
                                   f"{data['sheetWidth']}（修前是被容器压窄的 459.45px）")
        self.assertAlmostEqual(data["docWidth"], A4_PX, delta=2.0,
                               msg="iframe 内容视口未按 A4 宽渲染")
        # iframe 的**内容视口**是真 A4（793.7），而它的**视觉矩形**被 scale 缩小
        self.assertAlmostEqual(outer["iframeClient"]["w"], A4_PX, delta=2.0,
                               msg=f"iframe 内容视口应为 A4 宽，实测 {outer['iframeClient']['w']}")
        ratio = data["sheetPaddingLeft"] / data["sheetWidth"]
        self.assertAlmostEqual(ratio, PADDING_RATIO, delta=0.002,
                               msg=f"padding-left/纸宽 应≈{PADDING_RATIO:.4f}（2cm/210mm），"
                                   f"实测 {ratio:.5f}（修前 0.1647）")
        self.assertIsNotNone(outer["scaleX"], "iframe 上没有 transform: scale()")
        self.assertLess(outer["scaleX"], 1.0, "缩放系数应 < 1（纸面比画布大）")
        self.assertAlmostEqual(
            outer["scaleX"], outer["scaleY"], delta=0.005,
            msg=f"宽高必须同一个系数（等比），实测 scaleX={outer['scaleX']} "
                f"scaleY={outer['scaleY']}")
        self.assertAlmostEqual(
            outer["iframeRect"]["w"], outer["iframeClient"]["w"] * outer["scaleX"], delta=1.5,
            msg="视觉宽应 = 内容视口宽 × 缩放系数")
        self.assertAlmostEqual(
            outer["iframeRect"]["h"], outer["iframeClient"]["h"] * outer["scaleY"], delta=1.5,
            msg="视觉高应 = 内容视口高 × 缩放系数")

    def test_before_value_from_my_pre_fix_measurement(self):
        """修前对照值来自**我在修前那个提交上量的**产物（同一条测量路径）。"""
        path = os.path.join(ARTIFACTS, "a7-geometry.json")
        if not os.path.exists(path):                       # 首次运行时缺历史产物
            self.skipTest("缺少修前产物 a7-geometry.json（task-16 在 0a7ae8e 上生成）")
        with open(path, encoding="utf-8") as fh:
            before = json.load(fh)
        before_width = before["paper"]["w"]
        self.assertAlmostEqual(before_width, 459.45, delta=1.5,
                               msg=f"修前的纸面宽应为被压窄的 ≈459.45px，产物里是 {before_width}")
        self.assertGreater(A4_PX - before_width, 300,
                           "修前后宽度差应显著（459→794），否则说明对比对象不对")


class TestA9ReverseComparisonWithPdf(A9Case):
    """**反向验证**：预览内部量到的比值 == PDF 里量到的同一比值（缩放不改变比值）。"""

    def test_preview_ratios_match_pdf_ratios(self):
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                preview, _outer = self.measure(page, pid)
            finally:
                browser.close()
        pdf = pdf_question_row(self.export_pdf(pid))
        sheet = preview["sheetRect"]
        cols = preview["grid"]["cols"]
        gap = preview["grid"]["gap"]

        preview_left = (preview["firstItemX"] - sheet["x"]) / sheet["w"]
        preview_pitch = (cols[0] + gap) / sheet["w"]

        report = {
            "pdf": {"page_w": pdf["page_w"], "left_ratio": pdf["left_ratio"],
                    "pitch_ratio": pdf["pitch_ratio"], "row_text": pdf["row_text"]},
            "preview": {"sheet_w": preview["sheetWidth"], "left_ratio": preview_left,
                        "pitch_ratio": preview_pitch, "item_count": preview["itemCount"]},
            "delta": {"left_ratio": abs(preview_left - pdf["left_ratio"]),
                      "pitch_ratio": abs(preview_pitch - pdf["pitch_ratio"])},
        }
        with open(os.path.join(ARTIFACTS, "a9-preview-vs-pdf.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

        self.assertLess(report["delta"]["left_ratio"], RATIO_TOL,
                        f"首列左边界/纸宽：预览 {preview_left:.5f} vs PDF {pdf['left_ratio']:.5f}"
                        f"（差 {report['delta']['left_ratio']:.5f} > {RATIO_TOL}）")
        self.assertLess(report["delta"]["pitch_ratio"], RATIO_TOL,
                        f"列间距/纸宽：预览 {preview_pitch:.5f} vs PDF {pdf['pitch_ratio']:.5f}"
                        f"（差 {report['delta']['pitch_ratio']:.5f} > {RATIO_TOL}）")
        # PDF 侧本当是 4 列题目行（`pdf_question_row` 已按「恰好 4 簇」筛选）
        self.assertEqual(len(pdf["all_rows"]) > 0, True)

    def test_instrument_detects_a_1cm_margin_error(self):
        """**正对照**：故意把纸内左边距改成 3cm，同一套比值必须报警（证明判据有区分力）。"""
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        pdf = pdf_question_row(self.export_pdf(pid))
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                frame = self.open_preview(page, pid)
                frame.add_style_tag(content=".sheet{padding-left:3cm !important}")
                tampered = frame.evaluate(PREVIEW_JS)
            finally:
                browser.close()
        sheet = tampered["sheetRect"]
        left = (tampered["firstItemX"] - sheet["x"]) / sheet["w"]
        delta = abs(left - pdf["left_ratio"])
        self.assertGreater(delta, RATIO_TOL,
                           f"正对照失败：改了 1cm 边距后比值差仍只有 {delta:.5f}，判据没有区分力")


class TestA9NoAbnormalWrapping(A9Case):
    def test_items_are_single_line_and_columns_wide_enough(self):
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                frame = self.open_preview(page, pid)
                data = frame.evaluate(PREVIEW_JS)
            finally:
                browser.close()
        self.assertGreater(data["itemCount"], 0, "没有量到题块")
        self.assertEqual(len(data["itemHeights"]), 1,
                         f"题块高度不统一 → 有题换行：{data['itemHeights']}")
        self.assertEqual(data["itemOverflow"], 0,
                         f"{data['itemOverflow']} 个题块内容溢出（换行）")
        self.assertEqual(len(data["itemInkHeights"]), 1,
                         f"题面文本墨迹高度不统一 → 有题换行：{data['itemInkHeights']}")
        col = data["grid"]["cols"][0]
        self.assertGreaterEqual(col, 100,
                                f"列宽只有 {col}px（修前 76.95px，算式必然折行）")
        self.assertAlmostEqual(col / data["sheetWidth"], 146.641 / A4_PX, delta=0.01)
        with open(os.path.join(ARTIFACTS, "a9-wrapping.json"), "w", encoding="utf-8") as fh:
            json.dump({"col_width": col, "item_heights": data["itemHeights"],
                       "ink_heights": data["itemInkHeights"],
                       "overflow": data["itemOverflow"]}, fh, ensure_ascii=False, indent=2)


class TestA9FocusRingClosed(A9Case):
    """task-16 的非阻断观察是否闭环：改到包裹层 `:focus-within` 后**功能上真的有环**。

    ⚠ 本条在 task-18 验收时**实测为缺陷**（详见 `artifacts/a9-focus-ring.json` 与报告）：
    键盘 Tab 确实让 `iframe` 成为 `document.activeElement`，但 Chromium **不把 iframe 元素
    当作 `:focus` / `:focus-within` 的触发者**（实测 `iframe.matches(':focus') === false`），
    所以 `.mg-paper-stage:focus-within` 永远不生效 → 卷面聚焦时**没有任何可见焦点环**。
    正对照（在 stage 内临时放一个 tabindex 元素并聚焦）证明规则本身是好的（`solid 2px`）。
    """

    def test_wrapper_shows_focus_ring_when_sheet_focused(self):
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                page.goto(f"{self.server.base_url}/teacher/paper/{pid}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                reached = False
                for _ in range(60):
                    page.keyboard.press("Tab")
                    if page.evaluate("() => document.activeElement && document.activeElement.id") \
                            == "sheet-frame":
                        reached = True
                        break
                self.assertTrue(reached, "键盘 Tab 走不到卷面 iframe")
                probe = page.evaluate(STAGE_JS)
                probe["activeIsIframe"] = page.evaluate(
                    "() => document.activeElement === document.getElementById('sheet-frame')")
                probe["iframeMatchesFocus"] = page.evaluate(
                    "() => document.getElementById('sheet-frame').matches(':focus')")
                # 正对照：同一套 CSS，在 stage 里放一个 tabindex 元素并聚焦，环应当出现
                probe["positiveControl"] = page.evaluate(
                    """() => { const st = document.querySelector('.mg-paper-stage');
                         const el = document.createElement('div'); el.tabIndex = 0; el.id = 'verify-probe';
                         st.appendChild(el); el.focus();
                         const cs = getComputedStyle(st);
                         const out = {stageFocusWithin: st.matches(':focus-within'),
                                      outline: cs.outlineStyle + ' ' + cs.outlineWidth};
                         el.remove(); return out; }""")
                with open(os.path.join(ARTIFACTS, "a9-focus-ring.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(probe, fh, ensure_ascii=False, indent=2)

                self.assertIsNotNone(probe["stage"], "找不到包裹层 .mg-paper-stage")
                self.assertTrue(probe["positiveControl"]["stageFocusWithin"],
                                f"正对照失败：连普通元素聚焦都触发不了 :focus-within —— {probe}")
                self.assertTrue(
                    probe["stage"]["focusWithin"],
                    "卷面聚焦时包裹层未进入 :focus-within（`:focus-within` 对 iframe 不触发）："
                    f"activeIsIframe={probe['activeIsIframe']}, "
                    f"iframeMatchesFocus={probe['iframeMatchesFocus']}, stage={probe['stage']}")
                self.assertNotEqual(probe["stage"]["outlineStyle"], "none",
                                    f"包裹层没有可见 outline（非阻断观察未闭环）：{probe['stage']}")
                self.assertGreater(probe["stage"]["outlineWidth"], 0, f"{probe['stage']}")
            finally:
                browser.close()

    def test_no_visible_ring_is_measured_while_sheet_focused(self):
        """独立复测（与上面同源，但只做「有没有任何一个元素画出环」的判定）。"""
        from playwright.sync_api import sync_playwright

        pid = self.make_paper()
        with sync_playwright() as pw:
            browser, page = browser_page(pw)
            try:
                page.goto(f"{self.server.base_url}/teacher/paper/{pid}",
                          wait_until="networkidle", timeout=60_000)
                page.wait_for_selector("#sheet-frame", timeout=30_000)
                for _ in range(60):
                    page.keyboard.press("Tab")
                    if page.evaluate("() => document.activeElement && document.activeElement.id") \
                            == "sheet-frame":
                        break
                rings = page.evaluate(
                    """() => { const out = [];
                         for (const el of [document.getElementById('sheet-frame'),
                                           document.querySelector('.mg-paper-scaler'),
                                           document.querySelector('.mg-paper-stage'),
                                           document.querySelector('.mg-paper-frame')]) {
                           if (!el) continue;
                           const cs = getComputedStyle(el);
                           const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0)
                                        || (cs.boxShadow && cs.boxShadow !== 'none');
                           out.push({cls: el.className, ring,
                                     outline: cs.outlineStyle + ' ' + cs.outlineWidth,
                                     shadow: (cs.boxShadow || '').slice(0, 40),
                                     focusWithin: el.matches(':focus-within')});
                         } return out; }""")
                with open(os.path.join(ARTIFACTS, "a9-focus-ring-elements.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump(rings, fh, ensure_ascii=False, indent=2)
                self.assertTrue(any(r["ring"] for r in rings),
                                f"卷面聚焦时没有任何元素画出可见焦点环：{rings}")
            finally:
                browser.close()

    def test_rule_is_defined_in_stylesheet(self):
        rules = parse_rules(open(STYLES, encoding="utf-8").read())
        matched = [(sel, body) for sel, body in rules
                   if ".mg-paper-stage" in sel and ":focus-within" in sel]
        self.assertTrue(matched, "styles.css 里没有 .mg-paper-stage:focus-within 规则")
        bodies = "".join(body for _sel, body in matched)
        self.assertIn("outline", bodies, "包裹层焦点环应给出 outline")
        self.assertIn("var(--mg-", bodies, "包裹层焦点环应取自令牌")


if __name__ == "__main__":
    unittest.main()
