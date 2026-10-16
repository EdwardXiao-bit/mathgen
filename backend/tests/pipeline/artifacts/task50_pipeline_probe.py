"""task-50 复现/回归**探针**（原始证据生成器，不是 unittest 判据）。

用法：

    & $VPY backend/tests/pipeline/artifacts/task50_pipeline_probe.py --world pipe --json <out.json>
    & $VPY backend/tests/pipeline/artifacts/task50_pipeline_probe.py --world boot --json <out.json>

两个世界：

* `--world pipe`：**旧行为的忠实复刻** —— 自己 `Popen(..., stdout=PIPE, stderr=STDOUT)`
  起 uvicorn，整个生命周期**无人读**这段管道。它不依赖 `_uvicorn_boot` 的代码，所以修复
  前后都能跑，是**留在仓库里的负对照**。
* `--world boot`：走**仓库当前**的 `boot_uvicorn(...)`（修复后带 `log_path=`）。修复前它同样
  把输出写进无人读的管道 → 客户端超时、日志近似 0 字节；修复后输出落到文件 → 客户端拿到
  响应、日志里读得到那条输出。

要不要传 `log_path` 由**签名探测**决定（`inspect.signature`），于是本脚本在修复前也能如实
跑出「旧世界」的结论，而不是因为参数不存在直接崩掉。

场面参数：`/boom` 让服务端先写再回（NFR：先日志后响应），`/explode` 让 uvicorn 打
>4KB 的 traceback。
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))   # artifacts -> pipeline -> tests -> backend
REPO = os.path.dirname(BACKEND)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests._uvicorn_boot import boot_uvicorn, probe_free_port       # noqa: E402

VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):
    VPY = sys.executable

MARKER_ENV = "MATHGEN_PIPELINE_MARKER"
BIG_CHARS = "30000"
TRACE_CHARS = "8000"


def child_env(marker: str) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = BACKEND
    env["PYTHONIOENCODING"] = "utf-8"
    env[MARKER_ENV] = marker
    env["MATHGEN_PIPELINE_BIG_CHARS"] = BIG_CHARS
    env["MATHGEN_PIPELINE_TRACE_CHARS"] = TRACE_CHARS
    return env


def command_for_port(port: int) -> list:
    return [VPY, "-m", "uvicorn", "tests.pipeline.fixture_app:app",
            "--app-dir", BACKEND, "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning"]


def kill_tree(process) -> None:
    """连**整棵进程树**一起收掉。

    本机 venv 的 `python.exe` 是 launcher：它再起一个真解释器子进程，`Popen.pid` 是 launcher。
    只 `terminate()` launcher 会留下那个（可能正卡在写管道上的）子进程占着端口。
    """
    if process is None:
        return
    pid = process.pid
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, text=True, check=False)
    try:
        process.wait(timeout=10)
    except Exception:                                              # noqa: BLE001
        pass


def wait_ready(port: int, *, deadline: float) -> dict:
    url = f"http://127.0.0.1:{port}/api/papers"
    last = ""
    while time.monotonic() < deadline:
        try:
            got = httpx.get(url, timeout=2.0)
            if got.status_code == 200:
                return {"ready": True, "status": got.status_code}
            last = f"status={got.status_code}"
        except Exception as exc:                                   # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.3)
    return {"ready": False, "last": last}


def request(port: int, path: str, *, timeout: float) -> dict:
    started = time.monotonic()
    try:
        got = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=timeout)
        return {"path": path, "seconds": round(time.monotonic() - started, 3),
                "status": got.status_code, "body": got.text[:200]}
    except Exception as exc:                                       # noqa: BLE001
        return {"path": path, "seconds": round(time.monotonic() - started, 3),
                "error": type(exc).__name__, "message": str(exc)[:200]}


def read_pipe_after_kill(process) -> str:
    """旧设施收尾时的读法：进程杀掉之后一次性读干管道。"""
    stream = getattr(process, "stdout", None)
    if stream is None:
        return ""
    try:
        return stream.read() or ""
    except (OSError, ValueError):
        return ""


def peek_pipe(process) -> dict:
    """**不动它**地看管道里到底积了多少字节（非阻塞读）。

    这一步是为了把「管道写不进去」从一个说法变成一个数字：服务端正卡在 `WriteFile` 上时，
    能读到的字节数就是匿名管道的实际缓冲上限（Windows 约 4KB），也就是它写出去的那部分。
    读走这些字节会**顺手解开**它的阻塞 —— 所以只在收尾阶段做，并且单独记一笔。
    """
    stream = getattr(process, "stdout", None)
    if stream is None:
        return {"bytes_readable": None, "note": "stdout 不是管道（新设施：已重定向到文件）"}
    try:
        fd = stream.fileno()
        os.set_blocking(fd, False)
        raw = os.read(fd, 1 << 20)
        os.set_blocking(fd, True)
        return {"bytes_readable": len(raw), "head": raw[:200].decode("utf-8", "replace")}
    except (OSError, ValueError) as exc:
        return {"bytes_readable": None, "note": f"{type(exc).__name__}: {exc}"}


def port_still_open(port: int) -> bool:
    """收尾之后端口是否还有活监听者（用来证明没有泄漏的 uvicorn 子进程）。"""
    import socket as _socket

    with _socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", choices=("pipe", "boot"), required=True)
    parser.add_argument("--json", required=True)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args(argv)

    marker = "task50-" + uuid.uuid4().hex
    env = child_env(marker)
    report: dict = {
        "task": "task-50",
        "world": args.world,
        "marker": marker,
        "python": VPY,
        "client_timeout_s": args.timeout,
        "big_chars": BIG_CHARS,
        "trace_chars": TRACE_CHARS,
        "boot_uvicorn_supports_log_path": "log_path" in inspect.signature(boot_uvicorn).parameters,
    }

    process = None
    log_path = os.path.join(os.path.dirname(os.path.abspath(args.json)),
                            f"task50-{args.world}-server.log")
    try:
        if args.world == "pipe":
            port = probe_free_port()
            process = subprocess.Popen(
                command_for_port(port), cwd=REPO, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace")
            report["boot"] = {"how": "Popen(stdout=PIPE, stderr=STDOUT)，全程无人读",
                              "port": port, "popen_pid": process.pid}
        else:
            port = probe_free_port()
            kwargs = {"log_path": log_path} if report["boot_uvicorn_supports_log_path"] else {}
            process, port, attempts = boot_uvicorn(
                command_for_port, cwd=REPO, env=env, timeout=30.0, **kwargs)
            report["boot"] = {
                "how": "boot_uvicorn(%s)" % ("log_path=…" if kwargs else "无 log_path（修复前签名）"),
                "port": port, "popen_pid": process.pid,
                "stdout_is_pipe": getattr(process, "stdout", None) is not None,
                "attempts": [{k: v for k, v in a.items() if k != "output"} for a in attempts],
            }
        report["boot"]["ready"] = wait_ready(port, deadline=time.monotonic() + 30.0)

        if report["boot"]["ready"].get("ready"):
            report["pid_route"] = request(port, "/pid", timeout=args.timeout)
            report["boom"] = request(port, "/boom", timeout=args.timeout)
            report["explode"] = request(port, "/explode", timeout=args.timeout)
        report["after_requests"] = {
            "process_alive": process.poll() is None,
            "stdout_is_pipe": getattr(process, "stdout", None) is not None,
        }
        # 收尾前的「不动它」探针：管道里积了多少字节 = 服务端写不出去的那部分。
        report["pipe_peek_before_kill"] = peek_pipe(process)

        if args.world == "boot" and report["boot_uvicorn_supports_log_path"]:
            kill_tree(process)
            text = ""
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            report["log"] = {"source": "file", "path": log_path,
                             "bytes": len(text.encode("utf-8")),
                             "marker_present": marker in text,
                             "traceback_present": "Traceback" in text,
                             "head": text[:400], "tail": text[-400:]}
        else:
            kill_tree(process)
            text = read_pipe_after_kill(process)
            report["log"] = {"source": "pipe-after-kill", "path": None,
                             "bytes": len(text.encode("utf-8")),
                             "marker_present": marker in text,
                             "traceback_present": "Traceback" in text,
                             "head": text[:400], "tail": text[-400:]}
        report["port_still_open_after_kill"] = port_still_open(port)
    finally:
        if process is not None and process.poll() is None:
            kill_tree(process)

    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ("world", "boot", "boom", "explode", "log")
                      if k in report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
