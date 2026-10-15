"""task-36 判据 8：卷库收纳的**教师端界面**（真实构建产物 + 真浏览器）。

三种世界喂给**同一批判据函数**（V9.1 / V9.2）：

  · 真后端 + 真 dist（desktop 1440×900）：有成绩的卷「删除」事先禁用且有**可见理由**；
    没成绩的卷**真的能点**（点开二次确认再取消）；默认档只看在用；切档看得到恢复入口。
  · 撒谎的桩（只伪造 `/api/papers` 的 JSON，`frontend/**` 一个字不改）：让 `has_attempts`
    两个方向都撒谎 —— 该禁的没禁、不该禁的禁了 —— 同一批判据必须都变红。
    这证明判据读的是**渲染结果**，而不是后端的自述。
  · 桩 + 25 张在用卷：点「下一页」到第 2 页，再切档 → 必须回第 1 页；证据是桩收到的
    `/api/papers` 请求参数（`offset=0&archived=only`），不是页面上的文字。

边界：本模块证明「界面对 `has_attempts` 的处理正确」。`has_attempts` 本身可不可信
（批量取数、非恒值）由 `test_paper_archive_acceptance.py` 的判据 5 / 7 负责。
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from contextlib import ExitStack

from tests.e2e_m4 import _archive_support as sup
from tests.e2e_m4._harness import ARTIFACTS, assert_dist_fresh, browser_session

#: 证据（渲染事实 + 桩收到的请求），每个用例往里塞，模块结束时落盘
_EVIDENCE: dict = {}


def _record(key: str, **payload) -> None:
    _EVIDENCE[key] = payload


def tearDownModule():
    path = os.path.join(ARTIFACTS, "task36-ui-evidence.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(_EVIDENCE, handle, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"\n[task36-ui] 证据已落盘：{path}")


class _PageCase(unittest.TestCase):
    """共用读法：把**渲染后**的页面变成事实 + 等页面走到位。不做任何判定。"""

    browser = None

    def _open(self, page, base_url: str, *, wait_row: int, stub=None):
        """打开卷库页并等它走到「有列表」。失败时**大声报出**页面正文与收到的请求。

        不做这一步的话，桩坏掉（例如响应编码失败）只会表现为「等 20 秒超时」，
        看不出到底是页面错了还是桩错了 —— 本模块第一次跑就被这个坑咬过。
        """
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page.goto(f"{base_url}/teacher/library", wait_until="domcontentloaded")
        try:
            page.wait_for_selector('[data-testid="library-summary"]', timeout=20000)
            page.wait_for_selector(f'[data-paper-id="{wait_row}"]', timeout=20000)
        except PlaywrightTimeout as exc:
            raise AssertionError(
                "卷库页没渲染出列表行：{}\n页面正文：{}\n这个地址收到的请求：{}".format(
                    exc, page.inner_text("body")[:500].replace("\n", " | "),
                    getattr(stub, "all_requests", "（未提供桩）"))) from None

    def _switch_archive_tab(self, page, value: str):
        """点分段控件并等它真的切过去（`data-state` 是控件的自述，可以等它）。"""
        label = "已归档" if value == "only" else "在用"
        page.click(f'[data-testid="library-archive-filter"] label:has-text("{label}")')
        page.wait_for_function(
            "value => (document.querySelector('[data-testid=\"library-archive-filter\"]')"
            "?.dataset.state ?? '') === value", arg=value)

    def _page_info(self, page) -> str:
        return page.inner_text('[data-testid="library-page-info"]').strip()

    def _wait_page(self, page, number: int):
        """等分页摘要说出第 N 页 —— 取空值要安全：重渲染瞬间元素会短暂不存在。"""
        page.wait_for_function(
            "n => (document.querySelector('[data-testid=\"library-page-info\"]')"
            "?.textContent ?? '').includes(`第 ${n} 页`)", arg=number)


class TestLibraryUiWithRealBackend(_PageCase):
    """真后端 + 真 dist：判据 8 的主证据（「事先禁用」不是纸面约定，是渲染事实）。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = assert_dist_fresh()
        cls.stack = ExitStack()
        cls.server = cls.stack.enter_context(sup.ArchiveServer())
        cls.browser = cls.stack.enter_context(browser_session()).browser()

        cls.graded_id = cls.server.new_paper(title="UI 有成绩卷")
        cls.graded_code = cls.server.publish(cls.graded_id)["code"]
        student = cls.server.student_id("UI验收学生", "20261015001")
        sup.submit_attempt(cls.server, student_id=student, paper_id=cls.graded_id,
                           uuid_hex=uuid.uuid4().hex, answers=[])
        cls.clean_id = cls.server.new_paper(title="UI 没成绩卷")
        cls.archived_id = cls.server.new_paper(title="UI 已归档卷")
        cls.server.archive(cls.archived_id)

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    # -- 用例 -------------------------------------------------------------
    def test_01_graded_paper_is_disabled_with_a_visible_reason(self):
        """有成绩 → 事先禁用 + 可见理由；没成绩 → 可点。两侧同一批次判定。"""
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.server.base_url, wait_row=self.graded_id)
            graded = sup.row_facts(page, self.graded_id)
            clean = sup.row_facts(page, self.clean_id)
            problems = sup.archive_ui_problems(graded, clean)
            _record("rows", graded=graded, clean=clean, problems=problems,
                    freshness=self.freshness)
            self.assertEqual(problems, [], "卷库界面与判据不符：" + "；".join(problems))
        finally:
            context.close()

    def test_02_clean_paper_can_really_be_clicked(self):
        """没成绩的卷不只是「属性上没禁用」——真的点得开二次确认（然后取消，不改数据）。"""
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.server.base_url, wait_row=self.clean_id)
            page.click(f'[data-paper-id="{self.clean_id}"] [data-testid="library-delete"]')
            page.wait_for_selector('[data-testid="library-delete-confirm"]', timeout=10000)
            confirm = page.inner_text('[data-testid="library-delete-confirm"]')
            self.assertIn("UI 没成绩卷", confirm, f"确认条没提这张卷：{confirm!r}")
            page.click('[data-testid="library-delete-confirm-cancel"]')
            page.wait_for_selector('[data-testid="library-delete-confirm"]',
                                   state="detached", timeout=10000)
            _record("clean_delete_is_operable", confirm_text=confirm)
        finally:
            context.close()

    def test_03_default_tab_hides_archived_and_only_tab_offers_restore(self):
        """默认档只看「在用」；切到「已归档」能看到恢复入口、看不到在用卷。"""
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.server.base_url, wait_row=self.graded_id)
            state = page.get_attribute('[data-testid="library-archive-filter"]', "data-state")
            self.assertEqual(state, "exclude", "默认档不是「在用」")
            self.assertIsNone(page.query_selector(f'[data-paper-id="{self.archived_id}"]'),
                              "默认档不该出现已归档的卷")

            self._switch_archive_tab(page, "only")
            page.wait_for_selector(f'[data-paper-id="{self.archived_id}"]', timeout=20000)
            row = f'[data-paper-id="{self.archived_id}"]'
            self.assertIsNotNone(page.query_selector(f'{row} [data-testid="library-unarchive"]'),
                                 "已归档的卷没有恢复入口")
            self.assertIsNone(page.query_selector(f'{row} [data-testid="library-archive"]'),
                              "已归档的卷不该还有归档按钮")
            self.assertIsNone(page.query_selector(f'[data-paper-id="{self.graded_id}"]'),
                              "已归档档不该出现在用卷")
            summary = page.inner_text('[data-testid="library-summary"]')
            self.assertIn("已归档", summary, f"摘要没说明当前档位：{summary!r}")
            _record("archive_tabs", default_state=state, summary=summary)

            self._switch_archive_tab(page, "exclude")
            page.wait_for_selector(f'[data-paper-id="{self.graded_id}"]', timeout=20000)
        finally:
            context.close()


