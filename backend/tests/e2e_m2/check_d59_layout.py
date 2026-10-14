"""D59 浏览器实测（**独立进程**运行，避免 Playwright sync API 与 TestClient 的事件循环冲突）。

在真实 Chromium 里量「CSS 变量是否真的驱动版式」：
  * `--answer-line-display` / `--header-bar-display` → `.q-blank` / `.header-bar` 的**计算 display**；
  * 再注入 `:root{--…:none}`（**只改变量、不动 HTML**，复刻老师端面板的覆盖行为）后复测；
  * `--page-margin` → 屏幕媒体下 `.sheet` 的宽度（≈210mm）与内边距（≈设定的 cm）。

用法（单独运行；输出 JSON 到 stdout）：
    & $VPY check_d59_layout.py --base <on.html> --off <off.html> --margin 1.0=<p1.html> ...
"""
from __future__ import annotations

import argparse
import json
import sys

OVERRIDE_CSS = ":root{--answer-line-display:none;--header-bar-display:none}"

MEASURE_JS = """
() => {
  const blank = document.querySelector('.q-blank');
  const header = document.querySelector('.header-bar');
  const sheet = document.querySelector('.sheet');
  const out = {
    blankDisplay: blank ? getComputedStyle(blank).display : null,
    headerDisplay: header ? getComputedStyle(header).display : null,
    blankPresent: !!blank,
    headerPresent: !!header,
  };
  if (sheet) {
    const rect = sheet.getBoundingClientRect();
    const style = getComputedStyle(sheet);
    out.sheetWidth = rect.width;
    out.paddingLeft = parseFloat(style.paddingLeft);
    out.paddingRight = parseFloat(style.paddingRight);
  }
  return out;
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="开启状态的卷面 HTML")
    parser.add_argument("--off", required=True, help="关闭状态的卷面 HTML")
    parser.add_argument("--margin", action="append", default=[],
                        help="CM=PATH，例如 2.0=m2.html（可重复）")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1000})

        with open(args.base, encoding="utf-8") as fh:
            page.set_content(fh.read(), wait_until="load")
        result["base"] = page.evaluate(MEASURE_JS)

        # **原始故障现场**：只覆盖变量，绝不动 HTML
        page.add_style_tag(content=OVERRIDE_CSS)
        result["base_after_variable_override"] = page.evaluate(MEASURE_JS)
        result["override_css"] = OVERRIDE_CSS

        with open(args.off, encoding="utf-8") as fh:
            page.set_content(fh.read(), wait_until="load")
        result["off"] = page.evaluate(MEASURE_JS)

        for spec in args.margin:
            cm, _, path = spec.partition("=")
            with open(path, encoding="utf-8") as fh:
                page.set_content(fh.read(), wait_until="load")
            measured = page.evaluate(MEASURE_JS)
            measured["page_margin_cm"] = float(cm)
            result[f"margin_{cm}"] = measured

        browser.close()

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
