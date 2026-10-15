"""Tier 0-③ 移动端版式与可操作性（判据不求人，也不需要手机）。

判据（三档竖屏宽度 360 / 390 / 414）：

1. **无横向溢出**：`documentElement.scrollWidth <= innerWidth`；
2. **点击目标 >= 44×44**（Apple HIG 44pt；Material 48dp，取更宽的 44 作硬线）；
3. **输入框 font-size >= 16px** 且带 `inputmode="numeric"`（否则 iOS 聚焦时会放大页面、
   安卓不弹数字键盘）；
4. **`100dvh` 与 `env(safe-area-inset-*)` 真的随产物发布且生效**（地址栏收放、
   刘海屏底部安全区——这两个只有手机才会真正触发，所以这里判的是「规则在、单位被支持」，
   真机渲染属 Tier 2 / `UNVERIFIED`）。

负对照走**真实产品路径**：`frontend/dist` 副本里注入坏样式，用
`MATHGEN_STATIC_DIR` 指向副本起服务（不改产品源码），要求两条判据**当场变红**——
证明它们是判据而不是装饰。
"""
from __future__ import annotations

import glob
import os
import unittest
from contextlib import ExitStack

from tests.e2e_m4._harness import (
    DIST,
    IPHONE_13,
    IOS_ZOOM_SAFE_FONT,
    TAP_MIN,
    WIDTHS,
    UiServer,
    assert_dist_fresh,
    browser_session,
    collect_page_errors,
    degraded_dist,
    dump_evidence,
    record_evidence,
    warm_student_page,
)

#: 一次取回全部版式度量。测量探针用完即删，不留在页面上。
MEASURE_JS = """
() => {
  const px = (v) => parseFloat(v) || 0;
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { w: +r.width.toFixed(2), h: +r.height.toFixed(2) };
  };
  const visible = (el) => el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const buttons = [...document.querySelectorAll('button')].filter(visible);
  const input = document.querySelector('#st-answer');
  const question = document.querySelector('.st-question');
  const footer = document.querySelector('.st-footer');
  const confirm = document.querySelector('.st-confirm-title');

  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;top:-9999px;left:0;width:1px;height:100dvh';
  document.body.appendChild(probe);
  const dvh = probe.getBoundingClientRect().height;
  probe.style.height = '100vh';
  const vh = probe.getBoundingClientRect().height;
  probe.remove();

  return {
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    bodyWidth: +document.body.getBoundingClientRect().width.toFixed(2),
    dvh: +dvh.toFixed(2),
    vh: +vh.toFixed(2),
    supportsDvh: CSS.supports('height', '100dvh'),
    questionText: question ? (question.textContent || '').trim() : '',
    questionRect: question ? rectOf(question) : null,
    input: input ? Object.assign({
      fontSize: px(getComputedStyle(input).fontSize),
      inputMode: input.getAttribute('inputmode'),
      type: input.type,
    }, rectOf(input)) : null,
    buttons: buttons.map((b) => Object.assign({
      text: (b.textContent || '').trim().slice(0, 12),
      minHeight: getComputedStyle(b).minHeight,
    }, rectOf(b))),
    footerPaddingBottom: footer ? getComputedStyle(footer).paddingBottom : null,
    confirmTitle: confirm ? (confirm.textContent || '').trim() : '',
  };
}
"""


#: 主操作按钮（走完一整套流程要点的）必须同时满足 44 宽与 44 高
PRIMARY_LABELS = frozenset({
    "开始答题", "下一题", "上一题", "交卷", "确定交卷", "回去补一补", "再做一遍", "回首页",
})

#: WCAG 2.5.8（AA）的绝对下限——比 44×44 宽，是任何按钮都不该跌破的线
WCAG_MIN = 24.0

#: 已知低于 44 宽但不低于 WCAG 下限的按钮：**登记在案、随报告上报**，
#: 不静默放行——新出现的越界按钮会让判据变红。
#: `退出` 是 `.st-quiet`（`frontend/src/student/student.css:253-261` 注释写明
#: 「可点高度仍然按 44px 给」），项目只约定了高度；宽度 32px < 44。
KNOWN_SUB_44 = {
    "退出": "32x44：项目只约定 min-height:44px（student.css:253-261）；宽度未约定，已上报",
}