class TestLibraryUiAgainstALyingStub(_PageCase):
    """桩撒谎（两个方向）：同一条判据必须都变红 —— 否则它只是「读了后端自述」。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = assert_dist_fresh()
        cls.stack = ExitStack()
        cls.stub = cls.stack.enter_context(sup.LibraryStubServer([
            # 101：真实世界它有成绩、该被禁用 —— 桩说 has_attempts=False
            sup.stub_paper(101, title="桩说没成绩（其实有）", has_attempts=False,
                           status="published"),
            # 102：真实世界它没成绩、该能删 —— 桩说 has_attempts=True
            sup.stub_paper(102, title="桩说有成绩（其实没有）", has_attempts=True),
        ]))
        cls.browser = cls.stack.enter_context(browser_session()).browser()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_lying_stub_turns_the_criterion_red_in_both_directions(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.stub.base_url, wait_row=101, stub=self.stub)
            graded = sup.row_facts(page, 101)
            clean = sup.row_facts(page, 102)
            problems = sup.archive_ui_problems(graded, clean)
            _record("lying_stub", graded=graded, clean=clean, problems=problems)
            self.assertTrue(problems, "桩撒谎时判据居然通过了 —— 判据没有牙（V11）")
            joined = "；".join(problems)
            self.assertIn("没有事先禁用", joined, f"方向一没点名：{joined}")
            self.assertIn("恒禁用", joined, f"方向二没点名：{joined}")
        finally:
            context.close()


class TestLibraryPagingResetsOnTabSwitch(_PageCase):
    """切档必须回第 1 页 —— 证据取桩收到的请求参数，不取页面上的字。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = assert_dist_fresh()
        cls.stack = ExitStack()
        papers = [sup.stub_paper(300 + i, title=f"在用卷 {i:02d}") for i in range(25)]
        papers += [sup.stub_paper(400 + i, title=f"已归档卷 {i}", archived=True)
                   for i in range(3)]
        cls.stub = cls.stack.enter_context(sup.LibraryStubServer(papers))
        cls.browser = cls.stack.enter_context(browser_session()).browser()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def test_switching_archive_tab_returns_to_the_first_page(self):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            page = context.new_page()
            self._open(page, self.stub.base_url, wait_row=300, stub=self.stub)
            self.assertIn("第 1 页", self._page_info(page))
            page.click('[data-testid="library-next"]')
            self._wait_page(page, 2)
            on_page_two = self.stub.papers_requests_summary()
            self.assertEqual(on_page_two[-1].get("offset"), "20",
                             f"翻页请求没带 offset=20：{on_page_two[-1]}")

            self._switch_archive_tab(page, "only")
            self._wait_page(page, 1)
            page.wait_for_selector('[data-paper-id="400"]', timeout=20000)
            after_switch = self.stub.papers_requests_summary()
            _record("paging", on_page_two=on_page_two, after_switch=after_switch,
                    page_info=self._page_info(page))
            self.assertEqual(after_switch[-1].get("offset"), "0",
                             f"切档后没有回到第 1 页：{after_switch[-1]}")
            self.assertEqual(after_switch[-1].get("archived"), "only",
                             f"切档请求没带 archived=only：{after_switch[-1]}")
        finally:
            context.close()


