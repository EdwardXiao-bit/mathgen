"""API 测试支撑（T-M2-BE）。

关键设计：**测试不碰生产库**。
- 每个测试用例一个内存 SQLite（`StaticPool` 让所有连接共享同一份内存库）；
- 用 FastAPI 官方的 `dependency_overrides[get_db]` 注入测试会话；
- `create_app(engine=...)` 的 lifespan 会在 TestClient 启动时建表。

同时是两个实验台：
- `patch_engine`：替换 `app.engine.generate`，验证已存卷的读取路径**不重跑引擎**；
- `render_with` / `render_unavailable`：注入假渲染器 / 模拟渲染模块缺失，
  验证 `/sheet` 与 `/export` 的同源与 503 兜底（`/export` 也在这里取用）。
"""
from __future__ import annotations

import builtins
import contextlib
import sys
import types
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
    "render_unavailable", "render_with", "docx_with", "docx_unavailable",
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


def make_client(engine: Engine | None = None, *, static_dir: object = False) -> TestClient:
    """建一个 TestClient（**必须配合 `with` 使用**，见 `ApiTestCase`）。

    * 注意：Starlette 的 `TestClient` 只有进入上下文管理器才跑 lifespan，
      而建表在 lifespan 里 —— 不进入上下文会拿到「no such table: paper」。
    * `static_dir=False`（默认）：**不挂**前端产物，让 API 用例与真实 `dist` 解耦；
      静态托管用例显式传目录，或传 `None` 走 `MATHGEN_STATIC_DIR` / 仓库默认。
    """
    engine = engine if engine is not None else fresh_engine()
    app = create_app(engine=engine, static_dir=static_dir)
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


@contextlib.contextmanager
def render_unavailable() -> Iterator[None]:
    """让 `from app.render.sheet import render_sheet` 必定 ImportError。

    与真实渲染模块是否存在无关（因此「渲染未就绪 → 503」这条用例永远有效）。
    """
    saved_module = sys.modules.pop("app.render.sheet", None)
    saved_package = sys.modules.pop("app.render", None)
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.render.sheet" or (name == "app.render" and fromlist):
            raise ImportError(f"模拟渲染模块缺失：{name}")
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = fake_import
    try:
        yield
    finally:
        builtins.__import__ = real_import
        if saved_package is not None:
            sys.modules["app.render"] = saved_package
        if saved_module is not None:
            sys.modules["app.render.sheet"] = saved_module


@contextlib.contextmanager
def render_with(render_sheet) -> Iterator[None]:
    """注入一个假渲染器（通常用来记录收到的 `SheetContext`）。"""
    module = types.ModuleType("app.render.sheet")
    module.render_sheet = render_sheet
    package = types.ModuleType("app.render")
    package.sheet = module
    saved_package = sys.modules.get("app.render")
    saved_module = sys.modules.get("app.render.sheet")
    sys.modules["app.render"] = package
    sys.modules["app.render.sheet"] = module
    try:
        yield
    finally:
        for key, value in (("app.render", saved_package), ("app.render.sheet", saved_module)):
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


@contextlib.contextmanager
def docx_with(export_docx) -> Iterator[None]:
    """注入一个假 Word 导出器（`app.export.docx.export_docx(ctx, path)`）。

    与 `render_with` 同一套路：注册到 `sys.modules`，让服务层的延迟导入拿到假的。
    """
    module = types.ModuleType("app.export.docx")
    module.export_docx = export_docx
    package = sys.modules.get("app.export")
    saved_module = sys.modules.get("app.export.docx")
    saved_attr = getattr(package, "docx", None) if package is not None else None
    sys.modules["app.export.docx"] = module
    if package is not None:
        package.docx = module
    try:
        yield
    finally:
        if saved_module is None:
            sys.modules.pop("app.export.docx", None)
        else:
            sys.modules["app.export.docx"] = saved_module
        if package is not None:
            if saved_attr is None:
                with contextlib.suppress(AttributeError):
                    del package.docx
            else:
                package.docx = saved_attr


@contextlib.contextmanager
def docx_unavailable() -> Iterator[None]:
    """让 `from app.export.docx import export_docx` 必定 ImportError（未就绪 → 503）。"""
    saved_module = sys.modules.pop("app.export.docx", None)
    package = sys.modules.get("app.export")
    saved_attr = getattr(package, "docx", None) if package is not None else None
    if package is not None and hasattr(package, "docx"):
        with contextlib.suppress(AttributeError):
            del package.docx
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.export.docx" or (name == "app.export" and "docx" in (fromlist or ())):
            raise ImportError(f"模拟 Word 模块缺失：{name}")
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = fake_import
    try:
        yield
    finally:
        builtins.__import__ = real_import
        if saved_module is not None:
            sys.modules["app.export.docx"] = saved_module
        if package is not None and saved_attr is not None:
            package.docx = saved_attr


def url_of(name: str, **params) -> str:
    from app.contracts.api import ROUTES

    return ROUTES[name].format(**params)
