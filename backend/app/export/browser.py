"""Chromium 定位与复用 —— `export` 的浏览器通道（NFR-5 / C3）。

「预览 = 打印 = PDF」同源的物理实现就在这里：**只有这一个地方**启动浏览器，
并且它打印的就是 `app.render.render_sheet()` 产出的那份 HTML，没有任何第二套排版。

本机实测到的两个环境坑（都不是实现缺陷，已在 T-M2-RENDER 报告里记录）：

1. `playwright install chromium` 默认把工作目录建在 `%LOCALAPPDATA%\\Temp` 下，
   在本机会得到 `EPERM: operation not permitted, mkdtemp`。
   **对策**：启动前把 `TMP` / `TEMP` / `TMPDIR` 指到仓库内可写目录
   （`backend/.playwright-tmp`），不动用户环境变量。
2. 已安装的浏览器版本与 `playwright` 包期望的版本号不一致
   （包期望 `chromium-1248`，机器上是 `chromium-1223`），
   `p.chromium.launch()` 会报「Executable doesn't exist」。
   **对策**：按**已安装的最高版本**显式指定 `executable_path`，
   并让 `MATHGEN_CHROMIUM` 环境变量可以覆盖（便于换机器/换版本）。
   这不是换方案：仍然是 Playwright 打印同一份 HTML，只是把可执行文件指对。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

__all__ = [
    "BrowserError",
    "chromium_executable",
    "playwright_tmp",
    "browser_session",
]


class BrowserError(RuntimeError):
    """Playwright / Chromium 不可用（**环境问题**，不是排版问题）。"""


def _repo_backend_dir() -> str:
    # .../backend/app/export/browser.py -> .../backend
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def playwright_tmp() -> str:
    """Playwright 的临时目录。

    支持两个变量名（`MATHGEN_PW_TMP` 是 `AGENTS.md` 里公布的实现口径，
    `MATHGEN_PLAYWRIGHT_TMP` 是别名）；都没设置时返回系统默认 TMP。
    """
    for name in ("MATHGEN_PW_TMP", "MATHGEN_PLAYWRIGHT_TMP"):
        override = os.environ.get(name)
        if override:
            return override
    return os.environ.get("TMP") or os.environ.get("TEMP") or ""


def _ensure_tmp_env() -> str:
    """把 Playwright 的临时目录固定到可写位置，返回该目录。

    **为什么要改 `os.environ` 而不是 `launch(env=...)`**：本机实测的
    `EPERM: mkdtemp '...\\Temp\\playwright-artifacts-XXXXXX'` 发生在 **Playwright 的
    Node 驱动进程**里（`playwright-artifacts-*` 由驱动创建），而不是浏览器进程。
    `launch(env=...)` 只改浏览器进程的环境，**治不了这个错**。
    驱动进程继承的是 Python 进程的环境，所以必须在这里设。

    为了让副作用最小化 + 不在仓库里留目录：**只有显式提供了
    `MATHGEN_PW_TMP` / `MATHGEN_PLAYWRIGHT_TMP` 时才改写**；
    没提供就完全不动环境（其他机器走系统默认）。
    """
    override = os.environ.get("MATHGEN_PW_TMP") or os.environ.get("MATHGEN_PLAYWRIGHT_TMP")
    if not override:
        return os.environ.get("TMP") or os.environ.get("TEMP") or ""
    os.makedirs(override, exist_ok=True)
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = override
    return override


def _browsers_root() -> str:
    return os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
    )


def chromium_executable() -> str | None:
    """找可用的 Chromium 可执行文件；找不到返回 None。

    查找顺序：
      1. `MATHGEN_CHROMIUM`（显式覆盖）
      2. `chromium-<最高版本>/chrome-win64/chrome.exe`（Windows）
      3. `chromium-<最高版本>/chrome-linux/chrome`（Linux，为将来容器化留口）
      4. `chromium_headless_shell-<最高版本>/...`（兜底）
    """
    explicit = os.environ.get("MATHGEN_CHROMIUM")
    if explicit:
        return explicit if os.path.exists(explicit) else None

    root = _browsers_root()
    if not os.path.isdir(root):
        return None

    def versions(prefix: str) -> list[str]:
        found = []
        for name in os.listdir(root):
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if suffix.isdigit():
                    found.append(name)
        return sorted(found, key=lambda n: int(n[len(prefix):]), reverse=True)

    for prefix, relative in (
        ("chromium-", os.path.join("chrome-win64", "chrome.exe")),
        ("chromium-", os.path.join("chrome-linux", "chrome")),
        ("chromium-", os.path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium")),
        ("chromium_headless_shell-", os.path.join("chrome-headless-shell-win64", "chrome-headless-shell.exe")),
        ("chromium_headless_shell-", os.path.join("chrome-headless-shell-linux64", "chrome-headless-shell")),
    ):
        for name in versions(prefix):
            candidate = os.path.join(root, name, relative)
            if os.path.exists(candidate):
                return candidate
    return None


class _BrowserSession:
    """进程内复用的 Playwright + Chromium。

    `docs/02-技术设计.md` §11「导出 PDF」要求常驻浏览器实例复用启动开销，
    所以这里把实例挂在模块级、由调用方决定何时 `close()`。
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._tmp = None

    def browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        _ensure_tmp_env()
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - 依赖缺失时的环境分支
            raise BrowserError(
                f"Playwright 未安装或不可导入：{type(exc).__name__}: {exc}"
            ) from exc

        executable = chromium_executable()
        try:
            self._playwright = sync_playwright().start()
            kwargs = {}
            if executable:
                kwargs["executable_path"] = executable
            self._browser = self._playwright.chromium.launch(**kwargs)
        except Exception as exc:
            raise BrowserError(
                "Chromium 启动失败（这是环境问题，不是排版问题）："
                f"{type(exc).__name__}: {exc}\n"
                f"  TMP={os.environ.get('TMP')!r}\n"
                f"  MATHGEN_CHROMIUM={os.environ.get('MATHGEN_CHROMIUM')!r}\n"
                f"  探测到的可执行文件={executable!r}\n"
                "  修法：`& $VPY -m playwright install chromium`，"
                "或设置 MATHGEN_CHROMIUM 指向已有的 chrome.exe。"
            ) from exc
        return self._browser

    def close(self) -> None:
        for closer in (self._browser, self._playwright):
            if closer is None:
                continue
            try:
                closer.close() if hasattr(closer, "close") else closer.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None


_SESSION = _BrowserSession()


def session() -> _BrowserSession:
    """取进程级浏览器会话（`export_pdf` 与分页测量共用）。"""
    return _SESSION


@contextmanager
def browser_session() -> Iterator[_BrowserSession]:
    """`with browser_session():` —— 退出时释放 Chromium。"""
    try:
        yield _SESSION
    finally:
        _SESSION.close()


def is_available() -> bool:
    """浏览器通道是否可用（不启动浏览器，只看能否找到可执行文件）。"""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return chromium_executable() is not None
