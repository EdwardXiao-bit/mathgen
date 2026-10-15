"""uvicorn 启动引导：把「端口被别人抢走」从偶发失败里救回来（task-33）。

## 它修的是什么

四处套件（`e2e_ui` / `e2e_m3` / `e2e_m4`）原先各自写了一份 `free_port()`：

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

这是**经典 TOCTOU**：它只是问内核要一个「此刻」空闲的端口就立刻关掉，之后到 uvicorn
真正 `bind` 之间有一段窗口。窗口里任何东西（同进程里别的 server、Playwright/Chromium 的
出站连接、httpx 客户端……）都可能把这个端口当自己的临时端口拿走 ——
本机 TIME_WAIT 状态的 `127.0.0.1` 端口常年在百位数，这个池子确实在激烈复用。

## 为什么错误信息会骗人

全树门禁第一次跑时唯一失败是：

    ERROR: [Errno 13] error while attempting to bind on address ('127.0.0.1', 64695):
    [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。

看起来像权限/防火墙。**它不是。** Lead 的受控实验（`[A]` TIME_WAIT + 默认 bind → OK；
`[B]` TIME_WAIT + `SO_REUSEADDR` → OK；`[C]` **已有活监听者** + `SO_REUSEADDR` →
`errno=13 winerror=10013`）说明：TIME_WAIT 无害，10013 在这里的含义就是「端口已被别人占着」。
之所以不报 10048（地址已在使用），是因为 **uvicorn 0.54.0 的 `Config.bind_socket()`
无条件设了 `SO_REUSEADDR`**；Windows 的 `SO_REUSEADDR` 没有 Unix 那种共享语义，
于是「已被占用」被翻译成了「访问权限不允许」。

## 本模块的两条纪律（V11：判据要对两种世界给出不同结论）

1. `looks_like_bind_failure(output)` **按证据**判「是不是 bind 失败」——
   只有输出里出现 10013 / `error while attempting to bind` / `Errno 13` / 地址已在使用
   这类证据才算。**任何其它提前退出一次都不重试**，原样透出（否则会吞掉真 bug）。
2. 重试**有上限**（默认 4 次）。超限后报错写清「疑似端口竞争」，并附试过的端口与每次的
   原始输出；错误信息里点明 10013 的真实含义，免得下一个人去查防火墙。
"""
from __future__ import annotations

import subprocess
import time
import socket

__all__ = [
    "BIND_FAILURE_HINT", "BIND_FAILURE_MARKERS", "MAX_BOOT_ATTEMPTS", "UvicornBootError",
    "boot_uvicorn", "describe_bind_failure", "free_port", "http_ready",
    "looks_like_bind_failure", "probe_free_port",
]

#: 默认重试上限（第一次 + 最多 3 次换端口重试）
MAX_BOOT_ATTEMPTS = 4

#: 「端口被占」在输出里可能留下的证据。宁可窄一点：多认一个词，就多一分吞掉真 bug 的风险。
BIND_FAILURE_MARKERS = (
    "WinError 10013",
    "10013",                       # 其它写法（如不带前缀的 winerror=10013）
    "error while attempting to bind",
    "[Errno 13]",
    "Errno 13",
    "address already in use",
    "[Errno 48]",                  # macOS
    "[Errno 98]",                  # Linux
    "WinError 10048",
    "10048",
)

#: 给「未来的你」的解释：这句话要能拦住「去查防火墙」的念头。
BIND_FAILURE_HINT = (
    "WinError 10013 在 Windows 上通常是「端口已被其他套接字占用」——uvicorn 的 "
    "Config.bind_socket() 无条件设了 SO_REUSEADDR，Windows 下这会把「地址已在使用」"
    "报成「访问权限不允许」。**不是防火墙、也不是权限问题。**"
)


class UvicornBootError(AssertionError):
    """启动失败。

    `AssertionError` 的子类：`setUpClass` 里抛出来仍是 error、仍是红，不会被降级成 skip。
    `attempts` 是每次尝试的 `{"port", "outcome", "output"}`；`port_race` 表示
    「失败原因已判定为端口竞争（bind 失败）」。
    """

    def __init__(self, message: str, *, attempts: list | None = None,
                 port_race: bool = False):
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.port_race = port_race


def probe_free_port(host: str = "127.0.0.1") -> int:
    """向内核要一个**此刻**空闲的端口。

    注意：这只是一次探测，**不保证**之后仍然空闲（这正是需要 `boot_uvicorn` 的原因）。
    调用方应当把它当作「起点」而不是「留位」。
    """
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


#: 旧名字（四处套件原先各自定义的就是它）。保留以便逐处替换时读起来不突兀。
free_port = probe_free_port


def looks_like_bind_failure(output: str | None) -> bool:
    """输出里是否有「端口绑定失败」的证据。

    窄口径：只认已知的 bind 失败标记；**不**把「进程提前退出」本身当端口问题。
    """
    text = output or ""
    return any(marker in text for marker in BIND_FAILURE_MARKERS)


def describe_bind_failure(port: int, output: str | None) -> str:
    """人话版失败描述：真实含义 + 原始输出（截断到 800 字符）。"""
    tail = (output or "").strip()
    return (
        f"端口 {port} 绑定失败（uvicorn 提前退出）。\n"
        f"    {BIND_FAILURE_HINT}\n"
        f"    uvicorn 原始输出：\n{tail[-800:] if tail else '（无输出）'}"
    )


