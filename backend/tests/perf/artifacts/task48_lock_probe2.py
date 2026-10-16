r"""写锁探针第 2 步：请求到底卡在哪一层（task-48 负对照取证，只跑一次）。

第 1 步（`task48_lock_probe.py`）已经把矛盾量清楚了：

* 裸 `sqlite3` 与**产品那条 SQLAlchemy 引擎**在锁下都在 ~7.4 秒报
  `OperationalError: database is locked`（`timeout` 缺省 5.0 秒，实际等待
  7.4 秒 = SQLite 忙等回调的粒度）；
* 但真机 uvicorn 上**一条**提交在锁下挂了 19 秒还没响应，服务端 stderr 一个字都没有。

所以差异不在数据库层，也不在「引擎没给 timeout」。这个脚本分两段定位：

A. 进程内 `TestClient`（磁盘库）+ 外部 `BEGIN EXCLUSIVE`：
   ① `/openapi.json`（不碰库）多快；② `POST /api/attempts` 是「到点报错」
   还是「一直等」；③ 锁释放后那条请求会不会补完。
B. 真机 uvicorn + 每 40 秒客户端超时、**12 秒后释放锁**：请求是在锁释放的
   瞬间完成（= 一直在等锁），还是根本没在看锁。

运行：
    .\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe2.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent            # backend/tests
BACKEND = TESTS.parent                # backend
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS.parent))

from tests.perf import _perf_support as perf          # noqa: E402

STEPS: dict = {}


def fresh(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        stale = Path(str(path) + suffix)
        if stale.exists():
            stale.unlink()


class Locker:
    """在探针进程里拿住某库的写锁（`BEGIN EXCLUSIVE` 立即取锁）。"""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=3.0)
        self.connection.execute("BEGIN EXCLUSIVE")

    def proof(self, path: Path) -> dict:
        return perf.lock_is_held(str(path), timeout=0.3)

    def release(self) -> None:
        try:
            self.connection.execute("ROLLBACK")
        finally:
            self.connection.close()


def timed(fn) -> dict:
    started = time.perf_counter()
    try:
        value = fn()
        return {"outcome": "ok", "value": value,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1)}
    except Exception as exc:                           # noqa: BLE001
        return {"outcome": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1)}


def row_count(db_path: Path) -> dict:
    """锁被持有时读库自己也会 `database is locked`（EXCLUSIVE 连读都不放），如实记。"""
    return timed(lambda: len(perf.attempt_rows(str(db_path))))


def in_a_thread(fn, timeout: float) -> dict:
    """跑 fn，最多等 timeout 秒；到点就如实报「还在等」。"""
    box: dict = {"alive": True}
    started = time.perf_counter()

    def run() -> None:
        result = timed(fn)
        box.update(result)
        box["alive"] = False

    thread = threading.Thread(target=run, daemon=True, name="probe2")
    thread.start()
    thread.join(timeout)
    box["waited_s"] = round(time.perf_counter() - started, 1)
    box["still_blocked"] = thread.is_alive()
    return box


# --------------------------------------------------------------------------- A
def part_a() -> dict:
    from tests.e2e_m5._m5_support import InProcessApp

    db = HERE / "task48-probe2-inproc.db"
    fresh(db)
    out: dict = {}
    with InProcessApp(db_path=str(db)) as app:
        paper_id = app.make_paper(count=3, seed=20261016)
        app.publish(paper_id)
        created = app.client.post("/api/students", json={
            "name": "锁探针学生", "student_no": "2026LOCK01"})
        out["student_status"] = created.status_code
        student_id = created.json()["student_id"]
        spec = {"student_id": student_id, "attempt_uuid": uuid.uuid4().hex,
                "answers": [], "duration_ms": 1234, "paper_id": paper_id}

        app.client.raise_server_exceptions = False
        out["openapi_no_db"] = timed(
            lambda: app.client.get("/openapi.json").status_code)

        locker = Locker(db)
        out["lock_proof"] = locker.proof(db)
        out["post_under_lock"] = in_a_thread(
            lambda: {"status": app.client.post("/api/attempts", json=spec).status_code},
            timeout=25.0)
        out["rows_while_blocked"] = row_count(db)
        locker.release()
        time.sleep(2.0)
        out["post_under_lock"]["finished_after_release"] = not out["post_under_lock"]["alive"]
        out["post_after_release"] = in_a_thread(
            lambda: {"status": app.client.post("/api/attempts", json=spec).status_code},
            timeout=20.0)
        out["rows_after_release"] = row_count(db)
    return out


# --------------------------------------------------------------------------- B
def part_b() -> dict:
    db = HERE / "task48-probe2-server.db"
    fresh(db)
    out: dict = {}
    server = perf.PerfServer(db_path=str(db))
    server.__enter__()
    try:
        paper_id, questions = server.published_paper(
            title="task48 写锁探针卷 2", seed=20261016, count=perf.PAPER_QUESTIONS)
        student_id = server.make_students(
            name_prefix="探针学生", no_prefix="2026LOCKB", count=1)[0]

        def one_submit() -> dict:
            spec = perf.submit_spec(student_id=student_id, paper_id=paper_id,
                                    attempt_uuid=perf.new_uuid(),
                                    answers=perf.answers_for(questions, correct_count=40))
            batch = perf.fire_concurrent(server.base_url, [spec], timeout=40.0)
            result = batch["results"][0]
            return {"status": result.get("status"), "error": result.get("error"),
                    "elapsed_ms": round(result["elapsed_ms"], 1)}

        locker = Locker(db)
        out["lock_proof"] = locker.proof(db)
        out["no_db_endpoint_under_lock"] = timed(
            lambda: perf.httpx_client(server.base_url, 5.0).get("/openapi.json").status_code)

        box: dict = {"alive": True}
        started = time.perf_counter()

        def request() -> None:
            box.update(timed(one_submit))
            box["alive"] = False

        thread = threading.Thread(target=request, daemon=True, name="probe2-http")
        thread.start()
        time.sleep(12.0)
        out["blocked_at_12s"] = thread.is_alive()
        out["rows_while_blocked"] = row_count(db)
        out["released_at_s"] = round(time.perf_counter() - started, 1)
        locker.release()
        thread.join(40.0)
        box["still_blocked_at_end"] = thread.is_alive()
        box["waited_s"] = round(time.perf_counter() - started, 1)
        out["post"] = box
        out["rows_after"] = row_count(db)
        out["server_log_len"] = len(perf.drain_server_log(server))
    finally:
        try:
            server.__exit__(None, None, None)
        except Exception:                              # noqa: BLE001
            pass
    return out


def main() -> int:
    STEPS["A_in_process_testclient"] = part_a()
    STEPS["B_real_uvicorn_release_after_12s"] = part_b()
    perf.write_evidence("task48-lock-probe2", STEPS)
    print(json.dumps(STEPS, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
