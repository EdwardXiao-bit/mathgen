"""卷面上下文组装（契约 §5.1 / `app/contracts/sheet.py`，冻结格式）。

**唯一耦合点**：后端把快照组装成 `SheetContext` 交给 `app.render.sheet.render_sheet`。
本模块**只做组装**，不含任何 HTML / 排版逻辑（那是 task-6 的写域）。
输入模式为 `question` 时也照常把所有字段填进 `SheetItem`——
**是否泄露 answer/steps 由渲染器按契约 §9 决定**，后端不替它裁剪，
否则 `SheetContext` 里丢失答案会让「渲染器输出正确与否」无法被独立验证。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode
from app.services.answer_layout_svc import answer_layout_from_json
from app.services.layout_svc import layout_from_json
from app.services.paper_service import get_paper_or_404, load_item_pairs

__all__ = ["build_sheet_context", "parse_mode"]


def parse_mode(raw: str | None) -> SheetMode:
    """`mode=question|answer`（默认 question）。"""
    if raw is None:
        return SheetMode.QUESTION
    try:
        return SheetMode(raw)
    except ValueError as exc:
        from app.services.errors import ApiError

        raise ApiError(
            "VALIDATION_ERROR", f"mode 只能是 question 或 answer，实际为 {raw!r}"
        ) from exc


def build_sheet_context(
    db: Session,
    paper_id: int,
    mode: SheetMode,
    header_note: str = "",
) -> SheetContext:
    """从数据库快照组装一次渲染所需的全部输入。

    * **不重跑引擎**：题目一律来自 `paper_item.payload`（契约 §6 快照语义）。
    * **答案版形式随卷携带**（M3 / D53③）：`/sheet`、PDF、Word 三条路都拿到
      同一个 `answer_layout`，行为不会各说各话。
    """
    paper = get_paper_or_404(db, paper_id)
    layout: LayoutParams = layout_from_json(paper.layout_json)
    items = tuple(
        SheetItem(
            seq=row.seq,
            display=question.display,
            answer=question.answer,
            difficulty=question.difficulty,
            tags=tuple(question.tags),
            steps=tuple(question.steps),
        )
        for row, question in load_item_pairs(db, paper.id)
    )
    return SheetContext(
        title=paper.title,
        mode=mode,
        items=items,
        layout=layout,
        code=paper.code,
        header_note=header_note,
        answer_layout=answer_layout_from_json(paper.answer_layout),
    )
