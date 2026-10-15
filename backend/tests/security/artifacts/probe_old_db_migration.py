"""只读探针：老库的 `attempt` 表能不能被 `ensure_attempt_schema` 补到 D76 形状。

**为什么需要它（这是对 be-paper 那条新断言的独立复核）**：
`backend/tests/api/test_schema.py::test_attempt_uuid_is_unique_per_student` 断言的是
**ORM 元数据**里的 `UniqueConstraint`（`Attempt.__table__.constraints`），
不是**真实数据库**里的唯一索引。在这个「没有 Alembic、只靠 `create_all` + 手写
`ALTER TABLE`/`CREATE UNIQUE INDEX`」的项目里，这两件事不是一回事，所以从 DB 层面
再独立验一遍：

1. 真实生产库 `backend/data/mathgen.db` 用 **URI `mode=ro`** 只读打开，报告 `attempt` 表现状；
2. 用 `sqlite3.Connection.backup()` 把生产库**复制**到本目录，在副本上跑
   `init_db` + `ensure_attempt_schema`，对比补列/补唯一索引**前后**的 DB 层快照；
3. 换一个**新 engine** 指向同一个副本再跑一次，证明 DDL 幂等（`_ENSURED` 只按 engine 记账）；
4. 另建一个**全新**库，用 `PRAGMA index_list` / `index_info` 证明唯一索引在 DB 层真的存在。

**从不写生产库**：只有 `mode=ro` 连接 + 副本。可重复运行。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
PROD = BACKEND / "data" / "mathgen.db"
OUT = HERE / "evidence-schema-migration.json"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, text  # noqa: E402

from app.db import enable_sqlite_foreign_keys, init_db, session_factory  # noqa: E402
from app.services.attempt_service import ensure_attempt_schema  # noqa: E402

UNIQUE_TARGET = {"student_id", "attempt_uuid"}


def snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """DB 层快照：attempt 表的列、唯一约束/唯一索引、行数。"""
    tables = sorted(
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if "attempt" in row[0]
    )
    if "attempt" not in tables:
        return {"attempt_like_tables": tables, "attempt_exists": False}
    columns = [row[1] for row in conn.execute("PRAGMA table_info(attempt)")]
    unique_sets: list[dict[str, Any]] = []
    for row in conn.execute("PRAGMA index_list(attempt)"):
        name, unique, origin = row[1], bool(row[2]), row[3]
        cols = [info[2] for info in conn.execute(f'PRAGMA index_info("{name}")')]
        if unique:
            unique_sets.append({"name": name, "columns": cols, "origin": origin})
    rows = conn.execute("SELECT COUNT(*) FROM attempt").fetchone()[0]
    return {
        "attempt_like_tables": tables,
        "attempt_exists": True,
        "columns": columns,
        "has_attempt_uuid_column": "attempt_uuid" in columns,
        "unique_sets": unique_sets,
        "has_target_unique_index": any(
            set(entry["columns"]) == UNIQUE_TARGET for entry in unique_sets
        ),
        "attempt_rows": rows,
    }


def read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def main() -> int:
    report: dict[str, Any] = {"probe": "old-db migration / DB-level unique index"}
    workdirs: list[Path] = []

    # 1) 生产库（只读）
    if PROD.exists():
        with read_only(PROD) as conn:
            report["production_before"] = snapshot(conn)
        report["production_path"] = str(PROD)
        report["production_mtime"] = PROD.stat().st_mtime
    else:
        report["production_before"] = {"error": f"{PROD} 不存在"}

    # 2) 复制生产库到副本，在副本上跑迁移
    if PROD.exists():
        copy_dir = HERE / f"db-old-{uuid.uuid4().hex[:8]}"
        copy_dir.mkdir(parents=True, exist_ok=True)
        workdirs.append(copy_dir)
        copy_path = copy_dir / "old.db"
        with read_only(PROD) as src, sqlite3.connect(copy_path.as_posix()) as dst:
            src.backup(dst)
        with sqlite3.connect(copy_path.as_posix()) as conn:
            report["copy_before"] = snapshot(conn)

        url = f"sqlite+pysqlite:///{copy_path.as_posix()}"
        engine = enable_sqlite_foreign_keys(
            create_engine(url, connect_args={"check_same_thread": False}, future=True)
        )
        init_db(engine)
        with session_factory(engine)() as db:
            ensure_attempt_schema(db)
            db.commit()
        with sqlite3.connect(copy_path.as_posix()) as conn:
            report["copy_after_migration"] = snapshot(conn)

        # 3) 新 engine 指向同一副本 → DDL 必须幂等
        engine2 = enable_sqlite_foreign_keys(
            create_engine(url, connect_args={"check_same_thread": False}, future=True)
        )
        with session_factory(engine2)() as db:
            ensure_attempt_schema(db)
            db.commit()
        with sqlite3.connect(copy_path.as_posix()) as conn:
            report["copy_after_second_run"] = snapshot(conn)
        report["migration_idempotent"] = (
            report["copy_after_migration"] == report["copy_after_second_run"]
        )
        engine.dispose()
        engine2.dispose()

    # 4) 全新库：DB 层唯一索引必须由 create_all 建出来
    fresh_dir = HERE / f"db-fresh-{uuid.uuid4().hex[:8]}"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    workdirs.append(fresh_dir)
    fresh_path = fresh_dir / "fresh.db"
    fresh_url = f"sqlite+pysqlite:///{fresh_path.as_posix()}"
    fresh_engine = enable_sqlite_foreign_keys(
        create_engine(fresh_url, connect_args={"check_same_thread": False}, future=True)
    )
    init_db(fresh_engine)
    with sqlite3.connect(fresh_path.as_posix()) as conn:
        report["fresh_after_create_all"] = snapshot(conn)
    fresh_engine.dispose()

    # 5) 结论
    report["verdict"] = {
        "orm_metadata_only": "be-paper 的用例断言的是 ORM 元数据，本探针补 DB 层",
        "old_db_needed_migration": (
            not report.get("copy_before", {}).get("has_attempt_uuid_column", True)
            or not report.get("copy_before", {}).get("has_target_unique_index", True)
        ),
        "old_db_fixed_by_ensure_attempt_schema": bool(
            report.get("copy_after_migration", {}).get("has_attempt_uuid_column")
            and report.get("copy_after_migration", {}).get("has_target_unique_index")
        ),
        "fresh_db_has_unique_index_at_db_level": bool(
            report.get("fresh_after_create_all", {}).get("has_target_unique_index")
        ),
        "migration_idempotent": report.get("migration_idempotent"),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for directory in workdirs:
        shutil.rmtree(directory, ignore_errors=True)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[probe] 证据落盘：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
