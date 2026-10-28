"""mathgen 启动入口（`python -m app` / 安装后的 `mathgen` 命令）。

存在的理由：**打包后的三种运行方式需要同一个启动器**。
源码 checkout 里可以手写
`uvicorn app.main:app --app-dir backend`，但 `pip install` / Docker / `npx mathgen`
装出来的副本里没有 `<repo>/frontend/dist`，写 `data/mathgen.db` 也会写进 site-packages。
本模块把这些路径约定收成一处，让三种安装形态都只需要一条命令。

它做的事情只有三件：

1. 决定**数据文件**放哪（`MATHGEN_DB`）—— 必须在 `import app.main` **之前**设好，
   因为 `app/db.py:31` 是在模块 import 期读一次环境变量的（见 docs/05 的 T-M8-01）。
2. 决定**前端产物**放哪（`MATHGEN_STATIC_DIR`）—— 本地开发默认 `<repo>/frontend/dist`，
   打包安装默认包内 `static/`。
3. 起 uvicorn，并打印老师端 / 学生端 / 接口文档的地址。

刻意**不**做：不做服务守护、不做多进程、不写配置文件。这个应用的使用场景是
「老师在教室的电脑上双击一下就打开」，多余的东西只会增加坏掉的面积。
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

__all__ = ["main", "resolve_data_dir", "resolve_static_dir", "lan_ip"]

#: 打包安装（非源码 checkout）时的默认数据目录：`~/.mathgen`
USER_DATA_DIR = Path.home() / ".mathgen"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


# --------------------------------------------------------------------------- 路径解析


def _is_source_checkout() -> bool:
    """本包是不是跑在源码 checkout 里？

    判据是「`app/` 的父目录旁边有 `frontend/`」——打包安装后不存在这种布局。
    """
    backend_dir = Path(__file__).resolve().parents[1]  # backend/
    return (backend_dir.parent / "frontend").is_dir()


def resolve_data_dir(explicit: str | None) -> Path:
    """数据目录：`--data-dir` > `MATHGEN_DATA_DIR` > 源码布局用 `<backend>/data` > `~/.mathgen/data`。

    注意 `MATHGEN_DB`（整文件路径）由调用方优先处理，它比「目录 + /mathgen.db」更具体。
    """
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get("MATHGEN_DATA_DIR")
    if from_env:
        return Path(from_env).expanduser()
    if _is_source_checkout():
        return Path(__file__).resolve().parents[1] / "data"
    return USER_DATA_DIR / "data"


def resolve_static_dir(explicit: str | None) -> Path | None:
    """前端产物目录：显式 > `MATHGEN_STATIC_DIR` > 源码 `<repo>/frontend/dist` > 包内 `static/`。

    返回 `None` 表示找不到 —— 服务照常起，只是没有界面（`app.main` 会打日志说明）。
    """
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_dir() else None
    from_env = os.environ.get("MATHGEN_STATIC_DIR")
    if from_env:
        candidate = Path(from_env).expanduser()
        return candidate if candidate.is_dir() else None
    # 源码树优先：开发时改的是 `frontend/src`，别让一个过期的打包副本（`app/static`）把它盖住。
    # 装出来的包里没有 `<repo>/frontend/dist`，自然会落到 `app/static`。
    repo_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if repo_dist.is_dir():
        return repo_dist
    packaged = Path(__file__).resolve().parent / "static"
    return packaged if packaged.is_dir() else None


def lan_ip() -> str | None:
    """本机在局域网里的地址（不发包，只让内核选一块网卡）。失败返回 None。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return None


# --------------------------------------------------------------------------- 版本与横幅


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("mathgen-backend")
    except Exception:  # pragma: no cover - 源码直接跑时没有发行版元数据
        from app import __version__

        return __version__


