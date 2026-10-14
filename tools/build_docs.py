# -*- coding: utf-8 -*-
"""把 docs/*.md 生成为课程提交用的 Word 文档（D36 / D37）。

设计原则：
  - md 是唯一事实来源，本脚本只做"派生"，绝不反向修改 md。
  - 图的位置由 ANCHORS 显式声明；锚点找不到就报错退出，避免静默错位。
  - 表自动编号并加题注（题注取所属小节的标题），图同理由 ANCHORS 给定。
"""
import os
import re
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
FIGS = os.path.join(DOCS, "word", "figures")
OUTDIR = os.path.join(DOCS, "word")

SONG = "宋体"
HEI = "黑体"
MONO = "Consolas"

COVER = {
    "course": "现代软件工程课程设计",
    "project": "小学四则运算练习题生成与批改系统",
    "members": "肖铭皓    胡宇书",
    "sid": "肖：待填　胡：待填",
    "teacher": "罗以宁",
    "date": "2026 年 10 月 14 日",
}


# ============================================================ markdown 解析
def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_md(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    blocks, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", "\n".join(buf)))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            blocks.append(("h", len(m.group(1)), m.group(2).strip()))
            i += 1
            continue
        if ln.strip().startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            header = split_row(ln)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            blocks.append(("table", header, rows))
            continue
        if ln.strip().startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(("quote", " ".join(x for x in buf if x)))
            continue
        if re.match(r"^\s*[-*]\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            blocks.append(("ul", items))
            continue
        if re.match(r"^\s*\d+\.\s+", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            blocks.append(("ol", items))
            continue
        if ln.strip() == "---":
            blocks.append(("hr",))
            i += 1
            continue
        if not ln.strip():
            i += 1
            continue
        buf = [ln]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|>|\s*[-*]\s|\s*\d+\.\s|\||```|---)", lines[i]):
            buf.append(lines[i])
            i += 1
        blocks.append(("p", " ".join(buf)))
    return blocks


# ============================================================ docx 基础件
def set_font(run, ascii_font, ea_font, size, bold=False, color=None, mono=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color
    name = MONO if mono else ascii_font
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), ea_font)


def add_inline(par, text, size=12, ea=SONG, ascii_font="Times New Roman", bold=False):
    text = text.replace("<br>", " ").replace("<br/>", " ")
    for seg in re.split(r"(\*\*.+?\*\*|`[^`]+`)", text):
        if not seg:
            continue
        if seg.startswith("**") and seg.endswith("**") and len(seg) > 4:
            r = par.add_run(seg[2:-2])
            set_font(r, ascii_font, ea, size, bold=True)
        elif seg.startswith("`") and seg.endswith("`") and len(seg) > 2:
            r = par.add_run(seg[1:-1])
            set_font(r, MONO, MONO, size - 1, mono=True)
        else:
            r = par.add_run(seg)
            set_font(r, ascii_font, ea, size, bold=bold)


def field(par, instr, placeholder=""):
    r = par.add_run()

    def fld(t):
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), t)
        return el

    r._element.append(fld("begin"))
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    r._element.append(it)
    r._element.append(fld("separate"))
    t = OxmlElement("w:t")
    t.text = placeholder
    r._element.append(t)
    r._element.append(fld("end"))
    return r


def shape_styles(doc):
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(0)

    sizes = {1: 16, 2: 14, 3: 13, 4: 12}
    for lvl, size in sizes.items():
        st = doc.styles[f"Heading {lvl}"]
        st.font.name = "Times New Roman"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0, 0, 0)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), HEI)
        st.paragraph_format.space_before = Pt(14 if lvl == 1 else 10)
        st.paragraph_format.space_after = Pt(8 if lvl == 1 else 6)
        st.paragraph_format.line_spacing = 1.4

    for name in ("List Bullet", "List Number"):
        st = doc.styles[name]
        st.font.name = "Times New Roman"
        st.font.size = Pt(12)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)
        st.paragraph_format.line_spacing = 1.5


