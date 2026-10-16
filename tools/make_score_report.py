# -*- coding: utf-8 -*-
"""生成「班级成绩报告」Word 文档（**演示数据，明确标注**）。

为什么要单独写一个脚本，而不是手搓一份 Word：
  1. 报告里的题面、标准答案**取自真实卷库**（`backend/data/mathgen.db`，**只读**）；
  2. 报告里的分数**由本系统真实的判分器** `app.grading.grade()` 判出来，
     得分口径**复用契约里的唯一实现** `app.contracts.api.score_of()`（D80）——
     不是我在脚本里另算一套；
  3. 全部数据由固定随机种子确定性生成，任何人都能照命令重跑出逐字节同构的报告。

**它绝不写真实数据库**：只以 `mode=ro` 打开，也不插入任何 attempt 记录。
演示学生与真实学生（张三 / 小明）在库里互不相干。

用法：
    python tools/make_score_report.py                      # 默认卷 2610-0049、8 名学生
    python tools/make_score_report.py --code 2610-0049 --students 8 --seed 20261016
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from docx import Document  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402

from app.contracts.api import score_of  # noqa: E402
from app.engine.types import Question, QuestionType  # noqa: E402
from app.grading import grade  # noqa: E402

DB_PATH = os.path.join(ROOT, "backend", "data", "mathgen.db")
DEFAULT_OUT = os.path.join(ROOT, "docs", "word", "班级成绩报告-演示-2610-0049.docx")

SONG = "宋体"
HEI = "黑体"
RED = RGBColor(0xC0, 0x39, 0x2B)
GREY = RGBColor(0x66, 0x66, 0x66)

QTYPE_LABEL = {
    QuestionType.ORAL: "口算（横式）",
    QuestionType.STEPWISE: "脱式（递等式）",
    QuestionType.FILL: "填空（求未知数）",
}


# ------------------------------------------------------------------ 读真实卷库
def load_paper(code: str) -> dict:
    """只读打开真实库，取一套卷子与它的全部题目。"""
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"找不到数据库：{DB_PATH}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT id, code, title, status, config_json, created_at, published_at "
            "FROM paper WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"卷库里没有卷号 {code}")
        items = list(con.execute(
            "SELECT seq, payload FROM paper_item WHERE paper_id = ? ORDER BY seq",
            (row["id"],),
        ))
    finally:
        con.close()
    if not items:
        raise SystemExit(f"卷 {code} 没有任何题目")
    questions = []
    for it in items:
        payload = json.loads(it["payload"])
        questions.append((it["seq"], Question(
            expr=payload["expr"],
            display=payload["display"],
            answer=str(payload["answer"]),
            steps=list(payload.get("steps") or []),
            difficulty=int(payload.get("difficulty") or 1),
            tags=list(payload.get("tags") or []),
            signature=payload.get("signature", ""),
        )))
    cfg = json.loads(row["config_json"]) if row["config_json"] else {}
    return {
        "id": row["id"], "code": row["code"], "title": row["title"],
        "status": row["status"], "config": cfg,
        "created_at": row["created_at"], "published_at": row["published_at"],
        "questions": questions,
    }


# ------------------------------------------------------------------ 造演示作答
def wrong_answer(question: Question, rng: random.Random) -> str | None:
    """造一个**会被真实判分器判错**的答案；小概率返回 None 表示「未作答」。"""
    if rng.random() < 0.08:
        return None                      # 未作答
    try:
        correct = int(question.answer)
    except ValueError:
        return str(int(question.answer) + 1) if question.answer.isdigit() else None
    for _ in range(40):
        delta = rng.choice([-10, -5, -3, -2, -1, 1, 2, 3, 5, 10])
        if delta == 0:
            continue
        candidate = str(max(0, correct + delta))
        if not grade(question, candidate).is_correct:
            return candidate
    return str(correct + 1)              # 兜底：几乎不可能走到


def build_cohort(paper: dict, students: int, seed: int) -> list[dict]:
    """确定性生成一个班的演示作答，并用**真实判分器**判分。"""
    questions = paper["questions"]
    total = len(questions)
    cohort = []
    for index in range(students):
        rng = random.Random(seed + 1013 * (index + 1))
        # 能力档：第 1 名约 95% 正确率，最后一名约 50%，中间线性过渡
        ratio = 0.95 - (0.45 * index / max(1, students - 1))
        answers: list[str | None] = []
        for _seq, question in questions:
            if rng.random() < ratio:
                answers.append(question.answer)
            else:
                answers.append(wrong_answer(question, rng))
        # 判分只用真判分器；顺带断言「我打算让他答对的确实判对、打算错的确实判错」
        items = []
        for (seq, question), planned in zip(questions, answers):
            wanted_correct = planned == question.answer
            verdict = grade(question, "" if planned is None else planned)
            if verdict.is_correct != wanted_correct:
                raise SystemExit(
                    f"判分与预期不符：seq={seq} planned={planned!r} "
                    f"answer={question.answer!r} -> {verdict.is_correct}")
            items.append({
                "seq": seq, "display": question.display, "answer": question.answer,
                "student_answer": None if planned is None else verdict.normalized_input,
                "is_correct": verdict.is_correct,
            })
        correct = sum(1 for item in items if item["is_correct"])
        duration_ms = rng.randint(180_000, 720_000)
        cohort.append({
            "student_no": f"{index + 1:02d}",
            "name": f"演示学生{index + 1:02d}",
            "items": items,
            "total": total,
            "correct": correct,
            "score": score_of(total, correct),   # 唯一得分口径（D80）
            "duration_ms": duration_ms,
            "submitted_at": datetime(2026, 10, 16, 9, 0, 0, tzinfo=timezone.utc)
                            + timedelta(seconds=rng.randint(0, 1500)),
        })
    return cohort


def self_check(paper: dict, cohort: list[dict]) -> list[dict]:
    """从**最底层的数据**重算一遍报告里的每个数字，对不上就报错。

    理由与 M5 的成绩对账同一条：报告里最容易出的错不是崩溃，而是「列表里的分数」与
    「明细里的对错」对不上——两个数字都会正常显示，谁也不会报错。所以这里不信任
    `build_cohort` 已经算好的字段，改从 `items[*]["is_correct"]` 重新数。
    """
    questions = paper["questions"]
    problems: list[str] = []

    for student in cohort:
        hits = sum(1 for item in student["items"] if item["is_correct"])
        if hits != student["correct"]:
            problems.append(f"{student['name']}：明细数出 {hits} 题对，报告写 {student['correct']}")
        if len(student["items"]) != student["total"]:
            problems.append(f"{student['name']}：明细 {len(student['items'])} 条，"
                            f"题量写 {student['total']}")
        if score_of(student["total"], hits) != student["score"]:
            problems.append(f"{student['name']}：score_of({student['total']}, {hits}) "
                            f"= {score_of(student['total'], hits)}，报告写 {student['score']}")
        if student["total"] != len(questions):
            problems.append(f"{student['name']}：题量与卷子不符")

    if len(cohort) != len({student["student_no"] for student in cohort}):
        problems.append("学号有重复")

    # 逐题正确率：每个题号上「答对的人数」之和，必须等于全班答对题数之和
    hits_sum = sum(1 for student in cohort for item in student["items"] if item["is_correct"])
    per_question_sum = sum(
        sum(1 for student in cohort if student["items"][position]["is_correct"])
        for position in range(len(questions))
    )
    if hits_sum != per_question_sum:
        problems.append(f"逐题正确率合计 {per_question_sum} ≠ 全班答对合计 {hits_sum}")

    if problems:
        raise SystemExit("报告自检不通过：\n  - " + "\n  - ".join(problems))
    return cohort


# ------------------------------------------------------------------ 文档工具
def set_font(run, name: str = SONG, size: float = 10.5, bold: bool = False,
             color: RGBColor | None = None) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def shade(cell, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def write_cell(cell, text: str, *, bold: bool = False, size: float = 10,
               align=WD_ALIGN_PARAGRAPH.CENTER, name: str = SONG,
               color: RGBColor | None = None) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    paragraph.alignment = align
    set_font(paragraph.add_run(text), name=name, size=size, bold=bold, color=color)


TWIPS_PER_CM = 567


def lock_widths(table, widths_cm: list[float]) -> None:
    """把列宽**钉死**。

    只设 `cell.width` 是不够的：python-docx 不写 `w:tblW` / `w:tblGrid`，Word 与
    LibreOffice 都会退化成「按内容自适应」，于是列多的时候整张表会**顶出右页边距**
    （实测：`班级概况` 表第 6 列被切到纸外）。必须同时写死 `tblLayout=fixed`、
    `tblW` 与 `tblGrid` 的每列宽度。
    """
    tbl = table._tbl
    tblPr = tbl.tblPr
    table.autofit = False
    for tag in ("w:tblLayout", "w:tblW"):
        for old in tblPr.findall(qn(tag)):
            tblPr.remove(old)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    total = int(round(sum(widths_cm) * TWIPS_PER_CM))
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:w"), str(total))
    tbl_w.set(qn("w:type"), "dxa")
    tblPr.append(tbl_w)

    for old in tbl.findall(qn("w:tblGrid")):
        tbl.remove(old)
    grid = OxmlElement("w:tblGrid")
    for width in widths_cm:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(int(round(width * TWIPS_PER_CM))))
        grid.append(grid_col)
    tbl.insert(list(tbl).index(tblPr) + 1, grid)

    for row in table.rows:
        for i, width in enumerate(widths_cm):
            row.cells[i].width = Cm(width)


def repeat_header(table) -> None:
    """让表头在跨页时自动重复。

    逐题正确率有 24 行，必然跨页；不写 `w:tblHeader` 的话第 2 页会从第 10 题开始、
    一列标题都没有，读的人得回头翻上一页才知道哪列是什么。
    """
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    flag = OxmlElement("w:tblHeader")
    flag.set(qn("w:val"), "true")
    tr_pr.append(flag)


def keep_row_together(row) -> None:
    """一行不被拆到两页（`w:cantSplit`）。"""
    tr_pr = row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))


def make_table(doc, header: list[str], widths: list[float]):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, title in enumerate(header):
        write_cell(table.rows[0].cells[i], title, bold=True, size=10, name=HEI)
        shade(table.rows[0].cells[i], "EEEEEE")
    repeat_header(table)
    keep_row_together(table.rows[0])
    return table


def finish_row(table, values: list[str], *,
               colors: dict[int, RGBColor] | None = None) -> None:
    row = table.add_row()
    keep_row_together(row)
    for i, value in enumerate(values):
        write_cell(row.cells[i], value, size=10,
                   color=(colors or {}).get(i))


def format_duration(ms: int) -> str:
    """与成绩导出（task-41）同一口径：人能读的形式，不写裸毫秒。"""
    total_seconds = round(ms / 1000)
    minutes, seconds = divmod(total_seconds, 60)
    if minutes and seconds:
        return f"{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分"
    return f"{seconds}秒"


# ------------------------------------------------------------------ 出文档
def build(paper: dict, cohort: list[dict], seed: int, out_path: str) -> None:
    doc = Document()

    # 页面：必须显式写 A4。python-docx 的默认模板是 **Letter（12240×15840 twips）**，
    # 不写的话导出的对标纸就不是 A4（实测 check_office 报 page_twips=12240×15840）。
    normal = doc.styles["Normal"]
    normal.font.name = SONG
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)

    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.4)
    section.right_margin = Cm(2.4)

    # 页眉：每一页都带演示标记
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(header.add_run("演示数据 · 非真实作答记录"), name=HEI, size=9, color=RED)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer.add_run("本报告由 tools/make_score_report.py 生成 · 种子 "
                            f"{seed} · 第 "), name=SONG, size=8, color=GREY)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    set_font(footer.add_run(" 页"), name=SONG, size=8, color=GREY)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(title.add_run("班级成绩报告"), name=HEI, size=22, bold=True)

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(subtitle.add_run(f"{paper['title']}　·　卷号 {paper['code']}"),
             name=HEI, size=12, color=GREY)

    # ---- 演示声明
    # ---- 演示声明（Word 不解释 markdown，所以强调只能用真正的粗体 run，
    #      不能写 `**…**` —— 那只会在纸面上原样印出星号）
    notice = doc.add_paragraph()
    notice.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(notice.add_run("演示声明"), name=HEI, size=12, bold=True, color=RED)
    body = doc.add_paragraph()
    body.paragraph_format.space_after = Pt(4)
    for text, bold in [
        ("本报告中的全部作答记录、分数、用时", False),
        ("均为演示数据", True),
        ("：题目与标准答案取自卷库中真实存在的这套卷子，分数由本系统真实的判分器"
         "（app.grading.grade）判出、得分按契约里的唯一口径 score_of 计算，"
         "但“谁答了什么”是脚本按固定随机种子生成的，", False),
        ("不来自任何真实学生的作答", True),
        ("，也与数据库中已存的真实成绩无关。该脚本对真实数据库", False),
        ("只读", True),
        ("，未写入任何记录。", False),
    ]:
        set_font(body.add_run(text), name=SONG, size=10.5, bold=bold, color=RED)

    # ---- 一、试卷信息
    heading = doc.add_paragraph()
    heading.paragraph_format.space_before = Pt(14)
    set_font(heading.add_run("一、试卷信息"), name=HEI, size=14, bold=True)

    cfg = paper["config"]
    tags: list[str] = []
    for _seq, question in paper["questions"]:
        for tag in question.tags:
            if tag not in tags:
                tags.append(tag)
    info = make_table(doc, ["卷号", "卷名", "题量", "题型", "运算", "知识点"], [2.4, 3.6, 1.4, 2.4, 1.8, 4.6])
    finish_row(info, [
        paper["code"], paper["title"], str(len(paper["questions"])),
        QTYPE_LABEL.get(QuestionType(cfg.get("question_type", "oral")), str(cfg.get("question_type"))),
        "、".join(cfg.get("ops") or []), "、".join(tags) or "—",
    ])
    lock_widths(info, [2.4, 3.6, 1.4, 2.4, 1.8, 4.6])

    # ---- 二、班级概况
    heading = doc.add_paragraph()
    heading.paragraph_format.space_before = Pt(12)
    set_font(heading.add_run("二、班级概况"), name=HEI, size=14, bold=True)
    count = len(cohort)
    scores = [s["score"] for s in cohort]
    durations = [s["duration_ms"] for s in cohort]
    overall = make_table(doc, ["参考人数", "平均分", "最高分", "最低分", "满分人数", "平均用时"],
                         [2.6, 2.6, 2.6, 2.6, 2.6, 2.8])
    finish_row(overall, [
        f"{count} 人",
        f"{sum(scores) / count:.1f}",
        str(max(scores)),
        str(min(scores)),
        f"{sum(1 for s in scores if s == 100)} 人",
        format_duration(sum(durations) // count),
    ])
    lock_widths(overall, [2.6, 2.6, 2.6, 2.6, 2.6, 2.8])

    # ---- 三、成绩明细
    heading = doc.add_paragraph()
    heading.paragraph_format.space_before = Pt(12)
    set_font(heading.add_run("三、成绩明细"), name=HEI, size=14, bold=True)
    detail = make_table(doc, ["学号", "姓名", "得分", "答对 / 题量", "正确率", "用时", "提交时间"],
                        [1.6, 2.8, 1.6, 2.8, 1.8, 2.2, 3.0])
    for student in cohort:
        rate = student["correct"] / student["total"] * 100
        color = RED if student["score"] < 60 else None
        row_index = len(detail.rows)
        finish_row(detail, [
            student["student_no"], student["name"], str(student["score"]),
            f"{student['correct']} / {student['total']}", f"{rate:.1f}%",
            format_duration(student["duration_ms"]),
            student["submitted_at"].astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S"),
        ], colors={2: color})
        if row_index % 2 == 0:
            for cell in detail.rows[row_index].cells:
                shade(cell, "FAFAFA")
    lock_widths(detail, [1.6, 2.8, 1.6, 2.8, 1.8, 2.2, 3.0])

    # ---- 四、逐题正确率
    heading = doc.add_paragraph()
    heading.paragraph_format.space_before = Pt(12)
    set_font(heading.add_run("四、逐题正确率"), name=HEI, size=14, bold=True)
    per_question = make_table(doc, ["题号", "题目", "答对人数", "正确率", "考点"],
                              [1.4, 4.6, 2.2, 2.0, 5.8])
    for position, (seq, question) in enumerate(paper["questions"]):
        hits = sum(1 for student in cohort if student["items"][position]["is_correct"])
        rate = hits / count * 100
        finish_row(per_question, [
            str(seq), question.display, f"{hits} / {count}", f"{rate:.0f}%",
            "、".join(question.tags) or "—",
        ], colors={3: (RED if rate < 60 else None)})
        if position % 2 == 0:
            for cell in per_question.rows[-1].cells:
                shade(cell, "FAFAFA")
    lock_widths(per_question, [1.4, 4.6, 2.2, 2.0, 5.8])

    # ---- 五、口径说明
    heading = doc.add_paragraph()
    heading.paragraph_format.space_before = Pt(12)
    set_font(heading.add_run("五、口径与生成方式"), name=HEI, size=14, bold=True)
    generated = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    for line in [
        "得分口径（D80）：四舍五入到整数分，score_of(题量, 答对题数) —— 全系统唯一实现，"
        "学生端结果页、教师端成绩页、导出与本报告共用同一个函数，不存在第二套算法。",
        "判分口径：只判最终答案数值，脱式题的过程不计分（契约 §3）。",
        "未作答按答错计分。",
        f"试卷与题目：取自真实卷库（paper id={paper['id']}，状态 {paper['status']}），未改动。",
        f"演示数据：由固定随机种子 {seed} 确定性生成，重跑同一条命令得到同一份数据。",
        f"生成时间：{generated}。",
        f"复现命令：python tools/make_score_report.py --code {paper['code']} "
        f"--students {count} --seed {seed}",
    ]:
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(3)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        set_font(paragraph.add_run("· " + line), name=SONG, size=10)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    doc.save(out_path)


# ------------------------------------------------------------------ 入口
def main() -> int:
    parser = argparse.ArgumentParser(description="生成班级成绩报告（演示数据）")
    parser.add_argument("--code", default="2610-0049", help="卷号（默认 2610-0049）")
    parser.add_argument("--students", type=int, default=8, help="演示学生数（默认 8）")
    parser.add_argument("--seed", type=int, default=20261016, help="随机种子（默认 20261016）")
    parser.add_argument("--out", default=None, help="输出 .docx 路径")
    args = parser.parse_args()

    paper = load_paper(args.code)
    cohort = self_check(paper, build_cohort(paper, args.students, args.seed))
    out_path = args.out or DEFAULT_OUT.replace("2610-0049", args.code)
    build(paper, cohort, args.seed, out_path)

    print(f"卷：{paper['code']} {paper['title']}  题目 {len(paper['questions'])} 道  "
          f"(paper id={paper['id']})")
    print(f"演示学生：{len(cohort)} 人   " + "  ".join(
        f"{s['name']}={s['score']}分({s['correct']}/{s['total']})" for s in cohort))
    print(f"输出：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
