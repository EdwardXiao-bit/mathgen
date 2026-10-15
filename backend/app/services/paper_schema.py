"""老库的 `paper` 表补列护栏（D78，幂等）。

为什么需要这段：本项目**没有 Alembic**，`db.init_db()` 只跑 `Base.metadata.create_all()`
——它只为**不存在**的表建表，对已存在的表**不会**补列。D78 给 `paper` 加了 `archived_at`，
M4 之前建的老库没有这一列；而 SQLAlchemy 的 `select(Paper)` 会把模型里声明的**所有**列
都列进 SQL，于是老库上任何一次卷库查询都会撞 `no such column: paper.archived_at`，
**整个卷库读不出来**——不只是新功能不可用。

与 `attempt_service.ensure_attempt_schema()` 是同一个模式（inspect 真实表结构 → 缺什么补什么，
用 `WeakSet` 按 engine 记忆），但**刻意分成两个模块、两个表**：
那边服务的是 `attempt` 表与 D76，动它会有既有回归风险；这里只碰 `paper` 的一列。
"""

from __future__ import annotations

import weakref

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

__all__ = ["ARCHIVED_AT_COLUMN", "ADD_ARCHIVED_AT_SQL", "ensure_paper_schema"]

#: 列名，与 `app/models.py` 的 `Paper.archived_at` 同名（测试直接引用它，避免抄写漂移）。
ARCHIVED_AT_COLUMN = "archived_at"

#: 与 `Paper.archived_at`（`String(40)`、nullable）逐字一致：允许 NULL 是**有意的**，
#: 它表示「未归档」，不是「未知」——所以老库补列后所有历史卷自动都是未归档。
ADD_ARCHIVED_AT_SQL = f"ALTER TABLE paper ADD COLUMN {ARCHIVED_AT_COLUMN} VARCHAR(40)"

_ENSURED: weakref.WeakSet = weakref.WeakSet()


def ensure_paper_schema(db: Session) -> None:
    """把老库的 `paper` 表补到 D78 需要的形状（幂等，可反复调用）。

    幂等的两层：同一 engine 上第二次调用直接命中 `_ENSURED` 短路；
    即使绕过它走到 DDL，也会先 inspect 一遍确认列是否真的缺。
    """
    bind = db.get_bind()
    if bind in _ENSURED:
        return
    connection = db.connection()
    inspector = inspect(connection)
    if "paper" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("paper")}
        if ARCHIVED_AT_COLUMN not in columns:
            connection.execute(text(ADD_ARCHIVED_AT_SQL))
    _ENSURED.add(bind)
