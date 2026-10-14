"""时间工具：一律 **ISO 8601 带时区** 字符串（契约 §5 约定）。

存字符串而非 datetime 的原因：SQLite 没有原生时间类型，
统一 ISO 文本可让「按卷号月份自增」这类字符串操作与展示、排序都稳定。
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["now", "to_iso", "utcnow_iso"]


def now() -> datetime:
    """当前时刻（带本地时区）。"""
    return datetime.now().astimezone()


def to_iso(moment: datetime) -> str:
    """转成 ISO 8601 字符串；无时区的按 UTC 补上（不允许裸时间）。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat()


def utcnow_iso() -> str:
    return to_iso(datetime.now(timezone.utc))
