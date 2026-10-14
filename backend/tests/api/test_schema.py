"""表结构与契约 §6 一致性测试。

不用 SQLite 方言 introspection（脆弱），而是直接对 `Base.metadata` 断言：
表数量、字段名集合、唯一约束、可空性、级联删除。
"""
from __future__ import annotations

import unittest

from sqlalchemy import UniqueConstraint

from app.models import (
    Attempt,
    AttemptItem,
    Base,
    EditLog,
    Paper,
    PaperItem,
    Student,
)
from tests.api._support import ApiTestCase


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
            {"id", "student_id", "paper_id", "config_json", "total", "correct",
             "duration_ms", "submitted_at"},
        )
        self.assertTrue(Attempt.__table__.c.paper_id.nullable, "自主练习的 paper_id 必须可空")
        self.assertTrue(Attempt.__table__.c.config_json.nullable)

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
