"""卷面 HTML 渲染 —— **冻结入口** `render_sheet(ctx) -> str`。

契约来源：`app/contracts/sheet.py` 文件末尾的五条渲染契约（docs/04-接口契约.md §4 / §9）。

实现要点与契约逐条对应：

| # | 契约 | 落实 |
| --- | --- | --- |
| 1 | 完整 HTML 文档、无外部资源 | `_document()` 产出 `<!DOCTYPE html>`…`</html>`；全文只有内联 `<style>`，不出现 `http://` / `https://` / `src=` / `href=`（NFR-9） |
| 2 | 六个排版参数经 `css_variables()` 注入 | `layout.root_style_attr(layout)` 的每一项都来自 `css_variables()`，变量名取自冻结的 `CSS_VAR_NAMES` |
| 3 | `mode=QUESTION` 不含 answer / steps | `_render_items()` / `_render_row()` 只在 `SheetMode.ANSWER` 分支里读 `item.answer` / `steps` / `difficulty` / `tags`；`include_answers = ctx.mode is SheetMode.ANSWER` |
| 4 | `mode=ANSWER` 答案单独成页 | `.answer-section { break-before: page }`（CSS）+ 答案区独立渲染 |
| 5 | 纯函数 | 无 I/O、无时间戳、无随机、无全局可变状态；遍历 `ctx.items` 顺序固定 |

**为什么不用 Jinja2 模板文件**：唯一收益是外部模板文件（打包与「模板找不到」这类运行期
失败面），而不是安全性；而本项目的 HTML 结构简单、字段少，字符串拼装 + 显式转义更可控，
也让「渲染是纯函数、不读磁盘」这条变得可静态检查。故**有意**采用字符串模板。
"""
from __future__ import annotations

from html import escape

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetItem, SheetMode

from app.layout.answers import (
    HEADER_FIELD_LABELS,
    AnswerEntry,
    AnswerPlan,
    build_answer_plan,
)
from app.layout.styles import (
    layout_fingerprint,
    root_style_attr,
    style_text,
)

__all__ = ["render_sheet", "render_with_layout", "chunk_rows", "SheetRenderError"]


class SheetRenderError(ValueError):
    """渲染输入不合法（例如排版参数越界）。"""


def render_sheet(ctx: SheetContext) -> str:
    """把一份 `SheetContext` 渲染成完整 HTML 文档（**冻结签名**）。"""
    if not isinstance(ctx, SheetContext):
        raise SheetRenderError("render_sheet 只接受 SheetContext")
    return render_with_layout(ctx, ctx.layout)


def render_with_layout(ctx: SheetContext, layout: LayoutParams) -> str:
    """用指定排版参数渲染同一份卷面。

    **它是 `render_sheet` 的实现体，有真实调用者**：`render_sheet(ctx)` 的函数体就是
    `return render_with_layout(ctx, ctx.layout)`（本文件 :49），另有测试
    `tests/render/test_sheet_html.py::test_render_with_layout_matches_render_sheet`
    直接对照两者。签名与 `render_sheet` 一样是**冻结**的（`app/contracts/sheet.py`）。

    **但没有任何东西在用它做「NFR-5 自动微调」**：`app.layout.pagination.reflow()`
    在 `app/**` 里是**零个生产调用点**（它的真实调用只在
    `tests/render/test_pagination.py::TestReflow`）。本函数此前被描述为「reflow 会反复
    调用它」，那是**从未发生的接线**——读起来像「导出时会自动降级字号」，实际不会。

    NFR-5 目前的保证来自**导出前的拒绝判据**，不是来自自动降级：放不下就
    `LAYOUT_DOES_NOT_FIT`（409）并给出可照做的建议，见 `app/layout/fitting.py`。
    自动降级（悄悄把老师的字号改小再交付）是**刻意不做**的——那会让交付物与老师设的
    参数不是同一个东西。谁要接这条线，`reflow()` 仍在 `app/layout/pagination.py`。
    """
    if not isinstance(ctx, SheetContext):
        raise SheetRenderError("render_sheet 只接受 SheetContext")
    if not isinstance(layout, LayoutParams):
        raise SheetRenderError("layout 必须是 LayoutParams")

    items = tuple(ctx.items)
    for item in items:
        if not isinstance(item, SheetItem):
            raise SheetRenderError("items 里出现非 SheetItem 元素")

    # 答案版内容计划：HTML 与 export_docx 共用同一份判据（契约第 6 条）。
    # `mode=QUESTION` 时 plan.enabled 恒为 False，所以这里不可能读到 answer / steps。
    plan = build_answer_plan(ctx)

    body_parts = [
        '<main class="sheet" data-mode="%s"' % escape(ctx.mode.value, quote=True),
        ' data-header-bar="%s"' % ("1" if layout.header_bar else "0"),
        ' data-answer-line="%s">' % ("1" if layout.answer_line else "0"),
        _render_title(ctx),
        _render_meta(ctx),
        _render_header_bar(ctx),
        _render_items(items, layout, plan.enabled),
    ]
    if plan.enabled:
        body_parts.append(_render_answer_section(ctx, plan))
    body_parts.append("</main>")

    return _document(ctx, layout, "".join(body_parts))


