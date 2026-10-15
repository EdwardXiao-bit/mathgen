"""作答入口与成绩读取（契约 §5.2 `POST /api/attempts`；§5.3 成绩列表/明细）。

`POST` 那条是学生端唯一的「写」接口，两个特殊性都在服务层：判分**只**走
`app.grading.grade()`（不自己比较答案），重复提交按 `(student_id, attempt_uuid)`
幂等去重（D76，关闭 DQ8）。

状态码：首次提交真的新建了记录 → 201；命中 D76 去重、没新建记录 → 200。
契约 §5.2 明写重放是 200，所以这个分支不是自选动作，而是照契约实现
（`SubmitOutcome.replayed` 带出这个事实，路由层不靠猜）。

成绩读取（M5 / D80）：`GET /api/attempts` 分页列表 + `GET /api/attempts/{attempt_id}` 明细。
两条都是**只读**接口，筛选与排序全在服务层（`attempt_service.list_attempts` /
`get_attempt_detail`），路由只做 wire 参数校验与「找不到 → 404」这一步。

**导出路由（`/api/attempts/export`）不在这里**：契约 §5.1 要求它必须先于
`get_attempt` 注册（否则 `export` 会被当成 `attempt_id` 吃掉）；那条由 render-export
在 task-41 落地，取数走 `attempt_service.list_attempts_all(...)`（Lead 冻结的接缝）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES, AttemptDetailOut, AttemptListOut
from app.db import get_db
from app.routers._common import finish
from app.routers.student_routes import ROUTE_SUBMIT_ATTEMPT, AttemptIn, AttemptResultOut
from app.services import attempt_service
from app.services.errors import ApiError

__all__ = ["router", "ROUTE_LIST_ATTEMPTS", "ROUTE_GET_ATTEMPT"]

router = APIRouter(tags=["attempts"])

ROUTE_LIST_ATTEMPTS = ROUTES["list_attempts"]
ROUTE_GET_ATTEMPT = ROUTES["get_attempt"]



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


@router.get(ROUTE_LIST_ATTEMPTS, response_model=AttemptListOut)
def list_attempts(
    limit: int = Query(
        default=attempt_service.DEFAULT_LIMIT,
        ge=1,
        le=attempt_service.MAX_LIMIT,
        description="本页条数",
    ),
    offset: int = Query(default=0, ge=0, description="跳过条数"),
    paper_id: int | None = Query(default=None, ge=1, description="只看某套卷的成绩"),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="姓名或学号模糊匹配；留空/不传 = 不筛选",
    ),
    db: Session = Depends(get_db),
) -> AttemptListOut:
    """成绩列表（FR-T11）：分页 + 按卷筛选 + 按姓名/学号搜索。

    `q` **不给 `min_length`**：前端清空搜索框后常会原样发 `q=`，那是一个合法的
    「不筛选」请求，不该挨 422（服务层 `_conditions` 会把空白当成没有筛选）。
    `limit`/`offset` 只影响这一页；`total` 是筛选后的总数，由服务层在分页前数出来。
    """
    total, attempts = attempt_service.list_attempts(
        db, paper_id=paper_id, q=q, limit=limit, offset=offset
    )
    return AttemptListOut(total=total, limit=limit, offset=offset, attempts=attempts)


@router.get(ROUTE_GET_ATTEMPT, response_model=AttemptDetailOut)
def get_attempt(attempt_id: int, db: Session = Depends(get_db)) -> AttemptDetailOut:
    """一次练习的完整明细（FR-T12）：逐题对错 + 快照题面 + 正确答案。

    `items` 按 `seq` 升序（服务层排序），`total` 取作答记录里写下的题数——
    见 `attempt_service.get_attempt_detail` 的文档：那条等式是写路径的保证，
    不在这里也不在服务层「用 len(items) 凑」。
    """
    detail = attempt_service.get_attempt_detail(db, attempt_id)
    if detail is None:
        raise ApiError("ATTEMPT_NOT_FOUND", f"作答记录 {attempt_id} 不存在")
    return detail
