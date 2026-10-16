"""离线守卫（task-49 / NFR-9）：把「非环回」的网络访问变成一次**立刻失败且可辨认**的异常。

为什么需要它
------------
`netsh` / `Disable-NetAdapter` / `route add` 都要管理员权限，本会话没有。所以「断网」
只能在进程层面造：把本文件所在目录放进某个进程的 `PYTHONPATH`，Python 启动时就会自动
导入 `sitecustomize`，该进程（以及它在本进程内拉起的 Chromium，见下）从此访问不了
外网，而**别的进程不受影响** —— 判据进程因此能保持联网，在同一时间窗里给出
「没装守卫时外网是通的」这条对照（V9）。

守卫做两件事
------------
1. **Python 层**：给 `socket.getaddrinfo` / `socket.socket.connect` / `connect_ex` /
   `socket.create_connection` / `socket.socket.sendto` 打补丁。目标是**环回**就放行
   （`None`、空串、`localhost`、`*.localhost`、`127.0.0.0/8`、`::1`），否则抛
   `OfflineBlocked`（带 `operation` / `target`，消息前缀 `[offline-guard]`）。
   这也是「断网自证」的判据基础：故意打外网的请求必须**失败**，而且失败的异常类型
   必须是 `OfflineBlocked` —— 这样「我真拦住了」与「网本来就不通」才区分得开。
2. **Chromium 层**：产品自己的 Chromium 由服务进程内的 Playwright 拉起
   （`backend/app/export/browser.py`），Python 层的 socket 补丁管不到它。所以这里给
   `playwright.sync_api._generated.BrowserType.launch` 打补丁，强制追加死代理与
   `--host-resolver-rules`（见 `CHROMIUM_OFFLINE_ARGS`）。

自证与取证
----------
* `MATHGEN_OFFLINE_LOG=<file>`：每进程把 `{"guard","pid","blocked","chromium_args"}`
  写成 JSON（建议文件名带 pid，避免多进程互踩）。
* `MATHGEN_OFFLINE_GUARD=0`：关掉守卫（用于对照实验）。
* 进程内可直接读 `blocked_entries()` / `forced_chromium_args()`。

本方法**不能**发现什么（写进报告，别让读者高估它）
--------------------------------------------------
* 只拦本进程（及其子进程里被同一钩子覆盖的 Python 代码）；系统里别的进程照常上网。
* 只拦「按主机名/地址去连」的路径；已经建立好的连接、以及显式配置了本机代理
  （`http://127.0.0.1:7890` 是环回，被放行）的客户端仍能出去。
* Chromium 的补丁只覆盖 `BrowserType.launch`；`launch_persistent_context` 之类不覆盖，
  它的 DNS 由 `--host-resolver-rules` 管，系统的后台流量（自动更新等）只能靠
  `--disable-background-networking` 尽量关掉，不是硬保证。
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import time

LOG_ENV = "MATHGEN_OFFLINE_LOG"
GUARD_ENV = "MATHGEN_OFFLINE_GUARD"

_LOOPBACK_NAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)

#: 强制追加给产品 Chromium 的离线参数。`MAP * 0.0.0.0` 让所有域名解析到不可路由地址，
#: 死代理（127.0.0.1:9）让直连 IP 的 HTTP(S) 也失败。
#:
#: **`--proxy-bypass-list` 是实测踩出来的**：Playwright 一旦用它的 `proxy=` 参数配代理，
#: 就会自动追加 `--proxy-bypass-list=<-loopback>`（即**取消** Chromium 默认的「环回不走
#: 代理」），结果服务自己的 `http://127.0.0.1:PORT/...` 预览页也变成
#: `net::ERR_PROXY_CONNECTION_FAILED`。所以这里**不用** `proxy=`，改成直接给
#: `--proxy-server` + 显式放行环回的 bypass 列表。
CHROMIUM_OFFLINE_ARGS = [
    # 实测（`artifacts/task49_chromium_args_probe.py`）：`MAP * 0.0.0.0` 单独用会把字面
    # IP `127.0.0.1` 也改掉（ERR_ADDRESS_INVALID），`EXCLUDE localhost` 救不了字面 IP；
    # 必须先给 `127.0.0.1` 一条恒等规则，环回预览才打得开而外网仍然断。
    "--host-resolver-rules=MAP localhost 127.0.0.1, MAP 127.0.0.1 127.0.0.1, MAP * 0.0.0.0",
    "--proxy-server=http://127.0.0.1:9",
    "--proxy-bypass-list=localhost;127.0.0.1;<local>",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--no-first-run",
    "--no-default-browser-check",
]

DEAD_PROXY = "http://127.0.0.1:9"


class OfflineBlocked(OSError):
    """被离线守卫拒绝的连接尝试（**带标记**，判据靠它区分「守卫拦的」与「网本来不通」）。"""

    kind = "offline-guard"

    def __init__(self, operation: str, target) -> None:
        self.operation = operation
        self.target = str(target)
        super().__init__(f"[offline-guard] 拒绝 {operation} {self.target}")


def is_loopback_host(host) -> bool:
    """环回/本地判定。宽松到「空主机名也算本地」，严到「域名一律算外部」。"""
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii")
        except UnicodeDecodeError:
            return False
    text = str(host).strip()
    if not text:
        return True
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    lowered = text.lower()
    if lowered in _LOOPBACK_NAMES or lowered.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


_BLOCKED: list[dict] = []
_FORCED_CHROMIUM_ARGS: list[str] = []
_INSTALLED = False


def _record(kind: str, detail: dict) -> None:
    _BLOCKED.append(
        {
            "kind": kind,
            "detail": detail,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    _write_log()


def _write_log() -> None:
    base = os.environ.get(LOG_ENV)
    if not base:
        return
    # **文件名带 pid**：同一份日志会被多个进程写（判据进程、服务进程、各探针），
    # 共用一个文件名时后启动的进程会把前一个的证据整份覆盖掉（本任务踩过：诊断用
    # 的 `python -c` 也加载了守卫，把探针刚写的 `chromium-launch` 记录冲掉了）。
    path = f"{base}.{os.getpid()}.json"
    payload = {
        "guard": "offline",
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "blocked": _BLOCKED,
        "chromium_args": _FORCED_CHROMIUM_ARGS,
    }
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def blocked_entries() -> list[dict]:
    return list(_BLOCKED)


def forced_chromium_args() -> list[str]:
    return list(_FORCED_CHROMIUM_ARGS)


# ---------------------------------------------------------------- Python 层补丁

_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_create_connection = socket.create_connection
_real_sendto = socket.socket.sendto


def _host_of(address):
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if not is_loopback_host(host):
        _record("getaddrinfo", {"host": str(host), "port": port})
        raise OfflineBlocked("getaddrinfo", f"{host}:{port}")
    return _real_getaddrinfo(host, port, *args, **kwargs)


def _guarded_connect(self, address):
    if not is_loopback_host(_host_of(address)):
        _record("connect", {"address": repr(address)[:160]})
        raise OfflineBlocked("connect", repr(address)[:160])
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not is_loopback_host(_host_of(address)):
        _record("connect_ex", {"address": repr(address)[:160]})
        raise OfflineBlocked("connect_ex", repr(address)[:160])
    return _real_connect_ex(self, address)


def _guarded_create_connection(address, *args, **kwargs):
    if not is_loopback_host(_host_of(address)):
        _record("create_connection", {"address": repr(address)[:160]})
        raise OfflineBlocked("create_connection", repr(address)[:160])
    return _real_create_connection(address, *args, **kwargs)


def _guarded_sendto(self, data, *args):
    address = args[-1] if args else None
    if address is not None and not is_loopback_host(_host_of(address)):
        _record("sendto", {"address": repr(address)[:160]})
        raise OfflineBlocked("sendto", repr(address)[:160])
    return _real_sendto(self, data, *args)


# ------------------------------------------------------------- Chromium 层补丁


def _patch_playwright() -> str:
    try:
        from playwright.sync_api import _generated  # type: ignore
    except Exception as exc:  # pragma: no cover - 环境问题由探针如实记录
        return f"playwright 不可导入：{type(exc).__name__}: {exc}"
    original = getattr(_generated.BrowserType, "launch")
    if getattr(original, "_mathgen_offline", False):
        return "已经打过补丁"
    if getattr(original, "_mathgen_original", False):  # pragma: no cover
        return "已经被别的补丁包住"

    def launch(self, **kwargs):
        args = list(kwargs.get("args") or [])
        kwargs["args"] = list(CHROMIUM_OFFLINE_ARGS) + args
        # 不用 Playwright 的 `proxy=`（它会追加 `<-loopback>` 把环回也赶进代理）。
        kwargs.pop("proxy", None)
        _FORCED_CHROMIUM_ARGS[:] = list(kwargs["args"])
        _record(
            "chromium-launch",
            {"args": kwargs["args"], "proxy": DEAD_PROXY},
        )
        return original(self, **kwargs)

    launch._mathgen_offline = True  # type: ignore[attr-defined]
    _generated.BrowserType.launch = launch
    return "已打补丁"


def install() -> str:
    """装上守卫；幂等。返回一行人类可读的安装结论（供探针写进 JSON）。"""
    global _INSTALLED
    if os.environ.get(GUARD_ENV, "1") == "0":
        return "守卫被 MATHGEN_OFFLINE_GUARD=0 关掉了"
    if not _INSTALLED:
        socket.getaddrinfo = _guarded_getaddrinfo
        socket.socket.connect = _guarded_connect
        socket.socket.connect_ex = _guarded_connect_ex
        socket.create_connection = _guarded_create_connection
        socket.socket.sendto = _guarded_sendto
        _INSTALLED = True
    verdict = _patch_playwright()
    _record("install", {"python_guard": "on", "playwright": verdict})
    _write_log()
    return f"python 层已拦，playwright：{verdict}"


VERDICT = install()
