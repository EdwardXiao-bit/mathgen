"""API 测试支撑（T-M2-BE）。

关键设计：**测试不碰生产库**。
- 每个测试用例一个内存 SQLite（`StaticPool` 让所有连接共享同一份内存库）；
- 用 FastAPI 官方的 `dependency_overrides[get_db]` 注入测试会话；
- `create_app(engine=...)` 的 lifespan 会在 TestClient 启动时建表。

同时是「快照不重算」实验台：`patch_engine` 上下文管理器替换 `app.engine.generate`，
用来验证已存卷的读取路径**不会**回头调用引擎。
"""
from __future__ import annotations

import contextlib
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import enable_sqlite_foreign_keys, get_db  # noqa: E402
from app.main import create_app  # noqa: E402

__all__ = [
    "ApiTestCase", "make_client", "cli", "fresh_engine", "session_of", "patch_engine",
    "CONFIG_ADD_SUB", "CONFIG_ORAL", "url_of",
]

#: 最小合法出题配置：20 以内加减、口算、6 题（生成快、约束好满足）
CONFIG_ADD_SUB = {
    "ops": ["+", "-"],
    "ranges": ["R1"],
    "question_type": "oral",
    "require_carry": False,
    "require_borrow": False,
    "exact_division": False,
    "allow_remainder": False,
    "parentheses": False,
    "count": 6,
}

#: 只需加法的最小配置（用于换题等场景）
CONFIG_ORAL = dict(CONFIG_ADD_SUB, ops=["+"], count=4)


def fresh_engine() -> Engine:
    """一个独立的内存库（同进程内所有连接共享）。"""
    return enable_sqlite_foreign_keys(
        create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    )


def session_of(engine: Engine) -> Session:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()


def make_client(engine: Engine | None = None) -> TestClient:
    """建一个 TestClient（**必须配合 `with` 使用**，见 `ApiTestCase`）。

    注意：Starlette 的 `TestClient` 只有进入上下文管理器才跑 lifespan，
    而建表在 lifespan 里 —— 不进入上下文会拿到「no such table: paper」。
    """
    engine = engine if engine is not None else fresh_engine()
    app = create_app(engine=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    def _override_get_db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def cli(client: TestClient, method: str, path: str, **kwargs):
    """统一的请求入口（把 httpx 的调用集中在一处，便于断言）。"""
    return client.request(method, path, **kwargs)


class ApiTestCase(unittest.TestCase):
    """所有 API 用例的基类：每个用例一套独立内存库 + 独立 TestClient。"""

    def setUp(self) -> None:  # noqa: D102
        super().setUp()
        self.engine = fresh_engine()
        self._client_cm = make_client(self.engine)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:  # noqa: D102
        self._client_cm.__exit__(None, None, None)
        self.engine.dispose()
        super().tearDown()

    # ---- 常用动作 -------------------------------------------------------

    def create_paper(self, config: dict | None = None, **query) -> dict:
        """建卷：`config` 是**覆盖项**，未给的字段用 `CONFIG_ADD_SUB` 补齐。"""
        body = dict(CONFIG_ADD_SUB)
        if config:
            body.update(config)
        response = self.client.post(
            url_of("create_paper"),
            json=body,
            params=query or {"seed": 2025},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def publish(self, paper_id: int):
        return self.client.post(url_of("publish_paper", paper_id=paper_id))

    def detail(self, paper_id: int) -> dict:
        response = self.client.get(url_of("get_paper", paper_id=paper_id))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def session(self) -> Session:
        """直连数据库的会话（用来注入特定卷号、断言表内容）。"""
        return session_of(self.engine)


@contextlib.contextmanager
def patch_engine(**patches) -> Iterator[None]:
    """临时替换 `app.engine` 模块上的函数（模拟引擎升级 / 断言不被调用）。"""
    import app.engine as engine_module

    saved = {}
    for name, value in patches.items():
        saved[name] = getattr(engine_module, name)
        setattr(engine_module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(engine_module, name, value)


def url_of(name: str, **params) -> str:
    from app.contracts.api import ROUTES

    return ROUTES[name].format(**params)
