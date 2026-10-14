"""排版包：A4 常量、CSS 变量注入、分页几何、答案版内容计划。

**写域**：`backend/app/layout/**`（角色 render-export）。

* `styles`：`LayoutParams` -> CSS（六个参数的唯一落点，名取自冻结的 `CSS_VAR_NAMES`）
* `pagination`：NFR-5 的分页几何判定与自动微调（纯计算）
* `answers`：答案版内容计划 —— HTML 与 DOCX **共用**的判据（契约第 6 条）

本包不导入 `app.render` / `app.export`，避免循环依赖。
"""
from __future__ import annotations

import os
import sys

if __package__:
    _BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _BACKEND not in sys.path:  # pragma: no cover
        sys.path.insert(0, _BACKEND)

from app.layout.answers import (  # noqa: E402
    AnswerEntry,
    AnswerPlan,
    build_answer_plan,
)
from app.layout.pagination import (  # noqa: E402
    MEASURED_BREAK_CEILING_PX,
    MM_PER_PX,
    Overflow,
    PageCrossing,
    PageMeasurement,
    PaginationGeometry,
    ReflowResult,
    detect_overflow,
    detect_page_crossing,
    geometry_for,
    measure_page,
    reflow,
    rows_for,
    usable_geometry,
)
from app.layout.styles import (  # noqa: E402
    A4_HEIGHT_MM,
    A4_WIDTH_MM,
    DIRECTIONS,
    layout_fingerprint,
    measure_js,
    page_rule,
    root_style_attr,
    style_text,
)

__all__ = [
    "A4_WIDTH_MM",
    "A4_HEIGHT_MM",
    "AnswerEntry",
    "AnswerPlan",
    "DIRECTIONS",
    "MEASURED_BREAK_CEILING_PX",
    "MM_PER_PX",
    "Overflow",
    "PageCrossing",
    "PageMeasurement",
    "PaginationGeometry",
    "ReflowResult",
    "build_answer_plan",
    "detect_overflow",
    "detect_page_crossing",
    "geometry_for",
    "layout_fingerprint",
    "measure_js",
    "measure_page",
    "page_rule",
    "reflow",
    "root_style_attr",
    "rows_for",
    "style_text",
    "usable_geometry",
]
