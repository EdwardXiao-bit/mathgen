"""S7-② 判据：答案版的跨页行为（`docs/08` §8.2 第 4 条此前一直是「未验」）。

答案区（`.answer-section`，D20 默认另起一页）里每道题带逐步过程（`.a-item` 的
`break-inside: avoid`）。题目版有 NFR-5 服务端守卫盯着「行被拦腰切在两张纸上」，
但**答案版从没有判据**——守卫量的是 `.q-row`（题目行），不是 `.a-item`。

这里补上三条可红的世界：

1. 长逐步过程的答案版导出后，**每一步的首尾 marker 必须落在同一页**（一步被切成两页 → 红）；
2. 所有 marker 都必须在 PDF 里找得到（内容被裁掉 → 红）；
3. 步骤变多/变长时页数**不减**（分页塌陷或答案区被挤没 → 红）。

方法学边界（诚实写清）：Word/Chromium 在「单步本身高过一整页」时无法保持整段，
本判据只覆盖**真实可生成规模**的逐步过程（每步 ≤ 90 字符、每题 ≤ 8 步），
不声称极端合成输入也整段不切。
"""
from __future__ import annotations

import unittest

from app.contracts.api import AnswerLayoutIn
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.export.pdf import export_pdf_report
from app.services.sheet_render import render_html
from tests.e2e_m6 import _nfr5_support as sup

ITEMS = 6


def build_html(*, items: int = ITEMS, steps: int = 8, step_chars: int = 90) -> str:
    """答案版卷面：每道题的每一步两端各放一个唯一 marker，好从 PDF 文本里找它落在哪页。"""
    sheet_items = []
    for i in range(items):
        step_texts = tuple(
            f"STP{i:02d}S{k:02d}H" + sup.filler(step_chars) + f"STP{i:02d}S{k:02d}T"
            for k in range(steps)
        )
        sheet_items.append(SheetItem(seq=i + 1, display=f"MKR{i:02d}A", answer=str(i + 1),
                                     difficulty=1, tags=("s7-02",), steps=step_texts))
    ctx = SheetContext(title="答案版跨页判据", mode=SheetMode.ANSWER, items=tuple(sheet_items),
                       layout=sup.DEFAULT_LAYOUT, code="2610-5204",
                       answer_layout=AnswerLayoutIn())
    return render_html(ctx)


def pagination_problems(pages: list[str], *, items: int = ITEMS, steps: int = 8) -> list[str]:
    """把一个 PDF 的逐页文本变成问题列表：空列表 = 每一步都整段留在同一页且没丢。"""
    problems: list[str] = []
    for i in range(items):
        for k in range(steps):
            head, tail = f"STP{i:02d}S{k:02d}H", f"STP{i:02d}S{k:02d}T"
            head_page = next((n + 1 for n, page in enumerate(pages) if head in page), None)
            tail_page = next((n + 1 for n, page in enumerate(pages) if tail in page), None)
            if head_page is None or tail_page is None:
                problems.append(f"第 {i + 1} 题第 {k + 1} 步内容丢失（head={head_page} tail={tail_page}）")
            elif head_page != tail_page:
                problems.append(f"第 {i + 1} 题第 {k + 1} 步被切在第 {head_page}/{tail_page} 页")
    return problems


def monotonic_problems(counts: dict[str, int]) -> list[str]:
    """页数必须随内容体积不减（键按内容体积从小到大给）。"""
    problems: list[str] = []
    ordered = list(counts.items())
    for (small_name, small), (big_name, big) in zip(ordered, ordered[1:]):
        if big < small:
            problems.append(f"内容更多的 {big_name} 反而页数更少（{big} < {small}）")
    return problems


class TestAnswerSheetNeverSplitsAStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for name, steps, chars in (("sparse", 4, 60), ("dense", 8, 90)):
            out = sup.ARTIFACTS / f"s7-02-answer-{name}.pdf"
            report = export_pdf_report(build_html(steps=steps, step_chars=chars), str(out))
            pages = sup.pdf_page_texts(str(out))
            cls.cases[name] = {
                "path": str(out), "steps": steps, "step_chars": chars,
                "pages": report.pages, "page_texts": pages,
                "problems": pagination_problems(pages, steps=steps),
            }
        cls.observation = {
            "items": ITEMS,
            "cases": {name: {"steps": c["steps"], "step_chars": c["step_chars"],
                             "pages": c["pages"], "problems": c["problems"]}
                      for name, c in cls.cases.items()},
        }
        sup.write_evidence("s7-02-answer-pagination", cls.observation)

    def test_long_answer_steps_stay_on_one_page(self):
        for name, case in self.cases.items():
            with self.subTest(case=name):
                self.assertEqual(case["problems"], [],
                                 f"{name}：{case['problems'][:3]}")

    def test_page_count_grows_with_step_volume(self):
        counts = {name: case["pages"] for name, case in self.cases.items()}
        self.assertEqual(monotonic_problems(counts), [], counts)
        self.assertGreaterEqual(counts["dense"], 2, "答案版没有分页，这条判据等于没测")

    def test_the_pagination_checker_can_go_red(self):
        """负对照：给一个「第 1 题第 1 步跨页 + 另一处丢内容」的假世界，checker 必须报出来。"""
        rest = "".join(f"STP{i:02d}S{k:02d}HSTP{i:02d}S{k:02d}T"
                       for i in range(ITEMS) for k in range(8) if not (i == 0 and k == 0))
        pages = ["STP00S00H" + rest, "STP00S00T"]
        problems = pagination_problems(pages)
        self.assertTrue(any("被切在" in p for p in problems), problems)
        truncated = ["".join(f"STP{i:02d}S{k:02d}H" for i in range(ITEMS) for k in range(8))]
        self.assertTrue(any("内容丢失" in p for p in pagination_problems(truncated)),
                        "尾部 marker 全缺时必须报内容丢失")
        self.assertEqual(monotonic_problems({"a": 3, "b": 2}), ["内容更多的 b 反而页数更少（2 < 3）"])
