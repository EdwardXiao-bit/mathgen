"""提交作答入口（契约 §5.2 `POST /api/attempts`；判分在 `services/attempt_service`）。

这一条是学生端唯一的「写」接口，两个特殊性都在服务层：判分**只**走
`app.grading.grade()`（不自己比较答案），重复提交按 `(student_id, attempt_uuid)`
幂等去重（D76，关闭 DQ8）。

状态码：首次提交真的新建了记录 → 201；命中 D76 去重、没新建记录 → 200。
契约 §5.2 明写重放是 200，所以这个分支不是自选动作，而是照契约实现
（`SubmitOutcome.replayed` 带出这个事实，路由层不靠猜）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers._common import finish
from app.routers.student_routes import ROUTE_SUBMIT_ATTEMPT, AttemptIn, AttemptResultOut
from app.services import attempt_service

__all__ = ["router"]

router = APIRouter(tags=["attempts"])


@router.post(ROUTE_SUBMIT_ATTEMPT, response_model=AttemptResultOut, status_code=201)
def submit_attempt(
    payload: AttemptIn,
    response: Response,
    db: Session = Depends(get_db),
) -> AttemptResultOut:
    """提交作答并判分（FR-S7 / FR-S8）。

    wire → domain 的适配只在这里做：`answers` 从 `{seq, answer}` 对象列表摊平成
    `(seq, answer)` 元组序列（服务层不依赖 HTTP 模型，也就不必知道 `AnswerIn` 长什么样）。
    """
    answers = [(item.seq, item.answer) for item in payload.answers]
    with finish(db):
        outcome = attempt_service.submit(
            db,
            student_id=payload.student_id,
            attempt_uuid=payload.attempt_uuid,
            answers=answers,
            duration_ms=payload.duration_ms,
            paper_id=payload.paper_id,
            config=payload.config,
            seed=payload.seed,
        )
    if outcome.replayed:
        response.status_code = 200  # D76：重放没有新建记录，不能报 201
    return AttemptResultOut.model_validate(outcome.payload)
