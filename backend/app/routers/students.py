"""学生端 HTTP 入口（契约 §5.2 的学生三条：报到 / 按卷号取题 / 自主练习）。

三条都**没有登录态**（D41 / D75：报到即身份，卷号即凭据），所以这里没有鉴权依赖；
唯一的信息边界是「按卷号取题」要求卷已发布——未发布返回 404 `CODE_NOT_FOUND`
而**不是** 409，否则等于告诉拿到卷号的人「这个卷号存在，只是还没发布」（服务层注释详述）。

wire 模型与路径常量在 `app/routers/student_routes.py`：M4 期间它们**不进冻结包**
（`docs/04` §8 的显式例外），权威是 §5.2 字段表 + 白名单守卫用例。

状态码如实反映现实：报到时**真的新建了**学生行才 201，命中已存在的 `(name, student_no)`
返回 200（与 `POST /api/attempts` 的 201/200 同一套口径，见 D76）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.contracts.api import PaperConfigIn
from app.db import get_db
from app.routers._common import finish
from app.routers.student_routes import (
    ROUTE_CREATE_STUDENT,
    ROUTE_STUDENT_PAPER,
    ROUTE_STUDENT_PRACTICE,
    PracticeOut,
    StudentIn,
    StudentOut,
    StudentPaperOut,
)
from app.services import student_service as svc

__all__ = ["router"]

router = APIRouter(tags=["students"])


@router.post(ROUTE_CREATE_STUDENT, response_model=StudentOut, status_code=201)
def create_student(
    payload: StudentIn,
    response: Response,
    db: Session = Depends(get_db),
) -> StudentOut:
    """学生报到（FR-S2）：`(name, student_no)` **取或建** → `student_id`。"""
    with finish(db):
        outcome = svc.report_student(db, name=payload.name, student_no=payload.student_no)
    if not outcome.created:
        response.status_code = 200  # 已存在：这是「取」，不是「建」
    return StudentOut(student_id=outcome.student_id)


@router.get(ROUTE_STUDENT_PAPER, response_model=StudentPaperOut)
def student_paper(code: str, db: Session = Depends(get_db)) -> StudentPaperOut:
    """按卷号取题（FR-S4）：题干 + 题量，**不含** `answer` / `steps`（D74）。

    题目来自 `paper_item.payload` 快照（D27），所以学生看到的题干与老师打印的卷面逐字一致。
    """
    return StudentPaperOut.model_validate(svc.paper_payload(db, code))


@router.post(ROUTE_STUDENT_PRACTICE, response_model=PracticeOut)
def student_practice(
    payload: PaperConfigIn,
    seed: int | None = Query(default=None, ge=0, le=svc.MAX_SEED),
    db: Session = Depends(get_db),
) -> PracticeOut:
    """自主练习出题（FR-S3）：请求体是**平铺的**出题参数（`count` 来自滑杆，D55）。

    `seed` 是**可选查询参数**，与老师端建卷同构：不给则服务端随机取，
    并把**实际用的那个 seed 返回**（学生提交时原样回传，D75）。
    """
    return PracticeOut.model_validate(svc.practice_payload(payload, seed=seed))
