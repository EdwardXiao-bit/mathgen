"""task-50 判别实验：把「服务端为什么不再响应」钉到具体机制上。

第一版复现（`task50_pipeline_probe.py --world pipe`）给出的结果**自相矛盾**：
客户端 `/boom` 超时，但收尾时从管道里一次读出了 **30075 字节**（整整一条 30000 字符的日志）。
如果管道真的「写满就卡死」，那些字节不可能全部躺在缓冲里。

所以先做矩阵实验，把变量分开：

| 变量 | 取法 | 想区分什么 |
| --- | --- | --- |
| 输出大小 | 2000 / 30000 / 300000 字符 | 小于缓冲 vs 大于缓冲；「刚好写进去」时会不会卡 |
| 有无读者 | 无 / 后台线程持续 read | 「有没有人读」是不是决定性的 |

每个 case 都记录：`/pid`（打印前，证明服务还活着）、`/boom`、`/boom` 之后**再** `/pid`
（区分「服务整体卡死」与「只有那一条请求没回」）、收尾时管道里可读的字节数、杀进程后读到的字节数。

用法：`& $VPY backend/tests/pipeline/artifacts/task50_pipe_matrix.py --json <out.json>`
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
REPO = os.path.dirname(BACKEND)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests._uvicorn_boot import probe_free_port                      # noqa: E402

VPY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(VPY):
    VPY = sys.executable

CASES = (
    ("small-2000-nodrain", 2000, False),
    ("big-30000-nodrain", 30000, False),
    ("big-30000-drain", 30000, True),
    ("huge-300000-nodrain", 300000, False),
    ("huge-300000-drain", 300000, True),
)


class Drain(threading.Thread):
    """后台读者：持续把管道读走（模拟「有人在读」的世界）。"""

    def __init__(self, stream):
        super().__init__(daemon=True, name="task50-matrix-drain")
        self._stream = stream
        self._stop = threading.Event()
        self.bytes = 0

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._stream.readline()
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self.bytes += len(chunk.encode("utf-8", "replace"))

    def stop(self) -> int:
        self._stop.set()
        return self.bytes


def kill_tree(process) -> None:
    if process is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                   capture_output=True, text=True, check=False)
    try:
        process.wait(timeout=10)
    except Exception:                                                  # noqa: BLE001
        pass


def peek(process) -> int | None:
    stream = getattr(process, "stdout", None)
    if stream is None:
        return None
    try:
        fd = stream.fileno()
        os.set_blocking(fd, False)
        raw = os.read(fd, 1 << 22)
        os.set_blocking(fd, True)
        return len(raw)
    except (OSError, ValueError):
        return None


def get(port: int, path: str, timeout: float) -> dict:
    started = time.monotonic()
    try:
        got = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=timeout)
        return {"status": got.status_code, "seconds": round(time.monotonic() - started, 3),
                "body": got.text[:120]}
    except Exception as exc:                                           # noqa: BLE001
        return {"error": type(exc).__name__, "seconds": round(time.monotonic() - started, 3)}


def wait_ready(port: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/papers", timeout=2.0).status_code == 200:
                return True
        except Exception:                                              # noqa: BLE001
            pass
        time.sleep(0.3)
    return False


def run_case(name: str, big_chars: int, drain: bool, *, timeout: float) -> dict:
    marker = "matrix-" + uuid.uuid4().hex[:12]
    env = dict(os.environ)
    env.update({"PYTHONPATH": BACKEND, "PYTHONIOENCODING": "utf-8",
                "MATHGEN_PIPELINE_MARKER": marker,
                "MATHGEN_PIPELINE_BIG_CHARS": str(big_chars),
                "MATHGEN_PIPELINE_TRACE_CHARS": "8000"})
    port = probe_free_port()
    command = [VPY, "-m", "uvicorn", "tests.pipeline.fixture_app:app",
               "--app-dir", BACKEND, "--host", "127.0.0.1", "--port", str(port),
               "--log-level", "warning"]
    process = subprocess.Popen(command, cwd=REPO, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    pump = None
    case: dict = {"case": name, "big_chars": big_chars, "drain": drain,
                  "port": port, "popen_pid": process.pid, "marker": marker}
    try:
        case["ready"] = wait_ready(port, time.monotonic() + 30.0)
        if drain:
            pump = Drain(process.stdout)
            pump.start()
        case["pid_before"] = get(port, "/pid", timeout=5.0)
        case["boom"] = get(port, "/boom", timeout=timeout)
        case["pid_after"] = get(port, "/pid", timeout=5.0)
        case["pipe_bytes_at_peek"] = peek(process)
        if pump is not None:
            case["drained_bytes"] = pump.stop()
        kill_tree(process)
        stream = process.stdout
        try:
            text = (stream.read() or "") if stream is not None else ""
        except (OSError, ValueError):
            text = ""
        case["log_after_kill_bytes"] = len(text.encode("utf-8"))
        case["log_marker_present"] = marker in text
    finally:
        if pump is not None:
            pump.stop()
        if process.poll() is None:
            kill_tree(process)
    return case


def main(argv: list) -> int:
    out = argv[argv.index("--json") + 1] if "--json" in argv else os.path.join(HERE, "task50-pipe-matrix.json")
    timeout = float(argv[argv.index("--timeout") + 1]) if "--timeout" in argv else 8.0
    report = {"task": "task-50", "kind": "判别矩阵：输出大小 x 有无读者",
              "client_timeout_s": timeout, "cases": []}
    for name, big_chars, drain in CASES:
        case = run_case(name, big_chars, drain, timeout=timeout)
        report["cases"].append(case)
        print(json.dumps({k: case[k] for k in ("case", "boom", "pid_after",
                                               "pipe_bytes_at_peek", "log_after_kill_bytes",
                                               "log_marker_present") if k in case},
                         ensure_ascii=False))
        sys.stdout.flush()
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print("wrote " + out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
