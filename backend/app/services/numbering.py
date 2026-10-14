"""卷号分配：`YYMM-NNNN`，序号**按月自增**（契约 §6 冻结，D34）。

格式：年月各 2 位 + 4 位十进制序号，如 `2510-0007`。
规则：
- 只统计**同一年月前缀**的已发布卷，取最大序号 +1；
- 序号达到 9999 时抛 `VALIDATION_ERROR`（宁可显式失败，也不悄悄溢出成 5 位）；
- 分配与写入在**同一事务**内；`paper.code` 有唯一约束，并发冲突由调用方重试
  （见 `paper_service.publish_paper`）。
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Paper
from app.services.errors import ApiError
from app.services.timeutil import now

__all__ = ["CODE_PATTERN", "month_prefix", "next_code", "MAX_SEQ"]

#: 卷号格式（冻结）：2 位年 + 2 位月 - 4 位序号
CODE_PATTERN = re.compile(r"^\d{4}-\d{4}$")
MAX_SEQ = 9999


def month_prefix(moment: datetime | None = None) -> str:
    """返回 `YYMM`（如 `2510`）。"""
    moment = moment if moment is not None else now()
    return moment.strftime("%y%m")


def next_code(db: Session, moment: datetime | None = None) -> str:
    """分配下一个卷号（同月最大序号 +1）。"""
    prefix = month_prefix(moment)
    row = db.execute(
        select(func.max(Paper.code)).where(Paper.code.like(f"{prefix}-%"))
    ).scalar()
    if row is None:
        seq = 1
    else:
        if not CODE_PATTERN.match(row):  # pragma: no cover - 数据损坏路径
            raise ApiError("VALIDATION_ERROR", f"已存卷号格式非法：{row!r}")
        seq = int(row.split("-")[1]) + 1
    if seq > MAX_SEQ:
        raise ApiError(
            "VALIDATION_ERROR",
            f"本月卷号已用尽（{prefix}-{MAX_SEQ}），请稍后再试或联系维护者",
        )
    return f"{prefix}-{seq:04d}"
