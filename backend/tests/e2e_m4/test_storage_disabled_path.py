"""Tier 0-② 存储被禁用时的降级路径（DQ7 的内核：微信内置浏览器 / 隐私模式）。

「微信里 `localStorage` 可能不可用」是**浏览器能力问题，不是设备问题**，
所以它可以在没有手机的条件下被判据抓住：让 `Storage.prototype` 的每个方法都抛
`SecurityError`（注入生效本身也要自证），然后走完整流程：

   扫码 → 填姓名学号 → 逐题作答 → 交卷 → 结果页

期望（产品自己声称的契约，见 `frontend/src/student/identity.ts:8-11`、
`resultStore.ts:26-29`、`ResultPage.tsx:22-26`）：**每次重新填身份、结果页从路由
state 兜底**，两条都不许白屏、不许中断做题。

判据边界：这条证明「存储不可用时可用」；它**不能**证明微信 X5 内核的真实行为
（那是 Tier 2），也不证明真机上一定可用。
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack

from tests.e2e_m4._harness import (
    DEVICE_WECHAT_UA,
    IPHONE_13,
    UiServer,
    assert_dist_fresh,
    browser_session,
    collect_page_errors,
    dump_evidence,
    record_evidence,
    run_paper_flow,
    storage_block_script,
    storage_is_blocked,
)


class StorageDisabledCase(unittest.TestCase):
    """一个真实服务 + 一张已发布卷；浏览器只走产品通道 `browser_session()`。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = assert_dist_fresh(cls)
        cls.stack = ExitStack()
        cls.server = cls.stack.enter_context(UiServer())
        cls.paper = cls.server.published_paper(
            count=3, seed=20261015, title="Tier0 存储降级验收卷")
        detail = cls.server.teacher_detail(cls.paper["id"])
        cls.answers = [str(item["answer"]) for item in detail["items"]]
        cls.browser = cls.stack.enter_context(browser_session()).browser()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    # -- 工具 -------------------------------------------------------------
    def _blocked_context(self, device: str = "iPhone 13"):
        context = self.browser.new_context(**IPHONE_13) if device == "iPhone 13" \
            else self.browser.new_context(**DEVICE_WECHAT_UA["微信内置浏览器（iOS UA）"])
        context.add_init_script(storage_block_script())
        return context

    # -- 用例 -------------------------------------------------------------
    def test_01_injection_really_disables_storage(self):
        """注入生效自证：若存储其实还能用，下面所有结论都无效。"""
        context = self._blocked_context()
        try:
            page = context.new_page()
            page.goto(f"{self.server.base_url}/student/", wait_until="domcontentloaded")
            page.wait_for_selector("#root")
            probes = storage_is_blocked(page)
            record_evidence("storage_injection", **probes)
            self.assertTrue(probes["flag"], "注入标记未生效")
            for key in ("localStorage_get", "localStorage_set",
                        "sessionStorage_get", "sessionStorage_set"):
                self.assertEqual(probes[key], "SecurityError",
                                 f"{key} 没有抛异常 → 存储其实可用，判据不成立")
        finally:
            context.close()

    def test_02_full_flow_survives_disabled_storage(self):
        context = self._blocked_context()
        try:
            page = context.new_page()
            errors = collect_page_errors(page)
            result = run_paper_flow(page, self.server.base_url, self.paper["code"],
                                    self.answers)
            record_evidence("storage_disabled_flow", **result, page_errors=errors,
                            freshness=self.freshness)
            self.assertIn("/student/result", result["url"])
            self.assertEqual(
                result["score_sub"], f"答对 {len(self.answers)} 题，共 {len(self.answers)} 题",
                "存储被禁用时判分结果页没有拿到结果（路由 state 兜底失效？）")
            self.assertEqual(result["rows"], len(self.answers))
            self.assertEqual(errors, [], "页面上有未捕获异常")
            submits = [call for call in result["api_calls"]
                       if call["url"].endswith("/api/attempts")]
            self.assertEqual(len(submits), 1, f"交卷请求异常：{result['api_calls']}")
            self.assertEqual(submits[0]["status"], 201)
        finally:
            context.close()

    def test_03_positive_control_storage_enabled_also_flows(self):
        """正对照：同样的流程在存储可用时必须同样成立（否则 02 是空过）。"""
        context = self.browser.new_context(**IPHONE_13)
        try:
            page = context.new_page()
            errors = collect_page_errors(page)
            # 必须先落到真实来源（http://…）再探针：`about:blank` 是不透明源，
            # 那时 localStorage 也会抛 SecurityError，与「存储被禁」无法区分。
            page.goto(f"{self.server.base_url}/student/", wait_until="domcontentloaded")
            page.wait_for_selector("#root")
            probes = storage_is_blocked(page)          # 未注入 → 必须没被禁
            self.assertNotEqual(probes["localStorage_set"], "SecurityError",
                                "没注入存储禁用，却已经不可写 —— 探针或环境有问题")
            result = run_paper_flow(page, self.server.base_url, self.paper["code"],
                                    self.answers, student_no="20260406003")
            record_evidence("storage_enabled_flow", **result, page_errors=errors,
                            probes=probes)
            self.assertEqual(
                result["score_sub"], f"答对 {len(self.answers)} 题，共 {len(self.answers)} 题")
            self.assertEqual(errors, [])
        finally:
            context.close()

    def test_04_wechat_ua_with_disabled_storage(self):
        """微信内置浏览器 UA + 存储不可用：两者叠加仍要走通。"""
        context = self._blocked_context(device="wechat")
        try:
            page = context.new_page()
            errors = collect_page_errors(page)
            page.goto(f"{self.server.base_url}/student/", wait_until="domcontentloaded")
            page.wait_for_selector("#root")
            ua = page.evaluate("() => navigator.userAgent")
            self.assertIn("MicroMessenger", ua)
            probes = storage_is_blocked(page)
            self.assertEqual(probes["localStorage_get"], "SecurityError")
            result = run_paper_flow(page, self.server.base_url, self.paper["code"],
                                    self.answers, student_no="20260406004")
            record_evidence("wechat_ua_flow", ua=ua, **result, page_errors=errors)
            self.assertEqual(
                result["score_sub"], f"答对 {len(self.answers)} 题，共 {len(self.answers)} 题")
            self.assertEqual(errors, [])
        finally:
            context.close()

    def test_05_reload_on_result_page_does_not_white_screen(self):
        """存储不可用时刷新结果页：必须给出可读页面，不能白屏。"""
        context = self._blocked_context()
        try:
            page = context.new_page()
            errors = collect_page_errors(page)
            run_paper_flow(page, self.server.base_url, self.paper["code"], self.answers,
                           student_no="20260406005")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#root")
            text = (page.inner_text("body") or "").strip()
            has_score = page.locator(".st-score").count() > 0
            has_fallback = "这里暂时没有结果" in text
            record_evidence("storage_disabled_reload",
                            body_text=text[:200], has_score=has_score,
                            has_fallback_note=has_fallback, page_errors=errors)
            self.assertTrue(text, "刷新后页面是空的（白屏）")
            self.assertTrue(has_score or has_fallback,
                            f"刷新后既没有分数也没有兜底提示：{text[:120]!r}")
            self.assertEqual(errors, [])
        finally:
            context.close()


def tearDownModule():
    print(f"\n[Tier0-②] 证据已落盘：{dump_evidence()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