def page_setup(section):
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(2.5), Cm(2.5)
    section.left_margin, section.right_margin = Cm(3.0), Cm(2.5)
    section.header_distance, section.footer_distance = Cm(1.5), Cm(1.5)


def cover(doc, doc_kind):
    for _ in range(3):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(COVER["course"]), "Times New Roman", HEI, 22, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(36)
    set_font(p.add_run(COVER["project"]), "Times New Roman", HEI, 26, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    set_font(p.add_run(doc_kind), "Times New Roman", HEI, 20, bold=True)

    for _ in range(4):
        doc.add_paragraph()

    info = [("课程名称", COVER["course"]), ("项目名称", COVER["project"]),
            ("文档名称", doc_kind), ("项目成员", COVER["members"]),
            ("学　　号", COVER["sid"]), ("指导教师", COVER["teacher"]),
            ("完成日期", COVER["date"])]
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False          # 固定布局，避免列宽被内容撑开
    for k, v in info:
        row = t.add_row().cells
        pk = row[0].paragraphs[0]
        pk.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        set_font(pk.add_run(k + "："), "Times New Roman", HEI, 14, bold=True)
        pv = row[1].paragraphs[0]
        set_font(pv.add_run(v), "Times New Roman", SONG, 14)
    t.columns[0].width = Cm(4.0)
    t.columns[1].width = Cm(11.5)


def toc_page(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run("目　　录"), "Times New Roman", HEI, 18, bold=True)
    doc.add_paragraph()
    p = doc.add_paragraph()
    field(p, 'TOC \\o "1-3" \\h \\z \\u',
          "（右键此处 →「更新域」→「更新整个目录」即可生成目录）")


def content_header_footer(section, doc_title):
    section.header.is_linked_to_previous = False
    section.footer.is_linked_to_previous = False

    hp = section.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(hp.add_run(f"{COVER['course']}　|　{doc_title}"),
             "Times New Roman", SONG, 9)
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "999999")
    pbdr.append(bottom)
    hp._p.get_or_add_pPr().append(pbdr)

    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(fp.add_run("第 "), "Times New Roman", SONG, 10)
    field(fp, "PAGE", "1")
    set_font(fp.add_run(" 页"), "Times New Roman", SONG, 10)


