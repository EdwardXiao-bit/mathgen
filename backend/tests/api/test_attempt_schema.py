"""老库补列护栏：`attempt_service.ensure_attempt_schema()`（task-24 / D76）。

为什么值得单独一个文件：项目**没有 Alembic**，`db.init_db()` 只跑
`Base.metadata.create_all()`——它给已存在的 `attempt` 表**不会**补列。所以
「M4 之前建的库」与「M4 之后新建的库」必须都满足
`UNIQUE(student_id, attempt_uuid)`；两条路径（DDL / ALTER+索引）在这里各测一遍。

老库形态是**用原始 SQL 手工造**的：直接拿当前模型建表就测不到补列路径了
（模型里已经有那一列），这也是本文件存在的意义。
"""
from __future__ import annotations

import unittest

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import db as db_module
from app.services.attempt_service import UNIQUE_NAME, ensure_attempt_schema

from tests.api._support import fresh_engine, session_of

#: M4 之前的 `attempt` 表（没有 attempt_uuid 列），照 docs/04 §6 的旧形状写
LEGACY_ATTEMPT_DDL = """
CREATE TABLE student (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    student_no VARCHAR(50) NOT NULL,
    created_at VARCHAR(32) NOT NULL,
    CONSTRAINT uq_student_name_no UNIQUE (name, student_no)
)
"""

LEGACY_ATTEMPT_TABLE_DDL = """
CREATE TABLE attempt (
    id INTEGER NOT NULL PRIMARY KEY,
    student_id INTEGER NOT NULL,
    paper_id INTEGER,
    config_json TEXT,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    submitted_at VARCHAR(32) NOT NULL,
    FOREIGN KEY(student_id) REFERENCES student (id)
)
"""


def legacy_engine(*, with_attempt: bool = True, students: int = 1, attempts: int = 0):
    """造一个「M4 之前的库」：内存 SQLite + 旧 DDL（可选带旧数据行）。"""
    engine = fresh_engine()
    with engine.begin() as connection:
        connection.execute(text(LEGACY_ATTEMPT_DDL))
        for index in range(students):
            connection.execute(
                text(
                    "INSERT INTO student (id, name, student_no, created_at) "
                    "VALUES (:id, :name, :no, '2026-01-01T00:00:00')"
                ),
                {"id": index + 1, "name": f"学生{index + 1}", "no": str(index + 1)},
            )
        if with_attempt:
            connection.execute(text(LEGACY_ATTEMPT_TABLE_DDL))
            for index in range(attempts):
                connection.execute(
                    text(
                        "INSERT INTO attempt "
                        "(id, student_id, paper_id, config_json, total, correct, duration_ms, submitted_at) "
                        "VALUES (:id, 1, NULL, '{}', 3, 1, 100, '2026-01-01T00:00:00')"
                    ),
                    {"id": index + 1},
                )
    return engine


def columns_of(engine) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns("attempt")}


