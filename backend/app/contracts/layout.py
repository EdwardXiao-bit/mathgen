"""排版参数与 CSS 变量映射 —— **冻结**（docs/04-接口契约.md §4，D30）。

六个可调项就是老师端排版面板的全部控件；它们必须原样映射为 CSS 变量，
使**屏幕预览、浏览器打印、PDF 导出**共用同一套样式（C3 / NFR-5）。
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LayoutParams", "CSS_VAR_NAMES", "css_variables", "validate_layout"]


@dataclass(frozen=True)
class LayoutParams:
    """A4 卷面的六个可调参数（D30）。"""

    cols_per_row: int = 4        # 每行题数，2..6
    row_gap: float = 1.0         # 行间距倍数，0.8..2.0
    font_size: int = 14          # 题目字号 pt，10..20
    page_margin: float = 2.0     # 页边距 cm，1.0..3.0
    answer_line: bool = True     # 是否留答题横线 / 书写空位
    header_bar: bool = True      # 是否显示卷头信息栏（班级 / 姓名 / 得分）


#: CSS 变量名（**冻结**）：渲染模板与前端预览必须使用同名变量
#:
#: **D59 修正**：两个布尔项原来暴露为 `--answer-line: 1|0` / `--header-bar: 1|0`，
#: 但样式表里**没有任何规则读取它们**（隐藏逻辑走的是渲染期烘死的 `data-*` 属性），
#: 导致老师端排版面板改这两个开关时预览毫无反应——面板写的变量是死的。
#: 现改为输出 **CSS 可直接用作 `display` 的关键字**，样式表 `display: var(...)` 直接消费，
#: 于是「服务端渲染」与「面板实时覆盖」走的是同一条通路。
CSS_VAR_NAMES = {
    "cols_per_row": "--cols-per-row",
    "row_gap": "--row-gap",
    "font_size": "--font-size",
    "page_margin": "--page-margin",
    "answer_line": "--answer-line-display",
    "header_bar": "--header-bar-display",
}

#: 取值范围（**冻结**）：越界必须被拒绝，前后端用同一组边界
BOUNDS = {
    "cols_per_row": (2, 6),
    "row_gap": (0.8, 2.0),
    "font_size": (10, 20),
    "page_margin": (1.0, 3.0),
}


def validate_layout(layout: LayoutParams) -> None:
    """校验六个参数是否越界；越界抛 `ValueError`。

    API 层应在收到请求时调用，避免把非法排版写进数据库。
    """
    for name, (lo, hi) in BOUNDS.items():
        value = getattr(layout, name)
        if not (lo <= value <= hi):
            raise ValueError(f"排版参数 {name} 必须在 {lo}..{hi} 之间，实际为 {value!r}")


def css_variables(layout: LayoutParams) -> dict[str, str]:
    """把排版参数转成 CSS 变量字典（供模板内联为 `style="..."`）。

    数值项带单位，布尔项输出 **`display` 关键字**（`block`/`none`、`grid`/`none`），
    样式表用 `display: var(--…)` 直接消费——这样面板改开关时预览才真的会变（D59）。
    """
    return {
        CSS_VAR_NAMES["cols_per_row"]: str(layout.cols_per_row),
        CSS_VAR_NAMES["row_gap"]: str(layout.row_gap),
        CSS_VAR_NAMES["font_size"]: f"{layout.font_size}pt",
        CSS_VAR_NAMES["page_margin"]: f"{layout.page_margin}cm",
        CSS_VAR_NAMES["answer_line"]: "block" if layout.answer_line else "none",
        CSS_VAR_NAMES["header_bar"]: "grid" if layout.header_bar else "none",
    }
