"""路由层共用的小工具（非路由模块，不注册到 app）。

`render_not_ready()` 之所以单独拿出来：`/sheet` 与 `/export` 两条路由
都需要在渲染模块未就绪时返回**同一个** 503 结构（契约 §9 `RENDER_NOT_READY`）。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from app.services.sheet_render import RENDER_MODULE

__all__ = ["json_error", "render_not_ready"]


def json_error(status: int, code: str, message: str) -> JSONResponse:
    """统一错误响应结构（契约 §5.4）：`{"error": {"code", "message"}}`。"""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def render_not_ready(expected: str = RENDER_MODULE) -> JSONResponse:
    """渲染模块未就绪的标准 503 响应（结构与其他错误一致）。"""
    return json_error(
        503,
        "RENDER_NOT_READY",
        f"卷面渲染模块尚未就绪（缺少 {expected}），请稍后重试",
    )
