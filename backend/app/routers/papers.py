"""卷 API（契约 §5.1 老师端 12 条中的 9 条）。

已实现：创建 / 列表 / 详情 / 删题 / 换一题 / 改排版 / 发布 / 另存副本 / 二维码。
未实现（属其他里程碑）：导出、成绩列表、成绩明细、CSV 导出。

强制约束：
- 全部出入参用 `response_model=` 指定冻结模型（字段名 = JSON 字段名 = snake_case）
- 全部路径引用 `ROUTES` 常量（含 D53 新收录的 `copy_paper`），不写字符串字面量
- 已发布卷的编辑操作统一 409 `PAPER_NOT_EDITABLE`
- 写操作统一走 `_finish()`：成功 commit，异常 rollback 后原样抛出
- 学生端 URL 与二维码内容一律用**请求的 Host 头**构造绝对地址（D54）
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.contracts.api import (
    ROUTES,
    LayoutIn,
    PaperConfigIn,
    PaperDetail,
    PaperItemOut,
    PaperListOut,
    PaperStatus,
    PublishResult,
)
from app.db import get_db
from app.routers.urls import qr_url_for, student_url_for
from app.services import paper_service as svc
from app.services import qr_svc
from app.services.layout_svc import layout_from_json, layout_in_from_params, to_layout_params

__all__ = ["router"]

router = APIRouter(tags=["papers"])


@contextmanager
def _finish(db: Session) -> Iterator[None]:
    """写操作事务出口：成功 commit；异常 rollback 并原样抛出。"""
    try:
        yield
    except Exception:
        db.rollback()
        raise
    db.commit()


@router.post(ROUTES["create_paper"], response_model=PaperDetail, status_code=201)
def create_paper(
    payload: PaperConfigIn,
    seed: int | None = Query(default=None, ge=0),
    title: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
) -> PaperDetail:
    """生成一张新 draft 卷（FR-T1/T2）。`seed` 不给则随机；同 seed 必得同一套题。"""
    with _finish(db):
        paper = svc.create_paper(db, payload, seed=seed, title=title)
        return svc.to_detail(db, paper)


@router.get(ROUTES["list_papers"], response_model=PaperListOut)
def list_papers(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    status: PaperStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PaperListOut:
    """分页列出卷（FR-T14）。"""
    total, papers = svc.list_papers(db, page, size, status)
    return PaperListOut(total=total, papers=papers)


@router.get(ROUTES["get_paper"], response_model=PaperDetail)
def get_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperDetail:
    """卷详情（含题目快照，读取不重跑引擎）。"""
    return svc.get_paper(db, paper_id)


@router.delete(ROUTES["delete_item"], response_model=list[PaperItemOut])
def delete_item(paper_id: int, seq: int, db: Session = Depends(get_db)) -> list[PaperItemOut]:
    """删题（FR-T3）：后续题号前移，`edit_log` 记 `deleted seq=N`。"""
    with _finish(db):
        rows = svc.delete_item(db, paper_id, seq)
        return [svc.to_item_out(row) for row in rows]


@router.post(ROUTES["regenerate_item"], response_model=PaperItemOut)
def regenerate_item(paper_id: int, seq: int, db: Session = Depends(get_db)) -> PaperItemOut:
    """换一题（FR-E9）：只替换第 seq 题，其余题保持原样。"""
    with _finish(db):
        row = svc.regenerate_item(db, paper_id, seq)
        return svc.to_item_out(row)


@router.patch(ROUTES["update_layout"], response_model=LayoutIn)
def update_layout(paper_id: int, payload: LayoutIn, db: Session = Depends(get_db)) -> LayoutIn:
    """改排版（FR-T4）：六个参数越界 → 422 VALIDATION_ERROR。"""
    params = to_layout_params(payload)
    with _finish(db):
        paper = svc.update_layout(db, paper_id, params)
        return layout_in_from_params(layout_from_json(paper.layout_json))


@router.post(ROUTES["publish_paper"], response_model=PublishResult)
def publish_paper(paper_id: int, request: Request,
                  db: Session = Depends(get_db)) -> PublishResult:
    """发布（FR-T10）：分配 `YYMM-NNNN` 卷号并冻结；幂等，重复发布返回原卷号。

    `student_url` 是**绝对**地址，用请求的 `Host` 头构造（契约 §5.3 / D54）；
    `qr_url` 保持相对路径，前端同源引用。
    """
    with _finish(db):
        paper = svc.publish_paper(db, paper_id)
        detail = svc.to_detail(db, paper)
    code = detail.code or ""
    return PublishResult(
        id=detail.id,
        code=code,
        qr_url=qr_url_for(detail.id),
        student_url=student_url_for(request, code),
    )


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

    新 id、新 draft、`code` 为空，题目快照逐字复制；原卷（及其成绩）不受影响。
    """
    with _finish(db):
        clone = svc.copy_paper(db, paper_id, title=title)
        return svc.to_detail(db, clone)