def violations(metrics: dict, *, width: int) -> dict[str, list[str]]:
    """按判据分组返回**违规明细**（空 dict 就是全过）。"""
    problems: dict[str, list[str]] = {}

    overflow: list[str] = []
    if metrics["scrollWidth"] > width + 1:
        overflow.append(f"scrollWidth={metrics['scrollWidth']} > 视口宽={width}")
    if metrics["bodyWidth"] > width + 1:
        overflow.append(f"body 宽={metrics['bodyWidth']} > 视口宽={width}")
    if overflow:
        problems["无横向溢出"] = overflow

    typography: list[str] = []
    if metrics["input"] is None:
        typography.append("答题页找不到 #st-answer")
    else:
        if metrics["input"]["fontSize"] < IOS_ZOOM_SAFE_FONT:
            typography.append(
                f"输入框 font-size={metrics['input']['fontSize']}px < {IOS_ZOOM_SAFE_FONT}px"
                "（iOS 聚焦会放大页面）")
        if metrics["input"]["inputMode"] != "numeric":
            typography.append(
                f"输入框 inputmode={metrics['input']['inputMode']!r}，应为 'numeric'")
    if typography:
        problems["输入框字号与数字键盘"] = typography

    short = [b for b in metrics["buttons"] if b["h"] < TAP_MIN]
    if short:
        problems[f"按钮高度 >= {TAP_MIN:g}"] = [
            f"{b['text'] or '(无文案)'} 高={b['h']}" for b in short]

    small_primary = [
        f"{b['text']} {b['w']}x{b['h']}" for b in metrics["buttons"]
        if b["text"] in PRIMARY_LABELS and (b["w"] < TAP_MIN or b["h"] < TAP_MIN)
    ]
    if small_primary:
        problems[f"主操作 {TAP_MIN:g}x{TAP_MIN:g}"] = small_primary

    too_small = [
        f"{b['text'] or '(无文案)'} {b['w']}x{b['h']}" for b in metrics["buttons"]
        if b["w"] < WCAG_MIN or b["h"] < WCAG_MIN
    ]
    if too_small:
        problems[f"点击目标 >= {WCAG_MIN:g}（WCAG 2.5.8）"] = too_small

    viewport: list[str] = []
    if not metrics["supportsDvh"]:
        viewport.append("CSS.supports('height','100dvh') === false")
    if abs(metrics["dvh"] - metrics["innerHeight"]) > 1:
        viewport.append(
            f"100dvh={metrics['dvh']} 与 innerHeight={metrics['innerHeight']} 不一致")
    if viewport:
        problems["dvh 生效"] = viewport

    if not metrics["questionText"]:
        problems["题面可见"] = ["题面文本为空"]
    return problems


def sub_44_buttons(metrics: dict) -> list[dict]:
    """低于 44×44 但仍在 WCAG 下限之上的按钮（上报用，不算硬失败）。"""
    return [b for b in metrics["buttons"]
            if (b["w"] < TAP_MIN or b["h"] < TAP_MIN)
            and b["w"] >= WCAG_MIN and b["h"] >= WCAG_MIN]


def dist_css_text() -> str:
    """把产物里的 CSS 全部读回来（用于「规则真的随产物发布」这条静态判据）。"""
    chunks: list[str] = []
    for path in sorted(glob.glob(os.path.join(DIST, "assets", "*.css"))):
        with open(path, "r", encoding="utf-8") as handle:
            chunks.append(handle.read())
    return "\n".join(chunks)


def measure(page) -> dict:
    return page.evaluate(MEASURE_JS)


