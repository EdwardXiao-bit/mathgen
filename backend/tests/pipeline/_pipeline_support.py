"""task-50 判据的支撑：两个世界 + 一条**纯判据**。

两个世界（同一条判据分别喂进去，结论必须相反 —— V11）：

* `old_pipe_no_reader()`：**忠实复刻旧设施** —— `Popen(stdout=PIPE, stderr=STDOUT)` 起
  uvicorn，全程无人读。它不依赖 `_uvicorn_boot` 的实现，所以**修复之后仍然留在仓库里**
  充当负对照。
* `new_file_redirect()`：走仓库当前的 `boot_uvicorn(..., log_path=…)`，输出落到文件。

`server_log_pipeline_problems()` 是判据本体，只有两件事：**客户端拿得到响应** +
**日志非空且确实是这一次那波输出**。缺一件都是没修好。

另外提供 `pipe_popen_sites()`：用 **AST**（不看注释/字符串）枚举「把子进程输出接进管道」
的调用点，供反模式扫描与设施守卫使用。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))          # backend/tests/pipeline
BACKEND = os.path.dirname(os.path.dirname(HERE))           # backend
REPO = os.path.dirname(BACKEND)                            # mathgen
ARTIFACTS = os.path.join(HERE, "artifacts")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from tests._uvicorn_boot import probe_free_port, read_server_log        # noqa: E402

PY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

WORLD_OLD = "old-pipe-no-reader"
WORLD_NEW = "new-file-redirect"
BOOM_LINE = "BOOM-LINE marker="
EXPLODE_LINE = "EXPLODE-LINE marker="
BIG_CHARS = 30000
TRACE_CHARS = 8000
DEFAULT_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# 起停
# ---------------------------------------------------------------------------

def fixture_env(*, marker: str, big_chars: int = BIG_CHARS,
                trace_chars: int = TRACE_CHARS) -> dict:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": BACKEND,
        "PYTHONIOENCODING": "utf-8",
        "MATHGEN_PIPELINE_MARKER": marker,
        "MATHGEN_PIPELINE_BIG_CHARS": str(big_chars),
        "MATHGEN_PIPELINE_TRACE_CHARS": str(trace_chars),
    })
    return env


def fixture_command(port: int, *, log_level: str = "warning") -> list:
    return [PY, "-m", "uvicorn", "tests.pipeline.fixture_app:app",
            "--app-dir", BACKEND, "--host", "127.0.0.1", "--port", str(port),
            "--log-level", log_level]


def kill_tree(process) -> None:
    """连**整棵进程树**一起收掉。

    本机 venv 的 `python.exe` 是 launcher：真解释器是它的子进程（实测 `/pid` 报的 pid 与
    `Popen.pid` 不同），只 `terminate()` launcher 会留下那个可能正卡在写管道上的子进程。
    """
    if process is None:
        return
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                   capture_output=True, text=True, check=False)
    try:
        process.wait(timeout=10)
    except Exception:                                                  # noqa: BLE001
        pass


def _wait_ready(port: int, *, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if request(port, "/api/papers", timeout=2.0).get("status") == 200:
            return True
        time.sleep(0.3)
    return False


def old_pipe_no_reader(*, marker: str | None = None, **kwargs) -> dict:
    """旧世界：输出进管道，**没有任何人读**。"""
    marker = marker or "old-" + uuid.uuid4().hex[:12]
    port = probe_free_port()
    process = subprocess.Popen(
        fixture_command(port), cwd=REPO, env=fixture_env(marker=marker, **kwargs),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    return {"kind": WORLD_OLD, "marker": marker, "port": port, "process": process,
            "log_path": None, "how": "Popen(stdout=PIPE, stderr=STDOUT)，全程无人读"}


def new_file_redirect(*, marker: str | None = None, log_path: str | None = None,
                      timeout: float = 40.0, **kwargs) -> dict:
    """新世界：走仓库当前的 `boot_uvicorn`，输出落到文件。"""
    from tests._uvicorn_boot import boot_uvicorn

    marker = marker or "new-" + uuid.uuid4().hex[:12]
    path = log_path or os.path.join(
        ARTIFACTS, f"task50-{uuid.uuid4().hex[:10]}.log")
    process, port, attempts = boot_uvicorn(
        fixture_command, cwd=REPO, env=fixture_env(marker=marker, **kwargs),
        timeout=timeout, log_path=path)
    return {"kind": WORLD_NEW, "marker": marker, "port": port, "process": process,
            "log_path": path, "attempts": attempts,
            "how": f"boot_uvicorn(log_path={os.path.basename(path)})"}


# ---------------------------------------------------------------------------
# 观测
# ---------------------------------------------------------------------------

def request(port: int, path: str, *, timeout: float,
            capture_body: bool = False) -> dict:
    import httpx

    started = time.monotonic()
    try:
        got = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=timeout)
        result = {"path": path, "status": got.status_code,
                  "seconds": round(time.monotonic() - started, 3)}
        if capture_body:
            result["body"] = got.text[:500]
        return result
    except Exception as exc:                                           # noqa: BLE001
        return {"path": path, "error": type(exc).__name__,
                "seconds": round(time.monotonic() - started, 3)}


def read_pipe_after_death(process, *, wait: float = 2.0) -> str:
    """旧设施收尾时的读法：杀进程之后读管道（**有界**，免得没人写时永远等 EOF）。"""
    stream = getattr(process, "stdout", None)
    if stream is None:
        return ""
    fd = stream.fileno()
    chunks: list[bytes] = []
    try:
        os.set_blocking(fd, False)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 1 << 20)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        try:
            os.set_blocking(fd, True)
        except OSError:
            pass
    return b"".join(chunks).decode("utf-8", "replace")


def observe(world: dict, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """跑一遍场景并采证：`/boom`（写大段日志后回 200）+ `/explode`（真实 500）。"""
    observation: dict = {
        "world": world["kind"], "how": world["how"], "marker": world["marker"],
        "port": world["port"], "log_path": world["log_path"], "client_timeout_s": timeout,
        "popen_pid": world["process"].pid,
        "ready": _wait_ready(world["port"], deadline=time.monotonic() + 30.0),
    }
    if observation["ready"]:
        observation["pid_route"] = request(world["port"], "/pid", timeout=5.0,
                                           capture_body=True)
        # 写日志的是**哪个进程**：服务流程自报的 pid/ppid，而不是 `Popen.pid`
        # （本机 venv 的 python.exe 是 launcher，两者实测不同）。
        try:
            identity = json.loads(observation["pid_route"].get("body") or "{}")
        except ValueError:
            identity = {}
        observation["writer_pid"] = identity.get("pid")
        observation["writer_ppid"] = identity.get("ppid")
        observation["pid_route"]["marker"] = identity.get("marker")
        observation["boom"] = request(world["port"], "/boom", timeout=timeout)
        observation["explode"] = request(world["port"], "/explode", timeout=timeout)

    if world["kind"] == WORLD_NEW:
        # 文件：**不终止进程**就能读，这正是它与管道的区别之一。
        log = read_server_log(world["log_path"])
        observation["log_source"] = "file"
    else:
        kill_tree(world["process"])
        log = read_pipe_after_death(world["process"])
        observation["log_source"] = "pipe-after-kill"

    observation["log_bytes"] = len(log.encode("utf-8"))
    observation["log_has_boom_marker"] = (BOOM_LINE + world["marker"]) in log
    observation["log_has_traceback"] = (
        ("Traceback" in log) and ((EXPLODE_LINE + world["marker"]) in log))
    observation["log_head"] = log[:200]
    return observation


# ---------------------------------------------------------------------------
# 判据本体（纯函数：喂观测，吐问题）
# ---------------------------------------------------------------------------

def server_log_pipeline_problems(observation: dict, *, label: str = "") -> list:
    """两件事**同时**成立才算修好：

    1. 客户端**拿得到响应**（`/boom` 与 `/explode` 都有真实状态码，不是超时/连接错误）；
    2. 日志**非空**，且里面确实是**这一次**那波输出（带本次唯一 marker 的 BOOM-LINE +
       这次异常的 traceback）—— 「非空」不能是别的来源凑出来的。

    缺一件都是没修好：只有响应没日志 → 失败原因丢失；只有日志没响应 → 服务端仍被堵住。
    """
    prefix = f"[{label}] " if label else ""
    problems: list[str] = []

    for route in ("boom", "explode"):
        got = observation.get(route) or {}
        if not isinstance(got.get("status"), int):
            problems.append(
                f"{prefix}{route}：客户端没拿到响应（{got.get('error', '未请求')}，"
                f"{got.get('seconds')}s）—— 服务端被堵在写日志上")

    if observation.get("log_bytes", 0) <= 0:
        problems.append(f"{prefix}日志为空（0 字节）—— 失败现场没有留下来")
    else:
        if not observation.get("log_has_boom_marker"):
            problems.append(
                f"{prefix}日志里没有本次的 BOOM-LINE（marker 不匹配）—— "
                f"「非空」不是这一次那条输出凑的")
        if not observation.get("log_has_traceback"):
            problems.append(f"{prefix}日志里没有本次异常的 traceback（EXPLODE-LINE）")
    return problems


# ---------------------------------------------------------------------------
# 反模式扫描（AST，不看注释/字符串）
# ---------------------------------------------------------------------------

_POPEN_NAMES = {"Popen", "run", "call", "check_call", "check_output"}


def _is_pipe(value) -> bool:
    if isinstance(value, ast.Attribute):
        return value.attr == "PIPE"
    if isinstance(value, ast.Name):
        return value.id == "PIPE"
    return False


def pipe_popen_sites(source: str, *, filename: str = "<source>") -> list:
    """枚举「把子进程输出接进管道」的调用点（`stdout=`/`stderr=` 为 PIPE）。

    用 AST 而不是文本匹配：docstring / 注释里提到 `stdout=PIPE` 不算调用点。
    返回 `[{"line", "call", "kwargs"}]`。
    """
    tree = ast.parse(source, filename)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in _POPEN_NAMES:
            continue
        kwargs = [kw.arg for kw in node.keywords
                  if kw.arg in {"stdout", "stderr"} and _is_pipe(kw.value)]
        if kwargs:
            found.append({"line": node.lineno, "call": name,
                          "kwargs": sorted(kwargs)})
    return found


def write_evidence(name: str, payload: dict) -> str:
    os.makedirs(ARTIFACTS, exist_ok=True)
    path = os.path.join(ARTIFACTS, f"{name}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path
