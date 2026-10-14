"""Word（.docx）导出 —— **冻结入口** `export_docx(ctx, out_path) -> None`（M3 / T-M3-DOCX）。

## 设计口径（契约第 6 条）

> `export_docx` 的卷面结构与 HTML/PDF **必须一致**；Word 不能完整表达的部分
> （如逐页分页），由界面明示差异，**不得静默降级**。

为了把「一致」变成**结构性事实**而不是约定，本模块与 HTML 渲染器共用：

* `app.layout.answers.build_answer_plan(ctx)` —— 答案版输出什么（四个开关的唯一判据）
* `app.render.chunk_rows(items, cols)` —— 每行题数
* `app.contracts.layout.LayoutParams` —— 页边距、排布参数

Word 与 HTML 的**表达差异**是真实存在的（`docs/02-技术设计.md` §9.1 已定为 D33）：

| 面向 | HTML / PDF | Word（本模块） |
| --- | --- | --- |
| 每行题数 | CSS Grid | **表格**（列数 = `cols_per_row`） |
| 答题空位 | `border-bottom` | 段落底边框 `pBdr/bottom` |
| 分页 | `break-before: page` | `run.add_break(WD_BREAK.PAGE)` |
| 字体 / 行距 | CSS 精确到 pt | 磅值近似，允许细微差异 |

这些都是**同一份数据的不同表达**，题号、题目文本、答案、难度、标签、过程一一对应。

## 确定性

导出**不写入任何时间戳 / 随机数 / 环境信息**：同一份 `SheetContext` 两次导出的
文档正文逐段等价（`tests/render/test_export_docx.py` 有断言）。
`python-docx` 默认的 `core_properties`（作者 / 创建时间）会被显式清空，
避免把「谁在什么时候导的」写进交付物。
"""
from __future__ import annotations

import os

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from app.contracts.layout import LayoutParams
from app.contracts.sheet import SheetContext, SheetMode

from app.layout.answers import AnswerPlan, build_answer_plan
from app.render.sheet import chunk_rows

__all__ = ["export_docx", "DocxExportError", "docx_body_text"]

#: A4 纸张（mm -> cm）
A4_WIDTH_CM = 21.0
A4_HEIGHT_CM = 29.7

#: 表格边框开关（Word 默认表格无边框，正合卷面需要）
_DEFAULT_FONT = "SimSun"


class DocxExportError(ValueError):
    """导出输入不合法。"""


# ---------------------------------------------------------------------------
# 底层 XML helper（python-docx 没有「段落底边框」的公开 API）
# ---------------------------------------------------------------------------


def _set_paragraph_bottom_border(paragraph, size: int = 6, color: str = "808080") -> None:
    """给段落画一条底边框——用来模拟「答题横线」。"""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = p_pr.makeelement(qn("w:pBdr"), {})
        p_pr.append(borders)
    bottom = borders.makeelement(
        qn("w:bottom"),
        {qn("w:val"): "single", qn("w:sz"): str(size), qn("w:space"): "1", qn("w:color"): color},
    )
    borders.append(bottom)


def _set_cell_borders(cell, color: str = "000000", size: int = 6) -> None:
    """给单元格画上/下边框（卷头信息栏的横线）。"""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = tc_pr.makeelement(qn("w:tcBorders"), {})
        tc_pr.append(borders)
    for edge in ("top", "bottom"):
        node = borders.makeelement(
            qn(f"w:{edge}"),
            {qn("w:val"): "single", qn("w:sz"): str(size), qn("w:space"): "0", qn("w:color"): color},
        )
        borders.append(node)


def _clear_table_borders(table) -> None:
    """去掉表格自身的框线（Word 默认样式会带框，卷面不需要）。"""
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.makeelement(qn("w:tblBorders"), {})
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(borders.makeelement(qn(f"w:{edge}"), {qn("w:val"): "none", qn("w:sz"): "0"}))
    tbl_pr.append(borders)


