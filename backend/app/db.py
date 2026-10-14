"""数据库连接与会话管理（T-M2-BE）。

设计要点：
1. **单例引擎 + 可注入**：`default_engine()` 懒加载单例（生产用 `data/mathgen.db`）；
   `create_app(engine=...)` 允许测试换成内存 SQLite。
2. **依赖注入**：路由通过 `Depends(get_db)` 拿会话，测试用
   `app.dependency_overrides[get_db]` 换成测试会话（FastAPI 官方测试姿势）。
3. **SQLite 外键**：默认关闭，这里显式 `PRAGMA foreign_keys=ON`，使级联删除语义真实生效。
4. 库文件路径 `data/mathgen.db` 已被 `.gitignore` 排除，不入库。

表结构契约：docs/04-接口契约.md §6（6 张表）。
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

__all__ = [
    "DB_PATH", "default_engine", "enable_sqlite_foreign_keys",
    "session_factory", "init_db", "get_db",
]

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("MATHGEN_DB", BACKEND_DIR / "data" / "mathgen.db"))

_ENGINE: Engine | None = None
_FACTORY: sessionmaker[Session] | None = None


def enable_sqlite_foreign_keys(engine: Engine) -> Engine:
    """SQLite 默认不校验外键；显式打开，使 ON DELETE CASCADE 真正生效。"""
    if engine.dialect.name != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, _record):  # pragma: no cover - 直通驱动回调
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def default_engine() -> Engine:
    """生产引擎（单例）：文件型 SQLite，零外部依赖。"""
    global _ENGINE
    if _ENGINE is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENGINE = enable_sqlite_foreign_keys(
            create_engine(
                f"sqlite+pysqlite:///{DB_PATH.as_posix()}",
                connect_args={"check_same_thread": False},
                future=True,
            )
        )
    return _ENGINE


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(engine: Engine | None = None) -> None:
    """建表（幂等）。M2 不引入迁移框架：表结构由 models.py 单一来源描述。"""
    Base.metadata.create_all(engine if engine is not None else default_engine())


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每个请求一个会话，异常时回滚。"""
    global _FACTORY
    if _FACTORY is None:
        _FACTORY = session_factory(default_engine())
    db = _FACTORY()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
