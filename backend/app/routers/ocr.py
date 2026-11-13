"""模式 C（OCR 批改）路由：上传批次 / 校对 / 入档 / 删批次（M18-03，docs/10 §7）。

约束与既有路由一致：
- 路径一律引用 `ROUTES` 常量；错误统一抛 `ApiError`（main.py 的异常处理器转 JSON）。
- 写操作走 `finish()`：成功 commit、异常 rollback。
- 上传只收 1–2 页、单页 ≤20MB；图片格式按魔数判断，不信客户端 `Content-Type`。
- 裁图走**受控路由**（`/crops/{seq}`）而不是静态目录挂载：`batch_id` 与 `seq` 都先校验。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.db import get_db
from app.routers._common import finish
from app.services import ocr_service
from app.services.errors import ApiError

router = APIRouter()


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    payload: list[tuple[str, bytes]] = []
    for upload in files:
        data = await upload.read()
        if not data:
            raise ApiError("VALIDATION_ERROR", "上传的图片是空文件")
        payload.append((upload.filename or "", data))
    return payload


@router.post(ROUTES["ocr_batches"], status_code=201)
async def create_batch(
    request: Request,
    paper_id: int = Form(...),
    student_name: str = Form(...),
    student_no: str = Form(...),
    source: str = Form("phone"),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """新建识别批次（multipart）：1–2 页 + 卷 + 学生姓名学号."""
    uploads = await _read_uploads(files)
    with finish(db):
        detail = ocr_service.create_batch(
            db,
            paper_id=paper_id,
            student_name=student_name,
            student_no=student_no,
            source=source,
            files=uploads,
        )
    return JSONResponse(status_code=201, content=detail)


@router.get(ROUTES["ocr_batch"])
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    return ocr_service.get_batch(db, batch_id)


@router.post(ROUTES["ocr_page"], status_code=202)
async def replace_page(
    batch_id: str,
    page_no: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """重传单页（Q10）。"""
    data = await file.read()
    if not data:
        raise ApiError("VALIDATION_ERROR", "上传的图片是空文件")
    with finish(db):
        detail = ocr_service.replace_page(db, batch_id, page_no, data)
    return JSONResponse(status_code=202, content=detail)


@router.patch(ROUTES["ocr_item"])
def update_item(
    batch_id: str,
    seq: int,
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """老师校对一题：`{answer?, reviewed?, unreadable?}`."""
    with finish(db):
        return ocr_service.update_item(
            db,
            batch_id,
            seq,
            answer=body.get("answer"),
            reviewed=body.get("reviewed"),
            unreadable=bool(body.get("unreadable", False)),
        )


@router.post(ROUTES["ocr_commit"], status_code=201)
def commit_batch(batch_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    """入档：全部题确认后生成成绩；重复调用幂等返回原 attempt."""
    with finish(db):
        result = ocr_service.commit_batch(db, batch_id)
    status = 200 if result["replayed"] else 201
    return JSONResponse(status_code=status, content=result)


@router.delete(ROUTES["ocr_batch"], status_code=204)
def delete_batch(batch_id: str, db: Session = Depends(get_db)) -> None:
    with finish(db):
        ocr_service.delete_batch(db, batch_id)


@router.get(ROUTES["ocr_crop"])
def get_crop(batch_id: str, seq: int, db: Session = Depends(get_db)) -> FileResponse:
    path = ocr_service.crop_file(db, batch_id, seq)
    return FileResponse(path, media_type="image/png")