class TestMobileLayout(unittest.TestCase):
    """三档竖屏宽度下的版式与可操作性。"""

    @classmethod
    def setUpClass(cls):
        cls.freshness = assert_dist_fresh(cls)
        cls.stack = ExitStack()
        cls.server = cls.stack.enter_context(UiServer())
        cls.paper = cls.server.published_paper(
            count=3, seed=20261015, title="Tier0 移动版式验收卷")
        cls.browser = cls.stack.enter_context(browser_session()).browser()
        cls.css_text = dist_css_text()

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()

    def _context(self, width: int):
        profile = dict(IPHONE_13)
        profile["viewport"] = {"width": width, "height": 664}
        return self.browser.new_context(**profile)

    def test_01_no_horizontal_overflow_at_three_widths(self):
        all_metrics: dict[str, dict] = {}
        for width in WIDTHS:
            context = self._context(width)
            try:
                page = context.new_page()
                errors = collect_page_errors(page)
                warm_student_page(page, self.server.base_url, self.paper["code"])
                metrics = measure(page)
                all_metrics[str(width)] = metrics
                problems = violations(metrics, width=width)
                with self.subTest(视口宽=width):
                    self.assertNotIn("无横向溢出", problems, problems)
                    self.assertEqual(errors, [], "页面有未捕获异常")
            finally:
                context.close()
        record_evidence("mobile_layout_widths", metrics=all_metrics)
        # 三档之间题面宽度应随视口变化（避免「宽度写死」被误判为通过）
        widths = {w: all_metrics[w]["questionRect"]["w"] for w in all_metrics
                  if all_metrics[w]["questionRect"]}
        self.assertGreater(len(set(widths.values())), 1,
                           f"三档视口下题面宽度一样，说明宽度与视口无关：{widths}")

    def test_02_tap_targets_on_answering_screen(self):
        context = self._context(390)
        try:
            page = context.new_page()
            warm_student_page(page, self.server.base_url, self.paper["code"])
            metrics = measure(page)
            below = sub_44_buttons(metrics)
            record_evidence("mobile_tap_targets", buttons=metrics["buttons"],
                            minimum=TAP_MIN, wcag_min=WCAG_MIN,
                            below_44=below, known_sub_44=KNOWN_SUB_44)
            problems = violations(metrics, width=390)
            for key in (f"按钮高度 >= {TAP_MIN:g}",
                        f"主操作 {TAP_MIN:g}x{TAP_MIN:g}",
                        f"点击目标 >= {WCAG_MIN:g}（WCAG 2.5.8）"):
                self.assertNotIn(key, problems, problems)
            labels = {b["text"] for b in below}
            self.assertTrue(
                labels <= set(KNOWN_SUB_44),
                f"出现了未登记的 <{TAP_MIN:g}×{TAP_MIN:g} 按钮："
                f"{sorted(labels - set(KNOWN_SUB_44))}")
            self.assertTrue(
                below, "登记在案的越界按钮已消失——请重新裁决并更新 KNOWN_SUB_44")
            self.assertGreaterEqual(len(metrics["buttons"]), 2,
                                    "答题页上的可见按钮少于 2 个，判据没覆盖到目标")
        finally:
            context.close()

    def test_03_answer_input_avoids_ios_zoom_and_has_numeric_keyboard(self):
        context = self._context(390)
        try:
            page = context.new_page()
            warm_student_page(page, self.server.base_url, self.paper["code"])
            metrics = measure(page)
            record_evidence("mobile_input", input=metrics["input"])
            problems = violations(metrics, width=390)
            self.assertNotIn("输入框字号与数字键盘", problems, problems)

        finally:
            context.close()

    def test_04_dvh_and_safe_area_are_shipped_and_effective(self):
        context = self._context(360)
        try:
            page = context.new_page()
            warm_student_page(page, self.server.base_url, self.paper["code"])
            metrics = measure(page)
            record_evidence("mobile_viewport_units",
                            supports_dvh=metrics["supportsDvh"], dvh=metrics["dvh"],
                            vh=metrics["vh"], inner_height=metrics["innerHeight"],
                            footer_padding_bottom=metrics["footerPaddingBottom"],
                            css_has_dvh="100dvh" in self.css_text,
                            css_has_safe_area="safe-area-inset-bottom" in self.css_text)
            problems = violations(metrics, width=360)
            self.assertNotIn("dvh 生效", problems, problems)
            self.assertIn("100dvh", self.css_text,
                          "产物 CSS 里没有 100dvh —— 地址栏收放时底部按钮会跑出屏幕")
            self.assertIn("safe-area-inset-bottom", self.css_text,
                          "产物 CSS 里没有 safe-area-inset-bottom —— 刘海屏底部安全区失效")
        finally:
            context.close()

    def test_05_confirm_card_buttons_are_tappable(self):
        """未作答就交卷时的确认卡也是点击目标，同样要够大。"""
        context = self._context(390)
        try:
            page = context.new_page()
            warm_student_page(page, self.server.base_url, self.paper["code"])
            # 走到最后一题（前两题答掉），第 3 题留空 → 点「交卷」应弹确认卡
            for _ in range(2):
                page.fill("#st-answer", "1")
                page.get_by_role("button", name="下一题", exact=True).click()
                page.wait_for_timeout(120)
            page.get_by_role("button", name="交卷", exact=True).click()
            page.wait_for_selector(".st-confirm-title")
            metrics = measure(page)
            record_evidence("mobile_confirm_card",
                            title=metrics["confirmTitle"], buttons=metrics["buttons"])
            self.assertTrue(metrics["confirmTitle"], "确认卡没有文案")
            problems = violations(metrics, width=390)
            for key in (f"按钮高度 >= {TAP_MIN:g}",
                        f"主操作 {TAP_MIN:g}x{TAP_MIN:g}", "无横向溢出"):
                self.assertNotIn(key, problems, problems)
        finally:
            context.close()


