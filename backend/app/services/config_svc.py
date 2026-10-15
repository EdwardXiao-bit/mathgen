"""出题配置 ↔ 存储 JSON 的转换与入参校验（契约 §2.1 / §5.1）。

职责边界：
- 本模块负责 **字段名、类型、组合约束** 的转换与校验，违约抛 `ApiError(CONFIG_INVALID)`。
- 档位数值语义仍由引擎 `app.engine.config.validate` 兜底（真正生成时再校验一次）。
"""
from __future__ import annotations

import json

from app.contracts.api import PaperConfigIn
from app.engine.types import ConfigError, GenerateConfig, Op, QuestionType, RangeId
from app.services.errors import ApiError

__all__ = ["to_engine_config", "config_from_json", "config_to_json", "question_type_of"]

_OP_BY_VALUE = {op.value: op for op in Op}
_RANGE_BY_VALUE = {rid.value: rid for rid in RangeId}
_TYPE_BY_VALUE = {qt.value: qt for qt in QuestionType}


def _invalid(message: str) -> ApiError:
    return ApiError("CONFIG_INVALID", message)


def to_engine_config(payload: PaperConfigIn) -> GenerateConfig:
    """把 API 入参转成引擎的 `GenerateConfig`，并做契约 §2.1 的组合校验。"""
    if not payload.ops:
        raise _invalid("ops 不能为空")
    if not payload.ranges:
        raise _invalid("ranges 不能为空")

    # 去重：引擎内部 ops 是 set、ranges 按下标轮转，重复项只会让轮转偏斜而不报错。
    # 契约要求「非空集合」，因此显式拒绝重复，避免静默的语义漂移。
    if len(set(payload.ops)) != len(payload.ops):
        raise _invalid(f"ops 不允许重复：{payload.ops}")
    if len(set(payload.ranges)) != len(payload.ranges):
        raise _invalid(f"ranges 不允许重复：{payload.ranges}")

    ops = {_OP_BY_VALUE[value] for value in payload.ops}
    ranges = [_RANGE_BY_VALUE[value] for value in payload.ranges]
    question_type = _TYPE_BY_VALUE[payload.question_type]

    config = GenerateConfig(
        ops=ops,
        ranges=ranges,
        question_type=question_type,
        max_value=payload.max_value,
        require_carry=payload.require_carry,
        require_borrow=payload.require_borrow,
        exact_division=payload.exact_division,
        allow_remainder=payload.allow_remainder,
        parentheses=payload.parentheses,
        count=payload.count,
    )
    try:
        # 引擎侧校验（互斥项 / R7 上限 / 题型要求 / count 范围）
        from app.engine.config import validate as validate_config

        validate_config(config)
    except ConfigError as exc:
        raise _invalid(str(exc)) from exc
    return config


def config_to_json(config: GenerateConfig) -> str:
    """序列化为存储 JSON（枚举存其 `value`，与 API 字段名逐字一致）。"""
    return json.dumps(
        {
            "ops": sorted(op.value for op in config.ops),
            "ranges": [rid.value for rid in config.ranges],
            "question_type": config.question_type.value,
            "max_value": config.max_value,
            "require_carry": config.require_carry,
            "require_borrow": config.require_borrow,
            "exact_division": config.exact_division,
            "allow_remainder": config.allow_remainder,
            "parentheses": config.parentheses,
            "count": config.count,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def config_from_json(raw: str) -> GenerateConfig:
    """读取存储 JSON → `GenerateConfig`（审计 / 再生成同配置的一套题用）。"""
    data = json.loads(raw)
    return to_engine_config(PaperConfigIn(**data))


def question_type_of(config_json: str | None) -> str:
    """从存储 JSON 里取题型名（`oral` / `stepwise` / `fill`）。

    老卷的 `config_json` 缺失或损坏时返回空串：题型是**展示用**的附加信息，
    缺它不该让整张卷/整次成绩读不出来——所以这里不做 `config_from_json()` 那样的强校验，
    也**不抛异常**。读取方（学生端取题、成绩明细）共用这一处口径。
    """
    if not config_json:
        return ""
    try:
        data = json.loads(config_json)
    except (TypeError, ValueError):
        return ""
    value = data.get("question_type") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""
