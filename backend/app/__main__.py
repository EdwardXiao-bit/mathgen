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
import ipaddress
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

__all__ = ["main", "resolve_data_dir", "resolve_static_dir", "lan_ip",
           "lan_ips", "wait_until_ready", "announce_ready"]

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


#: Windows 移动热点（ICS）在 AP 侧的固定网段：手机连热点后，**只有这个地址是可达的**。
#: 它排在候选列表最前面，理由见 `lan_ips()`；这条知识来自 M4 的手机实测
#: （`backend/tests/e2e_m4/REPORT-task-30.md`：热点下唯一能通的是 `192.168.137.1`）。
ICS_AP_NETWORK = ipaddress.ip_network("192.168.137.0/24")


def _default_route_ip() -> str | None:
    """默认路由出口网卡的地址（UDP connect 不发包，只让内核选路）。失败返回 None。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return sock.getsockname()[0]
    except OSError:
        return None


#: `ipconfig` 里 IPv4 那一行（中英文系统都能命中：`IPv4 地址` / `IPv4 Address`）。
_IPV4_LINE = re.compile(r"IPv4[^:：]*[:：]\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})")


def _system_ipv4_addresses() -> list[str]:
    """从系统命令里捞 IPv4，作为主机名解析的补充。

    为什么要多这一路：Windows 的 `gethostbyname_ex(主机名)` **不一定**把每块网卡都列出来
    （尤其是热点那块由 ICS 临时创建的适配器）。手机连热点时唯一可达的就是它，漏了就白报地址。
    `ipconfig` 的输出块里每个适配器都有一行 IPv4，按行抓即可；POSIX 用 `ip -4 -o addr`。
    任何异常都吞掉——这只是「多给一条线索」，不能让它挡住启动。
    """
    commands = [["ipconfig"]] if sys.platform == "win32" else [["ip", "-4", "-o", "addr"]]
    for command in commands:
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        output = done.stdout or ""
        found = _IPV4_LINE.findall(output)
        if not found and sys.platform != "win32":
            found = re.findall(r"inet\s+([0-9]{1,3}(?:\.[0-9]{1,3}){3})", output)
        if found:
            return found
    return []


def _ipv4_addresses() -> list[str]:
    """本机的 IPv4 候选：默认路由出口 + 主机名解析 + 系统命令（去重、保序）。

    为什么不止一块网卡：老师的笔记本常见「连着校园网 + 开着热点」——默认路由走校园网，
    而手机只连得上热点那块网卡（`192.168.137.1`）。只报默认路由那一块，学生就永远连不上。
    这也是用户 2026-11-04 报「笔记本连校园网、手机连热点，扫码打不开」的根因。
    """
    ordered: list[str] = []
    primary = _default_route_ip()
    if primary:
        ordered.append(primary)
    try:
        ordered.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    ordered.extend(_system_ipv4_addresses())
    return ordered


def _rank_ip(ip: str, primary: str | None) -> tuple[int, str]:
    """给候选地址排个序，并给出一句人话理由（排序 = 手机越可能连上越靠前）。

    顺序：热点 AP（0）> 默认路由出口（1）> 192.168 / 172.16 / 10 网段（2-4）> 其他（5）。
    回环与链路本地排在最后（7/8），调用方据此剔除 —— 它们可能恰好就是默认路由
    （例如没联网时 `10.255.255.255` 的探测会落回 127.0.0.1），所以必须**先**判掉。
    """
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return 9, ""
    if parsed.is_loopback:
        return 7, "回环地址"
    if parsed.is_link_local:
        return 8, "169.254 链路本地"
    if parsed in ICS_AP_NETWORK:
        return 0, "手机连这台电脑的热点，用这条"
    if ip == primary:
        return 1, "默认路由出口（电脑自己上网用的那块网卡）"
    if parsed in ipaddress.ip_network("192.168.0.0/16"):
        return 2, "常见家用/局域网网段"
    if parsed in ipaddress.ip_network("172.16.0.0/12"):
        return 3, "常见家用/局域网网段"
    if parsed in ipaddress.ip_network("10.0.0.0/8"):
        return 4, "常见校园网/公司网段"
    return 5, ""


def lan_ips() -> list[dict[str, object]]:
    """本机可作局域网地址的 IPv4，已被筛过并排好序。

    返回 `[{"ip": "192.168.137.1", "reason": "手机连这台电脑的热点，用这条"}, ...]`：
    - 回环与 169.254 链路本地**直接剔除**（列出来只会让老师挑错地址）；
    - Windows 热点的 AP 地址排最前，其次是默认路由出口，然后才是其他网卡；
    - `reason` 是给老师看的一句话，用来在横幅里说清「哪条给手机用」。
    """
    primary = _default_route_ip()
    scored: list[tuple[int, int, dict[str, object]]] = []
    seen: set[str] = set()
    for index, ip in enumerate(_ipv4_addresses()):
        if not ip or ip in seen:
            continue
        seen.add(ip)
        rank, reason = _rank_ip(ip, primary)
        if rank >= 7:  # 回环 / 链路本地：列出来也没用
            continue
        scored.append((rank, index, {"ip": ip, "reason": reason}))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]


def lan_ip() -> str | None:
    """本机最可能被学生连上的那个局域网地址；一个都没有时返回 None。"""
    candidates = lan_ips()
    return str(candidates[0]["ip"]) if candidates else None


# --------------------------------------------------------------------------- 版本与横幅


def _version() -> str:
    """当前版本号。

    以 ``app.__version__`` 为准：它是对外版本号的单点定义（D82），发布流水线会校验它
    与 ``pyproject.toml``、``frontend/package.json``、``npm/package.json`` 一致。

    以前这里优先读安装元数据，于是在「源码目录 + 早先 ``pip install -e`` 过」的机器上，
    横幅会显示上一次装包时的版本（比如已经发到 rc3 了还写 rc1），和入口页上标出来的
    版本对不上。
    """
    from app import __version__

    return __version__


def _banner(urls: dict[str, str], data_file: Path, host: str, mounted: bool,
            port: int = DEFAULT_PORT) -> str:
    lines = [
        "",
        "  mathgen v" + _version() + " 正在启动",
        "",
        f"  入口页    {urls['home']}",
        f"  老师端    {urls['teacher']}",
        f"  学生端    {urls['student']}",
        f"  接口文档  {urls['docs']}",
        "",
        f"  数据文件  {data_file}",
    ]
    if host in {"127.0.0.1", "localhost"}:
        # 一键脚本默认就是局域网模式；直连 CLI 的人若用默认值，这里要说清楚为什么手机连不上。
        lines += [
            "",
            "  现在只有这台电脑能打开。要让学生用手机连，用局域网模式启动：",
            "    Windows:       powershell -ExecutionPolicy Bypass -File scripts\\start.ps1",
            "    macOS / Linux: ./scripts/start.sh",
            "  启动窗口里会列出手机能连的那几条地址。",
        ]
    if host not in {"127.0.0.1", "localhost"}:
        candidates = lan_ips()
        if candidates:
            lines += ["", "  学生在别的设备上答题（同一局域网；手机连这台电脑的热点就用第一条）："]
            for item in candidates:
                url = f"http://{item['ip']}:{port}/student"
                reason = item["reason"]
                lines.append(f"    {url:<34} {reason}" if reason else f"    {url}")
    if not mounted:
        lines += [
            "",
            "  提醒：没找到前端产物，现在只提供 API。",
            "        源码运行时先执行  pnpm --dir frontend build",
            "        用 npm / Docker 安装的版本自带界面，不该看到这条。",
        ]
    lines += ["", "  按 Ctrl+C 停止", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- 启动就绪
#
# 为什么需要这一段：uvicorn 的 `run()` 会先打日志再真正 bind + listen，冷启动
# （导入 FastAPI / SQLAlchemy、建库、加载前端产物）在本机实测能到十几秒。
# 早先的做法是「打印横幅 → 等 1.5 秒 → 打开浏览器」，慢一点的机器上浏览器
# 抢在监听之前发请求，看到的是「无法访问此网站」。改成「等端口真的能连上再开」。

#: 最多等多久（秒）。冷启动最慢的一次实测约 15 秒，留足余量。
DEFAULT_STARTUP_TIMEOUT = 60.0

#: 探测间隔（秒）。
READINESS_PROBE_INTERVAL = 0.1


def _readiness_host(host: str) -> str:
    """探测时连哪个地址：监听在 `0.0.0.0` / `::` 时本机走回环地址。"""
    return "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host


def wait_until_ready(host: str, port: int, *,
                     timeout: float = DEFAULT_STARTUP_TIMEOUT,
                     interval: float = READINESS_PROBE_INTERVAL) -> bool:
    """等端口真的能建连，最多 `timeout` 秒；连上了返回 True。

    只看 TCP 握手：uvicorn 先 bind + listen 再跑 lifespan startup，所以
    连得上就说明浏览器此刻发请求会被正常排队处理，而不是「连接被拒绝」。
    """
    target = _readiness_host(host)
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((target, port), timeout=1.0):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)


def _say(message: str) -> None:
    """打印并立刻刷新：被重定向到文件时（`nohup`、脚本里重定向）才不会丢。"""
    print(message, flush=True)


def browser_target(urls: dict[str, str], host: str, port: int) -> str:
    """该给浏览器、该让老师复制的那条地址。

    绑在 ``0.0.0.0`` 上时不能给 ``127.0.0.1``：二维码是按请求的 Host 现编的，老师若用
    本机地址打开老师端，发卷页的二维码里也会是本机地址，学生手机扫了连不上。所以局域网
    模式下优先用「手机最可能连的那条」——``lan_ips()`` 的第一条（热点地址排在前面）。
    """
    if host in {"0.0.0.0", "::", ""}:
        candidates = lan_ips()
        if candidates:
            return f"http://{candidates[0]['ip']}:{port}/"
    return urls["home"]


def announce_ready(urls: dict[str, str], host: str, port: int, open_browser: bool, *,
                   timeout: float = DEFAULT_STARTUP_TIMEOUT,
                   out=_say) -> bool:
    """服务起来之后再报信、再开浏览器；没起来就不开（免得看到连接错误）。

    返回是否就绪，方便单独测。`out` 可替换，测试里用来收集输出。
    """
    if not wait_until_ready(host, port, timeout=timeout):
        out(f"\n  等了 {int(timeout)} 秒还没起来，先不打开浏览器。"
            f"请确认端口 {port} 没有被别的程序占着。\n")
        return False
    target = browser_target(urls, host, port)
    out(f"\n  启动完成  {target}\n")
    if open_browser:
        webbrowser.open(target)
    return True


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
    print(_banner(urls, data_file, args.host, static_dir is not None, args.port), flush=True)

    # 打开入口页而不是老师端：入口页上有「进入使用」与「我是学生」两个门
    # （`app/services/static_site.py` 的 `WELCOME_ENTRY`；没有公告页时它回落 index.html）。
    # 顺序很关键：uvicorn 还没监听就开浏览器，慢机器上会看到「无法访问此网站」，
    # 所以放到后台线程里先等端口就绪（见 `announce_ready`）。
    lan_mode = args.host in {"0.0.0.0", "::"}
    auto_open = not args.no_browser and (
        args.host in {"127.0.0.1", "localhost"} or (lan_mode and bool(lan_ips()))
    )
    threading.Thread(
        target=announce_ready,
        args=(urls, args.host, args.port, auto_open),
        name="mathgen-startup",
        daemon=True,
    ).start()

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
