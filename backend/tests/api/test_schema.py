"""表结构与契约 §6 一致性测试。

不用 SQLite 方言 introspection（脆弱），而是直接对 `Base.metadata` 断言：
表数量、字段名集合、唯一约束、可空性、级联删除。

**task-28 补充**：模型元数据之外，`attempt_uuid` 的唯一性还并存一条**真实库层**判据
（见文件后半 `assert_attempt_uuid_unique_in_db`）——「模型声明了约束」与「真实库上
真有那个唯一性」是两件事，老库走的是 `ensure_attempt_schema()` 的
`ALTER TABLE` + `CREATE UNIQUE INDEX` 路径，元数据判据看不见那条路径的结果。
"""
from __future__ import annotations

import unittest

from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Attempt,
    AttemptItem,
    Base,
    EditLog,
    Paper,
    PaperItem,
    Student,
)
from app.services.attempt_service import UNIQUE_NAME, ensure_attempt_schema
from tests.api._support import ApiTestCase, fresh_engine, session_of
from tests.api.test_attempt_schema import legacy_engine

#: D76 钦定的唯一键，**列序也是契约的一部分**：`UNIQUE(student_id, attempt_uuid)`
ATTEMPT_UUID_UNIQUE_COLUMNS = ("student_id", "attempt_uuid")

#: 一条**没有**该唯一性的老库形状（列都在、只缺索引/约束）——负对照用
ATTEMPT_TABLE_WITHOUT_THE_UNIQUE_INDEX = """
CREATE TABLE student (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    student_no TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE attempt (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL,
    attempt_uuid VARCHAR(64),
    paper_id INTEGER,
    config_json TEXT,
    total INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    submitted_at TEXT NOT NULL
);
"""


