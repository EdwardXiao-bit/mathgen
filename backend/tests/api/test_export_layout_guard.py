"""NFR-5 服务端出口的**路由层**端到端判据：三个世界（task-44 (ii) / D81）。

与 `tests/render/test_layout_proof.py::TestExportRefusesWhenItCannotFit` 的分工：

* 那边证明**守卫本身**在真实子进程导出里挡得住 —— 三个世界都走真 Chromium，
  但断言停在 `pdf_export.export_pdf_bytes()` 的返回/异常；
* 这边证明**路由层**把守卫的结果正确翻成 HTTP 语义 —— 409 的 `LAYOUT_DOES_NOT_FIT`
  错误码与可照做的文案、以及「跳过」在**响应头**与**日志**里看得见（D81 约束 2、3）。
  两边的接缝是 `export_pdf_bytes()` 的返回值，那一头已由 render 套的 world 2 钉住。

为什么这里替换 `render_html` 而不是去找一张真的放不下的卷子：实测整个合法参数空间
（720 组）里只有 24 组会断行，**全部要求比 API 生成器产出的题干更长**（报告 §6.2(b)）。
也就是说「同一张卷子」在路由这一层用生成器造不出来 —— 能替换的只有**卷面内容**这一层，
而它恰好就是「同一卷」的语义来源。所以这里把 `render_html` 换成一份必然超高的卷面 HTML，
路由、守卫、子进程导出、响应装配全部走真实路径。

三个世界（D81 约束 4）：

| 环境变量 | 期望 |
| --- | --- |
| 未设 | 409 `LAYOUT_DOES_NOT_FIT`，文案含参数实际值与具体动作 |
| `=1` | 200 + 合法 PDF + `X-Mathgen-Layout-Guard: skipped` + WARNING 含卷号与实际溢出行数 |
| `=0` | **仍然 409**（封死「读法碰巧宽松」） |
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

# 本机浏览器装在仓库内 `.playwright`（见 AGENTS.md「坑 2」）；其他机器走默认路径
_BACKEND = Path(__file__).resolve().parents[2]
_REPO = _BACKEND.parent
if (_REPO / ".playwright").is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_REPO / ".playwright"))

from app.contracts.layout import LayoutParams  # noqa: E402
from app.contracts.sheet import SheetContext, SheetItem, SheetMode  # noqa: E402
from app.export import is_available  # noqa: E402
from app.render import render_sheet  # noqa: E402
from app.routers import exports as exports_router  # noqa: E402
from app.services import pdf_export  # noqa: E402
from tests.api._support import ApiTestCase, url_of  # noqa: E402

#: 与 render 套的 `LONG_DISPLAY` 同一份题面：实测 46 字 + 「（第 N 题）」后缀在
#: 6 列 / 字号 20 / 页边距 3cm 下会让行高（1382.5px）超过该档一页容量，真实 PDF 页数翻倍。
_LONG_DISPLAY = "计算 125 乘 8 加 40 除以 5 减 60 乘 2 并写出完整脱式计算过程与验算步骤"

#: 必然超高的参数：合法（六项都在 `BOUNDS` 内）但组合起来放不下。
_DOOMED_LAYOUT = LayoutParams(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)

_DOOMED_HTML: str | None = None


def doomed_html() -> str:
    """必然超高的卷面 HTML（渲染一次后缓存，避免每个用例重渲染）。"""
    global _DOOMED_HTML
    if _DOOMED_HTML is None:
        items = tuple(
            SheetItem(seq=i + 1, display=f"{_LONG_DISPLAY}（第 {i + 1} 题）", answer=str(100 + i))
            for i in range(24)
        )
        _DOOMED_HTML = render_sheet(SheetContext(
            title="NFR-5 服务端出口端到端",
            mode=SheetMode.QUESTION,
            items=items,
            layout=_DOOMED_LAYOUT,
            code="2610-0001",
        ))
    return _DOOMED_HTML


@unittest.skipUnless(is_available(), "需要 Chromium（playwright install chromium）")
class TestExportRouteGuardWorlds(ApiTestCase):
    """三个世界都经由真实路由 + 真实 Chromium 导出。"""

    def setUp(self) -> None:
        super().setUp()
        self.paper_id = self.create_paper({"count": 5})["id"]
        self._saved_env = os.environ.pop(pdf_export.LAYOUT_GUARD_ENV, None)
        self._saved_render_html = exports_router.render_html
        # 只替换**卷面内容**这一层：路由、守卫、导出通道全部走真实实现。
        exports_router.render_html = lambda ctx: doomed_html()  # noqa: ARG005

    def tearDown(self) -> None:
        exports_router.render_html = self._saved_render_html
        os.environ.pop(pdf_export.LAYOUT_GUARD_ENV, None)
        if self._saved_env is not None:
            os.environ[pdf_export.LAYOUT_GUARD_ENV] = self._saved_env
        super().tearDown()

    def _export(self):
        return self.client.get(url_of("export", paper_id=self.paper_id), params={"format": "pdf"})

    def _assert_error(self, response, status: int, code: str) -> dict:
        """统一错误结构（契约 §9：`{"error": {"code", "message"}}`，无 `detail`）。"""
        self.assertEqual(response.status_code, status, response.text[:400])
        body = response.json()
        self.assertEqual(set(body), {"error"}, body)
        self.assertEqual(set(body["error"]), {"code", "message"}, body)
        self.assertEqual(body["error"]["code"], code)
        self.assertTrue(body["error"]["message"].strip())
        self.assertNotIn("detail", body)
        return body["error"]

    def _assert_world_one_rejects(self, response) -> None:
        """世界 1 的**全部断言**（供负对照复用：守卫被强制放行时必须红在这里）。"""
        error = self._assert_error(response, 409, "LAYOUT_DOES_NOT_FIT")
        message = error["message"]
        # 要求①：参数**实际值**必须在文案里（老师要知道自己设的是什么）
        self.assertIn("每行题数 6 题", message)
        self.assertIn("字号 20", message)
        self.assertIn("页边距 3", message)
        # 要求②：必须给出**具体动作**与**具体目标值**（不是「排版放不下」）
        self.assertRegex(message, r"请把")
        self.assertRegex(message, r"调到 \d+")
        # 拒绝时不得带「跳过」头（洞没被打开）
        self.assertNotIn(pdf_export.LAYOUT_GUARD_SKIPPED_HEADER, response.headers)

    # ── 世界 1：守卫启用（默认）→ 拒绝 ────────────────────────────────────
    def test_world_one_refuses_with_409_and_an_actionable_message(self):
        self._assert_world_one_rejects(self._export())

    # ── 世界 2：`=1` → 放行，且跳过在**响应头**与**日志**里看得见 ──────────
    def test_world_two_skip_is_visible_in_the_response_and_the_log(self):
        os.environ[pdf_export.LAYOUT_GUARD_ENV] = "1"
        with self.assertLogs("app.routers.exports", level="WARNING") as captured:
            response = self._export()
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertTrue(response.content.startswith(b"%PDF"), response.content[:16])
        self.assertEqual(
            response.headers.get(pdf_export.LAYOUT_GUARD_SKIPPED_HEADER),
            pdf_export.LAYOUT_GUARD_SKIPPED_VALUE,
        )
        # 要求③：WARNING 里要有**卷号**与**实际溢出行数**，不是「有溢出」三个字
        joined = "\n".join(captured.output)
        self.assertIn(str(self.paper_id), joined)
        rows = re.search(r"仍有 (\d+) 行放不下", joined)
        self.assertIsNotNone(rows, joined)
        self.assertGreater(int(rows.group(1)), 0, joined)

    # ── 世界 3：`=0` 不是「关闭守卫」→ 仍拒绝（D81 约束 1）────────────────
    def test_world_three_zero_still_refuses(self):
        os.environ[pdf_export.LAYOUT_GUARD_ENV] = "0"
        self._assert_world_one_rejects(self._export())

    # ── 负对照：把守卫改成无条件放行，世界 1 的判据必须变红（D81 追加要求）──
    def test_the_first_world_turns_red_when_the_guard_is_forced_open(self):
        saved = pdf_export.layout_guard_enabled
        pdf_export.layout_guard_enabled = lambda: False  # 注入「守卫无条件放行」
        try:
            with self.assertRaises(AssertionError):
                self._assert_world_one_rejects(self._export())
        finally:
            pdf_export.layout_guard_enabled = saved


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
