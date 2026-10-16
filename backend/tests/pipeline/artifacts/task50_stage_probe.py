"""task-50 分步探针：`/boom` 到底卡在哪一步，以及「有人读走之后会不会自己恢复」。

矩阵实验（`task50_pipe_matrix.py`）已经推翻了我最初的模型：
输出 30000 字符时客户端超时、服务整体不再响应，但**整条日志（含换行）都躺在管道里** ——
那就不是「写满 4KB 就卡死」。所以这里把 `/boom` 切成三步，让服务端自己在每一步之后留一句
标记，再看标记停在哪里：

    BOOM-LINE（写大段日志） → AFTER-PRINT（日志写完） → 发响应 → AFTER-RESPONSE

判读：

* 只有 `BOOM-LINE`、且它**不完整** → 卡在写日志（管道满，部分写）。
* `BOOM-LINE` 完整 + `AFTER-PRINT`、没有 `AFTER-RESPONSE` → 卡在**发响应**。
* 三个都在但客户端仍超时 → 卡在响应之后（服务端 socket/协议层）。

然后在**不杀进程**的前提下把管道读干（`peek`），再看服务是否自己恢复 —— 这一步区分
「阻塞在写」与「已经死锁」。最后才杀进程收尾。

用法：`& $VPY backend/tests/pipeline/artifacts/task50_stage_probe.py --big-chars 30000 [--drain]`
"""
from __future__ import annotations

import argparse
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


def kill_tree(process) -> None:
    if process is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                   capture_output=True, text=True, check=False)
    try:
        process.wait(timeout=10)
    except Exception:                                                  # noqa: BLE001
        pass


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


def main(argv: list) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--big-chars", type=int, default=30000)
    parser.add_argument("--drain", action="store_true", help="起后台读者（对照组）")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", default=os.path.join(HERE, "task50-stage-probe.json"))
    args = parser.parse_args(argv)

    marker = "stage-" + uuid.uuid4().hex[:12]
    env = dict(os.environ)
    env.update({"PYTHONPATH": BACKEND, "PYTHONIOENCODING": "utf-8",
                "MATHGEN_PIPELINE_MARKER": marker,
                "MATHGEN_PIPELINE_BIG_CHARS": str(args.big_chars),
                "MATHGEN_PIPELINE_TRACE_CHARS": "8000"})
    port = probe_free_port()
    process = subprocess.Popen(
        [VPY, "-m", "uvicorn", "tests.pipeline.fixture_app:app",
         "--app-dir", BACKEND, "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")

    pump = None
    report: dict = {"task": "task-50", "kind": "分步探针：/boom 卡在哪一步",
                    "big_chars": args.big_chars, "drain": args.drain, "marker": marker,
                    "popen_pid": process.pid, "port": port}
    try:
        report["ready"] = wait_ready(port, time.monotonic() + 30.0)
        if args.drain:
            pump = threading.Thread(target=lambda: _pump(process.stdout), daemon=True)
            pump.start()
        report["pid_before"] = get(port, "/pid", timeout=5.0)
        report["boom"] = get(port, "/boom", timeout=args.timeout)
        report["pid_after_timeout"] = get(port, "/pid", timeout=5.0)

        # 不动进程，先把管道读干 —— 看卡在哪一步，并看服务会不会自己恢复。
        # `first_read_bytes` 单独记：**第一次**非阻塞读拿到的字节数就是管道缓冲的实测容量；
        # 之后的循环里读到的字节是「读走一点、它就再写一点」追上来的，不能算容量。
        stream = process.stdout
        fd = stream.fileno()
        os.set_blocking(fd, False)
        raw = b""
        first_read = None
        try:
            while True:
                chunk = os.read(fd, 1 << 20)
                if first_read is None:
                    first_read = len(chunk)
                if not chunk:
                    break
                raw += chunk
        except BlockingIOError:
            pass
        except OSError as exc:
            report["drain_error"] = f"{type(exc).__name__}: {exc}"
        os.set_blocking(fd, True)
        text = raw.decode("utf-8", "replace")
        report["drained"] = {
            "first_read_bytes": first_read,
            "bytes": len(raw),
            "boom_line_complete": ("payload=" + "x" * min(args.big_chars, 64)) in text and
                                  text.rstrip("\n").endswith("x"),
            "has_after_print": "AFTER-PRINT" in text,
            "has_after_response": "AFTER-RESPONSE" in text,
            "line_count": text.count("\n"),
            "head": text[:160],
            "tail": text[-160:],
        }
        report["pid_after_drain"] = get(port, "/pid", timeout=5.0)
        report["process_alive_after_drain"] = process.poll() is None
    finally:
        if process.poll() is None:
            kill_tree(process)
        if pump is not None:
            pump.join(timeout=2.0)

    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _pump(stream) -> None:
    while True:
        try:
            if not stream.readline():
                return
        except (OSError, ValueError):
            return


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
