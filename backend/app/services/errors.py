"""统一错误结构与 HTTP 映射（契约 §5.3 / §9）。

错误响应体一律：

    {"error": {"code": "...", "message": "..."}}

本模块只放**错误词汇表与异常类型**，不 import FastAPI——
这样服务层可以抛业务异常，由 main.py 的异常处理器统一翻译成 JSON。
"""
from __future__ import annotations

from typing import Final

__all__ = ["ApiError", "ERROR_HTTP_STATUS", "http_status_for"]

#: 错误码 → HTTP 状态码（契约 §9；带 ★ 的是本任务为实现改题/删题语义做的必要扩展，
#: 已在交接报告中登记，等待 Lead 决定是否写入契约 §9）
ERROR_HTTP_STATUS: Final[dict[str, int]] = {
    "CONFIG_INVALID": 400,
    "INSUFFICIENT_QUESTIONS": 422,
    "PAPER_NOT_FOUND": 404,
    "PAPER_NOT_EDITABLE": 409,
    "PAPER_NOT_PUBLISHED": 409,
    "CODE_NOT_FOUND": 404,
    "ATTEMPT_DUPLICATE": 200,
    "VALIDATION_ERROR": 422,
    # ★ 扩展：题目序号不存在 / 越界
    "ITEM_NOT_FOUND": 404,
    # ★ 扩展：渲染模块尚未就绪（task-6 并行实现中）
    "RENDER_NOT_READY": 503,
}

DEFAULT_STATUS: Final[int] = 500


def http_status_for(code: str) -> int:
    return ERROR_HTTP_STATUS.get(code, DEFAULT_STATUS)


class ApiError(Exception):
    """业务异常：携带错误码与面向老师的中文提示。"""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status if status is not None else http_status_for(code)

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"ApiError(code={self.code!r}, status={self.status}, message={self.message!r})"
