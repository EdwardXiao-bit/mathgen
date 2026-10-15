"""task-42 判据 1 / 判据 6 的**界面侧**证据：真实构建产物 + 真浏览器。

三种世界喂给同一批判据函数（V9.1 / V9.2）：

  · **真后端 + 真 dist**（desktop 1440×900）：40 题的卷答对 23 题 —— 后端按 D80 给 **58**，
    而界面若自己用 M5 之前的旧浮点口径算会得到 **57**。行上的 `data-score` 与分数单元格
    都必须原样显示 58（判据 1 的界面侧）。
  · **撒谎的桩**（`frontend/**` 一个字不改，只换 `/api/attempts` 的 JSON）：给一个
    **任何本机算法都推不出来**的分数 91（total=40 / correct=23）→ 页面必须原样显示 91。
    界面若自己算，这里必然显示 57/58 —— 同一条判据变红。
  · **自主练习**那条（`paper_id` / `paper_code` / `paper_title` 全 null）：卷名单元格必须是
    界面补的「自主练习」，整行文本里不许出现 `undefined` / `null` / `NaN`（判据 6 界面侧）。

边界：本模块只证明「界面把后端给的 `score` 原样呈现、不自己算」。分数本身对不对、
列表 ↔ 明细 ↔ 库对不对账，由同目录 `test_m5_attempts_acceptance.py` 负责。

跑法（在仓库根；`-t backend` 必须在位置参数**之前**）：

    & $VPY -m unittest discover -s backend/tests/e2e_m5 -t backend -v
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import ExitStack

from tests.e2e_m4._harness import dist_freshness
from tests.e2e_m5 import _m5_support as m5

#: 证据（渲染事实 + 桩收到的请求），模块结束时落盘
_EVIDENCE: dict = {}


def _record(key: str, **payload) -> None:
    _EVIDENCE[key] = payload


def tearDownModule():
    # 注意用 `m5.ARTIFACTS`（e2e_m5 自己的证据目录），不是 `_harness.ARTIFACTS`
    # —— 后者指向 `backend/tests/e2e_m4/artifacts/`，那是 task-36 的写域。
    path = os.path.join(m5.ARTIFACTS, "task42-ui-evidence.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(_EVIDENCE, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"\n[task42-ui] 证据已落盘：{path}")


def _require_fresh_dist(cls) -> dict:
    """产物不新就**明确变红**（不重建：重建是 `frontend/**` 写域）。

    这里不用 `_harness.assert_dist_fresh(cls)`：它内部是 `case.assertTrue(...)`，
    而 `assertTrue` 是实例方法 —— 传**类**进去等于把 `self` 位占成 `fresh`，
    断言体变成 `if not msg: ...`（消息非空 → 永远不抛），**恒绿**。
    本模块自己判一次，才能真的红。
    """
    fresh, src_m, dist_m = dist_freshness()
    if not fresh:
        raise AssertionError(
            "前端产物早于源码 → 本套件判据无效。请在 `frontend/**` 写域里"
            f"先跑 `pnpm --dir frontend build`（src={src_m:.0f} dist={dist_m:.0f}）。")
    return {"dist_student_html_mtime": dist_m, "src_newest_mtime": src_m}


def _submit_practice(server, *, student_id: int) -> dict:
    """自主练习（D75）：请求体是平铺的出题参数 + `seed` 查询参数，种子里带回。"""
    practice = server.request("POST", "/api/student/practice", params={"seed": 777},
                              json_body=dict(m5.CONFIG, count=3), expect=200).json()
    return server.request("POST", "/api/attempts", json_body={
        "student_id": student_id,
        "attempt_uuid": uuid.uuid4().hex[:12],
        "answers": [],
        "duration_ms": 4321,
        "config": dict(m5.CONFIG, count=practice["total"]),
        "seed": practice["seed"],
    }, expect=201).json()


class _PageCase(unittest.TestCase):
    """共用读法：把**渲染后**的页面变成事实 + 等页面走到位。不做任何判定。"""

    browser = None

    def _open(self, page, base_url: str, *, wait_id: int, stub=None):
        """打开成绩页并等它走到「有成绩行」。失败时**大声报出**页面正文与收到的请求。

        不做这一步的话，桩坏掉（例如响应编码失败）只会表现为「等 20 秒超时」，
        看不出到底是页面错了还是桩错了。
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page.goto(f"{base_url}/teacher/attempts", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('[data-testid="attempts-summary"]', timeout=20000)
            page.wait_for_selector(f'[data-attempt-id="{wait_id}"]', timeout=20000)
        except PlaywrightTimeout as exc:
            raise AssertionError(
                "成绩页没渲染出成绩行：{}\n页面正文：{}\n这个地址收到的请求：{}".format(
                    exc, page.inner_text("body")[:500].replace("\n", " | "),
                    getattr(stub, "all_requests", "（未提供桩）"))) from None

    def _row_facts(self, page, attempt_id: int) -> dict:
        row = page.locator(f'[data-attempt-id="{attempt_id}"]')
        score_cell = row.locator('[data-testid="attempts-score"]')
        practice_label = row.locator('[data-testid="attempts-practice-label"]')
        return {
            "data_score": row.get_attribute("data-score"),
            "data_correct": row.get_attribute("data-correct"),
            "data_total": row.get_attribute("data-total"),
            "data_practice": row.get_attribute("data-practice"),
            "data_paper_code": row.get_attribute("data-paper-code"),
            "score_cell": score_cell.inner_text() if score_cell.count() else None,
            "practice_label": practice_label.inner_text() if practice_label.count() else None,
            "row_text": row.inner_text().replace("\n", " | "),
        }


class TestScoreUiWithRealBackend(_PageCase):
    """真后端 + 真 dist：判据 1 / 判据 6 界面侧的主证据。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = _require_fresh_dist(cls)
        cls.stack = ExitStack()
        cls.server = cls.stack.enter_context(m5.M5Server())
        cls.browser = cls.stack.enter_context(m5.browser_session()).browser()

        paper_id = cls.server.new_paper(title="M5 界面验收卷-A（40 题）", count=40)
        published = cls.server.publish(paper_id)
        cls.paper = {"id": paper_id, "code": published["code"]}
        student_id = cls.server.student_id("界面验收学生", "2026M5UI01")
        target = cls.server.submit_graded(
            student_id=student_id, paper_id=paper_id, uuid_hex=uuid.uuid4().hex[:12],
            correct_count=23, duration_ms=83000).json()
        cls.target = target
        cls.attempt_id = int(target["attempt_id"])

        # 夹具自检：这个世界的两个口径**真的分叉**，否则界面判据在该场景里恒真。
        assert (target["total"], target["correct"]) == (40, 23), target
        assert target["score"] == 58, target
        assert m5.score_by_old_js(40, 23) == 57, "负对照世界没造对"

        practice = _submit_practice(cls.server, student_id=cls.server.student_id(
            "界面练习学生", "2026M5UI02"))
        cls.practice_id = int(practice["attempt_id"])
        cls.practice_response = practice

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    # -- 用例 -------------------------------------------------------------
    def test_01_the_row_shows_the_backend_score_58_not_a_locally_computed_57(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.server.base_url, wait_id=self.attempt_id)
            facts = self._row_facts(page, self.attempt_id)
            payload = {"total": self.target["total"], "correct": self.target["correct"],
                       "score": self.target["score"]}
            problems = m5.score_render_problems(rendered=facts["score_cell"], payload=payload)
            _record("real_backend_score", facts=facts, payload=payload, problems=problems,
                    old_js=m5.score_by_old_js(40, 23), freshness=self.freshness)
            self.assertEqual(problems, [], "界面没有原样呈现后端分数：" + "；".join(problems))
            self.assertEqual("58", (facts["score_cell"] or "").strip(), facts)
            self.assertEqual("58", facts["data_score"], facts)
            self.assertEqual(("23", "40"), (facts["data_correct"], facts["data_total"]), facts)
        finally:
            context.close()

    def test_02_negative_control_a_locally_computed_score_is_caught(self):
        """同一条判据喂「界面自己算出来的 57」必须红 —— 负对照留在仓库里（V9）。"""
        payload = {"total": 40, "correct": 23, "score": 58}
        problems = m5.score_render_problems(rendered="57", payload=payload)
        self.assertTrue(problems, "界面自己算出的 57 必须被抓住")
        self.assertIn("界面显示", " | ".join(problems))
        self.assertIn("本地算分", " | ".join(problems))

    def test_03_practice_row_is_labeled_by_the_ui_without_undefined(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.server.base_url, wait_id=self.practice_id)
            facts = self._row_facts(page, self.practice_id)
            rows = self.server.list_attempts(limit=m5.PAGE_LIMIT)["attempts"]
            row = next(item for item in rows if int(item["id"]) == self.practice_id)
            problems = m5.rendered_text_problems(
                text=facts["row_text"], where="成绩页自主练习行")
            problems += m5.practice_contract_problems(summary=row)
            _record("practice_row", facts=facts, list_row=row, problems=problems)
            self.assertEqual(problems, [], "自主练习行渲染有问题：" + "；".join(problems))
            self.assertEqual("true", facts["data_practice"], facts)
            self.assertEqual("自主练习", (facts["practice_label"] or "").strip(), facts)
            self.assertEqual("", facts["data_paper_code"], facts)
        finally:
            context.close()

    def test_04_negative_control_a_rendered_undefined_is_caught(self):
        """判据 6 界面侧的负对照：渲染文本里漏出 `undefined` / `null` / `NaN` 必须红。"""
        for leaked in ("自主练习 | undefined | 0 / 3",
                       "自主练习 | null | 0 / 3",
                       "自主练习 | NaN | 0 / 3"):
            problems = m5.rendered_text_problems(text=leaked, where="负对照（人为文本）")
            self.assertTrue(problems, f"{leaked!r} 里的空值泄漏必须被抓住")
            self.assertIn(leaked.split(" | ")[1], " | ".join(problems))
        # 反面：正常渲染不许无中生有
        self.assertEqual(
            [], m5.rendered_text_problems(text="自主练习 | 0 / 3 | 0秒", where="负对照（正常文本）"))


class TestScoreUiAgainstADishonestStub(_PageCase):
    """撒谎的桩：分数 91 是**任何本机算法都推不出来**的值（老 57 / 新 58 都不是）。

    界面自己算 → 显示 57/58 ≠ 91 → 判据红；原样呈现后端字段 → 显示 91 → 绿。
    """

    @classmethod
    def setUpClass(cls):
        cls.freshness = _require_fresh_dist(cls)
        cls.stack = ExitStack()
        cls.browser = cls.stack.enter_context(m5.browser_session()).browser()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_01_a_dishonest_score_reaches_the_dom(self):
        payload = m5.stub_attempt(7, total=40, correct=23, score=91,
                                  student_name="桩学生", student_no="2026STUB01")
        self.assertEqual(57, m5.score_by_old_js(40, 23))
        self.assertEqual(58, m5.score_by_contract(40, 23))
        assert payload["score"] not in (57, 58), payload

        with m5.AttemptsStubServer([payload]) as stub:
            _record("stub_world", payload=payload)
            context = self.browser.new_context(viewport={"width": 1440, "height": 900})
            try:
                page = context.new_page()
                self._open(page, stub.base_url, wait_id=payload["id"], stub=stub)
                facts = self._row_facts(page, payload["id"])
                problems = m5.score_render_problems(rendered=facts["score_cell"], payload=payload)
                _record("stub_score", facts=facts, problems=problems,
                        requests=[dict(item) for item in stub.requests],
                        freshness=self.freshness)
                self.assertEqual(problems, [], "界面没有原样呈现桩给的分数：" + "；".join(problems))
                self.assertEqual("91", (facts["score_cell"] or "").strip(), facts)
                self.assertEqual("91", facts["data_score"], facts)
                self.assertEqual(("23", "40"), (facts["data_correct"], facts["data_total"]), facts)
                self.assertTrue(stub.requests, "桩没收到 /api/attempts —— 判据没打在真页面上")
                self.assertEqual("20", stub.requests[0]["limit"][0], stub.requests)
                self.assertEqual("0", stub.requests[0]["offset"][0], stub.requests)
            finally:
                context.close()

    def test_02_negative_control_a_locally_computed_score_is_caught(self):
        """同一批读法喂「界面自己算」的两个方向，必须都红。"""
        payload = m5.stub_attempt(7, total=40, correct=23, score=91)
        for rendered in ("57", "58"):
            problems = m5.score_render_problems(rendered=rendered, payload=payload)
            self.assertTrue(problems, f"界面自己算出的 {rendered} 必须被抓住")
            self.assertIn("界面在本地算分", " | ".join(problems), problems)


#: 空态/成绩页正文里**不许**出现的开发者词汇（口径照 `frontend/scripts/verify-ui-voice.mjs`）
DEV_WORDS = ("undefined", "null", "NaN", "Traceback", "ECONNREFUSED",
             "FetchError", "TypeError", "at Object.", "<![CDATA[")


class TestEmptyStateIsReadable(_PageCase):
    """**一条成绩都没有**时的成绩页：不许白屏，也不许冒出开发者术语。

    这条走的是「返回空列表的桩」，渲染路径与真实后端上「没有成绩的卷」完全相同
    （`frontend/src/teacher/pages/AttemptsPage.tsx:421-459` 的 `EmptyState`，
    两个分支共用 `attempts-empty`；`frontend/src/ui/State.tsx:20-28` 要求
    `title` + `action`，`hint` 可选 —— 类型层面卡住「只有一句报错」的白屏）。
    """

    @classmethod
    def setUpClass(cls):
        cls.freshness = _require_fresh_dist(cls)
        cls.stack = ExitStack()
        cls.browser = cls.stack.enter_context(m5.browser_session()).browser()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_01_empty_state_is_readable_and_free_of_developer_wording(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        with m5.AttemptsStubServer([]) as stub:
            context = self.browser.new_context(viewport={"width": 1440, "height": 900})
            try:
                page = context.new_page()
                page.goto(f"{stub.base_url}/teacher/attempts", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector('[data-testid="attempts-empty"]', timeout=20000)
                except PlaywrightTimeout as exc:
                    raise AssertionError(
                        "零成绩世界既没出空态也没出别的可读内容（白屏？）：{}\n页面正文：{}\n"
                        "这个地址收到的请求：{}".format(
                            exc, page.inner_text("body")[:500].replace("\n", " | "),
                            stub.all_requests)) from None

                empty = page.locator('[data-testid="attempts-empty"]')
                text = empty.inner_text().replace("\n", " | ")
                body = page.inner_text("body").replace("\n", " | ")
                leaked = [word for word in DEV_WORDS if word in body]
                _record("empty_state", empty_text=text, body_text=body[:600], leaked=leaked,
                        data_state=empty.get_attribute("data-state"),
                        action_count=empty.locator("a, button").count(),
                        requests=[dict(item) for item in stub.requests],
                        freshness=self.freshness)

                self.assertEqual([], leaked, f"空态里出现开发者术语 {leaked}：{body[:400]}")
                self.assertIn("还没有学生做过这套卷", text, text)
                self.assertGreater(len(text.strip()), 10, "空态只有一句话 —— 老师不知道下一步做什么")
                self.assertGreaterEqual(empty.locator("a, button").count(), 1,
                                        f"空态没有可执行的下一步：{text}")
                self.assertEqual("empty", empty.get_attribute("data-state"), text)
                # 零成绩 → 一行都没有；同时不许把「没有成绩」误渲染成错误态
                self.assertEqual(0, page.locator("[data-attempt-id]").count(), body[:300])
                self.assertEqual(0, page.locator('[data-testid="attempts-error"]').count(),
                                 f"没有成绩被渲染成了错误：{body[:400]}")
                self.assertTrue(stub.requests, "桩没收到 /api/attempts —— 判据没打在真页面上")
            finally:
                context.close()

    def test_02_negative_control_developer_wording_in_an_empty_state_is_caught(self):
        """空态判据的负对照：同一个读法喂「带 `undefined` 的空态」必须红。"""
        problems = m5.rendered_text_problems(
            text="还没有学生做过这套卷 | undefined | 去卷库看看", where="负对照（空态）")
        self.assertTrue(problems, "空态里的 undefined 必须被抓住")
        self.assertIn("undefined", " | ".join(problems))

