r"""写锁探针第 3 步：把服务端 stderr 落到**文件**（不是没人读的 PIPE），看它在锁下到底做了什么。

第 2 步结论：
* 进程内 `TestClient`（同一个产品 app）在锁下的 `POST /api/attempts` **7.38 秒**
  就抛 `OperationalError: (sqlite3.OperationalError) database is locked`
  （SQL 是 `SELECT name FROM sqlite_master …`，即 `ensure_attempt_schema` 的
  `inspect()`），锁一释放同一条请求 46ms 就 201；
* 真机 uvicorn 上同一条请求：`/openapi.json` 247ms 就 200（服务活着），
  但提交请求 **47 秒都没回来**，锁释放后也不恢复，服务端日志 0 字节。

文件版 stderr 可以把「服务端到底有没有抛异常、什么时候抛」直接钉死。

运行：
    .\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe3.py
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent            # backend/tests
BACKEND = TESTS.parent                # backend
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS.parent))

from tests.perf import _perf_support as perf          # noqa: E402

CONFIG = {"ops": ["+", "-"], "ranges": ["R2"], "question_type": "oral"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fresh(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        stale = Path(str(path) + suffix)
        if stale.exists():
            stale.unlink()


def main() -> int:
    db = HERE / "task48-probe3-server.db"
    log = HERE / "task48-probe3-server.log"
    fresh(db)
    if log.exists():
        log.unlink()

    port = free_port()
    env = dict(os.environ)
    env.update({"MATHGEN_DB": str(db), "MATHGEN_PW_TMP": str(REPO / ".tmp-pw"),
                "PYTHONIOENCODING": "utf-8"})
    handle = open(log, "w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env, stdout=handle, stderr=subprocess.STDOUT, text=True)
    base_url = f"http://127.0.0.1:{port}"
    out: dict = {"port": port, "db": str(db), "log": str(log)}

    def log_text() -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    try:
        client = perf.httpx_client(base_url, 10.0)
        ready = None
        for _ in range(80):
            try:
                if client.get("/api/papers").status_code == 200:
                    ready = True
                    break
            except Exception:                          # noqa: BLE001
                pass
            time.sleep(0.25)
        out["ready"] = bool(ready)
        if not ready:
            out["log_text"] = log_text()
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1

        created = client.post("/api/papers", params={"seed": 20261016,
                                                     "title": "task48 写锁探针卷 3"},
                              json=dict(CONFIG, count=perf.PAPER_QUESTIONS))
        paper_id = int(created.json()["id"])
        client.post(f"/api/papers/{paper_id}/publish")
        student = client.post("/api/students", json={
            "name": "写锁探针学生", "student_no": "2026LOCK3"})
        out["student_status"] = student.status_code
        student_id = student.json()["student_id"]
        spec = perf.submit_spec(student_id=student_id, paper_id=paper_id,
                               attempt_uuid=uuid.uuid4().hex, answers=[])

        connection = sqlite3.connect(str(db), isolation_level=None, timeout=3.0)
        connection.execute("BEGIN EXCLUSIVE")
        out["lock_proof"] = perf.lock_is_held(str(db), timeout=0.3)

        box: dict = {"alive": True}
        started = time.perf_counter()

        def request() -> None:
            try:
                response = client.post("/api/attempts", json=spec["json"])
                box["status"] = response.status_code
                box["body"] = response.text[:300]
            except Exception as exc:                   # noqa: BLE001
                box["error"] = f"{type(exc).__name__}: {exc}"
            box["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
            box["alive"] = False

        thread = threading.Thread(target=request, daemon=True, name="probe3")
        thread.start()

        timeline = []
        released_at = None
        for moment in (3.0, 6.0, 9.0, 12.0):
            time.sleep(max(0.0, moment - (time.perf_counter() - started)))
            timeline.append({"t_s": round(time.perf_counter() - started, 1),
                             "client_alive": thread.is_alive(),
                             "log_bytes": len(log_text().encode("utf-8"))})
            if moment == 9.0:
                connection.execute("ROLLBACK")
                connection.close()
                released_at = round(time.perf_counter() - started, 1)
                timeline.append({"t_s": released_at, "event": "锁已释放"})
        thread.join(45.0)
        box["still_alive_after_release"] = thread.is_alive()
        box["waited_s"] = round(time.perf_counter() - started, 1)
        out["timeline"] = timeline
        out["released_at_s"] = released_at
        out["request"] = box
        out["rows"] = len(perf.attempt_rows(str(db)))
        out["log_text"] = log_text()[-4000:]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        handle.close()

    perf.write_evidence("task48-lock-probe3", out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