def _banner(urls: dict[str, str], data_file: Path, host: str, mounted: bool) -> str:
    lines = [
        "",
        "  mathgen v" + _version() + " 已启动",
        "",
        f"  入口页    {urls['home']}",
        f"  老师端    {urls['teacher']}",
        f"  学生端    {urls['student']}",
        f"  接口文档  {urls['docs']}",
        "",
        f"  数据文件  {data_file}",
    ]
    if host not in {"127.0.0.1", "localhost"}:
        ip = lan_ip()
        if ip:
            lines += ["", f"  同一局域网的学生可以扫码或访问  http://{ip}:{DEFAULT_PORT}/student"]
    if not mounted:
        lines += [
            "",
            "  提醒：没找到前端产物，现在只提供 API。",
            "        源码运行时先执行  pnpm --dir frontend build",
            "        用 npm / Docker 安装的版本自带界面，不该看到这条。",
        ]
    lines += ["", "  按 Ctrl+C 停止", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- 主流程


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mathgen",
        description="小学四则运算练习题生成与批改系统 —— 本机 / 教室内网服务",
    )
    parser.add_argument("--host", default=os.environ.get("MATHGEN_HOST", DEFAULT_HOST),
                        help=f"监听地址，默认 {DEFAULT_HOST}；要让局域网学生访问用 0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MATHGEN_PORT", DEFAULT_PORT)),
                        help=f"监听端口，默认 {DEFAULT_PORT}")
    parser.add_argument("--data-dir", default=None,
                        help="数据库所在目录，默认源码 `<backend>/data`、安装版 `~/.mathgen/data`")
    parser.add_argument("--db", default=None, help="直接指定 sqlite 文件路径（等价于设 MATHGEN_DB）")
    parser.add_argument("--static-dir", default=None, help="前端产物目录（dist）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不要自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="改代码自动重启（仅开发用）")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug", "trace"])
    parser.add_argument("--version", action="version", version=f"mathgen {_version()}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ① 数据文件：必须在 import app.main 之前落定（app/db.py 在 import 期读环境变量）
    if args.db:
        data_file = Path(args.db).expanduser()
    elif os.environ.get("MATHGEN_DB"):
        data_file = Path(os.environ["MATHGEN_DB"]).expanduser()
    else:
        data_file = resolve_data_dir(args.data_dir) / "mathgen.db"
    os.environ["MATHGEN_DB"] = str(data_file)
    data_file.parent.mkdir(parents=True, exist_ok=True)
    # 顺手把数据**目录**也公布出去：导出通道的中间文件目录会用它
    # （见 `app/services/export_common.export_tmp_dir`——装成 wheel 时
    # `site-packages` 不可写，靠这个变量把中间文件落到数据目录旁边）。
    os.environ.setdefault("MATHGEN_DATA_DIR", str(data_file.parent))

    # ② 前端产物
    static_dir = resolve_static_dir(args.static_dir)
    if static_dir is not None:
        os.environ["MATHGEN_STATIC_DIR"] = str(static_dir)

    # ③ 起服务（用 import string，让 uvicorn 在子进程里也能拿到上面两个环境变量）
    import uvicorn  # 局部 import：保证上面的 env 先落定

    base = f"http://{'127.0.0.1' if args.host in {'0.0.0.0', '::'} else args.host}:{args.port}"
    urls = {
        "home": f"{base}/",
        "teacher": f"{base}/teacher",
        "student": f"{base}/student",
        "docs": f"{base}/docs",
    }
    print(_banner(urls, data_file, args.host, static_dir is not None), flush=True)

    # 打开入口页而不是老师端：入口页上有「进入使用」与「我是学生」两个门
    # （`app/services/static_site.py` 的 `WELCOME_ENTRY`；没有公告页时它回落 index.html）。
    if not args.no_browser and args.host in {"127.0.0.1", "localhost"}:
        threading.Timer(1.5, lambda: webbrowser.open(urls["home"])).start()

    try:
        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level=args.log_level,
        )
    except OSError as exc:  # pragma: no cover - 端口占用等
        print(
            f"\n启动失败：{exc}\n"
            f"端口 {args.port} 可能已被占用，换一个：mathgen --port {args.port + 1}\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
