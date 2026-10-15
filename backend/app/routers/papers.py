"""卷 API（契约 §5.1 老师端 12 条中的 11 条）。

已实现：创建 / 卷库列表 / 详情 / 按卷号取卷 / 删题 / 换一题 / 改排版 / 改答案版形式 /
发布 / 另存副本 / 二维码。未实现（其他里程碑）：成绩列表、成绩明细、CSV 导出。

强制约束：
- 全部出入参用 `response_model=` 指定冻结模型（字段名 = JSON 字段名 = snake_case）
- 全部路径引用 `ROUTES` 常量，不写字符串字面量
- 已发布卷的编辑操作统一 409 `PAPER_NOT_EDITABLE`
- 写操作统一走 `finish()`（`app.routers._common`）：成功 commit，异常 rollback 后原样抛出
- 学生端 URL 与二维码内容一律用**请求的 Host 头**构造绝对地址（D54）
- **单资源参数平铺**（D55）：`/layout`、`/answer-layout` 的请求体不包一层

M3 变更：
- `GET /api/papers` 改为 `limit`/`offset`/`q`/`status`；M2 的 `page`/`size` **已移除**
  （显式传旧参数报 422，不静默忽略）
- 新增 `GET /api/papers/by-code/{code}`（卷号无效 → `CODE_NOT_FOUND`）、
  `PATCH /api/papers/{id}/answer-layout`

M4 变更：事务出口移到 `app.routers._common.finish()`——学生端两条写接口
（报到、提交作答）也要提交，6 份 `_finish` 复制品没有理由存在。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.contracts.api import (
    ROUTES,
    AnswerLayoutIn,
    ArchivedFilter,
    LayoutIn,
    PaperConfigIn,
    PaperDetail,
    PaperItemOut,
    PaperListOut,
    PaperStatus,
    PublishResult,
)
from app.db import get_db
from app.routers._common import finish
from app.routers.urls import qr_url_for, student_url_for
from app.services import paper_service as svc
from app.services import qr_svc
from app.services.answer_layout_svc import answer_layout_from_json
from app.services.errors import ApiError
from app.services.layout_svc import layout_from_json, layout_in_from_params, to_layout_params

__all__ = ["router"]

router = APIRouter(tags=["papers"])


@router.post(ROUTES["create_paper"], response_model=PaperDetail, status_code=201)
def create_paper(
    payload: PaperConfigIn,
    seed: int | None = Query(default=None, ge=0),
    title: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> PaperDetail:
    """生成一张新 draft 卷（FR-T1/T2）。`seed` 不给则随机；同 seed 必得同一套题。

    答案版形式不在请求体里（D55 的入参表只有 config/seed/title），新卷一律 D20 默认；
    要改走 `PATCH /answer-layout`。
    """
    with finish(db):
        paper = svc.create_paper(db, payload, seed=seed, title=title)
        return svc.to_detail(db, paper)


@router.get(ROUTES["list_papers"], response_model=PaperListOut)
def list_papers(
    limit: int = Query(default=svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200, description="标题模糊匹配"),
    status: PaperStatus | None = Query(default=None),
    archived: ArchivedFilter = Query(
        default=svc.ARCHIVED_EXCLUDE,
        description="归档筛选：exclude（默认，只看未归档）/ include（全部）/ only（只看已归档）",
    ),
    page: int | None = Query(default=None, deprecated=True, description="M2 旧参数，已移除"),
    size: int | None = Query(default=None, deprecated=True, description="M2 旧参数，已移除"),
    db: Session = Depends(get_db),
) -> PaperListOut:
    """卷库检索（FR-T14 / M3 / D78）：`total` 是过滤后的总数，不受 `limit`/`offset` 影响。

    分页参数**只有** `limit`/`offset`（D64）。M2 的 `page`/`size` 已删除，
    只保留一条**迁移护栏**：显式传旧参数 → 422 并提示新写法。
    护栏不是兼容层（它不接受旧参数）；存在的原因是 FastAPI 会**默默忽略**未知查询参数，
    让调用方拿到第一页却以为翻页成功了。

    `archived` 用 `ArchivedFilter`（Literal）声明，非法值由 FastAPI 直接 422
    ——与 `status` 同一个模式，不自己写字符串校验。
    """
    if page is not None or size is not None:
        raise ApiError(
            "VALIDATION_ERROR",
            "分页参数已改为 limit/offset（D64）；旧参数 page/size 不再支持，"
            "请改用 ?limit=<1..100>&offset=<>=0>",
        )
    total, papers = svc.list_papers(
        db, limit=limit, offset=offset, status=status, q=q, archived=archived
    )
    return PaperListOut(total=total, limit=limit, offset=offset, papers=papers)


@router.get(ROUTES["get_paper_by_code"], response_model=PaperDetail)
def get_paper_by_code(code: str, db: Session = Depends(get_db)) -> PaperDetail:
    """按卷号取卷（「用卷号打开历史卷」）。

    卷号无效 → 404 **`CODE_NOT_FOUND`**（契约 §9）；按 **id** 找不到卷才用 `PAPER_NOT_FOUND`。
    """
    return svc.to_detail(db, svc.get_paper_by_code(db, code))


@router.get(ROUTES["get_paper"], response_model=PaperDetail)
def get_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    """卷详情（含题目快照，读取不重跑引擎）。"""
    return svc.get_paper(db, paper_id)


@router.delete(ROUTES["delete_item"], response_model=list[PaperItemOut])
def delete_item(paper_id: int, seq: int, db: Session = Depends(get_db)) -> list[PaperItemOut]:
    """删题（FR-T3）：后续题号前移，`edit_log` 记 `deleted seq=N`。"""
    with finish(db):
        rows = svc.delete_item(db, paper_id, seq)
        return [svc.to_item_out(row) for row in rows]


@router.post(ROUTES["regenerate_item"], response_model=PaperItemOut)
def regenerate_item(paper_id: int, seq: int, db: Session = Depends(get_db)) -> PaperItemOut:
    """换一题（FR-E9）：只替换第 seq 题，其余题保持原样。"""
    with finish(db):
        row = svc.regenerate_item(db, paper_id, seq)
        return svc.to_item_out(row)


@router.patch(ROUTES["update_layout"], response_model=LayoutIn)
def update_layout(paper_id: int, payload: LayoutIn, db: Session = Depends(get_db)) -> LayoutIn:
    """改排版（FR-T4）：六个参数越界 → 422 VALIDATION_ERROR。"""
    params = to_layout_params(payload)
    with finish(db):
        paper = svc.update_layout(db, paper_id, params)
        return layout_in_from_params(layout_from_json(paper.layout_json))


@router.patch(ROUTES["update_answer_layout"], response_model=AnswerLayoutIn)
def update_answer_layout(
    paper_id: int, payload: AnswerLayoutIn, db: Session = Depends(get_db)
) -> AnswerLayoutIn:
    """改「答案版形式」（M3 第六维，D55 平铺入参）；已发布卷 → 409 `PAPER_NOT_EDITABLE`。"""
    with finish(db):
        paper = svc.update_answer_layout(db, paper_id, payload)
        return answer_layout_from_json(paper.answer_layout)


@router.post(ROUTES["publish_paper"], response_model=PublishResult)
def publish_paper(paper_id: int, request: Request,
                  db: Session = Depends(get_db)) -> PublishResult:
    """发布（FR-T10）：分配 `YYMM-NNNN` 卷号并冻结；幂等，重复发布返回原卷号。

    `student_url` 是**绝对**地址，用请求的 `Host` 头构造（契约 §5.3 / D54）；
    `qr_url` 保持相对路径，前端同源引用。
    """
    with finish(db):
        paper = svc.publish_paper(db, paper_id)
        detail = svc.to_detail(db, paper)
    code = detail.code or ""
    return PublishResult(
        id=detail.id,
        code=code,
        qr_url=qr_url_for(detail.id),
        student_url=student_url_for(request, code),
    )


@router.post(ROUTES["archive_paper"], response_model=PaperDetail)
def archive_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    """归档（软删除，D78）：**任何状态都能归档**，返回归档后的 `PaperDetail`。

    - 幂等：重复归档保留**第一次**的 `archived_at`（不报错、不改写时间戳）。
    - **与 status 正交**：这里不改 `status`，所以取消归档后回到原来的 draft/published。
    - 归档**不影响学生端**：已发出的卷照常可答（契约 §5「卷库收纳」）。
    """
    with finish(db):
        return svc.to_detail(db, svc.archive_paper(db, paper_id))


@router.post(ROUTES["unarchive_paper"], response_model=PaperDetail)
def unarchive_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    """取消归档（D78）：`archived_at` 归 `null`，**幂等**（本来就未归档时原样返回）。"""
    with finish(db):
        return svc.to_detail(db, svc.unarchive_paper(db, paper_id))


@router.delete(ROUTES["delete_paper"], status_code=204, response_class=Response)
def delete_paper(paper_id: int, db: Session = Depends(get_db)) -> Response:
    """物理删除整卷（D78）：**只允许删没有任何学生成绩的卷**。

    - 无成绩 → **204**，无响应体（卷与它的题目行真的消失）。
    - 有成绩 → **409 `PAPER_HAS_ATTEMPTS`**，卷与成绩都**原样保留**，提示改用归档。
    - 卷不存在 → 404 `PAPER_NOT_FOUND`。

    为什么有成绩就不许删：`attempt.paper_id` 外键没有 `ondelete` 且外键校验是开的，
    真删只会撞数据库错误（详见 `services/paper_service.purge_paper` 的 docstring）。
    """
    with finish(db):
        svc.purge_paper(db, paper_id)
    return Response(status_code=204)


@router.get(
    ROUTES["qr"],
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_qr(paper_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    """卷号二维码（FR-T10 / 契约 §5.3）：`image/png`，内容为**绝对**学生端 URL。

    - 卷不存在 → 404 `PAPER_NOT_FOUND`
    - 未发布（draft）→ 409 `PAPER_NOT_PUBLISHED`（draft 没有卷号，二维码无从谈起）
    """
    paper = svc.get_paper_or_404(db, paper_id)
    svc.require_published(paper)
    payload = student_url_for(request, paper.code or "")
    return Response(content=qr_svc.png_bytes(payload), media_type="image/png")


@router.post(ROUTES["copy_paper"], response_model=PaperDetail, status_code=201)
def copy_paper(
    paper_id: int,
    title: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> PaperDetail:
    """另存为可编辑副本（D32 / 契约 §5.1 `copy_paper`）。

    新 id、新 draft、`code` 为空，题目快照与排版 / 答案版形式逐一复制；原卷不受影响。
    """
    with finish(db):
        clone = svc.copy_paper(db, paper_id, title=title)
        return svc.to_detail(db, clone)
