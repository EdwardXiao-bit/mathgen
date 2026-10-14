"""老师端 REST 出入参模型与路径常量 —— **冻结**（docs/04-接口契约.md §5.1）。

**为什么需要它**：卷 API（生产方）与老师端界面（消费方）由不同智能体实现。
把 JSON 形状冻结成 Pydantic 模型后，API 用 `response_model=` 强制约束，
前端按同一份字段名写，集成时不会因为 `paperId` / `paper_id` 这类分歧返工。

约定：
- JSON 字段一律 **snake_case**（与数据库、Python 一致），前端自行映射。
- 时间一律 **ISO 8601 带时区** 字符串。
- 错误响应统一 `{"error": {"code": ..., "message": ...}}`。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "ROUTES", "PaperConfigIn", "LayoutIn", "PaperItemOut",
    "PaperDetail", "PaperSummary", "PaperListOut", "PublishResult",
    "ErrorBody", "ErrorResponse",
]

#: 路径常量（**冻结**）：前端与后端都必须引用这里，禁止各写字符串字面量
ROUTES = {
    "create_paper": "/api/papers",
    "list_papers": "/api/papers",
    "get_paper": "/api/papers/{paper_id}",
    "delete_item": "/api/papers/{paper_id}/items/{seq}",
    "regenerate_item": "/api/papers/{paper_id}/items/{seq}/regenerate",
    "update_layout": "/api/papers/{paper_id}/layout",
    "publish_paper": "/api/papers/{paper_id}/publish",
    "sheet": "/api/papers/{paper_id}/sheet",
    "export": "/api/papers/{paper_id}/export",
    "qr": "/api/papers/{paper_id}/qr",
}

PaperStatus = Literal["draft", "published"]
QuestionTypeName = Literal["oral", "stepwise", "fill"]
RangeName = Literal["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
OpName = Literal["+", "-", "×", "÷"]


class PaperConfigIn(BaseModel):
    """出题参数（对应 `app.engine.types.GenerateConfig`）。"""

    ops: list[OpName] = Field(min_length=1)
    ranges: list[RangeName] = Field(min_length=1)
    question_type: QuestionTypeName
    max_value: int | None = None
    require_carry: bool = False
    require_borrow: bool = False
    exact_division: bool = False
    allow_remainder: bool = False
    parentheses: bool = False
    count: int = Field(default=20, ge=1, le=100)


class LayoutIn(BaseModel):
    """排版参数（对应 `app.contracts.layout.LayoutParams`）。"""

    cols_per_row: int = Field(default=4, ge=2, le=6)
    row_gap: float = Field(default=1.0, ge=0.8, le=2.0)
    font_size: int = Field(default=14, ge=10, le=20)
    page_margin: float = Field(default=2.0, ge=1.0, le=3.0)
    answer_line: bool = True
    header_bar: bool = True


class PaperItemOut(BaseModel):
    """卷面题目快照。老师端**包含** answer / steps（学生端必须剔除，契约 §9）。"""

    seq: int
    display: str
    expr: str
    answer: str
    difficulty: int
    tags: list[str]
    steps: list[str]
    origin: str = "generated"


class PaperDetail(BaseModel):
    id: int
    code: str | None = None
    title: str
    status: PaperStatus
    seed: int
    config: PaperConfigIn
    layout: LayoutIn
    created_at: str
    published_at: str | None = None
    items: list[PaperItemOut]


class PaperSummary(BaseModel):
    id: int
    code: str | None = None
    title: str
    status: PaperStatus
    item_count: int
    created_at: str
    published_at: str | None = None


class PaperListOut(BaseModel):
    total: int
    papers: list[PaperSummary]


class PublishResult(BaseModel):
    id: int
    code: str
    qr_url: str
    student_url: str


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