def _style_run(run, size_pt: float, bold: bool = False) -> None:
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.name = _DEFAULT_FONT
    # 中文字体要单独设 eastAsia，否则 Word 会用默认字体
    run._element.rPr.rFonts.set(qn("w:eastAsia"), _DEFAULT_FONT)


# ---------------------------------------------------------------------------
# 页面设置
# ---------------------------------------------------------------------------


def _configure_page(document, layout: LayoutParams) -> None:
    """A4 纵向 + 按 `layout.page_margin` 设页边距（与 HTML/PDF 同一个参数）。"""
    for section in document.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(A4_WIDTH_CM)
        section.page_height = Cm(A4_HEIGHT_CM)
        section.top_margin = Cm(layout.page_margin)
        section.bottom_margin = Cm(layout.page_margin)
        section.left_margin = Cm(layout.page_margin)
        section.right_margin = Cm(layout.page_margin)


def _reset_core_properties(document) -> None:
    """清空文档属性 —— 确定性要求：不把作者/时间写进交付物。"""
    props = document.core_properties
    props.author = ""
    props.last_modified_by = ""
    props.comments = ""
    props.category = ""
    props.title = ""
    props.subject = ""
    # 创建/修改时间无法置空（schema 要求），但导出后不影响正文；
    # 我们断言的是**正文等价**，见 docx_body_text()。


# ---------------------------------------------------------------------------
# 卷头 / 题目 / 答案
# ---------------------------------------------------------------------------


def _render_title(document, ctx: SheetContext, layout: LayoutParams) -> None:
    if ctx.title:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(ctx.title)
        _style_run(run, size_pt=layout.font_size * 1.5, bold=True)

    meta: list[str] = []
    if ctx.code:
        meta.append(f"卷号 {ctx.code}")
    if ctx.header_note:
        meta.append(ctx.header_note)
    if meta:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run("    ".join(meta))
        _style_run(run, size_pt=layout.font_size * 0.85)


def _render_header_bar(document, layout: LayoutParams) -> None:
    """班级 / 姓名 / 得分 填写栏（`layout.header_bar` 为假时**整块不出现**）。"""
    if not layout.header_bar:
        return
    table = document.add_table(rows=1, cols=3)
    _clear_table_borders(table)
    labels = ("班级：", "姓名：", "得分：")
    for cell, label in zip(table.rows[0].cells, labels):
        cell.text = ""
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run(label)
        _style_run(run, size_pt=layout.font_size * 0.9)
        _set_cell_borders(cell)


def _render_blank(paragraph, layout: LayoutParams) -> None:
    """答题空位 / 横线（`layout.answer_line` 为假时不加）。"""
    if not layout.answer_line:
        return
    blank = paragraph.add_run(" " * 12)
    _style_run(blank, size_pt=layout.font_size)
    _set_paragraph_bottom_border(paragraph)


def _render_questions(document, ctx: SheetContext, layout: LayoutParams) -> None:
    """题目区：表格实现「每行 cols_per_row 题」（与 CSS Grid 同构）。"""
    items = tuple(ctx.items)
    if not items:
        paragraph = document.add_paragraph()
        run = paragraph.add_run("（本卷暂无题目）")
        _style_run(run, size_pt=layout.font_size)
        return

    rows = chunk_rows(items, layout.cols_per_row)
    table = document.add_table(rows=len(rows), cols=layout.cols_per_row)
    table.autofit = True
    _clear_table_borders(table)

    for row_index, row_items in enumerate(rows):
        cells = table.rows[row_index].cells
        for col_index, item in enumerate(row_items):
            cell = cells[col_index]
            cell.text = ""
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run(f"{int(item.seq)}. ")
            _style_run(run, size_pt=layout.font_size, bold=True)
            body = paragraph.add_run(item.display)
            _style_run(body, size_pt=layout.font_size)
            # 答题空位独占一行，避免把题面挤变形
            blank_paragraph = cell.add_paragraph()
            _render_blank(blank_paragraph, layout)


