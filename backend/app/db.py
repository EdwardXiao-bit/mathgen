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
    "DB_PATH", "db_path", "default_engine", "enable_sqlite_foreign_keys",
    "session_factory", "init_db", "get_db",
]

BACKEND_DIR = Path(__file__).resolve().parent.parent


def db_path() -> Path:
    """当前生效的库文件路径。

    每次调用都重新读 `MATHGEN_DB`，**不在导入时冻结**：测试套件里
    `os.environ.setdefault("MATHGEN_DB", …)` 往往晚于本模块的导入
    （`tests/api/_support.py` 在模块导入阶段就 import 了 `app.db`），
    导入即冻结会让 e2e 用例把数据写进产品库（T-M8-01）。
    """
    return Path(os.environ.get("MATHGEN_DB", str(BACKEND_DIR / "data" / "mathgen.db")))


#: 导入那一刻的默认路径快照，仅为兼容保留。**取引擎一律走 `db_path()`**。
DB_PATH = db_path()

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
    """生产引擎（单例）：文件型 SQLite，零外部依赖。

    路径在**第一次取引擎时**才解析（`db_path()`），这样「先 import、后设
    `MATHGEN_DB`」也能生效（T-M8-01）。
    """
    global _ENGINE
    if _ENGINE is None:
        target = db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        _ENGINE = enable_sqlite_foreign_keys(
            create_engine(
                f"sqlite+pysqlite:///{target.as_posix()}",
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