class TestLayoutNegativeControl(unittest.TestCase):
    """负对照：真实代码路径 + 被改坏的产物 → 判据必须当场变红。"""

    @classmethod
    def setUpClass(cls):
        assert_dist_fresh(cls)
        cls.stack = ExitStack()
        cls.static_dir = degraded_dist("layout")
        cls.server = cls.stack.enter_context(UiServer(static_dir=cls.static_dir))
        cls.paper = cls.server.published_paper(
            count=3, seed=20261015, title="Tier0 版式负对照卷")
        cls.browser = cls.stack.enter_context(browser_session()).browser()
        cls.context = cls.browser.new_context(**IPHONE_13)
        cls.page = cls.context.new_page()
        # 副本里 `min-width:1200px` 会让提交按钮落到 390px 视口之外，
        # 普通 click 会被 actionability 检查拦住 → 用 JS 触发真实 submit 事件。
        warm_student_page(cls.page, cls.server.base_url, cls.paper["code"],
                          js_submit=True)
        cls.metrics = measure(cls.page)
        cls.problems = violations(cls.metrics, width=390)

    @classmethod
    def tearDownClass(cls):
        cls.context.close()
        cls.stack.close()

    def test_01_injection_is_actually_served(self):
        """先证明「服务确实供的是副本」，否则负对照什么都说明不了。"""
        import httpx

        html = httpx.get(
            f"{self.server.base_url}/student/paper/{self.paper['code']}",
            timeout=10).text
        self.assertIn("tier0-degraded", html, "副本没有被服务到，负对照无效")
        record_evidence("layout_negative_control",
                        static_dir=self.static_dir,
                        metrics=self.metrics,
                        problems=self.problems)

    def test_02_overflow_criterion_goes_red(self):
        self.assertIn("无横向溢出", self.problems,
                      f"注入 min-width:1200px 后仍无横向溢出：{self.metrics}")

    def test_03_font_criterion_goes_red(self):
        self.assertIn("输入框字号与数字键盘", self.problems,
                      f"注入 font-size:11px 后字号判据仍绿：{self.metrics.get('input')}")

    def test_04_tap_target_criterion_goes_red(self):
        self.assertIn(f"按钮高度 >= {TAP_MIN:g}", self.problems,
                      f"注入 height:18px 后按钮高度判据仍绿：{self.metrics.get('buttons')}")

    def test_05_wcag_floor_criterion_goes_red(self):
        self.assertIn(f"点击目标 >= {WCAG_MIN:g}（WCAG 2.5.8）", self.problems,
                      f"注入 height:18px 后 WCAG 下限判据仍绿：{self.metrics.get('buttons')}")


def tearDownModule():
    print(f"\n[Tier0-③] 证据已落盘：{dump_evidence()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