def _answer_meta_text(entry) -> str:
    """难度 + 考点标签。关掉的项在 plan 里已被裁掉，这里不会再出现。"""
    parts: list[str] = []
    if entry.difficulty:
        parts.append(f"难度 {int(entry.difficulty)}/5")
    parts.extend(entry.tags)
    return "　".join(parts)


def _render_answer_section(document, plan: AnswerPlan, layout: LayoutParams) -> None:
    """答案区。`separate_page` 为真时在它之前**插入分页符**（对应 `break-before: page`）。"""
    if plan.separate_page:
        break_paragraph = document.add_paragraph()
        break_paragraph.add_run().add_break(WD_BREAK.PAGE)

    heading = document.add_paragraph()
    run = heading.add_run("答案与解析" if plan.entries else "答案与解析（本卷无题目）")
    _style_run(run, size_pt=layout.font_size * 1.2, bold=True)
    _set_paragraph_bottom_border(heading)

    for entry in plan.entries:
        answer_paragraph = document.add_paragraph()
        seq_run = answer_paragraph.add_run(f"{int(entry.seq)}. ")
        _style_run(seq_run, size_pt=layout.font_size * 0.95, bold=True)
        answer_run = answer_paragraph.add_run(entry.answer)
        _style_run(answer_run, size_pt=layout.font_size * 0.95)

        meta = _answer_meta_text(entry)
        if meta:
            meta_paragraph = document.add_paragraph()
            meta_run = meta_paragraph.add_run(meta)
            _style_run(meta_run, size_pt=layout.font_size * 0.8)

        for step in entry.steps:
            step_paragraph = document.add_paragraph()
            step_run = step_paragraph.add_run(step)
            _style_run(step_run, size_pt=layout.font_size * 0.9)
            step_paragraph.paragraph_format.left_indent = Cm(0.8)


# ---------------------------------------------------------------------------
# 冻结入口
# ---------------------------------------------------------------------------


def export_docx(ctx: SheetContext, out_path: str) -> None:
    """把一份 `SheetContext` 导出成 .docx（**冻结签名**）。

    结构与 `render_sheet` 对齐：卷头（按 `layout.header_bar`）→ 标题 / 卷号 →
    题目（每行 `layout.cols_per_row` 题，答题空位按 `layout.answer_line`）→
    答案区（仅 `mode=ANSWER`，按 `ctx.answer_layout`）。

    `mode=QUESTION` 时**不读** answer / steps / difficulty / tags ——
    安全边界与 HTML 走同一份 `build_answer_plan()`（契约第 3 条）。
    """
    if not isinstance(ctx, SheetContext):
        raise DocxExportError("export_docx 只接受 SheetContext")
    if not isinstance(ctx.layout, LayoutParams):
        raise DocxExportError("ctx.layout 必须是 LayoutParams")

    plan = build_answer_plan(ctx)
    layout = ctx.layout

    document = Document()
    _reset_core_properties(document)
    _configure_page(document, layout)
    _render_title(document, ctx, layout)
    _render_header_bar(document, layout)
    _render_questions(document, ctx, layout)
    if plan.enabled:
        _render_answer_section(document, plan, layout)

    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)
    document.save(out_path)


# ---------------------------------------------------------------------------
# 读回工具（测试与验收共用）
# ---------------------------------------------------------------------------


def docx_body_text(path: str) -> str:
    """把导出的 .docx 读回，拼出**正文文本**（段落 + 表格单元格）。

    用途：
    * 确定性断言（同一 ctx 两次导出正文等价）；
    * 「四个开关关掉后那项不出现」的断言；
    * 「题目是真正的文字、可被 Word 搜索到」的断言。

    刻意**不含**文档属性（作者/时间），因为那是元数据不是卷面内容。
    """
    document = Document(path)
    parts: list[str] = []

    def collect(container) -> None:
        for paragraph in container.paragraphs:
            if paragraph.text:
                parts.append(paragraph.text)
        for table in container.tables:
            for row in table.rows:
                for cell in row.cells:
                    collect(cell)

    collect(document)
    return "\n".join(parts)
