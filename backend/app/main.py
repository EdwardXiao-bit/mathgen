"""FastAPI 应用装配（T-M2-BE）。

启动：`& $VPY -m app`（等价于安装后的 `mathgen` 命令，见 `app/__main__.py`）。
本模块只负责装配应用；命令行参数、数据目录与前端产物的解析都在启动器里。

统一错误结构（契约 §5.4）：所有异常出口都是
`{"error": {"code": ..., "message": ...}}`，由本模块的异常处理器兜底，
绝不让 FastAPI / Starlette 默认的 `{"detail": ...}` 漏出去——
**包括未匹配路由**（`/api/nonexistent` → 404 `NOT_FOUND`，D57）。

`create_app(engine=...)` 支持注入引擎：测试用内存 SQLite，生产用 `data/mathgen.db`。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app import db as db_module
from app.engine.types import ConfigError, InsufficientQuestions
from app.routers import attempts as attempts_router
from app.routers import exports as exports_router
from app.routers import papers as papers_router
from app.routers import sheet as sheet_router
from app.routers import students as students_router
from app.services import static_site
from app.services.errors import ApiError, code_for_status, http_status_for

__all__ = ["app", "create_app"]

logger = logging.getLogger("mathgen.api")


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @application.exception_handler(ConfigError)
    async def _handle_config_error(_request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for("CONFIG_INVALID"),
            content=_error("CONFIG_INVALID", str(exc)),
        )

    @application.exception_handler(InsufficientQuestions)
    async def _handle_insufficient(
        _request: Request, exc: InsufficientQuestions
    ) -> JSONResponse:
        return JSONResponse(
            status_code=http_status_for("INSUFFICIENT_QUESTIONS"),
            content=_error("INSUFFICIENT_QUESTIONS", str(exc)),
        )

    @application.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求体 / 路径 / 查询参数校验失败 → 422 VALIDATION_ERROR（统一结构）。"""
        details = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()))
            details.append(f"{location}: {item.get('msg', '')}")
        message = "请求参数校验失败：" + "；".join(details) if details else "请求参数校验失败"
        return JSONResponse(
            status_code=http_status_for("VALIDATION_ERROR"),
            content=_error("VALIDATION_ERROR", message),
        )

    @application.exception_handler(ValueError)
    async def _handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
        """兜底的 ValueError（如冻结的 `validate_layout` 直接抛出的越界）→ 422。"""
        return JSONResponse(
            status_code=http_status_for("VALIDATION_ERROR"),
            content=_error("VALIDATION_ERROR", str(exc) or "参数非法"),
        )

    @application.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """未匹配路由 / 方法不允许等 Starlette 内建异常 → 统一结构（D57）。

        修复前：`GET /api/nonexistent` → `{"detail":"Not Found"}`，
        绕过了上方所有处理器，与本模块「绝不让 detail 漏出去」的声明矛盾。
        """
        code = code_for_status(exc.status_code)
        if code == "NOT_FOUND":
            # 不采用 Starlette 的英文默认 detail（"Not Found"），保持全站中文提示
            message = f"接口不存在：{request.method} {request.url.path}"
        elif code == "METHOD_NOT_ALLOWED":
            message = f"该接口不支持 {request.method} 方法：{request.url.path}"
        else:
            detail = exc.detail if isinstance(exc.detail, str) and exc.detail else ""
            message = detail or f"HTTP {exc.status_code}"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error(code, message),
            headers=getattr(exc, "headers", None),
        )


def create_app(
    engine: Engine | None = None,
    *,
    static_dir: "Path | str | bool | None" = None,
) -> FastAPI:
    """装配应用。

    * `engine=None`：用生产单例引擎，但**解析延迟到 lifespan**——
      这样 `import app.main`（例如测试）不会顺手创建 `data/mathgen.db`。
    * `static_dir`（M3 单一服务托管）：
      - `None`（默认）→ 用 `MATHGEN_STATIC_DIR`，否则 `<repo>/frontend/dist`
      - 具体路径 → 用它
      - `False` → 明确不挂界面（API 测试与纯后端场景）
      目录不存在时**照常启动**，只是不提供界面。
    """
    explicit_engine = engine

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved = explicit_engine if explicit_engine is not None else db_module.default_engine()
        application.state.engine = resolved
        db_module.init_db(resolved)
        yield

    application = FastAPI(
        title="mathgen 后端",
        version=__version__,
        description="小学四则运算练习题生成与批改系统 —— 老师端 API（出卷 / 卷库 / 学生扫码答题 / 判分与成绩）",
        lifespan=lifespan,
    )
    if explicit_engine is not None:
        application.state.engine = explicit_engine
    _register_error_handlers(application)
    application.include_router(papers_router.router)
    application.include_router(sheet_router.router)
    application.include_router(exports_router.router)
    application.include_router(students_router.router)
    application.include_router(attempts_router.router)

    # 界面兜底**必须最后挂**：靠注册顺序让位给 /api、/docs 等精确路由
    if static_site.mount_spa(application, static_dir):
        logger.info("已挂载前端产物：%s", application.state.static_dir)
    else:
        logger.info("未挂载前端产物（dist 不存在或已显式关闭），仅提供 API")
    return application


#: 生产实例（uvicorn 入口）：`app.main:app`
app = create_app()
