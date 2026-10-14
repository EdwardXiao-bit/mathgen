"""答案版形式 ↔ 存储 JSON 的转换（契约 §5.1 `AnswerLayoutIn`，D53③ 由 M3 定义）。

`paper.answer_layout` 存的是这 4 个布尔的 JSON 快照：
- **M2 期间**写的是占位 `{}`；读取时按 D20 默认（四项全 true）补齐，
  所以老数据不需要迁移就能得到正确行为；
- **M3 起**由 `PATCH /api/papers/{id}/answer-layout` 落真实值。

字段语义（冻结）：
- `separate_page`：答案单独成页（D20 默认 true）
- `include_steps`：脱式题是否带逐步过程
- `include_difficulty` / `include_tags`：是否标难度 / 考点标签
"""
from __future__ import annotations

import json

from app.contracts.api import AnswerLayoutIn
from app.services.errors import ApiError

__all__ = [
    "default_answer_layout",
    "answer_layout_from_json",
    "answer_layout_to_json",
]

#: 四个字段名（落库 JSON 与 API 平铺请求体同源，天然 round-trip）
FIELDS = ("separate_page", "include_steps", "include_difficulty", "include_tags")


def default_answer_layout() -> AnswerLayoutIn:
    """D20 默认（四项全 true）。每次返回**新实例**——pydantic 模型可变，不能共享。"""
    return AnswerLayoutIn()


def answer_layout_from_json(raw: str | None) -> AnswerLayoutIn:
    """存储 JSON → `AnswerLayoutIn`。

    空值 / `{}` / 缺字段都按默认补齐（老卷与 M2 占位数据因此天然可用）。
    """
    if not raw or raw.strip() in ("", "{}", "null"):
        return default_answer_layout()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", f"答案版形式快照不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", "答案版形式快照必须是 JSON 对象")
    unknown = set(data) - set(FIELDS)
    if unknown:  # pragma: no cover - 数据损坏路径
        raise ApiError("VALIDATION_ERROR", f"答案版形式快照含未知字段：{sorted(unknown)}")
    return AnswerLayoutIn(**{key: bool(data.get(key, True)) for key in FIELDS})


def answer_layout_to_json(payload: AnswerLayoutIn) -> str:
    """`AnswerLayoutIn` → 入库文本（中文无关，字段顺序固定便于 diff）。"""
    return json.dumps(
        {key: getattr(payload, key) for key in FIELDS},
        ensure_ascii=False,
        sort_keys=True,
    )
