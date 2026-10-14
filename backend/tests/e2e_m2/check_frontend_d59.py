"""D59 用户可见链路复核（verify 自用）：改开关 → 保存 → **预览确实更新**。

背景（用户实测的故障）：老师端排版面板关掉「答题横线」后预览毫无变化，界面却提示已保存。
render-export 修好了变量消费（见 `check_d59_layout.py`），前端由 fe-teacher 补了
「保存成功后重载卷面 iframe」的兜底（D59④）。

本脚本不信任何一方自测，直接驱动**构建产物**（`vite preview` + 真实后端）：
  1. 打开 `/teacher/paper/{id}`，读 iframe 文档里 `.q-blank` 的计算 display（应为 block）；
  2. 在面板上**取消勾选**「答题横线」这个开关；
  3. 等保存 + 卷面重载，再读 iframe 里的计算 display —— 必须是 `none`；
  4. 再勾回来，必须回到 `block`。

用法（先起后端 8137 与 `pnpm --dir frontend preview`，后者 /api 代理到 8137）：
    & $VPY backend/tests/e2e_m2/check_frontend_d59.py --preview-url http://127.0.0.1:4173
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS = os.path.join(HERE, "artifacts")
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def read_iframe_state(page):
    """读卷面 iframe 里的关键量（.q-blank 的 display + 根元素上的变量）。"""
    for frame in page.frames:
        if frame is page.main_frame:
            continue
        try:
            state = frame.evaluate(
                """() => {
                  const blank = document.querySelector('.q-blank');
                  return {
                    url: location.href,
                    blankDisplay: blank ? getComputedStyle(blank).display : null,
                    blankPresent: !!blank,
                    varLine: getComputedStyle(document.documentElement)
                              .getPropertyValue('--answer-line-display').trim(),
                  };
                }""")
        except Exception:
            continue
        if state.get("blankPresent") is not None:
            return state
    return {}


def toggle_answer_line(page):
    """像用户那样点开关：点包裹 checkbox 的 <label class="switch">。

    （直接点 `input[type=checkbox]` 会被其上的 `<span class="switch-track">`
    拦掉指针事件 —— 这是视觉开关的正常实现，用户点的就是 label。）
    """
    label = page.locator('label.switch:has([data-testid="layout-answer_line"])')
    if label.count() == 0:
        return False
    label.first.click()
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-url", default="http://127.0.0.1:4173")
    parser.add_argument("--api-url", default="http://127.0.0.1:8137")
    args = parser.parse_args()
    os.makedirs(ARTIFACTS, exist_ok=True)

    api = httpx.Client(base_url=args.api_url, timeout=60.0)
    cfg = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral", "count": 12}
    paper = api.post("/api/papers", params={"seed": 99, "title": "D59 前端复核卷"},
                     json=cfg).json()
    pid = paper["id"]
    api.patch(f"/api/papers/{pid}/layout",
              json={"cols_per_row": 4, "row_gap": 1.0, "font_size": 14,
                    "page_margin": 2.0, "answer_line": True, "header_bar": True})
    print(f"用例卷 id={pid}（draft，answer_line=True）")

    # 浏览器启动走 _browser（= 产品的 app.export.browser），裸跑即可用
    sys.path.insert(0, HERE)
    from _browser import browser_session

    with browser_session() as sess:
        browser = sess.browser()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.goto(f"{args.preview_url}/teacher/paper/{pid}", wait_until="networkidle",
                  timeout=60_000)
        page.wait_for_timeout(800)

        initial = read_iframe_state(page)
        check("① 初始预览：答题横线可见（iframe 内 .q-blank display=block）",
              initial.get("blankDisplay") == "block" and initial.get("varLine") == "block",
              f"{initial}")

        # 取消勾选「答题横线」
        box = page.query_selector('[data-testid="layout-answer_line"]')
        check("① 面板上找到「答题横线」开关", box is not None,
              '[data-testid="layout-answer_line"]')
        if box is not None:
            check("① 开关可被点击（点 label，与用户一致）", toggle_answer_line(page),
                  'label.switch:has([data-testid="layout-answer_line"])')
            # 等「保存 → 重载卷面」兜底生效：轮询 iframe 的 display
            deadline = 20
            after = {}
            for _ in range(deadline * 4):
                page.wait_for_timeout(250)
                after = read_iframe_state(page)
                if after.get("blankDisplay") == "none":
                    break
            check("② 关掉开关并保存后，**预览真的变了**（display: none）",
                  after.get("blankDisplay") == "none" and after.get("varLine") == "none",
                  f"{after}")

            # 再勾回来（反向验证，避免"恰好不变"的假通过）
            toggle_answer_line(page)
            restored = {}
            for _ in range(deadline * 4):
                page.wait_for_timeout(250)
                restored = read_iframe_state(page)
                if restored.get("blankDisplay") == "block":
                    break
            check("③ 勾回来后预览恢复可见（display: block）",
                  restored.get("blankDisplay") == "block",
                  f"{restored}")

        # 服务端也必须已落库（预览 = 后端渲染，不是前端假象）
        detail = api.get(f"/api/papers/{pid}").json()
        check("④ 后端 layout.answer_line 已持久化（与预览一致）",
              detail["layout"]["answer_line"] is True,
              f"answer_line={detail['layout']['answer_line']}（第③步已勾回）")

        page.screenshot(path=os.path.join(ARTIFACTS, "d59-frontend-preview.png"),
                        full_page=False)

    print("\n" + ("全部通过" if not FAILURES else "未通过：" + "; ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