# ---------------------------------------------------------------------------
# 文档骨架
# ---------------------------------------------------------------------------


def _document(ctx: SheetContext, layout: LayoutParams, body: str) -> str:
    """完整 HTML 文档。样式全部内联；不引用任何外部资源。"""
    title = escape(ctx.title or "练习卷")
    mode_label = "答案版" if ctx.mode is SheetMode.ANSWER else "题目版"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="mathgen render_sheet">\n'
        f'<meta name="layout-fingerprint" content="{escape(layout_fingerprint(layout), quote=True)}">\n'
        f"<title>{title}（{mode_label}）</title>\n"
        "<style>\n"
        f"{style_text(layout)}\n"
        "</style>\n"
        "</head>\n"
        f'<body class="mode-{escape(ctx.mode.value, quote=True)}">\n'
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


# ---------------------------------------------------------------------------
# 卷头
# ---------------------------------------------------------------------------


def _render_title(ctx: SheetContext) -> str:
    title = escape(ctx.title or "")
    if not title:
        return ""
    return f'<h1 class="sheet-title">{title}</h1>\n'


def _render_meta(ctx: SheetContext) -> str:
    """卷号（发布后才有）与卷头补充说明。"""
    parts: list[str] = []
    if ctx.code:
        parts.append(f'<span class="sheet-code">卷号 {escape(ctx.code)}</span>')
    if ctx.header_note:
        parts.append(f'<span class="sheet-note">{escape(ctx.header_note)}</span>')
    if not parts:
        return ""
    return '<div class="sheet-meta">' + "".join(parts) + "</div>\n"


#: 卷头栏字段（D41：学生身份 = **姓名 + 学号** 两者必填；`student_no NOT NULL`）。
#: 卷面必须与 D41 一致，所以这里是四项而不是三项。
#: 定义在 `app.layout.answers`，**HTML 与 DOCX 共用同一份**，两边不可能漂移。
HEADER_FIELDS = HEADER_FIELD_LABELS


def _render_header_bar(ctx: SheetContext) -> str:
    """班级 / 姓名 / 学号 / 得分 填写栏（D41 + D70）。

    **始终输出**该元素，显隐完全交给 CSS 变量 `--header-bar-display`
    （`.header-bar { display: var(--header-bar-display, grid) }`）。
    原因与 `_render_blank()` 相同：让老师端面板「只覆盖 CSS 变量」也能实时生效（D59）。

    D70 视觉轮的两点改动：

    * **补上学号**——修掉与 D41 的不一致（学生重名时需要学号区分）；
    * **去掉厚重横带**——不再用上下外框，改成「标签 + 一段下划线」，
      下划线只出现在**书写位**上（`.hb-line`），不再给整块加边框。
    """
    fields = "".join(
        f'<span class="hb-field"><span class="hb-label">{label}：</span>'
        f'<span class="hb-line"></span></span>'
        for label in HEADER_FIELDS
    )
    return f'<div class="header-bar">{fields}</div>\n'


# ---------------------------------------------------------------------------
# 题目区
# ---------------------------------------------------------------------------


def _chunk(items: tuple[SheetItem, ...], size: int) -> list[tuple[SheetItem, ...]]:
    """按每行题数切行。

    `size` 来自 `layout.cols_per_row`；这里**不做**任何重排或排序——
    `seq` 由 API 层负责前移（契约 §6 删题语义）。
    """
    if size <= 0:
        raise SheetRenderError("cols_per_row 必须为正整数")
    return [items[i:i + size] for i in range(0, len(items), size)]


def _render_items(items: tuple[SheetItem, ...], layout: LayoutParams, include_answers: bool) -> str:
    if not items:
        return '<p class="q-empty">（本卷暂无题目）</p>\n'
    rows = _chunk(items, layout.cols_per_row)
    return "".join(
        _render_row(row, layout, index) for index, row in enumerate(rows)
    )


def _render_row(row: tuple[SheetItem, ...], layout: LayoutParams, row_index: int = 0) -> str:
    """一行题：**块级外层** `.q-row`（负责「不跨页」）+ 内层 grid（负责每行题数）。

    见 `app.layout.styles` 里对这两层的说明——grid 容器上的 `break-inside: avoid`
    在本机 Chromium 上不生效，实测证据写在那边。

    `data-row-index` 是给**分页标定**用的锚点（`app.export.measure_page_box` 会在
    某一行前插入 `break-before: page` 再比较页数来定位真实分页线）。
    它只是行序号，不含题目内容，不影响题目版的安全边界。
    """
    cells = "".join(_render_item(item, layout) for item in row)
    return (
        f'<div class="q-row" data-row-index="{int(row_index)}">'
        f'<div class="q-row-grid">{cells}</div></div>\n'
    )


