"""FastAPI 应用装配（T-M2-BE）。

启动：`& $VPY -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend`

统一错误结构（契约 §5.3）：所有异常出口都是
`{"error": {"code": ..., "message": ...}}`，由本模块的异常处理器兜底，
绝不让 FastAPI 默认的 `{"detail": ...}` 漏出去。

`create_app(engine=...)` 支持注入引擎：测试用内存 SQLite，生产用 `data/mathgen.db`。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from app import db as db_module
from app.engine.types import ConfigError, InsufficientQuestions
from app.routers import papers as papers_router
from app.routers import sheet as sheet_router
from app.services.errors import ApiError, http_status_for

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


def create_app(engine: Engine | None = None) -> FastAPI:
    """装配应用。

    `engine=None` 表示用生产单例引擎，但**解析延迟到 lifespan**——
    这样 `import app.main`（例如测试）不会顺手创建 `data/mathgen.db`。
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
        version="0.1.0",
        description="小学四则运算练习题生成与批改系统 —— 老师端出卷 API（M2）",
        lifespan=lifespan,
    )
    if explicit_engine is not None:
        application.state.engine = explicit_engine
    _register_error_handlers(application)
    application.include_router(papers_router.router)
    application.include_router(sheet_router.router)
    return application


#: 生产实例（uvicorn 入口）：`app.main:app`
app = create_app()
