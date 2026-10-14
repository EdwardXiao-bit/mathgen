"""卷面渲染与导出的输入契约 —— **冻结**（docs/04-接口契约.md §5.1 / §9）。

**为什么需要它**：卷 API（生产方）与渲染/导出（消费方）由不同智能体实现，
如果双方各写一套数据结构，集成时必然对不上。这里的 `SheetContext` 就是
两边唯一认可的交换格式。

冻结的函数签名（实现分别在 render 与 export 包）：

    def render_sheet(ctx: SheetContext) -> str: ...          # 完整 HTML 文档
    def export_pdf(html: str, out_path: str) -> None: ...
    def export_docx(ctx: SheetContext, out_path: str) -> None: ...   # M3
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.contracts.layout import LayoutParams

__all__ = ["SheetMode", "SheetItem", "SheetContext"]


class SheetMode(str, Enum):
    """卷面模式（冻结）：题目版 / 答案版。"""

    QUESTION = "question"
    ANSWER = "answer"


@dataclass(frozen=True)
class SheetItem:
    """卷面上的一道题（渲染所需的**全部**字段）。

    `seq` 是**卷面题号**，删除题目后由 API 层负责前移（契约 §6 删题语义），
    渲染层不得重排。
    """

    seq: int
    display: str                                   # 卷面展示形态（填空含 □）
    answer: str = ""                               # 仅答案版使用
    difficulty: int = 0                            # 仅答案版使用
    tags: tuple[str, ...] = ()                     # 仅答案版使用
    steps: tuple[str, ...] = ()                    # 仅答案版使用


@dataclass(frozen=True)
class SheetContext:
    """一次渲染所需的全部输入。"""

    title: str
    mode: SheetMode
    items: tuple[SheetItem, ...]
    layout: LayoutParams
    code: str | None = None                        # 卷号，发布后才有（YYMM-NNNN）
    header_note: str = ""                          # 卷头补充说明（可选，如「限时 10 分钟」）


#: 渲染契约（**冻结**）：
#:   1. 返回**完整 HTML 文档**（含 <style>），不依赖任何外部资源（无 CDN，NFR-9）
#:   2. 六个排版参数必须通过 app.contracts.layout.css_variables 注入
#:   3. `mode=QUESTION` 时**不得**在输出中泄露 answer / steps（安全边界，契约 §9）
#:   4. `mode=ANSWER` 时答案默认单独成页（D20）
#:   5. 同一份 SheetContext 渲染两次结果必须完全一致（纯函数）