def _render_item(item: SheetItem, layout: LayoutParams) -> str:
    """单个题块。

    这里**不读** `answer` / `steps` / `difficulty` / `tags`——答案只走
    `_render_answer_section()`，而那条分支只在 `mode=ANSWER` 时才会被调用。
    把「答案字段只出现在一个函数里」当结构约束，比事后过滤字符串更可靠
    （契约第 3 条 / 契约 §9 安全边界）。
    """
    display = escape(item.display)
    parts = [
        f'<div class="q-item" data-seq="{int(item.seq)}"',
        f' data-answer-line="{"1" if layout.answer_line else "0"}">',
        f'<span class="q-seq">{int(item.seq)}.</span>',
        f'<span class="q-display">{display}</span>',
        _render_blank(layout),
        "</div>",
    ]
    return "".join(parts)


def _render_blank(layout: LayoutParams) -> str:
    """答题空位 / 横线。

    **始终输出**该元素，可见性完全交给 CSS 变量 `--answer-line-display`
    （`.q-blank { display: var(--answer-line-display, block) }`）。

    为什么不能像第一版那样在 `answer_line=False` 时干脆不输出：
    那样一来「老师端面板只覆盖 CSS 变量」的实时预览就永远看不到变化——
    面板写的变量没有任何规则消费，产品就是在骗人（D59 那起 bug 的根因）。
    现在服务端渲染与面板覆盖走**同一条通路**。

    `data-answer-line` 仍保留在 `.q-item` 上，但只作调试/锚点，**不再承担隐藏职责**。
    """
    return '<span class="q-blank" aria-hidden="true"></span>'


# ---------------------------------------------------------------------------
# 答案区（D20）
# ---------------------------------------------------------------------------


def _render_answer_section(ctx: SheetContext, plan: AnswerPlan) -> str:
    """答案区：最终答案 + 难度 + 考点标签 + 脱式逐步过程。

    内容与显隐**全部由 `app.layout.answers.build_answer_plan(ctx)` 决定**——
    HTML 与 `export_docx` 消费同一份 plan，契约第 6 条的结构一致性因此是结构性的。

    `answer_layout.separate_page` 决定是否单独成页：
    * True（D20 默认）→ `.answer-section { break-before: page }` 生效；
    * False → 加 `data-break-before="none"`，样式里由该属性关掉分页
      （与开关走同一条 CSS 通路，前端覆盖变量/属性都能实时生效）。
    """
    blocks = "".join(_render_answer_item(entry) for entry in plan.entries)
    heading = "答案与解析" if plan.entries else "答案与解析（本卷无题目）"
    break_attr = "page" if plan.separate_page else "none"
    return (
        f'<section class="answer-section" data-break-before="{break_attr}">\n'
        f'<h2 class="answer-heading">{escape(heading)}</h2>\n'
        f"{blocks}"
        "</section>\n"
    )


def _render_answer_item(entry: AnswerEntry) -> str:
    parts = [
        f'<div class="a-item" data-seq="{int(entry.seq)}">',
        '<div class="a-head">',
        f'<span class="a-seq">{int(entry.seq)}.</span>',
        f'<span class="a-answer">{escape(entry.answer)}</span>',
        "</div>",
        _render_answer_meta(entry),
        _render_answer_steps(entry),
        "</div>",
    ]
    return "".join(parts)


def _render_answer_meta(entry: AnswerEntry) -> str:
    """难度等级 + 知识点标签（D20 的第 ②③ 项）。

    关掉的项在 `build_answer_plan` 里已被裁掉（`difficulty=0` / `tags=()`），
    这里不需要再判断一次开关。
    """
    parts: list[str] = []
    if entry.difficulty:
        parts.append(f'<span class="a-difficulty">难度 {int(entry.difficulty)}/5</span>')
    for tag in entry.tags:
        parts.append(f'<span class="a-tag">{escape(tag)}</span>')
    if not parts:
        return ""
    return '<div class="a-meta">' + "".join(parts) + "</div>"


def _render_answer_steps(entry: AnswerEntry) -> str:
    """脱式 / 混合运算的逐步递等过程（D20 的第 ④ 项）。"""
    steps = tuple(entry.steps)
    if not steps:
        return ""
    lis = "".join(f"<li>{escape(step)}</li>" for step in steps)
    return f'<ol class="a-steps">{lis}</ol>'


# ---------------------------------------------------------------------------
# 供 external 调用方使用的稳定小工具
# ---------------------------------------------------------------------------


def chunk_rows(items: tuple[SheetItem, ...], cols_per_row: int) -> list[tuple[SheetItem, ...]]:
    """公开的行切分（测试与前端预览共用同一逻辑）。"""
    return _chunk(items, cols_per_row)
