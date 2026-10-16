"""写锁行为探针（task-48 负对照的取证脚本，只跑一次、不入常驻套件）。

要回答的问题
------------
`test_01_an_exclusive_write_lock_makes_the_same_criterion_red` 第一版预期
「锁被长期持有时服务端在 pysqlite 默认 5.0 秒 busy timeout 后报
`database is locked` → HTTP 500」，实测却是**4 条请求一起挂到客户端 60 秒
超时**、服务端日志 0 字节。这个脚本把「到底谁在等、等多久」逐层量出来：

1. 裸 `sqlite3`（timeout=0.5 / 5.0）在 `BEGIN EXCLUSIVE` 下的 SELECT 与 DDL 行为；
2. 产品那条 SQLAlchemy 引擎（`backend/app/db.py:default_engine`）实际交给
   pysqlite 的连接参数，以及它在锁下的 SELECT 是否会「到点报错」；
3. 真机 uvicorn：锁被持有期间打**一条**真 HTTP 提交（客户端超时 12 秒）；
4. 锁持 4 秒后释放：请求是在锁释放的瞬间完成（= 一直在等锁），
   还是早就失败了（= 快速失败）。

运行：
    .\backend\.venv\Scripts\python.exe backend\tests\perf\artifacts\task48_lock_probe.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent            # backend/tests
BACKEND = TESTS.parent                # backend
PROBE_DB = HERE / "task48-probe.db"

os.environ["MATHGEN_DB"] = str(PROBE_DB)          # `app.db` 在 import 期读它
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS.parent))             # 让 `tests.perf...` 可导入

from tests.perf import _perf_support as perf      # noqa: E402

STEPS: dict[str, object] = {}


def bounded(fn, timeout: float) -> dict:
    """在带截止时间的线程里跑 fn，避免探针自己被写锁永久挂住。"""
    out: dict = {}
    started = time.perf_counter()

    def run() -> None:
        try:
            out["value"] = fn()
            out["outcome"] = "ok"
        except Exception as exc:                   # noqa: BLE001
            out["outcome"] = f"{type(exc).__name__}: {exc}"
        out["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)

    thread = threading.Thread(target=run, daemon=True, name="probe")
    thread.start()
    thread.join(timeout)
    out["hit_deadline"] = thread.is_alive()
    if out["hit_deadline"]:
        out["outcome"] = f"still blocked after {timeout:.1f}s（没到点报错）"
    return out


def fresh(path: Path) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        stale = Path(str(path) + suffix)
        if stale.exists():
            stale.unlink()


def main() -> int:
    fresh(PROBE_DB)
    STEPS["environment"] = {
        "python": sys.version.split()[0],
        "sqlite3_module": sqlite3.version,
        "sqlite_library": sqlite3.sqlite_version,
        "probe_db": str(PROBE_DB),
    }

    # ---------------------------------------------------------------- 1. 裸 sqlite3
    seed = sqlite3.connect(str(PROBE_DB))
    seed.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, note TEXT)")
    seed.execute("INSERT INTO t (note) VALUES ('seed')")
    seed.commit()
    seed.close()

    locker = sqlite3.connect(str(PROBE_DB), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("INSERT INTO t (note) VALUES ('locker')")
    raw: dict = {"lock_acquired": True}
    for timeout in (0.5, 5.0):
        def read(timeout: float = timeout) -> dict:
            conn = sqlite3.connect(str(PROBE_DB), timeout=timeout)
            try:
                row = conn.execute("SELECT count(*) FROM t").fetchone()
                return {"rows_seen": row[0]}
            finally:
                conn.close()
        raw[f"select_timeout_{timeout}"] = bounded(read, timeout + 20.0)

        def ddl(timeout: float = timeout) -> dict:
            conn = sqlite3.connect(str(PROBE_DB), timeout=timeout)
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS probe_ddl (id INTEGER)")
                conn.commit()
                return {"ddl": "granted"}
            finally:
                conn.close()
        raw[f"ddl_timeout_{timeout}"] = bounded(ddl, timeout + 20.0)
    STEPS["raw_sqlite3"] = raw

    # ------------------------------------------------- 2. 产品引擎的 SQLAlchemy 参数
    from app.db import default_engine                  # noqa: E402  （紧跟在环境变量设置之后）

    engine = default_engine()
    connect_args = engine.dialect.create_connect_args(engine.url)
    steps_sqlalchemy: dict = {
        "url": str(engine.url).replace(str(PROBE_DB), "<probe.db>"),
        "create_connect_args": str(connect_args),
        "kwargs": str(connect_args[1]),
    }

    def select_via_engine() -> dict:
        connection = engine.connect()
        try:
            return {"rows": connection.exec_driver_sql("SELECT count(*) FROM t").scalar()}
        finally:
            connection.close()

    steps_sqlalchemy["select_under_lock"] = bounded(select_via_engine, 20.0)
    STEPS["product_engine"] = steps_sqlalchemy

    locker.execute("ROLLBACK")
    locker.close()
    engine.dispose()

    # ------------------------------------------------------ 3./4. 真机 uvicorn 两条
    server = perf.PerfServer(db_path=str(HERE / "task48-probe-server.db"))
    server.__enter__()
    try:
        paper_id, questions = server.published_paper(
            title="task48 写锁探针卷", seed=20261016, count=perf.PAPER_QUESTIONS)
        student_id = server.make_students(
            name_prefix="探针学生", no_prefix="2026PROBE", count=1)[0]
        spec = perf.submit_spec(student_id=student_id, paper_id=paper_id,
                                attempt_uuid=perf.new_uuid(),
                                answers=perf.answers_for(questions, correct_count=40))

        with perf.exclusive_write_lock(str(HERE / "task48-probe-server.db")):
            STEPS["http_under_lock"] = {
                "kind": "锁被一直持有，1 条提交（客户端只等 12 秒）",
                "batch": perf.fire_concurrent(server.base_url, [spec], timeout=12.0),
            }

        # 锁释放后：那条请求还会不会被服务端补做出来？
        time.sleep(2.0)
        STEPS["http_under_lock"]["rows_after_release"] = perf.attempt_rows(
            str(HERE / "task48-probe-server.db"))

        # ---- 4. 持锁 4 秒后释放，看响应是不是在释放的瞬间到
        spec2 = perf.submit_spec(student_id=student_id, paper_id=paper_id,
                                 attempt_uuid=perf.new_uuid(),
                                 answers=perf.answers_for(questions, correct_count=40))
        released: dict = {}

        def release_later() -> None:
            time.sleep(4.0)
            released["at_ms"] = 4000.0
            released["lock_released"] = True

        thread = threading.Thread(target=release_later, daemon=True)
        started = time.perf_counter()
        with perf.exclusive_write_lock(str(HERE / "task48-probe-server.db")):
            thread.start()
            batch = perf.fire_concurrent(server.base_url, [spec2], timeout=30.0)
        released["release_observed_after_ms"] = round(
            (time.perf_counter() - started) * 1000.0, 1)
        STEPS["http_after_short_lock"] = {
            "kind": "持锁约 4 秒后释放，1 条提交（客户端等 30 秒）",
            "batch": batch,
            "release": released,
        }
        STEPS["server_log"] = perf.drain_server_log(server)[-4000:]
    finally:
        try:
            server.__exit__(None, None, None)
        except Exception:                              # noqa: BLE001
            pass

    perf.write_evidence("task48-lock-probe", STEPS)
    print(json.dumps(STEPS, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
