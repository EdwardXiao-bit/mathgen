#!/usr/bin/env python3
"""mathgen 一键启动（源码运行）。

双击 `scripts\\start.ps1`（Windows）或执行 `scripts/start.sh`（macOS / Linux）
最终都会走到这里。本脚本按顺序做四件事，每件都能单独重跑：

1. 检查 Python 版本（需要 3.12+）
2. 建 / 复用仓库根下的 `.venv`，装 `backend/` 的依赖
3. 确保有前端产物 `frontend/dist`
4. 起服务（其余参数原样透传给 `python -m app`）

第 2 步默认走国内 PyPI 镜像（见 `DEFAULT_MIRROR` 的注释，那是实测结论，不是拍脑袋）。
`--no-mirror` 用官方源，`--mirror URL` 指定别的源；你自己设的 `PIP_INDEX_URL` 优先级最高。

刻意**不**做：改系统 Python、写全局配置、装 Node。缺什么就明确说缺什么、怎么补。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"
VENV = REPO / ".venv"
MARKER = VENV / ".mathgen-deps"
MIN_PYTHON = (3, 12)

# 默认走这个 PyPI 镜像。理由是实测出来的：在这台机器（国内网络 + Clash）上，
# 直连 pypi.org 的 `pip install -e backend` 跑了 15 分钟、CPU 烧满却**一个包都没下**；
# 换成清华源 88 秒装完 27 个包。学校机房里这种情况很常见，而这是「一键启动」，
# 卡住不动的代价比换个源的代价大得多。
# 想用官方源：`--no-mirror`（或自己设 PIP_INDEX_URL，脚本会尊重它）。
DEFAULT_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


# --------------------------------------------------------------------------- 小工具


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, total: int, title: str) -> None:
    say(f"\n[{n}/{total}] {title}")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list[str], **kw) -> int:
    say("    $ " + " ".join(cmd))
    return subprocess.call(cmd, **kw)


def pyproject_fingerprint() -> str:
    return hashlib.sha256((BACKEND / "pyproject.toml").read_bytes()).hexdigest()


def playwright_browsers_dir() -> Path:
    """与 `backend/app/export/browser.py::_browsers_root()` 保持同一套平台默认。"""
    explicit = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    cache_home = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache_home / "ms-playwright"


def has_chromium() -> bool:
    root = playwright_browsers_dir()
    if not root.is_dir():
        return False
    return any(p.name.lower().startswith("chromium") for p in root.iterdir())


# --------------------------------------------------------------------------- 四步


def step_python() -> None:
    if sys.version_info < MIN_PYTHON:
        say(
            f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 或更高，当前是 "
            f"{sys.version_info.major}.{sys.version_info.minor}。\n"
            "下载：https://www.python.org/downloads/"
        )
        raise SystemExit(1)
    say(f"    Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def resolve_index(mirror: str | None, no_mirror: bool) -> str | None:
    """决定装依赖时用哪个源；返回 None 表示「不指定，让 pip 用默认（官方源）」。"""
    if mirror:
        return mirror
    if no_mirror:
        return None
    if os.environ.get("PIP_INDEX_URL"):
        say(f"    尊重你设置的 PIP_INDEX_URL：{os.environ['PIP_INDEX_URL']}")
        return None
    return DEFAULT_MIRROR


def step_deps(reinstall: bool, mirror: str | None, no_mirror: bool) -> Path:
    fresh = reinstall or not VENV.exists()
    if not fresh:
        recorded = MARKER.read_text(encoding="utf-8").strip() if MARKER.is_file() else ""
        fresh = recorded != pyproject_fingerprint()

    if not fresh:
        say(f"    依赖已就绪（{VENV}），跳过安装")
        return venv_python()

    if not VENV.exists():
        say(f"    创建虚拟环境 {VENV}")
        if run([sys.executable, "-m", "venv", str(VENV)]) != 0:
            raise SystemExit("创建虚拟环境失败")

    target = venv_python()
    index = resolve_index(mirror, no_mirror)
    index_args = ["--index-url", index] if index else []
    if index:
        say(f"    用镜像安装：{index}（不想用就加 --no-mirror）")

    # 这台机器上 pip 对「版本数多的包」会满核空转、零下载（见工作区 AGENTS.md），
    # 所以有 uv 就优先用 uv，没有才退回 pip。
    #
    # **不要加 `pip install --upgrade pip`**：实测它会打印完「Requirement already satisfied: pip」
    # 之后继续满核烧 CPU 不返回（三次独立复现：pwsh-9 / pwsh-19 / pwsh-23），
    # 而同样的 venv 直接 `pip install -e backend` 只要 88 秒。venv 自带的 pip（3.12 上是 24.x）
    # 处理 PEP 621 的 editable 安装完全够用，升级它只是白多一个卡死点。
    uv = shutil.which("uv")
    if uv:
        say("    用 uv 安装依赖")
        code = run([uv, "pip", "install", "--python", str(target), *index_args, "-e", str(BACKEND)])
    else:
        say("    用 pip 安装依赖（首次会慢一点）")
        code = run(
            [
                str(target),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-e",
                str(BACKEND),
                *index_args,
            ]
        )
    if code != 0:
        hint = (
            "    scripts\\start.ps1 -NoMirror -Reinstall   # 换回官方源\n"
            if index
            else "    scripts\\start.ps1 -Mirror -Reinstall    # 换国内镜像\n"
        )
        say("\n依赖安装失败。常见原因：网络到不了那个源，换一个再试——\n" + hint)
        raise SystemExit(code)

    MARKER.write_text(pyproject_fingerprint(), encoding="utf-8")
    return target


def step_frontend(skip: bool) -> None:
    index = FRONTEND / "dist" / "index.html"
    if index.is_file():
        say(f"    前端产物已在 {FRONTEND / 'dist'}")
        return
    if skip:
        say("    跳过（--skip-frontend）：服务只提供 API，没有界面")
        return
    pnpm = shutil.which("pnpm") or shutil.which("npm")
    if not pnpm:
        say(
            "    没有前端产物，也没找到 pnpm / npm。\n"
            "    服务仍可启动，但只提供 API。要界面就装 Node.js 后重跑：\n"
            "    https://nodejs.org/"
        )
        return
    say("    构建前端（首次约 15 秒）")
    if run([pnpm, "--dir", str(FRONTEND), "build"]) != 0:
        say("    前端构建失败，服务仍会启动，但只有 API。")


def step_chromium(target: Path, skip: bool) -> None:
    if skip:
        say("    跳过（--skip-chromium）：PDF 导出会返回 503，界面照常可用")
        return
    if has_chromium():
        say(f"    Chromium 已在 {playwright_browsers_dir()}")
        return
    say("    下载 Chromium（PDF 导出需要，约 150 MB，只下一次）")
    if run([str(target), "-m", "playwright", "install", "chromium"]) != 0:
        say("    下载失败：PDF 导出会返回 503，其余功能正常。可稍后重试。")


# --------------------------------------------------------------------------- 入口

PARSER = argparse.ArgumentParser(
    prog="start.py",
    description="mathgen 一键启动（源码运行）；其余参数原样透传给 python -m app",
    add_help=False,
)
PARSER.add_argument("--reinstall", action="store_true", help="忽略依赖已就绪的标记，强制重装")
PARSER.add_argument(
    "--mirror",
    nargs="?",
    const=DEFAULT_MIRROR,
    default=None,
    metavar="URL",
    help=f"装依赖时用镜像（不带值就是 {DEFAULT_MIRROR}）",
)
PARSER.add_argument("--no-mirror", action="store_true", help="用官方 pypi.org，不用镜像")
PARSER.add_argument("--skip-frontend", action="store_true", help="不构建前端产物")
PARSER.add_argument("--skip-chromium", action="store_true", help="不下载 Chromium")
PARSER.add_argument("--prepare-only", action="store_true", help="只准备环境，不启动服务")
PARSER.add_argument("-h", "--help", action="store_true", help="显示帮助（含 python -m app 的参数）")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mine, forwarded = PARSER.parse_known_args(argv)

    if mine.help:
        PARSER.print_help()
        say("\n以下是透传给服务本身的参数：\n")
        target = venv_python()
        launcher = target if target.is_file() else Path(sys.executable)
        subprocess.call([str(launcher), "-m", "app", "--help"])
        return 0

    say("=" * 62)
    say("  mathgen —— 小学四则运算练习题生成与批改系统")
    say("  一键启动（源码运行）")
    say("=" * 62)

    step(1, 4, "检查 Python")
    step_python()
    step(2, 4, "准备 Python 依赖")
    target = step_deps(mine.reinstall, mine.mirror, mine.no_mirror)
    step(3, 4, "准备前端产物")
    step_frontend(mine.skip_frontend)
    step(4, 4, "准备 PDF 导出用的 Chromium")
    step_chromium(target, mine.skip_chromium)

    if mine.prepare_only:
        say("\n环境已准备好。启动：scripts\\start.ps1（或 python -m app）")
        return 0

    cmd = [str(target), "-m", "app", *forwarded]
    say("")
    try:
        os.execv(str(target), cmd)
    except OSError:
        return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