def model_unique_sets() -> set[tuple[str, ...]]:
    """模型层（ORM 元数据）里声明的唯一键。注意：它读的是**全局** `Attempt.__table__`。"""
    return {
        tuple(sorted(constraint.columns.keys()))
        for constraint in Attempt.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def attempt_uuid_unique_column_orders(engine) -> set[tuple[str, ...]]:
    """真实库里所有唯一性声明的**列序**（唯一约束 + 唯一索引两条来源都算）。"""
    with engine.connect() as connection:
        inspector = inspect(connection)
        if "attempt" not in inspector.get_table_names():
            return set()
        orders = {tuple(c["column_names"]) for c in inspector.get_unique_constraints("attempt")}
        orders |= {
            tuple(i["column_names"])
            for i in inspector.get_indexes("attempt")
            if i.get("unique")
        }
    return orders


def duplicate_uuid_is_rejected_by_db(engine) -> bool:
    """行为层：真往库里插两行同 `(student_id, attempt_uuid)`，第二次必须被库拒绝。"""
    insert = text(
        "insert into attempt (student_id, attempt_uuid, total, correct, duration_ms, submitted_at) "
        "values (:sid, :uuid, 0, 0, 0, '2026-01-01T00:00:00')"
    )
    with engine.connect() as connection:
        try:
            connection.execute(
                text(
                    "insert or ignore into student (id, name, student_no, created_at) "
                    "values (1, '测试学生', 'T-28', '2026-01-01T00:00:00')"
                )
            )
            connection.execute(insert, {"sid": 1, "uuid": "task-28-dup"})
            try:
                connection.execute(insert, {"sid": 1, "uuid": "task-28-dup"})
            except IntegrityError:
                return True
            return False
        finally:
            connection.rollback()


def assert_attempt_uuid_unique_in_db(tc, engine):
    """**DB 层**判据（task-28）：库上真有唯一性，而且真的在拦重复行。

    与 `test_attempt_uuid_is_unique_per_student`（模型元数据）互补：
    只改 `app/models.py` 而不改真实库、或老库升级路径漏了索引，只有这条判据会红。
    """
    orders = attempt_uuid_unique_column_orders(engine)
    tc.assertIn(
        ATTEMPT_UUID_UNIQUE_COLUMNS,
        orders,
        f"真实库上没有 (student_id, attempt_uuid) 唯一约束/索引（列序也须一致），实得 {sorted(orders)}",
    )
    tc.assertTrue(
        duplicate_uuid_is_rejected_by_db(engine),
        "同一个 (student_id, attempt_uuid) 的两行竟然都插进去了：库上的唯一性没有真正生效",
    )


def engine_without_the_unique_index():
    """负对照用的库：表结构齐全，就是**没有**那条唯一索引。"""
    engine = fresh_engine()
    with engine.begin() as connection:
        for statement in ATTEMPT_TABLE_WITHOUT_THE_UNIQUE_INDEX.strip().split(";"):
            if statement.strip():
                connection.execute(text(statement))
    return engine


class TestSchema(unittest.TestCase):
    def test_exactly_six_tables(self):
        """契约 §6：6 张表。多一张少一张都要在这里被发现。"""
        self.assertEqual(
            sorted(Base.metadata.tables),
            ["attempt", "attempt_item", "edit_log", "paper", "paper_item", "student"],
        )

    def test_paper_columns(self):
        self.assertEqual(
            set(Paper.__table__.columns.keys()),
            {"id", "code", "title", "status", "seed", "config_json", "layout_json",
             "answer_layout", "created_at", "published_at"},
        )
        self.assertTrue(Paper.__table__.c.code.unique)
        self.assertTrue(Paper.__table__.c.code.nullable)
        self.assertTrue(Paper.__table__.c.published_at.nullable)
        for name in ("title", "status", "seed", "config_json", "layout_json",
                     "answer_layout", "created_at"):
            self.assertFalse(Paper.__table__.c[name].nullable, f"paper.{name} 应 NOT NULL")

    def test_paper_item_columns_and_unique_seq(self):
        self.assertEqual(
            set(PaperItem.__table__.columns.keys()),
            {"id", "paper_id", "seq", "payload", "origin"},
        )
        unique_sets = {
            tuple(sorted(constraint.columns.keys()))
            for constraint in PaperItem.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn(("paper_id", "seq"), unique_sets)
        self.assertEqual(
            list(PaperItem.__table__.c.paper_id.foreign_keys)[0].target_fullname, "paper.id"
        )

    def test_paper_item_cascades_on_paper_delete(self):
        foreign_key = list(PaperItem.__table__.c.paper_id.foreign_keys)[0]
        self.assertEqual(foreign_key.ondelete, "CASCADE")

    def test_edit_log_columns(self):
        self.assertEqual(
            set(EditLog.__table__.columns.keys()), {"id", "paper_id", "action", "seq", "at"}
        )
        self.assertTrue(EditLog.__table__.c.seq.nullable)

    def test_student_requires_name_and_student_no_with_unique_pair(self):
        self.assertEqual(
            set(Student.__table__.columns.keys()), {"id", "name", "student_no", "created_at"}
        )
        self.assertFalse(Student.__table__.c.name.nullable)
        self.assertFalse(Student.__table__.c.student_no.nullable)
        unique_sets = {
            tuple(sorted(constraint.columns.keys()))
            for constraint in Student.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn(("name", "student_no"), unique_sets)

    def test_attempt_columns(self):
        self.assertEqual(
            set(Attempt.__table__.columns.keys()),
            {"id", "student_id", "attempt_uuid", "paper_id", "config_json", "total", "correct",
             "duration_ms", "submitted_at"},
        )
        self.assertTrue(Attempt.__table__.c.paper_id.nullable, "自主练习的 paper_id 必须可空")
        self.assertTrue(Attempt.__table__.c.config_json.nullable)
        self.assertTrue(
            Attempt.__table__.c.attempt_uuid.nullable,
            "历史成绩没有 uuid，列必须可空（NULL 在 SQLite 唯一索引里互不相等）",
        )

    def test_attempt_uuid_is_unique_per_student(self):
        """D76：模型层声明了 `(student_id, attempt_uuid)` 唯一。

        **只管模型声明**（task-28 的加固点）：真实库里有没有那条唯一性由
        `TestSchemaIsLive.test_attempt_uuid_uniqueness_holds_in_the_real_database` 守。
        """
        self.assertIn(("attempt_uuid", "student_id"), model_unique_sets())

    def test_attempt_item_columns_and_unique_seq(self):
        self.assertEqual(
            set(AttemptItem.__table__.columns.keys()),
            {"id", "attempt_id", "seq", "payload", "student_answer", "is_correct"},
        )
        unique_sets = {
            tuple(sorted(constraint.columns.keys()))
            for constraint in AttemptItem.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn(("attempt_id", "seq"), unique_sets)
        self.assertTrue(AttemptItem.__table__.c.student_answer.nullable)

    def test_indexes_exist_for_lookup_paths(self):
        paper_indexes = {index.name for index in Paper.__table__.indexes}
        attempt_indexes = {index.name for index in Attempt.__table__.indexes}
        self.assertIn("idx_paper_status", paper_indexes)
        self.assertIn("idx_attempt_paper", attempt_indexes)
        self.assertIn("idx_attempt_student", attempt_indexes)


class TestSchemaIsLive(ApiTestCase):
    def test_all_six_tables_created_in_test_database(self):
        from sqlalchemy import text

        with self.engine.connect() as connection:
            rows = connection.execute(
                text("select name from sqlite_master where type='table' order by name")
            ).fetchall()
        names = {row[0] for row in rows}
        for table in ("paper", "paper_item", "edit_log", "student", "attempt", "attempt_item"):
            self.assertIn(table, names)

    # ---- attempt_uuid 唯一性的 DB 层判据（task-28） -------------------------

    def test_attempt_uuid_uniqueness_holds_in_the_real_database(self):
        """新库路径：`create_all()` 出来的库上真有 `(student_id, attempt_uuid)` 唯一性。"""
        assert_attempt_uuid_unique_in_db(self, self.engine)

    def test_attempt_uuid_uniqueness_holds_after_legacy_migration(self):
        """老库路径：`ensure_attempt_schema()` 的 `ALTER TABLE` + 唯一索引之后同样成立。"""
        engine = legacy_engine()
        session = session_of(engine)
        try:
            ensure_attempt_schema(session)
        finally:
            session.close()
        assert_attempt_uuid_unique_in_db(self, engine)

    def test_the_criterion_goes_red_when_only_the_unique_index_is_dropped(self):
        """负对照（单变量）：同一个库，只 `DROP INDEX` → 新判据必须红。

        这是分辨力最强的一种负对照：别的什么都没变，只翻转了「库上有没有唯一性」
        这一个比特，绿色必须变红。
        """
        engine = legacy_engine()
        session = session_of(engine)
        try:
            ensure_attempt_schema(session)
        finally:
            session.close()
        assert_attempt_uuid_unique_in_db(self, engine)  # 先证明这个库本来是绿的

        with engine.begin() as connection:
            connection.execute(text(f"drop index {UNIQUE_NAME}"))

        with self.assertRaises(AssertionError):
            assert_attempt_uuid_unique_in_db(self, engine)

    def test_the_db_layer_criterion_sees_what_the_model_one_cannot(self):
        """负对照：库上从来没有那条唯一性 —— 模型元数据判据照样绿，DB 层判据必须红。

        `model_unique_sets()` 读的是全局 `Attempt.__table__`，跟这个库的真实结构无关；
        这正是 task-28 要补的那一格分辨力。
        """
        engine = engine_without_the_unique_index()
        self.assertIn(("attempt_uuid", "student_id"), model_unique_sets())
        self.assertNotIn(
            ATTEMPT_UUID_UNIQUE_COLUMNS, attempt_uuid_unique_column_orders(engine)
        )
        with self.assertRaises(AssertionError):
            assert_attempt_uuid_unique_in_db(self, engine)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
