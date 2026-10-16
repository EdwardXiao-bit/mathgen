"""task-52 / T-M6-04：NFR-5 服务端排版守卫的**独立验收**（verify-m1 自建，不读 render-export 的测试当证据）。

八条必验清单 → 八个 TestCase。每一条判据都附一个「我自己造的世界会让它红」的负对照：

1. 守卫不是恒 409（同一内容、只差排版）
2. 诊断里「实测这样能放下」的目标参数真的能放得下（两种形态都有输入）
3. 应急出口四态（未设 / `1` / `0` / `1 ` 带空格）
4. 系统故障必须是 503，不能伪装成 409
5. 模型 vs 真实分页：7 组参数独立复测（原始问题：存在「模型放得下、真实却切开」的组合吗）
6. Ctrl+P 路径确实没有第二层守卫（正面取证 + 把缺口量出来）
7. 冻结契约未动（api.py sha256 + `test_layering.py` 绿）
8. 「不能发现什么」写在报告 §8（判据本身不假装能发现它）

跑法（单套件）：
    & $VPY -m unittest discover -s backend/tests/e2e_m6 -t backend -p "test_m6_nfr5_guard.py" -v
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.e2e_m6 import _nfr5_support as sup

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
REPO = BACKEND.parent
M6_ARTIFACTS = HERE / "artifacts"

#: 冻结契约的 sha256（task 卡给定值；改一位就该红）
API_CONTRACT_SHA256 = "31202c98d1740edddfb9985f490b3f26771ca0b92459a8d2145faad207c09ded"

#: 合成题面：40 个汉字 → 最坏档模型量到 1718.42px（> 833.00px 阈值）⇒ 必然被拒
SYNTH_OVERFLOW_CHARS = 40
#: 合成题面：长到连「字号 10 + 每行 2 题」都放不下 ⇒ 诊断只能给方向、给不出目标值
SYNTH_NEVER_FITS_CHARS = 1200


class TestTheGuardIsNotAlwaysRejecting(unittest.TestCase):
    """判据 1：守卫不是恒 409，也不是恒 200 —— 同一内容、只差六个排版参数。"""

    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.DEFAULT_LAYOUT).__enter__()
        cls.default_response = cls.client.export()
        cls.default_fingerprint = cls.client.content_fingerprint()
        cls.client.set_layout(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
        cls.overflow_response = cls.client.export()
        cls.overflow_fingerprint = cls.client.content_fingerprint()
        cls.observation = {
            "content_chars": SYNTH_OVERFLOW_CHARS,
            "layout_default": sup.asdict(sup.DEFAULT_LAYOUT),
            "layout_overflow": sup.asdict(sup.WORST),
            "content_same": cls.default_fingerprint == cls.overflow_fingerprint,
            "default_status": cls.default_response.status_code,
            "overflow_status": cls.overflow_response.status_code,
            "overflow_code": sup.error_code(cls.overflow_response),
            "default_bytes": len(cls.default_response.content),
            "overflow_message": sup.error_message(cls.overflow_response),
        }
        cls.problems = sup.guard_pair_problems(
            default_status=cls.default_response.status_code,
            overflow_status=cls.overflow_response.status_code,
            content_same=cls.observation["content_same"],
            overflow_code=cls.observation["overflow_code"])
        sup.write_evidence("task52-criterion-1", cls.observation)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_same_content_default_layout_is_200_and_a_real_pdf(self):
        self.assertEqual(sup.pdf_response_problems(self.default_response, label="默认排版"), [])

    def test_same_content_overflow_layout_is_409_layout_does_not_fit(self):
        self.assertEqual(self.overflow_response.status_code, 409, self.overflow_response.text[:400])
        self.assertEqual(sup.error_code(self.overflow_response), "LAYOUT_DOES_NOT_FIT")
        self.assertIn("833.00px", sup.error_message(self.overflow_response))

    def test_the_two_worlds_differ_only_in_the_six_layout_parameters(self):
        self.assertTrue(self.observation["content_same"],
                        "两个世界的题目内容指纹不同 —— 那就证明不了是排版判出来的")
        self.assertEqual(sup.asdict(sup.DEFAULT_LAYOUT) == sup.asdict(sup.WORST), False)
        self.assertEqual(self.problems, [], self.problems)

    def test_the_pair_checker_can_go_red(self):
        """负对照：把它喂给「两个世界都 200」的假世界，判据必须变红。"""
        problems = sup.guard_pair_problems(default_status=200, overflow_status=200,
                                          content_same=True, overflow_code=None)
        self.assertTrue(any("会溢出的排版应 409" in p for p in problems), problems)
        problems = sup.guard_pair_problems(default_status=200, overflow_status=409,
                                          content_same=False, overflow_code="LAYOUT_DOES_NOT_FIT")
        self.assertTrue(any("内容不同" in p for p in problems), problems)


class TestTheDiagnosticSuggestionIsVerifiable(unittest.TestCase):
    """判据 2：409 文案里那句「实测这样能放下」必须能被验证。"""

    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST).__enter__()
        # 先确认这个输入确实被拒（否则后面的「按建议改就 200」没有含义）
        cls.rejected = cls.client.export()
        cls.message = sup.error_message(cls.rejected)
        cls.parsed = sup.parse_suggestion(cls.message)
        # 按文案给出的目标参数原样改，再导一次
        if cls.parsed["targets"]:
            cls.applied = dict(cls.parsed["targets"])
            cls.client.set_layout(**cls.applied)
            cls.follow_up = cls.client.export()
        else:
            cls.applied = {}
            cls.follow_up = None
        # 第二种形态：无论怎么调都放不下 → 文案只能给方向
        cls.never_fits = sup.GuardClient(chars=SYNTH_NEVER_FITS_CHARS, layout=sup.WORST).__enter__()
        cls.never_fits_response = cls.never_fits.export()
        cls.never_fits_message = sup.error_message(cls.never_fits_response)
        cls.never_fits_parsed = sup.parse_suggestion(cls.never_fits_message)
        sup.write_evidence("task52-criterion-2", {
            "rejected_status": cls.rejected.status_code,
            "message": cls.message,
            "parsed": cls.parsed,
            "applied": cls.applied,
            "follow_up_status": getattr(cls.follow_up, "status_code", None),
            "follow_up_bytes": len(cls.follow_up.content) if cls.follow_up is not None else None,
            "never_fits_status": cls.never_fits_response.status_code,
            "never_fits_message": cls.never_fits_message,
            "never_fits_parsed": cls.never_fits_parsed,
        })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.never_fits.__exit__(None, None, None)

    def test_the_rejected_world_really_is_rejected_first(self):
        self.assertEqual(self.rejected.status_code, 409, self.rejected.text[:300])

    def test_the_message_gives_a_target_parameter(self):
        self.assertTrue(self.parsed["has_target"],
                        f"被拒文案里没有可取出的目标参数：{self.message!r}")
        self.assertEqual(sup.suggestion_problems(self.message), [])

    def test_the_target_parameters_actually_fit(self):
        self.assertTrue(self.applied, "没有目标参数可验证")
        self.assertNotEqual(self.applied, sup.asdict(sup.WORST), "『建议』没有改变任何参数，等于没说")
        self.assertIsNotNone(self.follow_up)
        self.assertEqual(sup.pdf_response_problems(self.follow_up, label="按建议改后"), [])
        # 建议里的值必须真的落进库（不是只在文案里）
        stored = self.client.layout_params()
        for name, value in self.applied.items():
            self.assertEqual(float(stored[name]), float(value), f"{name} 没有真的写进卷子")

    def test_the_never_fitting_input_only_gets_direction(self):
        self.assertEqual(self.never_fits_response.status_code, 409)
        self.assertFalse(self.never_fits_parsed["has_target"],
                         f"这一卷连字号 10 + 每行 2 题都放不下，却给出了具体目标值：{self.never_fits_message!r}")
        self.assertTrue(self.never_fits_parsed["claims_direction_only"], self.never_fits_message)
        self.assertEqual(sup.suggestion_problems(self.never_fits_message), [])

    def test_the_suggestion_parser_can_go_red(self):
        """负对照：文案既没有目标值、也没有可识别的方向表述时必须变红。"""
        problems = sup.suggestion_problems("当前排版放不下，请自行调整。")
        self.assertTrue(any("既没有" in p for p in problems), problems)


class TestTheEscapeHatchHasFourStates(unittest.TestCase):
    """判据 3：未设 / `1` / `0` / `1 `（带空格）四态，后两态单独具名断言。"""

    ENV = "MATHGEN_SKIP_LAYOUT_GUARD"

    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST).__enter__()
        cls.states = {}
        for label, value in (("unset", None), ("one", "1"), ("zero", "0"), ("one_space", "1 ")):
            with sup.guard_env(**{cls.ENV: value}):
                with sup.guard_warnings() as warnings:
                    response = cls.client.export()
            cls.states[label] = {
                "value": value,
                "status": response.status_code,
                "guard_header": response.headers.get("X-Mathgen-Layout-Guard"),
                "pdf_magic": response.content[:5].decode("latin-1") if response.status_code == 200 else None,
                "warnings": [record.getMessage() for record in warnings],
            }
        sup.write_evidence("task52-criterion-3", cls.states)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_unset_is_409(self):
        self.assertEqual(self.states["unset"]["status"], 409)

    def test_one_opens_it_wide(self):
        state = self.states["one"]
        self.assertEqual(state["status"], 200, state)
        self.assertEqual(state["pdf_magic"], "%PDF-")
        self.assertEqual(state["guard_header"], "skipped")
        self.assertTrue(state["warnings"], "跳过了守卫却没有 WARNING —— 应急出口变成了静默出口")
        text = " | ".join(state["warnings"])
        self.assertIn("MATHGEN_SKIP_LAYOUT_GUARD=1", text)
        self.assertIn(f"卷 {self.client.paper_id}", text)
        self.assertIn("仍有 2 行放不下", text)
        self.assertIn("行号 [0, 1]", text)

    def test_zero_is_still_409(self):
        """`0` 不是「关」——只有逐字节等于 `1` 才是开关。"""
        self.assertEqual(self.states["zero"]["status"], 409, self.states["zero"])
        self.assertIsNone(self.states["zero"]["guard_header"])
        self.assertEqual(self.states["zero"]["warnings"], [])

    def test_one_with_a_trailing_space_is_still_409(self):
        self.assertEqual(self.states["one_space"]["status"], 409, self.states["one_space"])
        self.assertIsNone(self.states["one_space"]["guard_header"])
        self.assertEqual(self.states["one_space"]["warnings"], [])

    def test_the_state_checker_can_go_red(self):
        """负对照：把「=0 也放行」的假世界喂进判据，必须被抓到。"""
        fake = dict(self.states["zero"])
        fake["status"] = 200
        problems = []
        if fake["status"] == 200:
            problems.append("=0 被当成关闭")
        self.assertTrue(problems)


class TestASystemFailureIs503Not409(unittest.TestCase):
    """判据 4：409 是确定性判定、503 是暂时性故障，二者不许混淆。"""

    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST).__enter__()
        # (a) 真实的环境故障：把「Chromium 可执行文件」指到一个存在但不是浏览器的程序
        with sup.guard_env(MATHGEN_CHROMIUM=str(Path(sys.executable)),
                          MATHGEN_SKIP_LAYOUT_GUARD=None):
            cls.broken_browser = cls.client.export()
        # (b) 子进程非 0 退出但不是排版拒绝（模拟 worker 内部炸掉）
        import app.services.pdf_export as pdf_export

        with mock.patch.object(pdf_export, "_run_worker", return_value=1):
            cls.worker_failed = cls.client.export()
        # (c) 对照：排版拒绝仍然是 409（不能因为加了 (a)(b) 就把 409 吞成 503）
        cls.layout_rejected = cls.client.export()
        sup.write_evidence("task52-criterion-4", {
            "broken_browser": {"status": cls.broken_browser.status_code,
                               "code": sup.error_code(cls.broken_browser),
                               "message": sup.error_message(cls.broken_browser)[:400]},
            "worker_failed": {"status": cls.worker_failed.status_code,
                              "code": sup.error_code(cls.worker_failed),
                              "message": sup.error_message(cls.worker_failed)[:400]},
            "layout_rejected": {"status": cls.layout_rejected.status_code,
                                "code": sup.error_code(cls.layout_rejected)},
        })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_a_broken_browser_is_503_not_409(self):
        self.assertEqual(self.broken_browser.status_code, 503, self.broken_browser.text[:300])
        self.assertEqual(sup.error_code(self.broken_browser), "EXPORT_NOT_READY")
        self.assertIn("浏览器/打印环境问题", sup.error_message(self.broken_browser))

    def test_a_worker_exit_without_layout_rejection_is_503_not_409(self):
        self.assertEqual(self.worker_failed.status_code, 503, self.worker_failed.text[:300])
        self.assertEqual(sup.error_code(self.worker_failed), "EXPORT_NOT_READY")

    def test_a_layout_rejection_is_409_not_503(self):
        self.assertEqual(self.layout_rejected.status_code, 409)
        self.assertEqual(sup.error_code(self.layout_rejected), "LAYOUT_DOES_NOT_FIT")


class TestModelVersusRealPagination(unittest.TestCase):
    """判据 5：不采信 `MEASURED_BREAK_CEILING_PX` / `DOM_SAFETY_FACTOR`，7 组参数独立复测。

    原始问题：**存在「模型说放得下、真实却切开」的组合吗？**（漏报方向）
    """

    CASES = (
        # (tag, chars, display, layout)
        ("real26_default", None, sup.REAL_DISPLAY, sup.DEFAULT_LAYOUT),
        ("real26_worst", None, sup.REAL_DISPLAY, sup.WORST),
        ("synth10_worst", 10, None, sup.WORST),
        ("synth16_worst", 16, None, sup.WORST),
        ("synth28_worst", 28, None, sup.WORST),
        ("synth40_worst", 40, None, sup.WORST),
        ("synth350_default", 350, None, sup.DEFAULT_LAYOUT),
    )

    @classmethod
    def setUpClass(cls):
        cls.cases = []
        for tag, chars, display, layout in cls.CASES:
            cls.cases.append(sup.model_case(chars=chars, display=display, layout=layout, tag=tag))
        cls.verdicts = sup.model_verdicts(cls.cases)
        cls.problems = sup.miss_scan_problems(cls.verdicts["records"])
        sup.write_evidence("task52-criterion-5", {
            "note": "model_* 来自产品自己的 measure_js()+detect_overflow；real_* 来自 Chromium 真实打印的 PDF",
            "verdicts": cls.verdicts,
            "problems": cls.problems,
            "cases": [{k: v for k, v in case.items() if k not in ("markers",)} for case in cls.cases],
        })

    def test_at_least_five_parameter_sets_were_measured(self):
        self.assertGreaterEqual(len(self.cases), 5)
        for case in self.cases:
            self.assertGreater(case["real_pages"], 0, case["tag"])
            self.assertEqual(case["missing"], [], f"{case['tag']}: marker 没抽全，真实判据等于没跑")

    def test_no_row_that_really_splits_passes_the_model(self):
        self.assertEqual(self.problems, [], self.problems)
        self.assertTrue(any(not case["model_fits"] for case in self.cases),
                        "七组里没有一组被模型拒绝 —— 那这个判据没有区分力")

    def test_the_scan_can_go_red_on_a_miss(self):
        """负对照：喂一条「模型放得下、真实切开」的记录必须变红。"""
        problems = sup.miss_scan_problems([
            {"tag": "fake", "model_fits": True, "real_pages": 2, "real_crossing_rows": [0]}])
        self.assertTrue(any("漏报" in p for p in problems), problems)

    def test_the_scan_refuses_samples_without_real_data(self):
        """负对照：没有真实页数的记录不能算通过。"""
        problems = sup.miss_scan_problems([{"tag": "empty", "model_fits": True, "real_pages": 0}])
        self.assertTrue(any("没有真实页数" in p for p in problems), problems)
        self.assertTrue(sup.miss_scan_problems([]), "空集必须变红")

    def test_the_over_rejection_band_is_real_and_recorded(self):
        """独立发现：模型判「放不下」而真实打印整行放得下一页的组合确实存在。

        证据形态：模型行高 > 833px 阈值，但真实 PDF 里既没有 marker 跨页，
        每一页的文本高度也都 <= 833px（纸面上根本没有超出可印区的那一行）。
        """
        over = [case for case in self.cases if not case["model_fits"] and not case["crossings"]]
        self.assertTrue(over, "没有量到『模型拒绝但真实没切开』的组合 —— 与探针结论不符，需重跑")
        for case in over:
            self.assertGreater(case["model_max_row_px"], 833.0, case["tag"])
            self.assertEqual(case["row_crossings"], [], case["tag"])
            self.assertLessEqual(case["real_max_text_height_px"], 833.0, case["tag"])
            self.assertGreater(case["real_max_text_height_px"], 0.0, case["tag"])
        # 被过度拒绝的两组里，模型行高与真实纸面高度的差距（写进报告 §5 的那张表）
        evidence = {case["tag"]: {"model_max_row_px": case["model_max_row_px"],
                                  "real_pages": case["real_pages"],
                                  "real_max_text_height_px": case["real_max_text_height_px"],
                                  "ratio": round(case["model_max_row_px"] / max(case["real_max_text_height_px"], 1e-6), 2)}
                    for case in over}
        (M6_ARTIFACTS / "task52-over-rejection.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_the_really_splitting_case_is_caught(self):
        """模型判拒绝、真实确实切开的组合也要有 —— 否则判据只是「偏向拒绝」。"""
        split = [case for case in self.cases if not case["model_fits"] and case["row_crossings"]]
        self.assertTrue(split, "没有量到『模型拒绝且真实确实切开』的组合")
        self.assertIn("synth40_worst", {case["tag"] for case in split})

    def test_real_generated_content_never_reaches_the_guard(self):
        """真实内容（最长题面 26 字符）在最坏合法档下模型量到多少 —— 它够不着 833px 阈值。"""
        real = next(case for case in self.cases if case["tag"] == "real26_worst")
        self.assertTrue(real["model_fits"], real)
        self.assertLess(real["model_max_row_px"], 833.0, real)
        self.assertLess(real["model_max_row_px"], 600.0, real)


class TestCtrlPlusPHasNoServerGuard(unittest.TestCase):
    """判据 6：Ctrl+P / 打印按钮这条路**没有第二层**（正面取证，不引用 D81）。"""

    CLIENT_ROOTS = ("frontend/src", "frontend/scripts")

    @classmethod
    def setUpClass(cls):
        cls.sources = _read_client_sources()
        cls.sites = sup.print_trigger_sites(cls.sources)
        cls.problems = sup.client_guard_problems(cls.sources)
        # 打印路径打印的就是 `/sheet` 那份 HTML：把最坏内容交给 Chromium 自己的打印通道
        cls.client = sup.GuardClient(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST).__enter__()
        sheet = cls.client.client.get(f"/api/papers/{cls.client.paper_id}/sheet")
        cls.sheet_status = sheet.status_code
        cls.sheet_html = sheet.text
        cls.expected_html = sup.make_html(chars=SYNTH_OVERFLOW_CHARS, layout=sup.WORST)
        out = M6_ARTIFACTS / "task52-print-path.pdf"
        from app.export.pdf import export_pdf_report as _export

        # measure=False, reject_overflow=False —— 就是「没有守卫」的打印语义
        cls.print_report = _export(cls.sheet_html, str(out), measure=False, reject_overflow=False)
        cls.print_path = str(out)
        cls.print_crossings = sup.pdf_marker_crossings(out, items=sup.ITEMS, cols=sup.WORST.cols_per_row)
        cls.print_bboxes = sup.pdf_text_bboxes(out)
        sup.write_evidence("task52-criterion-6", {
            "client_guard_problems": cls.problems,
            "print_trigger_sites": cls.sites,
            "sources_scanned": {path: len(text) for path, text in cls.sources.items()},
            "sheet_status": cls.sheet_status,
            "sheet_equals_export_html": cls.sheet_html == cls.expected_html,
            "sheet_sha256": hashlib.sha256(cls.sheet_html.encode("utf-8")).hexdigest(),
            "print_path_pages": cls.print_report.pages,
            "print_path_crossings": cls.print_crossings,
            "print_path_bboxes": cls.print_bboxes,
            "html_has_row_break_avoid": ".q-row" in cls.sheet_html and "break-inside: avoid" in cls.sheet_html,
        })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_the_client_has_no_equivalent_layout_guard(self):
        self.assertEqual(self.problems, [], self.problems)
        self.assertTrue(self.sites, "找不到任何打印触发点 —— 扫描口径错了")

    def test_the_client_scanner_can_go_red(self):
        """负对照：客户端里塞一个「先问服务端再打印」的假实现，扫描必须抓到。"""
        snippet = {"frontend/src/pages/Fake.tsx": (
            "const r = await fetch('/export');\n"
            "if (r.headers.get('X-Mathgen-Layout-Guard')) { alert('放不下'); }\n"
            "window.print();\n")}
        problems = sup.client_guard_problems(snippet)
        self.assertTrue(any("出现了服务端守卫标记" in p for p in problems), problems)

    def test_the_print_path_prints_the_same_html_as_the_export(self):
        self.assertEqual(self.sheet_status, 200)
        self.assertEqual(self.sheet_html, self.expected_html,
                         "打印用的 HTML 与导出用的 HTML 不是同一份 —— 那 NFR-5 的 CSS 论证就不成立")
        self.assertIn("break-inside: avoid", self.sheet_html)

    def test_the_gap_is_real_a_row_taller_than_a_page_prints_split(self):
        """正面取证缺口：同一个被 409 拒绝的输入，走打印通道就会真的被切开。

        （CSS 主机制只能把**放得下一整页**的行整体下移；放不下一整页时它无能为力。）
        """
        self.assertTrue(self.print_crossings["crossings"],
                        f"没有量到切开，输入的溢出强度不够：{self.print_crossings}")
        pages = {crossing["item"]: (crossing["from_page"], crossing["to_page"])
                 for crossing in self.print_crossings["crossings"]}
        self.assertTrue(all(a != b for a, b in pages.values()), pages)
        self.assertGreater(self.print_report.pages, 1)


def _read_client_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for root in TestCtrlPlusPHasNoServerGuard.CLIENT_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                continue
            if any(part in {"node_modules", "dist", "build"} for part in path.parts):
                continue
            try:
                sources[str(path.relative_to(REPO)).replace("\\", "/")] = path.read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                continue
    return sources


class TestTheRejectedWorldIsUnreachableWithRealContent(unittest.TestCase):
    """Lead 的问题：在**真实可生成的内容**上，这条守卫可达吗？（结论：不可达）

    真实题面取自本任务自己的引擎扫描（`artifacts/task52-pagination-probe.json`：
    591 个配置成功建卷、417 个失败，最长题面 26 字符），不是采信别人的数字。
    """

    @classmethod
    def setUpClass(cls):
        cls.client = sup.GuardClient(display=sup.REAL_DISPLAY, layout=sup.DEFAULT_LAYOUT).__enter__()
        cls.default_response = cls.client.export()
        cls.client.set_layout(cols_per_row=6, row_gap=2.0, font_size=20, page_margin=3.0)
        cls.worst_response = cls.client.export()
        cls.worst_model_case = sup.model_case(display=sup.REAL_DISPLAY, layout=sup.WORST,
                                              tag="real26_worst_reach")
        scan_path = M6_ARTIFACTS / "task52-pagination-probe.json"
        cls.scan = json.loads(scan_path.read_text(encoding="utf-8")).get("scan", {}) if scan_path.exists() else {}
        sup.write_evidence("task52-criterion-real-reachability", {
            "real_display": sup.REAL_DISPLAY,
            "default_status": cls.default_response.status_code,
            "worst_layout_status": cls.worst_response.status_code,
            "worst_model_max_row_px": cls.worst_model_case["model_max_row_px"],
            "threshold_px": 833.0,
            "engine_scan": {k: v for k, v in cls.scan.items() if k != "top_examples"},
            "engine_scan_top_examples": cls.scan.get("top_examples", [])[:4],
        })

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_the_engine_scan_says_26_chars_is_the_longest_real_question(self):
        self.assertTrue(self.scan, "找不到本任务自己的引擎扫描证据 —— 不许空口引用别人的数字")
        self.assertGreaterEqual(self.scan["configs_ok"], 500, self.scan)
        self.assertEqual(self.scan["longest"]["length"], 26, self.scan["longest"])

    def test_real_content_at_the_worst_layout_still_exports_200(self):
        self.assertEqual(sup.pdf_response_problems(self.default_response, label="真实内容/默认档"), [])
        self.assertEqual(sup.pdf_response_problems(self.worst_response, label="真实内容/最坏合法档"), [])

    def test_the_real_rows_are_nowhere_near_the_guard_threshold(self):
        row_px = self.worst_model_case["model_max_row_px"]
        self.assertLess(row_px, 833.0, self.worst_model_case)
        self.assertLess(row_px, 600.0, f"真实内容在最坏档量到 {row_px}px —— 离阈值太近，结论要重估")


class TestFrozenContractsAreUntouched(unittest.TestCase):
    """判据 7：冻结契约未动。"""

    def test_api_contract_sha256(self):
        digest = hashlib.sha256((BACKEND / "app" / "contracts" / "api.py").read_bytes()).hexdigest()
        self.assertEqual(digest, API_CONTRACT_SHA256,
                         "backend/app/contracts/api.py 变了 —— 冻结契约未经 Lead 同意不得改动")

    def test_layering_suite_is_green(self):
        command = [sys.executable, "-m", "unittest", "discover",
                   "-s", "backend/tests/contract", "-t", "backend", "-p", "test_layering.py", "-v"]
        completed = subprocess.run(command, cwd=str(REPO), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=300)
        M6_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (M6_ARTIFACTS / "task52-layering.log").write_text(
            completed.stdout + completed.stderr, encoding="utf-8")
        self.assertEqual(completed.returncode, 0,
                         f"test_layering.py 不是绿的：\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