def http_ready(port: int, path: str = "/api/papers", host: str = "127.0.0.1",
               timeout: float = 2.0) -> bool:
    """默认的就绪判据：GET 一次返回 200。任何异常都算「还没就绪」。"""
    import httpx

    try:
        return httpx.get(f"http://{host}:{port}{path}", timeout=timeout).status_code == 200
    except Exception:                                  # noqa: BLE001 - 未就绪就是各种异常
        return False


def _drain(process) -> str:
    """把子进程留下的输出读干（调用前进程必须已退出，否则会阻塞）。"""
    stream = getattr(process, "stdout", None)
    if not stream:
        return ""
    try:
        return stream.read() or ""
    except (OSError, ValueError):
        return ""


def _reap(process, *, kill: bool = False) -> None:
    """收掉失败尝试留下的进程，避免把僵尸/失控的 uvicorn 留给下一次运行。"""
    if process is None or process.poll() is not None:
        return
    try:
        if kill:
            process.kill()
        else:
            process.terminate()
        process.wait(timeout=15)
    except Exception:                                  # noqa: BLE001
        try:
            process.kill()
        except Exception:                              # noqa: BLE001
            pass


def _one_attempt(command_for_port, port: int, *, cwd, env, is_ready, timeout: float,
                 popen, sleep, monotonic) -> tuple[object, str, str]:
    """跑一次尝试。返回 `(进程, 原始输出, 结果)`，结果 ∈ `{"ready", "bind-failure",
    "early-exit", "timeout"}`。**失败时进程已被收掉**（不会泄漏）。"""
    process = popen(
        command_for_port(port), cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if process.poll() is not None:
            output = _drain(process)
            outcome = "bind-failure" if looks_like_bind_failure(output) else "early-exit"
            _reap(process)
            return process, output, outcome
        try:
            if is_ready(port):
                return process, "", "ready"
        except Exception:                              # noqa: BLE001 - 未就绪即异常
            pass
        sleep(0.3)
    # 超时且进程还活着：bind 失败会立刻退出，所以这**不是**端口竞争 → 不重试。
    _reap(process, kill=True)
    return process, _drain(process), "timeout"


def boot_uvicorn(command_for_port, *, cwd, env, is_ready=None,
                 host: str = "127.0.0.1", timeout: float = 40.0,
                 max_attempts: int = MAX_BOOT_ATTEMPTS, first_port: int | None = None,
                 popen=subprocess.Popen, sleep=time.sleep, monotonic=time.monotonic):
    """起一个 uvicorn：**bind 失败就换端口重试（有限次）**，其它失败立刻原样抛出。

    * `command_for_port(port) -> list[str]`：按端口拼命令行（保持调用方对参数的完全控制）。
    * `is_ready(port) -> bool`：就绪判据，默认 `http_ready`（GET `/api/papers` == 200）。
    * `first_port`：**只给负对照用** —— 把第一次尝试强制指向这个（已被占住的）端口，
      用来复现 `free_port()` 探测后被抢走的竞态。默认为 `None`（每次都由内核分配）。

    返回 `(process, port, attempts)`；`attempts` 含每次的端口、结果与原始输出。
    失败抛 `UvicornBootError`（`AssertionError` 子类，`port_race` 标明是否已判定为端口竞争）。
    """
    attempts: list[dict] = []
    for index in range(max(1, max_attempts)):
        port = first_port if (index == 0 and first_port) else probe_free_port(host)
        process, output, outcome = _one_attempt(
            command_for_port, port, cwd=cwd, env=env,
            is_ready=is_ready or http_ready, timeout=timeout,
            popen=popen, sleep=sleep, monotonic=monotonic)
        attempts.append({"port": port, "outcome": outcome, "output": output})
        if outcome == "ready":
            return process, port, attempts
        if outcome != "bind-failure":
            reason = ("服务在 {t}s 内未就绪，且进程一直存活（bind 失败会立刻退出，"
                      "因此这**不是**端口竞争）").format(t=timeout) if outcome == "timeout" \
                else "uvicorn 因**非端口原因**提前退出"
            raise UvicornBootError(
                f"{reason}，不重试。\n    端口 {port} 上 uvicorn 的原始输出：\n"
                f"{(output or '（无输出）')[-800:]}",
                attempts=attempts, port_race=False)
        # 只有「有 bind 失败证据」才走到这里：换端口重试

    tried = "\n".join(
        f"    [{i + 1}] port={item['port']} outcome={item['outcome']}\n"
        f"        {(item['output'] or '（无输出）').strip()[-400:]}"
        for i, item in enumerate(attempts))
    raise UvicornBootError(
        f"uvicorn 连续 {len(attempts)} 次都因「端口绑定失败」提前退出 —— **疑似端口竞争**，"
        f"不是防火墙/权限问题。\n    {BIND_FAILURE_HINT}\n"
        f"  试过的端口与每次的原始输出：\n{tried}\n"
        f"  说明：`free_port()` 只是「当时空闲」的探测（TOCTOU）；本 helper 会换端口重试，"
        f"但重试 {len(attempts)} 次仍失败，说明此刻端口争用异常激烈。"
        f"可用 `netstat -ano | findstr \":{attempts[-1]['port']}\"` 看是谁占着。",
        attempts=attempts, port_race=True)
