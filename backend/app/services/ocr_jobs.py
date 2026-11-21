"""OCR 批次的后台执行（M18-04，docs/10 §4）。

- 单进程 `ThreadPoolExecutor`，`OCR_WORKERS` 默认 1：本机老师端够用，也避免多个
  onnxruntime 会话抢 CPU（Q11）。
- 线程只做「取会话 → 跑 `ocr_service.run_batch` → commit / rollback」，
  业务语义仍在 `ocr_service`，方便判据直接同步调用。
- 进程崩溃时残留的 `processing` 批次由 `reset_stale()` 在启动时退回 `pending`（Q11），
  等下一次启动重新排队；不静默丢批次。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import update

from app.db import default_engine, session_factory
from app.models import OCR_STATUS_FAILED, OCR_STATUS_PENDING, OCR_STATUS_PROCESSING, OcrBatch

__all__ = ["enqueue", "reset_stale", "wait_idle", "shutdown"]

_LOG = logging.getLogger(__name__)

#: 并发识别线程数；Q11 明确「多 worker 不支持」，这里也只在单进程内生效
WORKERS = max(1, int(os.environ.get("OCR_WORKERS", "1")))

_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="mathgen-ocr")
_LOCK = threading.Lock()
_ACTIVE: set[str] = set()


def _session(engine=None):
    return session_factory(engine if engine is not None else default_engine())()


def _job(batch_id: str) -> None:
    db = _session()
    try:
        from app.services.ocr_service import run_batch  # noqa: PLC0415 - 避免循环导入

        run_batch(db, batch_id)
        db.commit()
    except Exception:  # pragma: no cover - 兜底：线程里不能把异常吞成静默失败
        db.rollback()
        _LOG.exception("OCR 批次 %s 识别失败", batch_id)
        try:
            db.execute(
                update(OcrBatch)
                .where(OcrBatch.id == batch_id)
                .values(status=OCR_STATUS_FAILED, message="识别失败，请重试或改人工录入")
            )
            db.commit()
        except Exception:  # pragma: no cover - 连标记失败都写不进就只能记日志
            db.rollback()
            _LOG.exception("OCR 批次 %s 状态标记失败", batch_id)
    finally:
        db.close()
        with _LOCK:
            _ACTIVE.discard(batch_id)


def enqueue(batch_id: str) -> None:
    """把批次丢给后台线程；创建接口立即返回 201（Q2）。"""
    with _LOCK:
        _ACTIVE.add(batch_id)
    _POOL.submit(_job, batch_id)


def reset_stale(engine=None) -> int:
    """把崩溃残留的 `processing` 退回 `pending`，返回受影响的行数（Q11）。

    `engine` 显式传入：lifespan 里用的是 `create_app(engine=…)` 解析出来的引擎，
    测试与嵌入场景的临时库不是 `default_engine()`。
    """
    db = _session(engine)
    try:
        result = db.execute(
            update(OcrBatch)
            .where(OcrBatch.status == OCR_STATUS_PROCESSING)
            .values(status=OCR_STATUS_PENDING, message="上次识别中断，等待重新识别")
        )
        db.commit()
        return int(result.rowcount or 0)
    finally:
        db.close()


def wait_idle(timeout: float | None = None) -> bool:
    """等后台队列清空（判据专用；生产路径不调用）。"""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        with _LOCK:
            busy = bool(_ACTIVE)
        if not busy:
            return True
        if deadline is not None and time.monotonic() > deadline:
            return False
        time.sleep(0.02)


def shutdown() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)