# ============================================================ 正文构建
def build(md_name, out_name, doc_kind, anchors):
    blocks = parse_md(os.path.join(DOCS, md_name))
    doc = Document()
    shape_styles(doc)
    page_setup(doc.sections[0])
    cover(doc, doc_kind)
    doc.add_page_break()
    toc_page(doc)

    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    page_setup(sec)
    content_header_footer(sec, doc_kind)
    doc.sections[0].footer.is_linked_to_previous = False

    pending = list(anchors)
    used = set()
    fig_no = 0
    tbl_no = 0
    head_stack = {}

    def emit_figure(fname, caption):
        nonlocal fig_no
        fig_no += 1
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.keep_with_next = True
        p.add_run().add_picture(os.path.join(FIGS, fname), width=Cm(15))
        c = doc.add_paragraph()
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        c.paragraph_format.space_after = Pt(12)
        set_font(c.add_run(f"图 {fig_no}　{caption}"), "Times New Roman", SONG, 10.5)

    for blk in blocks:
        # 图锚点：命中则在当前标题之前插入
        if blk[0] == "h":
            text = blk[2]
            for a in list(pending):
                if a[0] == text:
                    emit_figure(a[1], a[2])
                    used.add(a[0])
                    pending.remove(a)
            head_stack[blk[1]] = text
            for k in [k for k in head_stack if k > blk[1]]:
                del head_stack[k]
            doc.add_heading(text, level=min(blk[1], 4))
            continue

        if blk[0] == "p":
            p = doc.add_paragraph()
            p.paragraph_format.first_line_indent = Cm(0.74)
            p.paragraph_format.space_after = Pt(4)
            add_inline(p, blk[1])

        elif blk[0] == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.8)
            p.paragraph_format.space_after = Pt(6)
            add_inline(p, blk[1], size=10.5)

        elif blk[0] == "ul":
            for it in blk[1]:
                p = doc.add_paragraph(style="List Bullet")
                p.paragraph_format.line_spacing = 1.4
                add_inline(p, it)

        elif blk[0] == "ol":
            for it in blk[1]:
                p = doc.add_paragraph(style="List Number")
                p.paragraph_format.line_spacing = 1.4
                add_inline(p, it)

        elif blk[0] == "code":
            for line in blk[1].split("\n"):
                p = doc.add_paragraph()
                p.paragraph_format.line_spacing = 1.0
                p.paragraph_format.left_indent = Cm(0.6)
                p.paragraph_format.space_after = Pt(0)
                set_font(p.add_run(line if line else " "), MONO, MONO, 9, mono=True)

        elif blk[0] == "hr":
            continue

        elif blk[0] == "table":
            header, rows = blk[1], blk[2]
            ncol = len(header)
            cur_head = head_stack.get(max(head_stack) if head_stack else 0, "")
            if cur_head and max(head_stack) >= 2:
                tbl_no += 1
                c = doc.add_paragraph()
                c.alignment = WD_ALIGN_PARAGRAPH.CENTER
                c.paragraph_format.space_before = Pt(6)
                c.paragraph_format.space_after = Pt(3)
                c.paragraph_format.keep_with_next = True
                set_font(c.add_run(f"表 {tbl_no}　{cur_head}"),
                         "Times New Roman", SONG, 10.5)
            t = doc.add_table(rows=1, cols=ncol)
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for j, h in enumerate(header):
                cell = t.rows[0].cells[j]
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                cell.paragraphs[0].paragraph_format.line_spacing = 1.15
                add_inline(cell.paragraphs[0], h, size=10.5, ea=HEI, bold=True)
            tr = t.rows[0]._tr
            trPr = tr.get_or_add_trPr()
            th = OxmlElement("w:tblHeader")
            trPr.append(th)
            for r in rows:
                cells = t.add_row().cells
                for j in range(ncol):
                    val = r[j] if j < len(r) else ""
                    cp = cells[j].paragraphs[0]
                    cp.paragraph_format.line_spacing = 1.15
                    add_inline(cp, val, size=10.5)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)

    missing = [a[0] for a in pending]
    if missing:
        raise SystemExit(f"[{md_name}] 找不到图锚点，请检查 md 标题是否变更：{missing}")

    out = os.path.join(OUTDIR, out_name)
    doc.save(out)
    print(f"OK {out_name}  图 {fig_no} 张 / 表 {tbl_no} 个 / {len(blocks)} 个块")
    return out


# ============================================================ 图锚点
REQ_ANCHORS = [
    ("3. 典型使用场景", "fig-usecase.png", "系统用例图"),
    ("4.1 老师端（FR-T）", "fig-function-tree.png", "系统功能结构图"),
    ("4.1 老师端（FR-T）", "fig-teacher-flow.png", "老师出卷业务流程"),
    ("4.1 老师端（FR-T）", "fig-student-flow.png", "学生答题业务流程"),
]

DESIGN_ANCHORS = [
    ("3.2 目录结构", "fig-architecture.png", "系统总体架构"),
    ("4.3 被否决的替代方案", "fig-state.png", "试卷状态机图"),
    ("5.2 表结构", "fig-class.png", "领域模型类图"),
    ("6. 出题引擎设计", "fig-er.png", "数据库 ER 图"),
    ("8. 前端设计", "fig-sequence.png", "学生提交作答与判分时序图"),
    ("11. 性能与并发", "fig-deployment.png", "系统部署图"),
]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    build("01-需求分析.md", "01-需求分析文档.docx", "需求分析文档", REQ_ANCHORS)
    build("02-技术设计.md", "02-技术设计文档.docx", "技术设计文档", DESIGN_ANCHORS)


if __name__ == "__main__":
    main()
