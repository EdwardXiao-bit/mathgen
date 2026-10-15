"""老库补列护栏：`paper_schema.ensure_paper_schema()`（task-34 / D78）。

为什么值得单独一个文件：项目**没有 Alembic**，`db.init_db()` 只跑
`Base.metadata.create_all()`——它给**已存在**的 `paper` 表**不会**补列。D78 新增了
`paper.archived_at`，而 SQLAlchemy 的 `select(Paper)` 会把模型声明的**所有**列都列进 SQL，
所以老库上不是「新功能不可用」，而是**整个卷库读不出来**（`no such column`）。
这里把「老库」与「新库」两条路径各测一遍，并配一条负对照证明护栏不是摆设。
"""
from __future__ import annotations

import unittest

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError

from app.models import Paper
from app.services.paper_schema import ARCHIVED_AT_COLUMN, ensure_paper_schema

from tests.api._support import fresh_engine, make_client, session_of

#: M4 之前的 `paper` 表（**没有 archived_at 列**），照 docs/04 §6 的旧形状写
LEGACY_PAPER_DDL = """
CREATE TABLE paper (
    id INTEGER NOT NULL PRIMARY KEY,
    code VARCHAR(16),
    title TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    seed INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    answer_layout TEXT NOT NULL DEFAULT '{}',
    created_at VARCHAR(40) NOT NULL,
    published_at VARCHAR(40)
)
"""

LEGACY_PAPER_ITEM_DDL = """
CREATE TABLE paper_item (
    id INTEGER NOT NULL PRIMARY KEY,
    paper_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    origin VARCHAR(16) NOT NULL,
    CONSTRAINT uq_paper_item_seq UNIQUE (paper_id, seq),
    FOREIGN KEY(paper_id) REFERENCES paper (id) ON DELETE CASCADE
)
"""


def legacy_engine(*, with_paper: bool = True, papers: int = 0):
    """造一个「M4 之前的库」：内存 SQLite + 旧 DDL（可选带旧数据行）。"""
    engine = fresh_engine()
    with engine.begin() as connection:
        if with_paper:
            connection.execute(text(LEGACY_PAPER_DDL))
            connection.execute(text(LEGACY_PAPER_ITEM_DDL))
            for index in range(papers):
                connection.execute(
                    text(
                        "INSERT INTO paper "
                        "(id, code, title, status, seed, config_json, layout_json, "
                        " answer_layout, created_at, published_at) "
                        "VALUES (:id, NULL, :title, 'draft', 2025, '{}', '{}', '{}', "
                        "        '2026-01-01T00:00:00', NULL)"
                    ),
                    {"id": index + 1, "title": f"老卷{index + 1}"},
                )
    return engine


def columns_of(engine) -> set[str]:
    with engine.connect() as connection:
        return {column["name"] for column in inspect(connection).get_columns("paper")}


class EnsurePaperSchemaTests(unittest.TestCase):
    """护栏本身：补列、幂等、没这张表时不出声。"""

    def test_legacy_table_gets_the_column(self):
        engine = legacy_engine()
        self.assertNotIn(ARCHIVED_AT_COLUMN, columns_of(engine))

        session = session_of(engine)
        try:
            ensure_paper_schema(session)
        finally:
            session.close()

        self.assertIn(ARCHIVED_AT_COLUMN, columns_of(engine))

    def test_history_rows_become_unarchived_not_unknown(self):
        """补出来的列必须全是 NULL：老卷的语义是「未归档」，不是「不知道」。"""
        engine = legacy_engine(papers=2)
        session = session_of(engine)
        try:
            ensure_paper_schema(session)
            rows = session.execute(select(Paper.archived_at)).scalars().all()
        finally:
            session.close()
        self.assertEqual(rows, [None, None])

    def test_it_is_idempotent(self):
        engine = legacy_engine()
        for _ in range(3):
            session = session_of(engine)
            try:
                ensure_paper_schema(session)
            finally:
                session.close()
        self.assertIn(ARCHIVED_AT_COLUMN, columns_of(engine))

    def test_a_fresh_database_already_has_the_column(self):
        """新库由 `create_all()` 建，本来就带这一列——护栏不该多此一举。"""
        from app import db as db_module

        engine = fresh_engine()
        db_module.init_db(engine)          # 等价于 lifespan 里跑的那一步
        before = columns_of(engine)
        self.assertIn(ARCHIVED_AT_COLUMN, before)

        session = session_of(engine)
        try:
            ensure_paper_schema(session)   # 应该什么都不做
        finally:
            session.close()
        self.assertEqual(columns_of(engine), before)

    def test_no_paper_table_is_not_an_error(self):
        engine = legacy_engine(with_paper=False)
        session = session_of(engine)
        try:
            ensure_paper_schema(session)   # 不该抛
            session.execute(text("SELECT 1"))
        finally:
            session.close()


class LegacyDatabaseServesTheApiTests(unittest.TestCase):
    """端到端：**老库**跑真实 app 的卷库接口——这才是护栏存在的理由。"""

    def test_listing_papers_works_on_a_legacy_database(self):
        engine = legacy_engine(papers=2)
        self.assertNotIn(ARCHIVED_AT_COLUMN, columns_of(engine))

        client_cm = make_client(engine)
        with client_cm as client:
            response = client.get("/api/papers")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["total"], 2)
            self.assertEqual([p["archived_at"] for p in body["papers"]], [None, None])
            self.assertEqual([p["has_attempts"] for p in body["papers"]], [False, False])

        # 护栏在请求过程中真的把列补上了（不是靠别的什么绕过去）
        self.assertIn(ARCHIVED_AT_COLUMN, columns_of(engine))

    def test_negative_control_the_legacy_database_really_is_broken_without_the_guard(self):
        """负对照（留在仓库）：**不跑护栏**时同一个老库确实读不出来。

        没有这条，上面那条「200」就可能是因为老库本来就没事——那样护栏被测成了摆设。
        这里指名道姓地断言数据库错误里提到 `archived_at`。
        """
        engine = legacy_engine(papers=1)
        session = session_of(engine)
        try:
            with self.assertRaises(OperationalError) as caught:
                session.execute(select(Paper)).scalars().all()
        finally:
            session.close()
        self.assertIn(ARCHIVED_AT_COLUMN, str(caught.exception),
                      "负对照必须正是缺列造成的失败，而不是别的错误")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
