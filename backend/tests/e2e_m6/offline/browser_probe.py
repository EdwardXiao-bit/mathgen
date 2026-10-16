"""task-49 探针（浏览器层）：产品自己的 Chromium 在断网态下能不能出题、预览、打印。

用法（由 `_m6_support.run_probe` 拼装）：

    python backend/tests/e2e_m6/offline/browser_probe.py --json <out.json> \
        --loopback-url http://127.0.0.1:PORT/api/papers/1/sheet --html-file <卷面 HTML> \
        [--proxy http://127.0.0.1:7890]

装了守卫时（目录在 `PYTHONPATH` 上）`BrowserType.launch` 已被打补丁，会强制死代理与
`--host-resolver-rules`；不装守卫时用它做对照（可显式 `--proxy` 走本机代理，证明
「此刻外网本来是通的」）。结论写 JSON 文件，不靠 stdout。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _load_executable():
    try:
        from app.export.browser import chromium_executable  # type: ignore
    except BaseException as exc:  # noqa: BLE001 - 探针要把失败原因写进 JSON
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return chromium_executable(), None
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _ensure_tmp_env() -> str:
    """把 Playwright 的临时目录固定到仓库内可写位置。

    本会话的系统 Temp 建不出目录（`EPERM: mkdtemp ...\\Temp\\playwright-artifacts-XXXXXX`
    发生在 Playwright 的 **Node 驱动进程**里），而驱动继承的是本进程的环境，所以必须在
    `sync_playwright()` 之前改 `os.environ`。产品的对策函数是
    `app.export.browser._ensure_tmp_env()`，这里优先用它。
    """
    try:
        from app.export.browser import _ensure_tmp_env as product_fix  # type: ignore

        return str(product_fix())
    except BaseException:  # noqa: BLE001 - 退回到自己的兜底
        override = os.environ.get("MATHGEN_PW_TMP") or os.environ.get("MATHGEN_PLAYWRIGHT_TMP")
        if not override:
            return os.environ.get("TMP") or os.environ.get("TEMP") or ""
        os.makedirs(override, exist_ok=True)
        for name in ("TMP", "TEMP", "TMPDIR"):
            os.environ[name] = override
        return override


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--loopback-url", required=True)
    parser.add_argument("--html-file", required=True)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--external-url", default="https://example.com/")
    args = parser.parse_args(argv)

    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    guard = sys.modules.get("sitecustomize")
    executable, executable_error = _load_executable()
    tmp_dir = _ensure_tmp_env()
    html = open(args.html_file, encoding="utf-8").read()

    payload: dict = {
        "probe": "browser",
        "pid": os.getpid(),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tmp_dir": tmp_dir,
        "guard": {
            "module_loaded": guard is not None,
            "verdict": getattr(guard, "VERDICT", None),
            "forced_chromium_args": getattr(guard, "forced_chromium_args", lambda: [])(),
            "pythonpath": os.environ.get("PYTHONPATH", ""),
        },
        "chromium": {
            "executable": executable,
            "executable_error": executable_error,
            "explicit_proxy": args.proxy,
        },
        "requests": [],
        "request_failures": [],
        "loopback": {},
        "pdf": {},
        "external": {},
    }
    requests = payload["requests"]
    failures = payload["request_failures"]

    with sync_playwright() as manager:
        launch_kwargs: dict = {}
        if executable:
            launch_kwargs["executable_path"] = executable
        if args.proxy:
            launch_kwargs["proxy"] = {"server": args.proxy}
        browser = manager.chromium.launch(**launch_kwargs)
        payload["chromium"]["version"] = browser.version
        # 启动之后再读一次：补丁是**在 launch 里**才知道自己塞了哪些参数
        # （payload 是在 launch 之前拼的，那会儿这个列表还是空的 —— 第一版就踩过）。
        if guard is not None:
            payload["guard"]["forced_chromium_args"] = list(
                getattr(guard, "forced_chromium_args", lambda: [])()
            )
        context = browser.new_context()
        nav_page = context.new_page()
        print_page = context.new_page()
        for page in (nav_page, print_page):
            page.on(
                "request",
                lambda request: requests.append(
                    {"url": request.url, "method": request.method, "type": request.resource_type}
                ),
            )
            page.on(
                "requestfailed",
                lambda request: failures.append(
                    {
                        "url": request.url,
                        "failure": (request.failure or ""),
                    }
                ),
            )

        # 1) 环回页：产品自己的预览 URL
        try:
            response = nav_page.goto(args.loopback_url, wait_until="load", timeout=20000)
            payload["loopback"] = {
                "outcome": "ok",
                "status": None if response is None else response.status,
                "title": nav_page.title(),
                "text_length": len(nav_page.inner_text("body")),
            }
        except BaseException as exc:  # noqa: BLE001
            payload["loopback"] = {
                "outcome": "error",
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }

        # 2) 打印：卷面 HTML 直接喂给 Chromium（与 backend/app/export/pdf.py 同路径）。
        #    用**新开的页**：上一步导航失败会把旧页的 execution context 打坏。
        before = len(requests)
        try:
            print_page.set_content(html, wait_until="load")
            pdf = print_page.pdf()
            requests_during_print = requests[before:]
            payload["pdf"] = {
                "outcome": "ok",
                "bytes": len(pdf),
                "magic": pdf[:5].decode("latin-1", "replace"),
                "requests": requests_during_print,
            }
        except BaseException as exc:  # noqa: BLE001
            payload["pdf"] = {
                "outcome": "error",
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }

        # 3) 故意打外网：断网态下必须失败。
        #    先记下标：这一步**本来就会**产生一个外网请求，判据只应看它之前的请求，
        #    否则「故意打外网」这条探针自己会被报成「渲染时打了外网」。
        payload["requests_before_external"] = len(requests)
        try:
            response = nav_page.goto(args.external_url, wait_until="load", timeout=20000)
            payload["external"] = {
                "outcome": "ok",
                "status": None if response is None else response.status,
                "note": "外网可达 —— 若这是断网态，则本次验证无效",
            }
        except (PlaywrightError, PlaywrightTimeout) as exc:
            payload["external"] = {
                "outcome": "failed",
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }
        except BaseException as exc:  # noqa: BLE001
            payload["external"] = {
                "outcome": "error",
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }

        context.close()
        browser.close()

    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"browser_probe → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
