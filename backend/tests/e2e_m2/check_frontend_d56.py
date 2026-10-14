"""D56 / NFR-9 独立复核（verify 自用）：预览是否真的来自后端 `/sheet`。

三个可判定的证据：
  ① **静态**：构建产物（`frontend/dist`）里不得出现任何外部 http(s) 资源引用；
  ② **静态**：前端源码里不得存在第二套卷面排版（无 `q-row` / `q-item` / `q-blank` /
     `sheet-title` 类名、无 `@page` 规则）——即卷面 DOM 只应由后端渲染；
  ③ **运行时**（Playwright 打真实构建产物）：打开 `/teacher/paper/1`，
     断言存在 `<iframe src=".../api/papers/1/sheet...">`，且该 iframe 的**文档内**含有
     `.q-item` 卷面节点，而**顶层文档**内没有 `.q-item`（= 预览不是前端自绘）。

用法（先起后端 8137 与 `pnpm --dir frontend preview`）：
    & $VPY backend/tests/e2e_m2/check_frontend_d56.py \\
        --preview-url http://127.0.0.1:4173 --paper-id 1
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
DIST = os.path.join(REPO_ROOT, "frontend", "dist")
SRC = os.path.join(REPO_ROOT, "frontend", "src")

EXTERNAL_RE = re.compile(rb"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
#: 允许出现的本地/协议类 URL（不是外部依赖）
ALLOWED_PREFIXES = (b"http://127.0.0.1", b"http://localhost", b"http://www.w3.org",
                    b"http://schemas", b"https://react.dev", b"https://vitejs.dev")
#: React 生产包内置的**错误说明**字符串（只在报错时拼进 message，不做任何请求）——
#: 已在下面用「运行时零外部请求」独立证伪它是依赖。
NON_RESOURCE_EXEMPT = (b"https://reactjs.org/docs/error-decoder.html",)
#: 真正构成外部依赖的位置：HTML 的 href/src、CSS 的 url() / @import
RESOURCE_URL_RE = re.compile(
    rb'(?:href|src)\s*=\s*["\'](https?://[^"\']+)["\']'
    rb'|url\(\s*["\']?(https?://[^)"\']+)'
    rb'|@import\s+(?:url\()?["\']?(https?://[^)"\']+)')
SHEET_CLASSES = ("q-row", "q-item", "q-blank", "sheet-title", "header-bar", "q-display")
#: 真正的 CSS @page 规则（排除注释与文档字符串里的提及）
PAGE_RULE_RE = re.compile(r"@page\s*(?::[a-z-]+)?\s*\{")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-url", default="http://127.0.0.1:4173")
    parser.add_argument("--paper-id", type=int, default=1)
    args = parser.parse_args()
    failures = []

    def check(name, ok, detail):
        print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")
        if not ok:
            failures.append(name)

    # ---------- ① 构建产物无外部资源引用 ----------
    resource_hits = []
    string_hits = []
    for dirpath, _dirs, files in os.walk(DIST):
        for name in files:
            if not name.lower().endswith((".html", ".js", ".css", ".json", ".svg")):
                continue
            path = os.path.join(dirpath, name)
            blob = open(path, "rb").read()
            for match in RESOURCE_URL_RE.findall(blob):
                url = next((m for m in match if m), b"")
                if url and not url.startswith(ALLOWED_PREFIXES):
                    resource_hits.append(f"{os.path.relpath(path, REPO_ROOT)}: {url[:80]!r}")
            for match in EXTERNAL_RE.findall(blob):
                if match.startswith(ALLOWED_PREFIXES) or match.startswith(NON_RESOURCE_EXEMPT):
                    continue
                string_hits.append(f"{os.path.relpath(path, REPO_ROOT)}: {match[:80]!r}")
    check("① dist 产物无外部资源引用（href/src/url()/@import）", not resource_hits,
          f"资源位外部 URL 命中 {len(resource_hits)} 条"
          + (f"：{resource_hits[:5]}" if resource_hits else ""))
    print(f"      · 非资源位的 http(s) 字符串命中 {len(string_hits)} 条（已豁免 React 报错说明 URL）"
          + (f"：{string_hits[:3]}" if string_hits else ""))

    # ---------- ② 前端源码没有第二套卷面排版 ----------
    offenders = []
    for dirpath, _dirs, files in os.walk(SRC):
        for name in files:
            if not name.endswith((".ts", ".tsx", ".css")):
                continue
            path = os.path.join(dirpath, name)
            text = open(path, encoding="utf-8").read()
            for token in SHEET_CLASSES:
                if re.search(rf"['\"\.]{re.escape(token)}", text):
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)} 出现 {token!r}")
    check("② 前端源码无卷面排版类名（D56）", not offenders,
          "无 q-row/q-item/q-blank/sheet-title/header-bar/q-display 类名"
          if not offenders else f"命中：{offenders[:5]}")

    # 静态：真正会发布的 CSS 产物里不得有 @page 规则
    # （源码里的 `@page { margin }` 只是面板控件 hint 文案与注释，不作为判据）
    css_page_rules = []
    for dirpath, _dirs, files in os.walk(DIST):
        for name in files:
            if not name.endswith(".css"):
                continue
            path = os.path.join(dirpath, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            if PAGE_RULE_RE.search(text):
                css_page_rules.append(os.path.relpath(path, REPO_ROOT))
    check("② 发布用 CSS 产物无 @page 规则", not css_page_rules,
          "dist 的 .css 里无 @page" if not css_page_rules else f"命中：{css_page_rules}")

    # ---------- ③ 运行时：iframe 来自后端 /sheet ----------
    # 浏览器启动走 _browser（= 产品的 app.export.browser），裸跑即可用
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _browser import browser_session

    url = f"{args.preview_url}/teacher/paper/{args.paper_id}"
    with browser_session() as sess:
        browser = sess.browser()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        api_calls: list[str] = []
        page.on("request", lambda req: api_calls.append(req.url))
        page.goto(url, wait_until="networkidle", timeout=60_000)

        iframe_srcs = page.eval_on_selector_all(
            "iframe", "els => els.map(e => e.getAttribute('src') || '')")
        sheet_iframes = [s for s in iframe_srcs if "/sheet" in s]
        check("③ 页面存在指向后端 /sheet 的 iframe",
              any(f"/api/papers/{args.paper_id}/sheet" in s for s in sheet_iframes),
              f"iframe src={iframe_srcs}")

        top_items = page.eval_on_selector_all(".q-item", "els => els.length")
        frame_items = 0
        frame_html_len = 0
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                frame_items = frame.eval_on_selector_all(".q-item", "els => els.length")
                frame_html_len = len(frame.content())
            except Exception:
                continue
            if frame_items:
                break
        check("③ 卷面 DOM 只存在于 iframe 内（顶层无 .q-item）",
              top_items == 0 and frame_items > 0,
              f"顶层 .q-item={top_items} / iframe 内 .q-item={frame_items} "
              f"（iframe HTML {frame_html_len} 字符）")

        requested_sheet = [u for u in api_calls if "/sheet" in u]
        check("③ 浏览器确实请求了后端 /sheet", bool(requested_sheet),
              f"网络请求={requested_sheet[:2]}")

        # 运行时 CSSOM：顶层文档不得有 @page 规则；iframe 内**必须**有（几何来自后端）
        page_rule_js = ("() => { for (const ss of document.styleSheets) {"
                        " try { for (const r of ss.cssRules) {"
                        " if (r.constructor.name === 'CSSPageRule') return r.cssText; } }"
                        " catch (e) {} } return ''; }")
        top_page_rule = page.evaluate(page_rule_js)
        frame_page_rule = ""
        frame_items_holder = 0
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                frame_page_rule = frame.evaluate(page_rule_js)
                frame_items_holder = frame.eval_on_selector_all(".q-item", "e => e.length")
            except Exception:
                continue
            if frame_items_holder:
                break
        check("③ 打印几何只来自后端卷面 HTML（顶层无 @page / iframe 内有 @page）",
              not top_page_rule and bool(frame_page_rule),
              f"顶层 @page={top_page_rule[:60]!r}；iframe @page={frame_page_rule[:60]!r}")

        # 运行时零外部请求（独立证伪「reactjs.org 那个字符串是外部依赖」）
        external = [u for u in api_calls
                    if not u.startswith(args.preview_url)
                    and "127.0.0.1" not in u and "localhost" not in u]
        check("①-b 运行时零外部网络请求（NFR-9）", not external,
              f"非本机请求={external[:3]}" if external else
              f"共 {len(api_calls)} 个请求，全部指向本站/后端")

        # 排版面板改值 → 只改 iframe 文档根元素的 CSS 变量，**不重新加载 iframe**（D56 第 2 条）
        before_calls = list(api_calls)
        slider = page.query_selector('input[type="range"]')
        if slider:
            slider.fill("5")
            page.wait_for_timeout(800)
        after_calls = list(api_calls)
        new_calls = [u for u in after_calls if u not in before_calls]
        new_sheet = [u for u in new_calls if "/sheet" in u]
        css_var = None
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                css_var = frame.evaluate(
                    "() => getComputedStyle(document.documentElement)"
                    ".getPropertyValue('--cols-per-row').trim()")
            except Exception:
                continue
            if css_var:
                break
        check("③ 排版改动实时写入 iframe 根元素且**不重载卷面**",
              css_var == "5" and not new_sheet,
              f"iframe 内 --cols-per-row={css_var!r}；新增请求={[u.split('//')[-1] for u in new_calls]}"
              f"（其中 /sheet 请求 {len(new_sheet)} 个）")

        page.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "artifacts", "teacher-preview.png"),
                        full_page=False)

    print("\n" + ("全部通过" if not failures else "未通过：" + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
