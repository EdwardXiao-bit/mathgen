"""排版参数 ↔ 存储 JSON 的转换与校验（契约 §4 / §5.1）。

`LayoutIn`（API 出入参）与 `LayoutParams`（冻结的值对象）字段一致，
转换时**必须**调用冻结的 `validate_layout`，避免把非法排版写进数据库。
"""
from __future__ import annotations

import json

from app.contracts.api import LayoutIn
from app.contracts.layout import LayoutParams, validate_layout
from app.services.errors import ApiError

__all__ = ["to_layout_params", "layout_to_json", "layout_from_json", "layout_in_from_params"]


def to_layout_params(payload: LayoutIn) -> LayoutParams:
    """API 入参 → 冻结值对象，并做取值范围校验（越界 → VALIDATION_ERROR 422）。"""
    params = LayoutParams(
        cols_per_row=payload.cols_per_row,
        row_gap=payload.row_gap,
        font_size=payload.font_size,
        page_margin=payload.page_margin,
        answer_line=payload.answer_line,
        header_bar=payload.header_bar,
    )
    try:
        validate_layout(params)
    except ValueError as exc:
        raise ApiError("VALIDATION_ERROR", f"排版参数非法：{exc}") from exc
    return params


def layout_in_from_params(params: LayoutParams) -> LayoutIn:
    """冻结值对象 → API 出参模型（字段名与契约 §5.1 一致）。"""
    return LayoutIn(
        cols_per_row=params.cols_per_row,
        row_gap=params.row_gap,
        font_size=params.font_size,
        page_margin=params.page_margin,
        answer_line=params.answer_line,
        header_bar=params.header_bar,
    )


def layout_to_json(params: LayoutParams) -> str:
    return json.dumps(layout_in_from_params(params).model_dump(), ensure_ascii=False, sort_keys=True)


def layout_from_json(raw: str) -> LayoutParams:
    """读取存储 JSON → `LayoutParams`。

    历史数据若越界（不应发生，写入前已校验），**读取时不抛异常**：
    改为回落失败并按边界裁剪会让旧卷静默变样，这里直接报 500 更诚实。
    """
    if not raw:
        return LayoutParams()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", f"排版快照不是合法 JSON：{exc}") from exc
    return to_layout_params(LayoutIn(**data))
