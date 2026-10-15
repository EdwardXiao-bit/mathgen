"""统一错误结构与 HTTP 映射（契约 §5.3 / §9）。

错误响应体一律：

    {"error": {"code": "...", "message": "..."}}

本模块只放**错误词汇表与异常类型**，不 import FastAPI——
这样服务层可以抛业务异常，由 main.py 的异常处理器统一翻译成 JSON。
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "ApiError", "ERROR_HTTP_STATUS", "STATUS_TO_CODE", "http_status_for", "code_for_status",
]

#: 错误码 → HTTP 状态码（契约 §9；带 ★ 的是 D53 收录或本任务的必要扩展）
ERROR_HTTP_STATUS: Final[dict[str, int]] = {
    "CONFIG_INVALID": 400,
    "INSUFFICIENT_QUESTIONS": 422,
    "PAPER_NOT_FOUND": 404,
    "PAPER_NOT_EDITABLE": 409,
    "PAPER_NOT_PUBLISHED": 409,
    "PAPER_HAS_ATTEMPTS": 409,      # 契约 §9（D78）有成绩的卷不许物理删除，只能归档
    "CODE_NOT_FOUND": 404,
    # 注：`ATTEMPT_DUPLICATE` 已按 D76 删除——它原本写作「HTTP 200 + 错误码」自相矛盾，
    # 且会让重复提交返回错误信封，与「重放返回首次那次的结果、响应体相同」冲突。
    # 契约 §9 里也已同步删掉（Lead 改的），所以实现里不该再留这个码。
    "VALIDATION_ERROR": 422,
    "ITEM_NOT_FOUND": 404,          # 契约 §9（D53）
    "ATTEMPT_NOT_FOUND": 404,       # 契约 §9（M5/D80）成绩明细细 id 不存在
    "RENDER_NOT_READY": 503,        # 契约 §9（D53）
    # ★ 扩展（D57）：Word 导出尚未实现 / 未匹配路由 / 导出通道不可用
    "NOT_IMPLEMENTED": 501,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "EXPORT_NOT_READY": 503,
}

#: HTTP 状态码 → 错误码（用于把 Starlette 的 404/405 等翻成统一结构，D57）
STATUS_TO_CODE: Final[dict[int, str]] = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
}

DEFAULT_STATUS: Final[int] = 500


def http_status_for(code: str) -> int:
    return ERROR_HTTP_STATUS.get(code, DEFAULT_STATUS)


def code_for_status(status: int) -> str:
    return STATUS_TO_CODE.get(status, "HTTP_ERROR")


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