class TestUiCriterionNegativeControls(unittest.TestCase):
    """纯函数负对照：每一条都要**真的制造出失败条件**并点名（V9 / D71）。"""

    @staticmethod
    def _world(*, graded: dict | None = None, clean: dict | None = None):
        graded_facts = {"id": 1, "present": True, "delete_disabled": True, "guard": "blocked",
                        "guard_title": sup.DELETE_BLOCKED_HINT,
                        "reason_text": sup.DELETE_BLOCKED_SHORT}
        clean_facts = {"id": 2, "present": True, "delete_disabled": False, "guard": "allowed",
                       "guard_title": None, "reason_text": None}
        graded_facts.update(graded or {})
        clean_facts.update(clean or {})
        return graded_facts, clean_facts

    def test_the_good_world_is_green_so_the_controls_are_not_vacuous(self):
        """正对照：判据在正确世界上必须是绿的，否则下面的红没有意义。"""
        self.assertEqual(sup.archive_ui_problems(*self._world()), [])

    def test_each_way_of_being_wrong_is_named(self):
        cases = {
            "该禁的没禁": (dict(graded={"delete_disabled": False}), "没有事先禁用"),
            "禁了但不说理由": (dict(graded={"reason_text": None}), "可见"),
            "理由没说清为什么": (dict(graded={"reason_text": "不能删除"}), "没说清"),
            "说了不能删但没给下一步": (dict(graded={"guard_title": None}), "下一步"),
            "恒禁用（没成绩的也禁）": (dict(clean={"delete_disabled": True}), "恒禁用"),
            "该允许的标记成了 blocked": (dict(clean={"guard": "blocked"}), "allowed"),
            "没成绩的卷冒出了理由": (dict(clean={"reason_text": sup.DELETE_BLOCKED_SHORT}), "不该出现"),
            "读不到有成绩那一行": (dict(graded={"present": False}), "读不到"),
        }
        for label, (patch, needle) in cases.items():
            with self.subTest(label):
                facts = self._world(**patch)
                problems = sup.archive_ui_problems(*facts)
                self.assertTrue(problems, f"{label}：判据没红（负对照无效）")
                self.assertTrue(any(needle in item for item in problems),
                                f"{label}：问题清单没点名（{problems}）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
