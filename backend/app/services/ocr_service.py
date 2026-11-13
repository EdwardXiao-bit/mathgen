"""模式 C（OCR 批改）的业务层：批次状态机、页面校正、校对与入档（M18-03）。

分层口径（与 `paper_service` 一致）：
- **本模块只算与写，不 commit**；提交/回滚由路由层的 `finish()` 收口。
- 磁盘只经 `ocr_store`（目录/上限），识别算法只经 `page_detect`（纯 cv2/numpy）。
- `attempt` 只在 `commit_batch()` 里生成一次：校对前的东西永远不进成绩列表（docs/10 §3）。

本期（M18-03）还不跑识别：批次创建后状态为 `reviewing`，`ocr_item` 只有题干序号
与裁图，`final_answer` 留空等老师填或等 M18-04 的两遍识别。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.api import ROUTES
from app.contracts.sheet import SheetMode
from app.models import (
    OCR_STATUS_COMMITTED,
    OCR_STATUS_REVIEWING,
    Attempt,
    AttemptItem,
    OcrBatch,
    OcrItem,
    OcrPage,
    Paper,
)
from app.render import marking
from app.services import ocr_store
from app.services.errors import ApiError
from app.services.paper_service import get_paper_or_404, load_item_pairs
from app.services.snapshot import dump_payload
from app.services.sheet_ctx import build_sheet_context
from app.grading.grader import grade

__all__ = [
    "MAX_PAGES", "MAX_PAGE_BYTES", "ALLOWED_IMAGE_TYPES",
    "create_batch", "get_batch", "replace_page", "update_item",
    "commit_batch", "delete_batch", "crop_file",
]

#: 一批 = 一份卷（1–2 页），见 docs/10 §2
MAX_PAGES = 2
#: 单张图片字节上限（docs/10 §17.1 / Q12）
MAX_PAGE_BYTES = 20 * 1024 * 1024

#: 允许的图片格式 → 魔数前缀；HEIC 明确拒绝（浏览器兼容差、体积大）
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}

_IMAGE_EXTS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sniff_type(data: bytes) -> str | None:
    """按魔数判图片格式（不信任客户端 `Content-Type`）."""
    for mime, magics in ALLOWED_IMAGE_TYPES.items():
        for magic in magics:
            if data.startswith(magic):
                if mime == "image/webp" and data[8:12] != b"WEBP":
                    continue
                return mime
    return None


def _get_batch_or_404(db: Session, batch_id: str) -> OcrBatch:
    ocr_store.validate_batch_id(batch_id)
    row = db.get(OcrBatch, batch_id)
    if row is None:
        raise ApiError("OCR_BATCH_NOT_FOUND", "批次不存在")
    return row


def _reject_code(exc: PageRejected) -> ApiError:
    """把 `PageRejected` 翻成契约错误码：定位失败 422、卷不符 409."""
    if exc.code in {"no_qr", "wrong_sheet"}:
        return ApiError("OCR_IMAGE_UNSUPPORTED", exc.message)
    return ApiError("OCR_PAGE_CONFLICT", exc.message)


def _paper_checksum(db: Session, paper: Paper) -> str | None:
    if not paper.code:
        return None
    ctx = build_sheet_context(db, paper.id, SheetMode.QUESTION)
    return marking.sheet_checksum(ctx)


def create_batch(
    db: Session,
    *,
    paper_id: int,
    student_name: str,
    student_no: str,
    source: str,
    files: list[tuple[str, bytes]],
) -> dict:
    """新建批次：校验卷/图片 → 逐页校正 → 落盘 → 建页与题行（docs/10 §7）."""
    if not files:
        raise ApiError("VALIDATION_ERROR", "请至少上传一页卷面")
    if len(files) > MAX_PAGES:
        raise ApiError("VALIDATION_ERROR", f"一批最多 {MAX_PAGES} 页")
    name = (student_name or "").strip()
    no = (student_no or "").strip()
    if not name or not no:
        raise ApiError("VALIDATION_ERROR", "姓名与学号都不能为空")

    paper = get_paper_or_404(db, paper_id)
    if paper.status != "published" or not paper.code:
        raise ApiError("PAPER_NOT_PUBLISHED", "只有已发布的卷才能上传批改")
    checksum = _paper_checksum(db, paper)
    if checksum is None:
        raise ApiError("PAPER_NOT_PUBLISHED", "只有已发布的卷才能上传批改")

    payload = [data for _name, data in files]
    ocr_store.ensure_capacity(sum(len(data) for data in payload))

    batch_id = str(uuid.uuid4())
    batch_dir = ocr_store.batch_dir(batch_id)
    batch_dir.mkdir(parents=True, exist_ok=True)

    try:
        for index, data in enumerate(payload, start=1):
            if len(data) > MAX_PAGE_BYTES:
                raise ApiError("VALIDATION_ERROR", f"第 {index} 页超过 20MB")
            if _sniff_type(data) is None:
                raise ApiError("VALIDATION_ERROR", f"第 {index} 页不是 JPEG/PNG/WebP 图片")
            from app.services.page_detect import PageRejected, rectify

            try:
                page = rectify(data, expected_checksum=checksum)
            except PageRejected as exc:
                raise _reject_code(exc) from exc
            ext = "jpg"
            raw_path = batch_dir / f"raw-{index}.{ext}"
            ocr_store.save_bytes(raw_path, data)
            rectified_path = batch_dir / f"page-{index}.jpg"
            ocr_store.save_bytes(rectified_path, _encode_jpeg(page.image))
    except Exception:
        ocr_store.delete_batch_dir(batch_id)
        raise

    # 题目行：整卷所有题都建，页号先记 1；M18-04 的识别步骤会按 `data-answer-box`
    # 的 `page` 字段把每题的页号与裁图补上（本期只做上传与校正，不裁图）。
    seqs = [row.seq for row, _question in load_item_pairs(db, paper.id)]
    page_count = len(payload)

    batch = OcrBatch(
        id=batch_id,
        paper_id=paper.id,
        student_name=name,
        student_no=no,
        source=source or "phone",
        page_count=page_count,
        status=OCR_STATUS_REVIEWING,
        message="等待识别（M18-04）",
        created_at=_now(),
    )
    db.add(batch)
    for page_no in range(1, page_count + 1):
        db.add(
            OcrPage(
                batch_id=batch_id,
                page_no=page_no,
                status="ready",
                reason=None,
                raw_path=str(ocr_store.batch_dir(batch_id) / f"raw-{page_no}.jpg"),
                rectified_path=str(ocr_store.batch_dir(batch_id) / f"page-{page_no}.jpg"),
            )
        )
    for seq in seqs:
        db.add(
            OcrItem(
                batch_id=batch_id,
                seq=seq,
                page_no=1,
                raw_text_p1=None,
                raw_text_p2=None,
                confidence_p1=None,
                confidence_p2=None,
                agreed=0,
                final_answer=None,
                reviewed=0,
            )
        )
    db.flush()
    return get_batch(db, batch_id)


def _encode_jpeg(image) -> bytes:
    import cv2  # 局部 import：同 create_batch

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:  # pragma: no cover - cv2 编码失败几乎不可能
        raise ApiError("VALIDATION_ERROR", "校正图编码失败")
    return buffer.tobytes()


def _crop_url(batch_id: str, seq: int) -> str | None:
    """裁图受控路由；本期还不裁图，文件不存在就如实给 None（不挂静态目录）."""
    if not ocr_store.crop_path(batch_id, seq).is_file():
        return None
    return ROUTES["ocr_crop"].format(batch_id=batch_id, seq=seq)


def get_batch(db: Session, batch_id: str) -> dict:
    batch = _get_batch_or_404(db, batch_id)
    pages = list(
        db.scalars(select(OcrPage).where(OcrPage.batch_id == batch.id).order_by(OcrPage.page_no))
    )
    items = list(
        db.scalars(select(OcrItem).where(OcrItem.batch_id == batch.id).order_by(OcrItem.seq))
    )
    return {
        "batch": {
            "id": batch.id,
            "paper_id": batch.paper_id,
            "student_name": batch.student_name,
            "student_no": batch.student_no,
            "source": batch.source,
            "page_count": batch.page_count,
            "status": batch.status,
            "message": batch.message,
            "created_at": batch.created_at,
            "committed_attempt_id": batch.committed_attempt_id,
        },
        "pages": [
            {
                "page_no": page.page_no,
                "status": page.status,
                "reason": page.reason,
                "crop_url": None,
            }
            for page in pages
        ],
        "items": [
            {
                "seq": item.seq,
                "page_no": item.page_no,
                "final_answer": item.final_answer,
                "agreed": bool(item.agreed),
                "reviewed": bool(item.reviewed),
                "raw_text_p1": item.raw_text_p1,
                "raw_text_p2": item.raw_text_p2,
                "confidence_p1": item.confidence_p1,
                "confidence_p2": item.confidence_p2,
                "crop_url": _crop_url(batch.id, item.seq),
            }
            for item in items
        ],
    }


def replace_page(db: Session, batch_id: str, page_no: int, data: bytes) -> dict:
    """重传单页（Q10）：只换图与校正结果，题目行保留（识别重跑时再覆盖）."""
    batch = _get_batch_or_404(db, batch_id)
    if batch.status == OCR_STATUS_COMMITTED:
        raise ApiError("OCR_BATCH_COMMITTED", "批次已入档，不能重传")
    page = db.scalar(
        select(OcrPage).where(OcrPage.batch_id == batch.id, OcrPage.page_no == page_no)
    )
    if page is None:
        raise ApiError("OCR_BATCH_NOT_FOUND", f"批次里没有第 {page_no} 页")
    if len(data) > MAX_PAGE_BYTES:
        raise ApiError("VALIDATION_ERROR", "图片超过 20MB")
    if _sniff_type(data) is None:
        raise ApiError("VALIDATION_ERROR", "不是 JPEG/PNG/WebP 图片")
    paper = get_paper_or_404(db, batch.paper_id)
    checksum = _paper_checksum(db, paper)
    ocr_store.ensure_capacity(len(data))
    from app.services.page_detect import PageRejected, rectify

    try:
        page_image = rectify(data, expected_checksum=checksum)
    except PageRejected as exc:
        raise _reject_code(exc) from exc
    directory = ocr_store.batch_dir(batch.id)
    ocr_store.save_bytes(directory / f"raw-{page_no}.jpg", data)
    ocr_store.save_bytes(directory / f"page-{page_no}.jpg", _encode_jpeg(page_image.image))
    page.status = "ready"
    page.reason = None
    db.flush()
    return get_batch(db, batch.id)


def update_item(
    db: Session,
    batch_id: str,
    seq: int,
    *,
    answer: str | None = None,
    reviewed: bool | None = None,
    unreadable: bool = False,
) -> dict:
    """老师校对一题：填答案 / 标未作答 / 仅确认（docs/10 §10）."""
    batch = _get_batch_or_404(db, batch_id)
    if batch.status == OCR_STATUS_COMMITTED:
        raise ApiError("OCR_BATCH_COMMITTED", "批次已入档，不能再改")
    item = db.scalar(select(OcrItem).where(OcrItem.batch_id == batch.id, OcrItem.seq == seq))
    if item is None:
        raise ApiError("ITEM_NOT_FOUND", f"批次里没有第 {seq} 题")
    if unreadable:
        item.final_answer = ""
        item.reviewed = 1
    else:
        if answer is not None:
            item.final_answer = answer.strip()
            item.reviewed = 1
        if reviewed is not None:
            item.reviewed = 1 if reviewed else 0
    db.flush()
    return get_batch(db, batch.id)


def commit_batch(db: Session, batch_id: str) -> dict:
    """入档：生成 `attempt`（source=ocr）并判分；已入档则幂等返回原 attempt."""
    batch = _get_batch_or_404(db, batch_id)
    if batch.status == OCR_STATUS_COMMITTED:
        return {
            "batch_id": batch.id,
            "attempt_id": batch.committed_attempt_id,
            "replayed": True,
        }

    items = list(
        db.scalars(select(OcrItem).where(OcrItem.batch_id == batch.id).order_by(OcrItem.seq))
    )
    unreviewed = [item.seq for item in items if not item.reviewed]
    if unreviewed:
        raise ApiError(
            "OCR_ITEMS_UNREVIEWED",
            "还有未确认的题：" + "、".join(str(seq) for seq in unreviewed),
        )
    paper = get_paper_or_404(db, batch.paper_id)
    pairs = load_item_pairs(db, paper.id)
    question_by_seq = {row.seq: question for row, question in pairs}
    if not question_by_seq:
        raise ApiError("VALIDATION_ERROR", "这份卷没有题目")

    from app.services.student_service import report_student

    outcome = report_student(db, name=batch.student_name, student_no=batch.student_no)
    attempt = Attempt(
        student_id=outcome.student_id,
        attempt_uuid=str(uuid.uuid4()),
        paper_id=paper.id,
        config_json=json.dumps({"source": "ocr", "batch_id": batch.id}, ensure_ascii=False),
        total=len(question_by_seq),
        correct=0,
        duration_ms=0,
        submitted_at=_now(),
    )
    db.add(attempt)
    db.flush()

    correct = 0
    by_seq = {item.seq: item for item in items}
    for seq, question in sorted(question_by_seq.items()):
        item = by_seq.get(seq)
        raw = (item.final_answer if item is not None else "") or ""
        verdict = grade(question, raw)
        if verdict.is_correct:
            correct += 1
        db.add(
            AttemptItem(
                attempt_id=attempt.id,
                seq=seq,
                payload=dump_payload(question),
                student_answer=raw or None,
                is_correct=1 if verdict.is_correct else 0,
            )
        )
    attempt.correct = correct
    batch.status = OCR_STATUS_COMMITTED
    batch.message = None
    batch.committed_attempt_id = attempt.id
    db.flush()
    return {"batch_id": batch.id, "attempt_id": attempt.id, "replayed": False}


def delete_batch(db: Session, batch_id: str) -> None:
    """删除草稿批次：物理删除目录与三张表的行（D91）；已入档的不可删."""
    batch = _get_batch_or_404(db, batch_id)
    if batch.status == OCR_STATUS_COMMITTED:
        raise ApiError("OCR_BATCH_COMMITTED", "批次已入档，不能删除")
    for row in db.scalars(select(OcrItem).where(OcrItem.batch_id == batch.id)):
        db.delete(row)
    for row in db.scalars(select(OcrPage).where(OcrPage.batch_id == batch.id)):
        db.delete(row)
    db.delete(batch)
    db.flush()
    ocr_store.delete_batch_dir(batch.id)


def crop_file(db: Session, batch_id: str, seq: int):
    """受控裁图路由：只允许读本批次目录里的 `crop-<seq>.png`."""
    batch = _get_batch_or_404(db, batch_id)
    item = db.scalar(select(OcrItem).where(OcrItem.batch_id == batch.id, OcrItem.seq == seq))
    if item is None:
        raise ApiError("ITEM_NOT_FOUND", f"批次里没有第 {seq} 题")
    path = ocr_store.crop_path(batch.id, seq)
    if not path.is_file():
        raise ApiError("ITEM_NOT_FOUND", "这道题还没有裁图")
    return path