def unique_sets_of(engine) -> set[frozenset]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        sets = {
            frozenset(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("attempt")
        }
        sets |= {
            frozenset(index["column_names"])
            for index in inspector.get_indexes("attempt")
            if index.get("unique")
        }
    return sets


def index_names_of(engine) -> list[str]:
    with engine.connect() as connection:
        return [index["name"] for index in inspect(connection).get_indexes("attempt")]


class LegacyUpgradeTests(unittest.TestCase):
    """老库：补列 + 补唯一约束，且不伤历史数据。"""

    def test_column_and_unique_constraint_are_added(self):
        engine = legacy_engine()
        self.assertNotIn("attempt_uuid", columns_of(engine))

        with session_of(engine) as db:
            ensure_attempt_schema(db)

        self.assertIn("attempt_uuid", columns_of(engine))
        self.assertIn(
            frozenset({"student_id", "attempt_uuid"}),
            unique_sets_of(engine),
            "老库必须补上 D76 的唯一约束，否则并发重发只能靠 SELECT 兜底",
        )

    def test_legacy_rows_survive_and_their_null_uuids_do_not_collide(self):
        """历史成绩没有 uuid：多行 NULL 在同一 student 下必须能共存（SQLite 里 NULL 互不相等）。"""
        engine = legacy_engine(attempts=3)

        with session_of(engine) as db:
            ensure_attempt_schema(db)
            rows = db.execute(
                text("select student_id, attempt_uuid, total from attempt order by id")
            ).all()

        self.assertEqual(len(rows), 3, "补列不能丢历史成绩")
        self.assertEqual({row[1] for row in rows}, {None})

    def test_calling_it_twice_adds_nothing_twice(self):
        engine = legacy_engine()

        with session_of(engine) as db:
            ensure_attempt_schema(db)
        first = index_names_of(engine)
        with session_of(engine) as db:
            ensure_attempt_schema(db)
        second = index_names_of(engine)

        self.assertEqual(first, second)
        self.assertEqual(first.count(UNIQUE_NAME), 1, "唯一索引不该被建两次")

    def test_the_per_engine_cache_does_not_hide_a_second_engine(self):
        """缓存按 engine 记忆：换了 engine（换了库）仍要各自补一遍。"""
        first = legacy_engine()
        second = legacy_engine()

        with session_of(first) as db:
            ensure_attempt_schema(db)
        self.assertNotIn(
            "attempt_uuid", columns_of(second), "另一个库不该被连带改动（缓存按 engine 记忆）"
        )

        with session_of(second) as db:
            ensure_attempt_schema(db)
        self.assertIn("attempt_uuid", columns_of(second))

    def test_missing_attempt_table_is_not_an_error(self):
        """库还没有 attempt 表（全新库、create_all 还没跑）时，什么都不做。"""
        engine = legacy_engine(with_attempt=False)

        with session_of(engine) as db:
            ensure_attempt_schema(db)  # 不抛异常

        self.assertNotIn("attempt", inspect(engine).get_table_names())


class ConstraintIsTheLastLineOfDefenceTests(unittest.TestCase):
    """补出来的唯一约束必须**真的挡住**同 uuid 重发。"""

    def _migrated(self):
        engine = legacy_engine()
        with session_of(engine) as db:
            ensure_attempt_schema(db)
        return engine

    def _insert(self, db, *, uuid: str | None, student_id: int = 1) -> None:
        db.execute(
            text(
                "INSERT INTO attempt "
                "(student_id, attempt_uuid, total, correct, duration_ms, submitted_at) "
                "VALUES (:student_id, :uuid, 1, 0, 1, '2026-01-01T00:00:00')"
            ),
            {"student_id": student_id, "uuid": uuid},
        )

    def test_duplicate_uuid_for_the_same_student_is_rejected(self):
        engine = self._migrated()
        with session_of(engine) as db:
            self._insert(db, uuid="same-uuid")
            db.commit()
            with self.assertRaises(IntegrityError):
                self._insert(db, uuid="same-uuid")

    def test_same_uuid_for_another_student_is_allowed(self):
        engine = legacy_engine(students=2)
        with session_of(engine) as db:
            ensure_attempt_schema(db)
            self._insert(db, uuid="shared", student_id=1)
            self._insert(db, uuid="shared", student_id=2)
            db.commit()
            self.assertEqual(db.execute(text("select count(*) from attempt")).scalar(), 2)

    def test_many_null_uuids_are_allowed(self):
        engine = self._migrated()
        with session_of(engine) as db:
            for _ in range(5):
                self._insert(db, uuid=None)
            db.commit()
            self.assertEqual(db.execute(text("select count(*) from attempt")).scalar(), 5)


class FreshDatabaseTests(unittest.TestCase):
    """新库：唯一约束由模型 DDL 直接建出来（不依赖补列路径）。"""

    def test_init_db_creates_the_unique_constraint(self):
        engine = fresh_engine()
        db_module.init_db(engine)

        self.assertIn("attempt_uuid", columns_of(engine))
        self.assertIn(frozenset({"student_id", "attempt_uuid"}), unique_sets_of(engine))

    def test_ensure_on_a_fresh_database_is_a_noop(self):
        engine = fresh_engine()
        db_module.init_db(engine)
        before = index_names_of(engine)

        with session_of(engine) as db:
            ensure_attempt_schema(db)

        self.assertEqual(index_names_of(engine), before, "新库不该再多建一个冗余唯一索引")
        self.assertIn(frozenset({"student_id", "attempt_uuid"}), unique_sets_of(engine))

    def test_model_declares_the_constraint_and_the_nullable_column(self):
        """模型层的护栏：约束一旦被谁删掉，这里先红（比老库迁移用例更早暴露）。"""
        from app.models import Attempt

        table = Attempt.__table__
        self.assertTrue(table.c.attempt_uuid.nullable, "历史成绩没有 uuid，列必须允许 NULL")
        self.assertEqual(table.c.attempt_uuid.type.length, 64)
        unique_sets = {
            frozenset(column.name for column in constraint.columns)
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(frozenset({"student_id", "attempt_uuid"}), unique_sets)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
